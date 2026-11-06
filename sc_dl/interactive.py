"""Geführter Ablauf im Terminal: anmelden, suchen, auswählen, herunterladen."""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Callable

from .api import ApiError, AuthError, SchulCloudClient, login_local
from .config import DEFAULT_BASE_URL, Config, token_is_expired
from .crawler import Crawler, FileItem
from .download import download_all, plan_paths
from .filters import extension
from .selection import build_tree, parse_selection

InputFn = Callable[[str], str]


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def files_word(n: int) -> str:
    return "1 Datei" if n == 1 else f"{n} Dateien"


def ask(prompt: str, default: str = "", input_fn: InputFn = input) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input_fn(f"{prompt}{suffix}: ").strip()
    return answer or default


def ask_selection(prompt: str, maximum: int, input_fn: InputFn = input) -> list[int]:
    while True:
        answer = input_fn(f"{prompt} (z.B. 1,3-5; Enter = alle): ")
        try:
            chosen = parse_selection(answer, maximum)
        except ValueError as err:
            print(f"  {err}")
            continue
        if chosen:
            return chosen
        print("  Bitte mindestens eine Nummer angeben.")


def get_client(
    input_fn: InputFn = input,
    password_fn: Callable[[str], str] = getpass.getpass,
    client_factory: Callable[[Config], SchulCloudClient] | None = None,
    login_fn=login_local,
) -> SchulCloudClient | None:
    cfg = Config.load()
    factory = client_factory or (lambda c: SchulCloudClient(c.base_url, c.token or "", api_v1_prefix=c.api_v1_prefix))

    if cfg.token and not token_is_expired(cfg.token):
        reuse = ask(f"Gespeicherte Anmeldung für {cfg.base_url} verwenden? (j/n)", "j", input_fn)
        if reuse.lower().startswith("j"):
            return factory(cfg)

    cfg.base_url = ask("Adresse der Schul-Cloud", cfg.base_url or DEFAULT_BASE_URL, input_fn).rstrip("/")
    for _ in range(3):
        user = ask("E-Mail", "", input_fn)
        password = password_fn("Passwort (wird nicht angezeigt): ")
        try:
            cfg.token = login_fn(cfg.base_url, user, password)
        except AuthError:
            print("  E-Mail oder Passwort falsch, bitte erneut versuchen.")
            continue
        except ApiError as err:
            print(f"  Anmeldung fehlgeschlagen: {err}")
            return None
        cfg.save()
        print("  Angemeldet.")
        return factory(cfg)
    print("Drei Fehlversuche, Abbruch.")
    return None


def choose(items: list[FileItem], input_fn: InputFn = input) -> list[FileItem]:
    tree = build_tree(items)
    print("\nGefunden:")
    for n, c in enumerate(tree, start=1):
        print(f"  {n:>3}. {c.label}  ({files_word(c.count)}, {human_size(c.size)})")
    picked_containers = [tree[i - 1] for i in ask_selection("\nWelche Kurse/Räume?", len(tree), input_fn)]

    groups = [(c, g) for c in picked_containers for g in c.groups.values()]
    if len(groups) > 1:
        print("\nAbschnitte:")
        current = None
        for n, (c, g) in enumerate(groups, start=1):
            if c is not current:
                print(f"  {c.label}")
                current = c
            print(f"    {n:>3}. {g.label}  ({files_word(len(g.items))}, {human_size(g.size)})")
        picked = ask_selection("\nWelche Abschnitte?", len(groups), input_fn)
        indices = [i for n in picked for i in groups[n - 1][1].items]
    else:
        indices = [i for _, g in groups for i in g.items]

    selected = [items[i] for i in sorted(indices)]

    types = sorted({extension(i.name) or "(ohne)" for i in selected})
    if len(types) > 1:
        print(f"\nDateitypen in der Auswahl: {', '.join(types)}")
        answer = ask("Nur bestimmte Typen? (z.B. pdf,docx; Enter = alle)", "", input_fn)
        if answer:
            wanted = {t.strip().lower().lstrip(".") for t in answer.split(",") if t.strip()}
            selected = [i for i in selected if (extension(i.name) or "(ohne)") in wanted]
    return selected


def run(
    input_fn: InputFn = input,
    password_fn: Callable[[str], str] = getpass.getpass,
    client_factory: Callable[[Config], SchulCloudClient] | None = None,
    login_fn=login_local,
) -> int:
    print("Schul-Cloud Massendownload\n")
    client = get_client(input_fn, password_fn, client_factory, login_fn)
    if client is None:
        return 1
    with client:
        print("\nSuche Dateien in allen Kursen und Räumen ...")
        try:
            report = Crawler(client, log=lambda m: print(f"  {m}")).run()
        except AuthError as err:
            print(str(err))
            return 2
        if not report.items:
            print("Keine Dateien gefunden.")
            return 0

        selected = choose(report.items, input_fn)
        if not selected:
            print("Nichts ausgewählt.")
            return 0

        out = Path(ask("\nZielordner", str(Path.home() / "Schulcloud"), input_fn)).expanduser().resolve()
        total = sum(i.size or 0 for i in selected)
        confirm = ask(f"{files_word(len(selected))} ({human_size(total)}) nach {out} laden? (j/n)", "j", input_fn)
        if not confirm.lower().startswith("j"):
            print("Abgebrochen.")
            return 0
        result = download_all(client, plan_paths(selected, out), out)
    print(
        f"\nFertig: {result.downloaded} geladen, {result.skipped} schon vorhanden, "
        f"{result.failed} fehlgeschlagen. Ordner: {out}"
    )
    return 1 if result.failed else 0
