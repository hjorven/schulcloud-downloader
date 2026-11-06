from __future__ import annotations

import json
import threading
import time
import urllib.request
from urllib.error import HTTPError

import pytest

from sc_dl import interactive
from sc_dl.api import SchulCloudClient, login_local
from sc_dl.selection import build_tree, parse_selection
from sc_dl.web import AppState, create_server

from .fake_server import FakeSchulCloud

BASE = "https://brandenburg.cloud"


@pytest.fixture
def fake():
    return FakeSchulCloud()


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SC_DL_CONFIG_DIR", str(tmp_path / "cfg"))


def test_parse_selection():
    assert parse_selection("", 4) == [1, 2, 3, 4]
    assert parse_selection("alle", 2) == [1, 2]
    assert parse_selection("1, 3-4", 5) == [1, 3, 4]
    assert parse_selection("4-2", 5) == [2, 3, 4]
    with pytest.raises(ValueError):
        parse_selection("7", 5)


def test_interactive_flow(fake, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    answers = iter(
        [
            BASE,  # Adresse
            "a@b.de",  # E-Mail
            "1",  # Kurs/Raum: Mathe 10a
            "9",  # ungültig -> erneut gefragt
            "1-2",  # Abschnitte: die ersten beiden
            str(out),  # Zielordner
            "j",  # bestätigen
        ]
    )
    transport = fake.transport()
    code = interactive.run(
        input_fn=lambda _: next(answers),
        password_fn=lambda _: "pw",
        client_factory=lambda cfg: SchulCloudClient(cfg.base_url, cfg.token, delay=0, transport=transport),
        login_fn=lambda url, u, p: login_local(url, u, p, transport=transport),
    )
    text = capsys.readouterr().out
    assert code == 0, text
    assert "Ungültige Nummer" in text
    files = sorted(str(p.relative_to(out)).replace("\\", "/") for p in out.rglob("*") if p.is_file() and p.name != ".sc-dl-index.json")
    assert "1. Kurs: Mathe 10a  (8 Dateien" in text
    assert build_tree([]) == []
    assert files == ["Mathe 10a/Kursdateien/Blatt.pdf", "Mathe 10a/Kursdateien/Prüfungen/Klausur.pdf"], text


def _req(base, path, key=None, body=None, host=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET")
    if key:
        req.add_header("X-SC-Key", key)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if host:
        req.add_header("Host", host)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read()


def _wait(base, key, phase):
    for _ in range(100):
        _, raw = _req(base, "/api/status", key)
        st = json.loads(raw)
        if st["phase"] in (phase, "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("Zeitüberschreitung")


def test_web_flow(fake, tmp_path):
    transport = fake.transport()
    state = AppState(
        client_factory=lambda url, token: SchulCloudClient(url, token, delay=0, transport=transport),
        login_fn=lambda url, u, p: login_local(url, u, p, transport=transport),
    )
    server, key = create_server(state, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, page = _req(base, "/")
        assert status == 200 and key.encode() in page

        # ohne Schlüssel oder mit fremdem Host gesperrt
        with pytest.raises(HTTPError) as err:
            _req(base, "/api/status")
        assert err.value.code == 403
        with pytest.raises(HTTPError) as err:
            _req(base, "/api/status", key, host="evil.example")
        assert err.value.code == 403

        with pytest.raises(HTTPError) as err:
            _req(base, "/api/login", key, {"url": BASE, "username": "a@b.de", "password": "falsch"})
        assert err.value.code == 401

        status, raw = _req(base, "/api/login", key, {"url": BASE, "username": "a@b.de", "password": "pw"})
        assert json.loads(raw)["school"] == "Testschule"

        _req(base, "/api/scan", key, {})
        assert _wait(base, key, "scanned")["found"] == 9

        _, raw = _req(base, "/api/items", key)
        items = json.loads(raw)
        room_ids = [i["id"] for i in items if i["container"] == "Raum: Physik-AG"]
        assert len(room_ids) == 1

        out = tmp_path / "web-out"
        _req(base, "/api/download", key, {"ids": room_ids, "out": str(out)})
        st = _wait(base, key, "done")
        assert st["result"]["downloaded"] == 1, st
        assert st["progress"] == {"done": 1, "total": 1}
        assert (out / "Raum Physik-AG/Board - AG/01 Abschnitt 1/01 Karte 1/Protokoll.pdf").read_bytes() == b"ROOMPD"
    finally:
        server.shutdown()
        server.server_close()
