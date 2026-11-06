"""Stellt FakeSchulCloud als echten HTTP-Server mit CORS bereit (für Browser- und Node-Tests)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from .fake_server import FakeSchulCloud


def start(fake: FakeSchulCloud | None = None, port: int = 0):
    fake = fake or FakeSchulCloud()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def _handle(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            host = self.headers.get("Host", "")
            # signierte Speicher-URLs zeigen im Test auf denselben Server unter /s3/...
            url = f"http://{host}{self.path}"
            if self.path.startswith("/s3/"):
                url = "https://s3.example.org" + self.path[3:]
            req = httpx.Request(method, url, headers=dict(self.headers), content=body)
            resp = fake.handler(req)
            content = resp.content
            if self.path.startswith("/api/v1/fileStorage/signedUrl"):
                content = content.replace(b"https://s3.example.org", f"http://{host}/s3".encode())
            self.send_response(resp.status_code)
            self._cors()
            self.send_header("Content-Type", resp.headers.get("content-type", "application/octet-stream"))
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):  # noqa: N802
            self._handle("GET")

        def do_POST(self):  # noqa: N802
            self._handle("POST")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, fake
