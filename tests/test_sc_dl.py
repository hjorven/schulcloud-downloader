from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest

from sc_dl import cli
from sc_dl.api import AuthError, SchulCloudClient, login_local
from sc_dl.config import Config, token_is_expired
from sc_dl.crawler import Crawler
from sc_dl.download import download_all, plan_paths, safe_name
from sc_dl.filters import FileFilter, parse_day

from .fake_server import FakeSchulCloud

BASE = "https://brandenburg.cloud"


@pytest.fixture
def fake():
    return FakeSchulCloud()


@pytest.fixture
def client(fake):
    c = SchulCloudClient(BASE, "tok", delay=0, transport=fake.transport())
    yield c
    c.close()


def rel_paths(planned, out):
    return sorted(str(t.relative_to(out)).replace("\\", "/") for _, t in planned)


def test_login_local(fake):
    token = login_local(BASE, "a@b.de", "pw", transport=fake.transport())
    assert token.startswith("header.")
    with pytest.raises(AuthError):
        login_local(BASE, "a@b.de", "falsch", transport=fake.transport())


def test_crawl_collects_all_sources(client, tmp_path):
    report = Crawler(client).run()
    planned = plan_paths(report.items, tmp_path)
    assert rel_paths(planned, tmp_path) == [
        "Mathe 10a/Aufgaben/Hausaufgabe 1/Aufgabe 1.docx",
        "Mathe 10a/Board - Unterricht/01 Woche 3/01 Geraden/Folien.pdf",
        "Mathe 10a/Board - Unterricht/01 Woche 3/02 Bilder/Fotos/Bild (2).png",
        "Mathe 10a/Board - Unterricht/01 Woche 3/02 Bilder/Fotos/Bild.png",
        "Mathe 10a/Kursdateien/Blatt.pdf",
        "Mathe 10a/Kursdateien/Prüfungen/Klausur.pdf",
        "Mathe 10a/Themen/01 Lineare Funktionen/Arbeitsblatt.pdf",
        "Mathe 10a/Themen/01 Lineare Funktionen/Blatt.pdf",
        "Raum Physik-AG/Board - AG/01 Abschnitt 1/01 Karte 1/Protokoll.pdf",
    ]
    assert any("gesperrt" in w for w in report.warnings)


def test_filters(client):
    items = Crawler(client).run().items
    only_pdf_topic = FileFilter(topics=["funktion"], types=["PDF"]).apply(items)
    assert {i.name for i in only_pdf_topic} == {"Arbeitsblatt.pdf", "Blatt.pdf"}

    since = FileFilter(since=parse_day("01.10.2026")).apply(items)
    assert "Klausur.pdf" not in {i.name for i in since}
    assert "Aufgabe 1.docx" not in {i.name for i in since}

    until = FileFilter(until=date(2026, 6, 30)).apply(items)
    assert [i.name for i in until] == ["Klausur.pdf"]

    rooms = FileFilter(containers=["physik"]).apply(items)
    assert [i.name for i in rooms] == ["Protokoll.pdf"]


def test_container_filter_skips_requests(fake, client):
    Crawler(client, container_filter=["physik"]).run()
    paths = [r.url.path for r in fake.requests]
    assert not any("course-rooms" in p for p in paths)


def test_download_and_resume(fake, client, tmp_path):
    items = Crawler(client).run().items
    planned = plan_paths(items, tmp_path)
    result = download_all(client, planned, tmp_path, workers=2, log=lambda _: None)
    assert result.failed == 0
    assert result.downloaded == len(planned)
    assert (tmp_path / "Mathe 10a/Kursdateien/Prüfungen/Klausur.pdf").read_bytes() == b"123456789"
    assert (tmp_path / "Raum Physik-AG/Board - AG/01 Abschnitt 1/01 Karte 1/Protokoll.pdf").read_bytes() == b"ROOMPD"
    assert not list(tmp_path.rglob("*.part"))

    # Token darf nie an den externen Speicher-Host gehen
    for req, auth in zip(fake.requests, fake.auth_headers):
        if req.url.host == "s3.example.org":
            assert auth is None

    again = download_all(client, planned, tmp_path, workers=2, log=lambda _: None)
    assert again.downloaded == 0
    assert again.skipped == len(planned)


def test_safe_name():
    assert safe_name('a/b:c*?"<>|.') == "a_b_c______"
    assert safe_name("CON.txt") == "_CON.txt"
    assert safe_name("  ") == "_"
    long = safe_name("x" * 300 + ".pdf")
    assert len(long) == 120 and long.endswith(".pdf")


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SC_DL_CONFIG_DIR", str(tmp_path))
    cfg = Config(base_url="https://example.org", token="abc")
    path = cfg.save()
    assert Config.load().token == "abc"
    assert json.loads(path.read_text())["base_url"] == "https://example.org"
    if os.name != "nt":  # Windows kennt keine POSIX-Rechte; %APPDATA% ist per ACL nur für den Benutzer
        assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_token_expiry():
    # {"exp":1} bzw. {"exp":9999999999}
    assert token_is_expired("h.eyJleHAiOjF9.s")
    assert not token_is_expired("h.eyJleHAiOjk5OTk5OTk5OTl9.s")
    assert not token_is_expired("kein-jwt")


def test_cli_download_dry_run(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SC_DL_CONFIG_DIR", str(tmp_path / "cfg"))
    Config(base_url=BASE, token="h.eyJleHAiOjk5OTk5OTk5OTl9.s").save()
    monkeypatch.setattr(
        cli, "make_client", lambda cfg, delay=0.1: SchulCloudClient(BASE, cfg.token, delay=0, transport=fake.transport())
    )
    out = tmp_path / "out"
    code = cli.main(["download", "--out", str(out), "--kurs", "mathe", "--typ", "pdf", "--dry-run", "--leise"])
    assert code == 0
    text = capsys.readouterr().out
    assert "5 Dateien" in text
    assert "Probelauf" in text
    assert not out.exists()
