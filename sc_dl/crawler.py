"""Sammelt alle Datei-Fundstellen aus Kursen und Räumen ein, ohne etwas herunterzuladen."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .api import ApiError, AuthError, SchulCloudClient

LEGACY_FILE_LINK = re.compile(r"file=([0-9a-fA-F]{24})")


@dataclass
class FileItem:
    container_kind: str  # "Kurs" oder "Raum"
    container_name: str
    folders: list[str]  # Unterordner innerhalb des Kurses/Raums
    topic: str  # Text, auf den der Themen-Filter angewendet wird
    name: str
    size: int | None
    mime: str | None
    created: str | None
    updated: str | None
    source: str  # "v3" (files-storage) oder "legacy" (alte Kursdateien)
    ref: str  # Download-Pfad (v3) bzw. Datei-ID (legacy)
    file_id: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}:{self.file_id or self.ref}:{'/'.join(self.folders)}"


@dataclass
class CrawlReport:
    items: list[FileItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    containers: list[tuple[str, str]] = field(default_factory=list)  # (Art, Name)


Log = Callable[[str], None]


def _nolog(_: str) -> None:
    pass


def _matches_any(name: str, needles: Iterable[str] | None) -> bool:
    if not needles:
        return True
    low = name.lower()
    return any(n.lower() in low for n in needles)


class Crawler:
    def __init__(
        self,
        client: SchulCloudClient,
        log: Log = _nolog,
        include_courses: bool = True,
        include_rooms: bool = True,
        include_legacy_files: bool = True,
        container_filter: list[str] | None = None,
    ):
        self.client = client
        self.log = log
        self.include_courses = include_courses
        self.include_rooms = include_rooms
        self.include_legacy_files = include_legacy_files
        self.container_filter = container_filter
        self.report = CrawlReport()
        self.school_id = ""

    # ------------------------------------------------------------- Helfer
    def _warn(self, msg: str) -> None:
        self.report.warnings.append(msg)
        self.log(f"Hinweis: {msg}")

    def _safe(self, what: str, fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except AuthError:
            raise
        except ApiError as err:
            if err.status not in (403, 404):
                self._warn(f"{what}: {err}")
            return default

    def _v3_files(
        self, school_id: str, parent_type: str, parent_id: str, kind: str, container: str, folders: list[str], topic: str
    ) -> None:
        records = self._safe(
            f"Dateien {parent_type}/{parent_id}",
            lambda: self.client.list_files(school_id, parent_type, parent_id),
            [],
        )
        for rec in records:
            if rec.get("deletedSince") or rec.get("isUploading"):
                continue
            if rec.get("securityCheckStatus") == "blocked":
                self._warn(f"Datei vom Virenscanner blockiert, übersprungen: {rec.get('name')}")
                continue
            self.report.items.append(
                FileItem(
                    container_kind=kind,
                    container_name=container,
                    folders=list(folders),
                    topic=topic,
                    name=rec.get("name") or rec["id"],
                    size=rec.get("size"),
                    mime=rec.get("mimeType"),
                    created=rec.get("createdAt"),
                    updated=rec.get("contentLastModifiedAt") or rec.get("updatedAt"),
                    source="v3",
                    ref=SchulCloudClient.file_download_path(rec),
                    file_id=rec["id"],
                )
            )

    # -------------------------------------------------------------- Ablauf
    def run(self) -> CrawlReport:
        me = self.client.me()
        self.school_id = (me.get("school") or {}).get("id", "")
        if self.include_courses:
            for course in self.client.courses():
                if not _matches_any(course.get("title", ""), self.container_filter):
                    continue
                if course.get("isLocked"):
                    self._warn(f"Kurs gesperrt, übersprungen: {course.get('title')}")
                    continue
                self.crawl_course(course)
        if self.include_rooms:
            rooms = self._safe("Räume", self.client.rooms, [])
            for room in rooms:
                if not _matches_any(room.get("name", ""), self.container_filter):
                    continue
                self.crawl_room(room)
        return self.report

    def crawl_course(self, course: dict[str, Any]) -> None:
        name = course.get("title") or course["id"]
        self.log(f"Kurs: {name}")
        self.report.containers.append(("Kurs", name))
        legacy_by_id: dict[str, FileItem] = {}
        if self.include_legacy_files:
            self._crawl_legacy_tree(course["id"], name, ["Kursdateien"], None, legacy_by_id, depth=0)

        board = self._safe(f"Kurs-Übersicht {name}", lambda: self.client.course_room_board(course["id"]), {})
        lesson_no = 0
        for element in board.get("elements", []):
            etype = element.get("type")
            content = element.get("content") or {}
            if etype == "lesson":
                lesson_no += 1
                self._crawl_lesson(content, name, lesson_no, legacy_by_id)
            elif etype == "task":
                title = content.get("name") or content["id"]
                self._v3_files(self.school_id, "tasks", content["id"], "Kurs", name, ["Aufgaben", title], title)
            elif etype == "column-board":
                title = content.get("title") or "Board"
                self.crawl_board(content["columnBoardId"], "Kurs", name, self.school_id, f"Board - {title}")

    def _crawl_lesson(
        self, content: dict[str, Any], course_name: str, number: int, legacy_by_id: dict[str, FileItem]
    ) -> None:
        title = content.get("name") or content["id"]
        folder = f"{number:02d} {title}"
        folders = ["Themen", folder]
        self._v3_files(self.school_id, "lessons", content["id"], "Kurs", course_name, folders, title)

        # In Themen eingebettete Links auf alte Kursdateien dem Thema zuordnen.
        lesson = self._safe(f"Thema {title}", lambda: self.client.lesson(content["id"]), {})
        seen: set[str] = set()
        for part in lesson.get("contents", []) or []:
            text = str((part.get("content") or {}).get("text", ""))
            for file_id in LEGACY_FILE_LINK.findall(text):
                if file_id in seen:
                    continue
                seen.add(file_id)
                original = legacy_by_id.get(file_id)
                if original is None:
                    continue
                self.report.items.append(
                    FileItem(
                        container_kind="Kurs",
                        container_name=course_name,
                        folders=list(folders),
                        topic=title,
                        name=original.name,
                        size=original.size,
                        mime=original.mime,
                        created=original.created,
                        updated=original.updated,
                        source="legacy",
                        ref=original.ref,
                        file_id=original.file_id,
                    )
                )

    def _crawl_legacy_tree(
        self,
        owner_id: str,
        course_name: str,
        folders: list[str],
        parent_id: str | None,
        index: dict[str, FileItem],
        depth: int,
    ) -> None:
        if depth > 20:
            self._warn(f"Ordner zu tief verschachtelt, abgebrochen: {'/'.join(folders)}")
            return
        entries = self._safe(
            f"Kursdateien {course_name}", lambda: self.client.list_legacy_files(owner_id, parent_id), []
        )
        for entry in entries:
            entry_id = str(entry.get("_id") or entry.get("id"))
            if entry.get("isDirectory"):
                self._crawl_legacy_tree(
                    owner_id, course_name, folders + [entry.get("name", entry_id)], entry_id, index, depth + 1
                )
                continue
            item = FileItem(
                container_kind="Kurs",
                container_name=course_name,
                folders=list(folders),
                topic="/".join(folders[1:]),
                name=entry.get("name") or entry_id,
                size=entry.get("size"),
                mime=entry.get("type"),
                created=entry.get("createdAt"),
                updated=entry.get("updatedAt"),
                source="legacy",
                ref=entry_id,
                file_id=entry_id,
            )
            index[entry_id] = item
            self.report.items.append(item)

    def crawl_room(self, room: dict[str, Any]) -> None:
        name = room.get("name") or room["id"]
        self.log(f"Raum: {name}")
        self.report.containers.append(("Raum", name))
        school_id = room.get("schoolId") or self.school_id
        boards = self._safe(f"Boards von Raum {name}", lambda: self.client.room_boards(room["id"]), [])
        for board in boards:
            title = board.get("title") or "Board"
            self.crawl_board(board["id"], "Raum", name, school_id, f"Board - {title}")

    def crawl_board(self, board_id: str, kind: str, container: str, school_id: str, board_folder: str) -> None:
        board = self._safe(f"{board_folder} ({container})", lambda: self.client.board(board_id), {})
        for col_no, column in enumerate(board.get("columns", []) or [], start=1):
            col_title = column.get("title") or f"Abschnitt {col_no}"
            card_ids = [c["cardId"] for c in column.get("cards", []) or [] if c.get("cardId")]
            if not card_ids:
                continue
            cards = self._safe(f"Karten in {col_title}", lambda: self.client.cards(card_ids), [])
            order = {cid: i for i, cid in enumerate(card_ids)}
            cards.sort(key=lambda c: order.get(c.get("id"), 0))
            for card_no, card in enumerate(cards, start=1):
                card_title = card.get("title") or f"Karte {card_no}"
                base = [board_folder, f"{col_no:02d} {col_title}", f"{card_no:02d} {card_title}"]
                topic = f"{board_folder.removeprefix('Board - ')} / {col_title} / {card_title}"
                for element in card.get("elements", []) or []:
                    etype = element.get("type")
                    if etype == "file":
                        self._v3_files(school_id, "boardnodes", element["id"], kind, container, base, topic)
                    elif etype == "fileFolder":
                        sub = (element.get("content") or {}).get("title") or element.get("title") or "Ordner"
                        self._v3_files(school_id, "boardnodes", element["id"], kind, container, base + [sub], topic)
