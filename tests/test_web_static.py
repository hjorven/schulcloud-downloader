from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from .http_fake import start

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js nicht installiert")
def test_static_app_crawls_and_zips(tmp_path):
    server, _ = start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        out = tmp_path / "out.zip"
        proc = subprocess.run(
            ["node", str(ROOT / "tests" / "web_node_check.js"), base, str(out)],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout)
    finally:
        server.shutdown()
        server.server_close()

    assert data["found"] == 9
    assert data["result"]["failed"] == 0, data["result"]
    assert data["badLogin"] == "E-Mail oder Passwort falsch."
    with zipfile.ZipFile(out) as zf:
        assert zf.testzip() is None
        names = sorted(zf.namelist())
        assert "Mathe 10a/Kursdateien/Prüfungen/Klausur.pdf" in names
        assert zf.read("Mathe 10a/Kursdateien/Prüfungen/Klausur.pdf") == b"123456789"
        assert zf.read("Raum Physik-AG/Board - AG/01 Abschnitt 1/01 Karte 1/Protokoll.pdf") == b"ROOMPD"
        assert "Mathe 10a/Board - Unterricht/01 Woche 3/02 Bilder/Fotos/Bild (2).png" in names
        assert len(names) == 9
