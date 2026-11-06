"""Lokale Webseite: läuft nur auf dem eigenen Rechner (127.0.0.1) und wird im Browser bedient.

Zugangsdaten gehen nur vom eigenen Rechner direkt an die Schul-Cloud, nirgendwo sonst hin.
"""

from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Callable

from .api import ApiError, AuthError, SchulCloudClient, login_local
from .config import DEFAULT_BASE_URL, Config, token_is_expired
from .crawler import Crawler, FileItem
from .download import download_all, plan_paths
from .selection import container_label, group_label

MAX_BODY = 1 << 20


class AppState:
    def __init__(
        self,
        client_factory: Callable[[str, str], SchulCloudClient] | None = None,
        login_fn: Callable[[str, str, str], str] = login_local,
    ):
        self.lock = threading.Lock()
        self.client_factory = client_factory or (lambda url, token: SchulCloudClient(url, token))
        self.login_fn = login_fn
        self.client: SchulCloudClient | None = None
        self.user_name = ""
        self.items: list[FileItem] = []
        self.phase = "login"  # login | ready | scanning | scanned | downloading | done | error
        self.log: list[str] = []
        self.progress = {"done": 0, "total": 0}
        self.result: dict[str, Any] | None = None
        self.error = ""

    def add_log(self, msg: str) -> None:
        with self.lock:
            self.log.append(msg)
            del self.log[:-200]

    # ------------------------------------------------------------- Aktionen
    def login(self, url: str, username: str, password: str, use_saved: bool) -> dict[str, Any]:
        url = (url or DEFAULT_BASE_URL).rstrip("/")
        cfg = Config.load()
        if use_saved and cfg.token and not token_is_expired(cfg.token):
            url, token = cfg.base_url, cfg.token
        else:
            token = self.login_fn(url, username, password)
            cfg.base_url, cfg.token = url, token
            cfg.save()
        client = self.client_factory(url, token)
        me = client.me()
        user = me.get("user") or {}
        with self.lock:
            if self.client:
                self.client.close()
            self.client = client
            self.user_name = f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()
            self.phase = "ready"
        return {"name": self.user_name, "school": (me.get("school") or {}).get("name", "")}

    def start_scan(self) -> None:
        if not self.client:
            raise RuntimeError("Nicht angemeldet")
        with self.lock:
            if self.phase in ("scanning", "downloading"):
                raise RuntimeError("Es läuft bereits ein Vorgang")
            self.phase, self.log, self.items, self.error = "scanning", [], [], ""

        def work() -> None:
            try:
                report = Crawler(self.client, log=self.add_log).run()
                with self.lock:
                    self.items = report.items
                    self.phase = "scanned"
            except Exception as err:  # noqa: BLE001
                with self.lock:
                    self.error, self.phase = str(err), "error"

        threading.Thread(target=work, daemon=True).start()

    def start_download(self, ids: list[int], out: str) -> None:
        if not self.client:
            raise RuntimeError("Nicht angemeldet")
        with self.lock:
            if self.phase in ("scanning", "downloading"):
                raise RuntimeError("Es läuft bereits ein Vorgang")
            selected = [self.items[i] for i in sorted(set(ids)) if 0 <= i < len(self.items)]
            if not selected:
                raise ValueError("Keine Dateien ausgewählt")
            out_dir = Path(out or (Path.home() / "Schulcloud")).expanduser().resolve()
            self.phase, self.log, self.error, self.result = "downloading", [], "", None
            self.progress = {"done": 0, "total": len(selected)}

        def on_progress(done: int, total: int) -> None:
            with self.lock:
                self.progress = {"done": done, "total": total}

        def work() -> None:
            try:
                planned = plan_paths(selected, out_dir)
                res = download_all(self.client, planned, out_dir, log=self.add_log, progress=on_progress)
                with self.lock:
                    self.result = {**asdict(res), "out": str(out_dir)}
                    self.phase = "done"
            except Exception as err:  # noqa: BLE001
                with self.lock:
                    self.error, self.phase = str(err), "error"

        threading.Thread(target=work, daemon=True).start()

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "phase": self.phase,
                "log": self.log[-30:],
                "found": len(self.items),
                "progress": dict(self.progress),
                "result": self.result,
                "error": self.error,
                "user": self.user_name,
            }

    def items_json(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                {
                    "id": i,
                    "container": container_label(it),
                    "group": group_label(it),
                    "path": "/".join(it.folders[2:]),
                    "name": it.name,
                    "size": it.size,
                    "updated": it.updated,
                }
                for i, it in enumerate(self.items)
            ]


def make_handler(state: AppState, key: str, page: bytes):
    class Handler(BaseHTTPRequestHandler):
        server_version = "sc-dl"

        def log_message(self, *args: Any) -> None:  # keine Zugriffs-Logs im Terminal
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: Any) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _authorized(self) -> bool:
            # Schützt vor Zugriffen anderer Webseiten auf diesen lokalen Server.
            return self._host_ok() and secrets.compare_digest(self.headers.get("X-SC-Key", ""), key)

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            path = self.path.split("?")[0]
            if path == "/":
                return self._send(200, page, "text/html; charset=utf-8")
            if not self._authorized():
                return self._json(403, {"error": "forbidden"})
            if path == "/api/status":
                return self._json(200, state.status())
            if path == "/api/items":
                return self._json(200, state.items_json())
            if path == "/api/defaults":
                cfg = Config.load()
                saved = bool(cfg.token and not token_is_expired(cfg.token))
                return self._json(
                    200,
                    {"url": cfg.base_url, "saved": saved, "out": str(Path.home() / "Schulcloud")},
                )
            return self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                return self._json(403, {"error": "forbidden"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json(413, {"error": "zu groß"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "ungültiges JSON"})
            path = self.path.split("?")[0]
            try:
                if path == "/api/login":
                    info = state.login(
                        body.get("url", ""), body.get("username", ""), body.get("password", ""), bool(body.get("useSaved"))
                    )
                    return self._json(200, info)
                if path == "/api/scan":
                    state.start_scan()
                    return self._json(202, {"ok": True})
                if path == "/api/download":
                    state.start_download([int(i) for i in body.get("ids", [])], body.get("out", ""))
                    return self._json(202, {"ok": True})
            except AuthError:
                return self._json(401, {"error": "E-Mail oder Passwort falsch bzw. Anmeldung abgelaufen."})
            except ApiError as err:
                return self._json(502, {"error": f"Schul-Cloud antwortet mit Fehler: HTTP {err.status}"})
            except (RuntimeError, ValueError) as err:
                return self._json(409, {"error": str(err)})
            except Exception as err:  # noqa: BLE001
                return self._json(500, {"error": f"{type(err).__name__}: {err}"})
            return self._json(404, {"error": "not found"})

    return Handler


def load_page(key: str) -> bytes:
    html = resources.files("sc_dl").joinpath("web_page.html").read_text(encoding="utf-8")
    return html.replace("__SC_KEY__", key).encode("utf-8")


def create_server(state: AppState, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    key = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state, key, load_page(key)))
    server.daemon_threads = True
    return server, key


def serve(port: int = 8765, open_browser: bool = True) -> int:
    state = AppState()
    try:
        server, _ = create_server(state, port)
    except OSError:
        server, _ = create_server(state, 0)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Webseite läuft auf {url}")
    print("Nur auf diesem Rechner erreichbar. Beenden mit Strg+C.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        server.server_close()
        if state.client:
            state.client.close()
    return 0
