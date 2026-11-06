"""Kommandozeile: sc-dl login | logout | probe | list | download"""

from __future__ import annotations

import argparse
import csv
import getpass
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from .api import ApiError, AuthError, SchulCloudClient, login_local
from .config import Config, config_file, token_is_expired
from .crawler import Crawler, FileItem
from .download import download_all, plan_paths
from .filters import FileFilter, parse_day


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def human_size(n: int | None) -> str:
    if n is None:
        return "?"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def make_client(cfg: Config, delay: float = 0.1) -> SchulCloudClient:
    if not cfg.token:
        raise SystemExit("Nicht angemeldet. Bitte zuerst `sc-dl login` ausführen.")
    if token_is_expired(cfg.token):
        raise SystemExit("Das gespeicherte Token ist abgelaufen. Bitte erneut `sc-dl login` ausführen.")
    return SchulCloudClient(cfg.base_url, cfg.token, api_v1_prefix=cfg.api_v1_prefix, delay=delay)


# ---------------------------------------------------------------------- login
def cmd_login(args: argparse.Namespace) -> int:
    cfg = Config.load()
    if args.url:
        cfg.base_url = args.url.rstrip("/")
    if args.api_v1_prefix:
        cfg.api_v1_prefix = args.api_v1_prefix
    if args.token:
        token = args.token.strip()
    else:
        user = args.user or input("E-Mail / Benutzername: ").strip()
        password = getpass.getpass("Passwort (wird nicht angezeigt): ")
        try:
            token = login_local(cfg.base_url, user, password)
        except AuthError:
            eprint("Anmeldung fehlgeschlagen: E-Mail oder Passwort falsch.")
            return 1
        except ApiError as err:
            eprint(f"Anmeldung fehlgeschlagen: {err}")
            return 1
    cfg.token = token
    with SchulCloudClient(cfg.base_url, token, api_v1_prefix=cfg.api_v1_prefix, delay=0) as client:
        try:
            me = client.me()
        except ApiError as err:
            eprint(f"Token wurde nicht akzeptiert: {err}")
            return 1
    path = cfg.save()
    user = me.get("user") or {}
    school = me.get("school") or {}
    print(f"Angemeldet als {user.get('firstName', '')} {user.get('lastName', '')} ({school.get('name', '?')}).")
    print(f"Zugang gespeichert in {path}")
    return 0


def cmd_logout(_: argparse.Namespace) -> int:
    cfg = Config.load()
    cfg.clear_token()
    print(f"Token gelöscht ({config_file()}).")
    return 0


# ---------------------------------------------------------------------- probe
def cmd_probe(_: argparse.Namespace) -> int:
    """Prüft, welche Endpunkte bei dieser Instanz funktionieren. Gibt keine Inhalte aus."""
    cfg = Config.load()
    client = make_client(cfg, delay=0)
    print(f"sc-dl {__version__}, Instanz: {cfg.base_url}, v1-Präfix: {cfg.api_v1_prefix}")

    def check(label: str, fn):
        try:
            value = fn()
            print(f"OK    {label}")
            return value
        except ApiError as err:
            print(f"FEHLT {label}: HTTP {err.status}")
            return None
        except Exception as err:  # noqa: BLE001
            print(f"FEHLT {label}: {type(err).__name__}: {err}")
            return None

    with client:
        me = check("GET /api/v3/me", client.me) or {}
        school_id = (me.get("school") or {}).get("id", "")
        courses = check("GET /api/v3/courses", client.courses) or []
        print(f"      {len(courses)} Kurse gefunden")
        rooms = check("GET /api/v3/rooms", client.rooms) or []
        print(f"      {len(rooms)} Räume gefunden")
        stats = Counter()
        for course in courses[:3]:
            board = check(f"GET /api/v3/course-rooms/<kurs>/board", lambda: client.course_room_board(course["id"]))
            for el in (board or {}).get("elements", []):
                stats[el.get("type")] += 1
            check("GET /api/v1/fileStorage?owner=<kurs>", lambda: client.list_legacy_files(course["id"]))
            check(
                "GET /api/v3/file/list/school/<schule>/courses/<kurs>",
                lambda: client.list_files(school_id, "courses", course["id"]),
            )
        if stats:
            print("      Elemente in den ersten Kursen: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
        for room in rooms[:1]:
            boards = check("GET /api/v3/rooms/<raum>/boards", lambda: client.room_boards(room["id"])) or []
            for board in boards[:1]:
                check("GET /api/v3/boards/<board>", lambda: client.board(board["id"]))
    print("Fertig. Diese Ausgabe enthält keine persönlichen Inhalte und kann geteilt werden.")
    return 0


# ----------------------------------------------------------------- list/download
def build_filter(args: argparse.Namespace) -> FileFilter:
    return FileFilter(
        containers=args.kurs or [],
        topics=args.thema or [],
        types=args.typ or [],
        since=parse_day(args.seit) if args.seit else None,
        until=parse_day(args.bis) if args.bis else None,
    )


def crawl(args: argparse.Namespace, client: SchulCloudClient) -> list[FileItem]:
    crawler = Crawler(
        client,
        log=eprint if not args.leise else (lambda _: None),
        include_courses=args.nur != "raeume",
        include_rooms=args.nur != "kurse",
        include_legacy_files=not args.ohne_kursdateien,
        container_filter=args.kurs,
    )
    report = crawler.run()
    items = build_filter(args).apply(report.items)
    if report.warnings:
        eprint(f"{len(report.warnings)} Hinweise beim Einsammeln (siehe oben).")
    return items


def cmd_list(args: argparse.Namespace) -> int:
    cfg = Config.load()
    with make_client(cfg) as client:
        items = crawl(args, client)
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(["Art", "Kurs/Raum", "Ordner", "Datei", "Groesse", "Geaendert", "Quelle"])
            for i in items:
                writer.writerow([i.container_kind, i.container_name, "/".join(i.folders), i.name, i.size, i.updated, i.source])
        print(f"{len(items)} Dateien nach {args.csv} geschrieben.")
        return 0
    by_container: dict[str, list[FileItem]] = {}
    for i in items:
        by_container.setdefault(f"{i.container_kind}: {i.container_name}", []).append(i)
    for container, files in by_container.items():
        total = sum(f.size or 0 for f in files)
        print(f"{container}  ({len(files)} Dateien, {human_size(total)})")
        if args.dateien:
            for f in files:
                print(f"    {'/'.join(f.folders)}/{f.name}  [{human_size(f.size)}]")
    print(f"Gesamt: {len(items)} Dateien, {human_size(sum(i.size or 0 for i in items))}")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    cfg = Config.load()
    out_dir = Path(args.out).expanduser().resolve()
    with make_client(cfg) as client:
        items = crawl(args, client)
        planned = plan_paths(items, out_dir)
        print(f"{len(planned)} Dateien ({human_size(sum(i.size or 0 for i in items))}) nach {out_dir}")
        if args.dry_run:
            for _, target in planned:
                print(f"    {target.relative_to(out_dir)}")
            print("Probelauf: nichts heruntergeladen.")
            return 0
        result = download_all(client, planned, out_dir, workers=args.parallel)
    print(
        f"Fertig: {result.downloaded} geladen ({human_size(result.bytes)}), "
        f"{result.skipped} schon aktuell, {result.failed} fehlgeschlagen."
    )
    return 1 if result.failed else 0


# ----------------------------------------------------------------------- main
def add_filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--kurs", action="append", metavar="TEXT", help="Nur Kurse/Räume, deren Name TEXT enthält (mehrfach möglich)")
    p.add_argument("--thema", action="append", metavar="TEXT", help="Nur Themen/Board-Abschnitte, die TEXT enthalten (mehrfach möglich)")
    p.add_argument("--typ", action="append", metavar="ENDUNG", help="Nur Dateien mit dieser Endung, z.B. pdf (mehrfach möglich)")
    p.add_argument("--seit", metavar="DATUM", help="Nur Dateien, geändert ab DATUM (JJJJ-MM-TT oder TT.MM.JJJJ)")
    p.add_argument("--bis", metavar="DATUM", help="Nur Dateien, geändert bis DATUM (einschließlich)")
    p.add_argument("--nur", choices=["kurse", "raeume"], help="Nur Kurse oder nur Räume durchsuchen")
    p.add_argument("--ohne-kursdateien", action="store_true", help="Den alten Bereich 'Kursdateien' auslassen")
    p.add_argument("--leise", action="store_true", help="Keine Fortschrittsmeldungen beim Einsammeln")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sc-dl", description="Massendownload aus der Schul-Cloud")
    parser.add_argument("--version", action="version", version=f"sc-dl {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="Anmelden und Token speichern")
    p.add_argument("--url", help="Adresse der Schul-Cloud, Standard: https://brandenburg.cloud")
    p.add_argument("--user", help="E-Mail bzw. Benutzername (sonst wird gefragt)")
    p.add_argument("--token", help="Statt Passwort: Wert des Browser-Cookies 'jwt'")
    p.add_argument("--api-v1-prefix", help="Pfad der alten API, Standard: /api/v1")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("logout", help="Gespeichertes Token löschen")
    p.set_defaults(func=cmd_logout)

    p = sub.add_parser("probe", help="Prüfen, welche Schnittstellen funktionieren (ohne Inhalte)")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("list", help="Übersicht anzeigen, ohne etwas zu laden")
    add_filter_args(p)
    p.add_argument("--dateien", action="store_true", help="Einzelne Dateien mit auflisten")
    p.add_argument("--csv", metavar="DATEI", help="Liste als CSV (für Excel) speichern")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("download", help="Dateien herunterladen")
    add_filter_args(p)
    p.add_argument("--out", default="Schulcloud", help="Zielordner, Standard: ./Schulcloud")
    p.add_argument("--parallel", type=int, default=3, help="Gleichzeitige Downloads (Standard 3, max. sinnvoll 5)")
    p.add_argument("--dry-run", action="store_true", help="Nur anzeigen, was geladen würde")
    p.set_defaults(func=cmd_download)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "parallel", 1) > 8:
        args.parallel = 8
    try:
        return args.func(args)
    except AuthError as err:
        eprint(str(err))
        return 2
    except KeyboardInterrupt:
        eprint("Abgebrochen. Ein erneuter Aufruf setzt dort fort.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
