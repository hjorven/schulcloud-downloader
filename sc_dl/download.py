"""Ordnerstruktur planen und Dateien parallel herunterladen."""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .api import ApiError, AuthError, SchulCloudClient
from .crawler import FileItem

INDEX_NAME = ".sc-dl-index.json"
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name: str, max_len: int = 120) -> str:
    """Macht einen Namen unter Windows, macOS und Linux als Datei-/Ordnername nutzbar."""
    cleaned = _INVALID.sub("_", name).strip().rstrip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "_"
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = "_" + cleaned
    if len(cleaned) > max_len:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            cleaned = stem[: max_len - len(ext) - 1].rstrip() + "." + ext
        else:
            cleaned = cleaned[:max_len].rstrip()
    return cleaned


def plan_paths(items: list[FileItem], out_dir: Path) -> list[tuple[FileItem, Path]]:
    """Ordnet jeder Datei einen eindeutigen Zielpfad zu (doppelte Namen bekommen " (2)" usw.)."""
    used: set[str] = set()
    planned: list[tuple[FileItem, Path]] = []
    for item in items:
        prefix = "Raum " if item.container_kind == "Raum" else ""
        parts = [safe_name(prefix + item.container_name), *(safe_name(f) for f in item.folders)]
        folder = out_dir.joinpath(*parts)
        name = safe_name(item.name)
        candidate = folder / name
        n = 2
        while str(candidate).lower() in used:
            stem, dot, ext = name.rpartition(".")
            candidate = folder / (f"{stem} ({n}).{ext}" if dot else f"{name} ({n})")
            n += 1
        used.add(str(candidate).lower())
        planned.append((item, candidate))
    return planned


@dataclass
class DownloadResult:
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    bytes: int = 0


class Index:
    """Merkt sich, welche Dateien schon geladen wurden, damit Wiederholungsläufe nur Neues holen."""

    def __init__(self, out_dir: Path):
        self.path = out_dir / INDEX_NAME
        self._lock = threading.Lock()
        try:
            self.data: dict[str, dict] = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def is_current(self, item: FileItem, target: Path) -> bool:
        entry = self.data.get(item.key)
        if not entry or not target.exists():
            return False
        if entry.get("updated") != item.updated:
            return False
        if item.size is not None and target.stat().st_size != item.size:
            return False
        return True

    def record(self, item: FileItem, target: Path) -> None:
        with self._lock:
            self.data[item.key] = {"path": str(target), "updated": item.updated, "size": item.size}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def download_all(
    client: SchulCloudClient,
    planned: list[tuple[FileItem, Path]],
    out_dir: Path,
    workers: int = 3,
    log: Callable[[str], None] = print,
    progress: Callable[[int, int], None] | None = None,
) -> DownloadResult:
    index = Index(out_dir)
    result = DownloadResult()
    todo: list[tuple[FileItem, Path]] = []
    for item, target in planned:
        if index.is_current(item, target):
            result.skipped += 1
        else:
            todo.append((item, target))

    total = len(todo)
    lock = threading.Lock()
    if progress:
        progress(0, total)

    def fetch(item: FileItem, target: Path) -> int:
        if item.source == "legacy":
            url = client.legacy_signed_url(item.ref)
            return client.download_to(url, target, authenticated=False)
        return client.download_to(item.ref, target, authenticated=True)

    done = 0
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(fetch, item, target): (item, target) for item, target in todo}
            for fut in as_completed(futures):
                item, target = futures[fut]
                with lock:
                    done += 1
                    rel = target.relative_to(out_dir)
                    try:
                        size = fut.result()
                        index.record(item, target)
                        result.downloaded += 1
                        result.bytes += size
                        log(f"[{done}/{total}] {rel}")
                    except AuthError:
                        for other in futures:
                            other.cancel()
                        raise
                    except (ApiError, OSError, Exception) as err:  # noqa: BLE001 - einzelne Fehler nicht abbrechen
                        result.failed += 1
                        log(f"[{done}/{total}] FEHLER {rel}: {err}")
                    if progress:
                        progress(done, total)
                    if done % 20 == 0:
                        index.save()
    finally:
        index.save()
    return result
