"""Filter auf der eingesammelten Dateiliste: Kurs/Raum, Thema, Datum, Dateityp."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import PurePosixPath

from .crawler import FileItem


def parse_day(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return datetime.strptime(value, "%d.%m.%Y").date()


def item_day(item: FileItem) -> date | None:
    stamp = item.updated or item.created
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def extension(name: str) -> str:
    return PurePosixPath(name).suffix.lower().lstrip(".")


@dataclass
class FileFilter:
    containers: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    since: date | None = None
    until: date | None = None

    def matches(self, item: FileItem) -> bool:
        if self.containers:
            low = item.container_name.lower()
            if not any(c.lower() in low for c in self.containers):
                return False
        if self.topics:
            haystack = " / ".join([item.topic, *item.folders]).lower()
            if not any(t.lower() in haystack for t in self.topics):
                return False
        if self.types:
            wanted = {t.lower().lstrip(".") for t in self.types}
            if extension(item.name) not in wanted:
                return False
        if self.since or self.until:
            day = item_day(item)
            if day is None:
                return False
            if self.since and day < self.since:
                return False
            if self.until and day > self.until:
                return False
        return True

    def apply(self, items: list[FileItem]) -> list[FileItem]:
        return [i for i in items if self.matches(i)]
