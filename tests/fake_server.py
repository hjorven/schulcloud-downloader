"""Nachgebaute Schul-Cloud-Antworten für Tests (Feldnamen wie im schulcloud-server)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx

SCHOOL = "5f2987e020834114b8efd6f1"
COURSE = "0000000000000000000000c1"
LESSON = "0000000000000000000000l1"
TASK = "0000000000000000000000t1"
CBOARD = "0000000000000000000000b1"
ROOM = "0000000000000000000000r1"
RBOARD = "0000000000000000000000b2"
LEGACY_DIR = "0000000000000000000000d1"
LEGACY_FILE = "0000000000000000000000f1"
LEGACY_FILE2 = "0000000000000000000000f2"


def file_record(fid: str, name: str, parent_type: str, parent_id: str, size: int, updated: str = "2026-10-01T08:00:00.000Z"):
    return {
        "id": fid,
        "name": name,
        "parentId": parent_id,
        "parentType": parent_type,
        "url": f"/api/v3/file/download/{fid}/{name}",
        "securityCheckStatus": "verified",
        "size": size,
        "mimeType": "application/pdf",
        "createdAt": updated,
        "updatedAt": updated,
    }


class FakeSchulCloud:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.auth_headers: list[str | None] = []
        self.files = {
            ("lessons", LESSON): [file_record("fl1", "Arbeitsblatt.pdf", "lessons", LESSON, 4)],
            ("tasks", TASK): [file_record("ft1", "Aufgabe 1.docx", "tasks", TASK, 3, "2026-09-15T08:00:00.000Z")],
            ("boardnodes", "el1"): [file_record("fb1", "Folien.pdf", "boardnodes", "el1", 5)],
            ("boardnodes", "el2"): [
                file_record("fb2", "Bild.png", "boardnodes", "el2", 2),
                file_record("fb3", "Bild.png", "boardnodes", "el2", 2),
            ],
            ("boardnodes", "el3"): [file_record("fb4", "Protokoll.pdf", "boardnodes", "el3", 6)],
        }
        self.blobs = {
            "fl1": b"LESS",
            "ft1": b"TSK",
            "fb1": b"BOARD",
            "fb2": b"P1",
            "fb3": b"P2",
            "fb4": b"ROOMPD",
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.auth_headers.append(request.headers.get("authorization"))
        path = request.url.path
        q = parse_qs(request.url.query.decode())

        def js(data, status=200):
            return httpx.Response(status, json=data)

        if request.url.host == "s3.example.org":
            legacy = {LEGACY_FILE: b"1234567", LEGACY_FILE2: b"123456789"}
            return httpx.Response(200, content=legacy[path.rsplit("/", 1)[-1]])
        if path == "/api/v3/authentication/local":
            body = json.loads(request.content)
            if body == {"username": "a@b.de", "password": "pw"}:
                return js({"accessToken": "header.eyJleHAiOjk5OTk5OTk5OTl9.sig"})
            return js({"message": "invalid"}, 401)
        if request.headers.get("authorization") is None:
            return js({"message": "unauthorized"}, 401)
        if path == "/api/v3/me":
            return js({"school": {"id": SCHOOL, "name": "Testschule"}, "user": {"firstName": "J", "lastName": "H"}})
        if path == "/api/v3/courses":
            data = [
                {"id": COURSE, "title": "Mathe 10a", "isLocked": False},
                {"id": "lockedcourse", "title": "Alt Kurs", "isLocked": True},
            ]
            skip = int(q.get("skip", ["0"])[0])
            return js({"data": data[skip:], "total": len(data), "skip": skip, "limit": 50})
        if path == f"/api/v3/course-rooms/{COURSE}/board":
            return js(
                {
                    "roomId": COURSE,
                    "title": "Mathe 10a",
                    "elements": [
                        {"type": "lesson", "content": {"id": LESSON, "name": "Lineare Funktionen"}},
                        {"type": "task", "content": {"id": TASK, "name": "Hausaufgabe 1"}},
                        {"type": "column-board", "content": {"id": "x", "columnBoardId": CBOARD, "title": "Unterricht"}},
                    ],
                }
            )
        if path == f"/api/v3/lessons/{LESSON}":
            return js(
                {
                    "id": LESSON,
                    "name": "Lineare Funktionen",
                    "contents": [
                        {"component": "text", "content": {"text": f'<a href="/files/file?file={LEGACY_FILE}">Blatt</a>'}}
                    ],
                }
            )
        if path == f"/api/v3/boards/{CBOARD}":
            return js(
                {
                    "id": CBOARD,
                    "title": "Unterricht",
                    "columns": [
                        {"id": "c1", "title": "Woche 3", "cards": [{"cardId": "card1", "height": 1}, {"cardId": "card2", "height": 1}]}
                    ],
                }
            )
        if path == f"/api/v3/boards/{RBOARD}":
            return js({"id": RBOARD, "title": "AG", "columns": [{"id": "c2", "title": "", "cards": [{"cardId": "card3"}]}]})
        if path == "/api/v3/cards":
            cards = {
                "card1": {"id": "card1", "title": "Geraden", "elements": [{"id": "el1", "type": "file", "content": {}}]},
                "card2": {
                    "id": "card2",
                    "title": "Bilder",
                    "elements": [
                        {"id": "el2", "type": "fileFolder", "title": "Fotos", "content": {}},
                        {"id": "elx", "type": "richText", "content": {"text": "hi"}},
                    ],
                },
                "card3": {"id": "card3", "title": None, "elements": [{"id": "el3", "type": "file", "content": {}}]},
            }
            ids = q.get("ids", [])
            # absichtlich umgekehrte Reihenfolge, um die Sortierung zu prüfen
            return js({"data": [cards[i] for i in reversed(ids)]})
        if path == "/api/v3/rooms":
            return js({"data": [{"id": ROOM, "name": "Physik-AG", "schoolId": SCHOOL}]})
        if path == f"/api/v3/rooms/{ROOM}/boards":
            return js({"data": [{"id": RBOARD, "title": "AG"}]})
        if path.startswith("/api/v3/file/list/school/"):
            parts = path.split("/")
            assert parts[6] == SCHOOL
            records = self.files.get((parts[7], parts[8]))
            if records is None:
                return js({"message": "forbidden"}, 403)
            return js({"data": records, "total": len(records), "skip": 0, "limit": 50})
        if path.startswith("/api/v3/file/download/"):
            fid = path.split("/")[5]
            return httpx.Response(200, content=self.blobs[fid])
        if path == "/api/v1/fileStorage":
            if q.get("parent") == [LEGACY_DIR]:
                return js([{"_id": LEGACY_FILE2, "name": "Klausur.pdf", "isDirectory": False, "size": 9, "updatedAt": "2026-06-01T10:00:00.000Z"}])
            return js(
                [
                    {"_id": LEGACY_DIR, "name": "Prüfungen", "isDirectory": True},
                    {"_id": LEGACY_FILE, "name": "Blatt.pdf", "isDirectory": False, "size": 7, "updatedAt": "2026-10-02T10:00:00.000Z"},
                ]
            )
        if path == "/api/v1/fileStorage/signedUrl":
            return js({"url": f"https://s3.example.org/bucket/{q['file'][0]}"})
        return js({"message": f"not found {path}"}, 404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
