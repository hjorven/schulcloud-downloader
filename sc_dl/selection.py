"""Gruppiert gefundene Dateien für die Auswahl im Terminal und auf der Webseite."""

from __future__ import annotations

from dataclasses import dataclass, field

from .crawler import FileItem


def container_label(item: FileItem) -> str:
    return f"{item.container_kind}: {item.container_name}"


def group_label(item: FileItem) -> str:
    """Abschnitt innerhalb eines Kurses/Raums, z.B. 'Themen / 01 Lineare Funktionen'."""
    if not item.folders:
        return "(ohne Ordner)"
    return " / ".join(item.folders[:2])


@dataclass
class Group:
    label: str
    items: list[int] = field(default_factory=list)  # Indizes in der Gesamtliste
    size: int = 0


@dataclass
class Container:
    label: str
    groups: dict[str, Group] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return sum(len(g.items) for g in self.groups.values())

    @property
    def size(self) -> int:
        return sum(g.size for g in self.groups.values())


def build_tree(items: list[FileItem]) -> list[Container]:
    containers: dict[str, Container] = {}
    for idx, item in enumerate(items):
        c = containers.setdefault(container_label(item), Container(container_label(item)))
        g = c.groups.setdefault(group_label(item), Group(group_label(item)))
        g.items.append(idx)
        g.size += item.size or 0
    return list(containers.values())


def parse_selection(text: str, maximum: int) -> list[int]:
    """'1,3-5' -> [1, 3, 4, 5]. 'alle', 'a', '*' oder leer -> alle. Nummern beginnen bei 1."""
    text = text.strip().lower()
    if text in ("", "a", "alle", "*", "all"):
        return list(range(1, maximum + 1))
    chosen: set[int] = set()
    for part in text.replace(" ", ",").split(","):
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            if start > end:
                start, end = end, start
            chosen.update(range(start, end + 1))
        else:
            chosen.add(int(part))
    bad = [n for n in chosen if n < 1 or n > maximum]
    if bad:
        raise ValueError(f"Ungültige Nummer(n): {', '.join(map(str, sorted(bad)))} (erlaubt 1 bis {maximum})")
    return sorted(chosen)
