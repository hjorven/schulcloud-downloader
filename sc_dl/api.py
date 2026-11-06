"""Dünner HTTP-Client für die Schul-Cloud-API (NestJS /api/v3 und Feathers /api/v1).

Die Endpunkte stammen aus dem Quellcode von hpi-schul-cloud/schulcloud-server.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote, urljoin, urlparse

import httpx

USER_AGENT = "schulcloud-downloader/0.1"
PAGE_SIZE = 50
RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(Exception):
    def __init__(self, status: int, message: str, path: str = ""):
        super().__init__(f"HTTP {status} bei {path}: {message}")
        self.status = status
        self.path = path


class AuthError(ApiError):
    pass


def login_local(base_url: str, username: str, password: str, transport: httpx.BaseTransport | None = None) -> str:
    """Login mit E-Mail/Benutzername und Passwort. Gibt den JWT (accessToken) zurück."""
    with httpx.Client(
        base_url=base_url, timeout=30, transport=transport, headers={"User-Agent": USER_AGENT}
    ) as client:
        resp = client.post("/api/v3/authentication/local", json={"username": username, "password": password})
    if resp.status_code in (401, 403):
        raise AuthError(resp.status_code, "Benutzername oder Passwort falsch", "/api/v3/authentication/local")
    if resp.status_code >= 400:
        raise ApiError(resp.status_code, resp.text[:300], "/api/v3/authentication/local")
    token = resp.json().get("accessToken")
    if not token:
        raise ApiError(resp.status_code, "Antwort enthält keinen accessToken", "/api/v3/authentication/local")
    return token


class SchulCloudClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        api_v1_prefix: str = "/api/v1",
        delay: float = 0.1,
        transport: httpx.BaseTransport | None = None,
        retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_v1_prefix = "/" + api_v1_prefix.strip("/")
        self.delay = delay
        self.retries = retries
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(60, connect=15),
            transport=transport,
            follow_redirects=False,
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
        )
        # Ohne Token: für vorab signierte Speicher-URLs (S3), die keinen Login brauchen.
        self._plain = httpx.Client(
            timeout=httpx.Timeout(120, connect=15),
            transport=transport,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        self._client.close()
        self._plain.close()

    def __enter__(self) -> "SchulCloudClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ Basis
    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            if self.delay:
                time.sleep(self.delay)
            try:
                resp = self._client.request(method, path, **kwargs)
            except httpx.TransportError:
                if attempt > self.retries:
                    raise
                time.sleep(2**attempt)
                continue
            if resp.status_code in RETRY_STATUS and attempt <= self.retries:
                wait = resp.headers.get("Retry-After")
                time.sleep(float(wait) if wait and wait.isdigit() else 2**attempt)
                continue
            if resp.status_code == 401:
                raise AuthError(401, "Nicht angemeldet oder Token abgelaufen. Bitte `sc-dl login` ausführen.", path)
            if resp.status_code >= 400:
                raise ApiError(resp.status_code, resp.text[:300], path)
            return resp

    def get_json(self, path: str, params: Any = None) -> Any:
        return self._request("GET", path, params=params).json()

    def v1(self, path: str) -> str:
        return f"{self.api_v1_prefix}/{path.lstrip('/')}"

    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        skip = 0
        while True:
            query = dict(params or {}, skip=skip, limit=PAGE_SIZE)
            page = self.get_json(path, params=query)
            data = page.get("data", [])
            yield from data
            skip += len(data)
            total = page.get("total")
            if not data or (total is not None and skip >= total):
                break

    # --------------------------------------------------------------- Struktur
    def me(self) -> dict[str, Any]:
        return self.get_json("/api/v3/me")

    def courses(self) -> list[dict[str, Any]]:
        return list(self._paginate("/api/v3/courses"))

    def course_room_board(self, course_id: str) -> dict[str, Any]:
        return self.get_json(f"/api/v3/course-rooms/{course_id}/board")

    def lesson(self, lesson_id: str) -> dict[str, Any]:
        return self.get_json(f"/api/v3/lessons/{lesson_id}")

    def rooms(self) -> list[dict[str, Any]]:
        return self.get_json("/api/v3/rooms").get("data", [])

    def room_boards(self, room_id: str) -> list[dict[str, Any]]:
        return self.get_json(f"/api/v3/rooms/{room_id}/boards").get("data", [])

    def board(self, board_id: str) -> dict[str, Any]:
        return self.get_json(f"/api/v3/boards/{board_id}")

    def cards(self, card_ids: list[str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for i in range(0, len(card_ids), 20):
            chunk = card_ids[i : i + 20]
            result.extend(self.get_json("/api/v3/cards", params=[("ids", cid) for cid in chunk]).get("data", []))
        return result

    # ---------------------------------------------------------------- Dateien
    def list_files(self, school_id: str, parent_type: str, parent_id: str) -> list[dict[str, Any]]:
        """Neuer Datei-Speicher (files-storage)."""
        return list(self._paginate(f"/api/v3/file/list/school/{school_id}/{parent_type}/{parent_id}"))

    def list_legacy_files(self, owner_id: str, parent_id: str | None = None) -> list[dict[str, Any]]:
        """Alter Datei-Speicher (Kursdateien). Gibt Dateien und Ordner einer Ebene zurück."""
        params = {"owner": owner_id}
        if parent_id:
            params["parent"] = parent_id
        data = self.get_json(self.v1("/fileStorage"), params=params)
        if isinstance(data, dict):
            data = data.get("data", [])
        return list(data)

    def legacy_signed_url(self, file_id: str) -> str:
        data = self.get_json(self.v1("/fileStorage/signedUrl"), params={"file": file_id, "download": "true"})
        url = data.get("url") if isinstance(data, dict) else None
        if not url:
            raise ApiError(200, "Keine Download-URL erhalten", "/fileStorage/signedUrl")
        return url

    @staticmethod
    def file_download_path(file_record: dict[str, Any]) -> str:
        url = file_record.get("url")
        if url:
            return url
        return f"/api/v3/file/download/{file_record['id']}/{quote(file_record['name'])}"

    def download_to(self, url: str, target: Path, authenticated: bool = True) -> int:
        """Lädt `url` nach `target` (über eine .part-Datei). Gibt die Bytes zurück."""
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        absolute = urljoin(self.base_url + "/", url)
        same_host = urlparse(absolute).netloc == urlparse(self.base_url).netloc
        # Den Token nur an die Schul-Cloud selbst schicken, nie an fremde Hosts.
        client = self._client if (authenticated and same_host) else self._plain

        attempt = 0
        while True:
            attempt += 1
            try:
                written = 0
                with client.stream("GET", absolute, follow_redirects=True) as resp:
                    if resp.status_code in RETRY_STATUS and attempt <= self.retries:
                        time.sleep(2**attempt)
                        continue
                    if resp.status_code == 401:
                        raise AuthError(401, "Token abgelaufen", url)
                    if resp.status_code >= 400:
                        raise ApiError(resp.status_code, "Download fehlgeschlagen", url)
                    with open(tmp, "wb") as fh:
                        for chunk in resp.iter_bytes(chunk_size=1 << 16):
                            fh.write(chunk)
                            written += len(chunk)
                tmp.replace(target)
                return written
            except httpx.TransportError:
                tmp.unlink(missing_ok=True)
                if attempt > self.retries:
                    raise
                time.sleep(2**attempt)
