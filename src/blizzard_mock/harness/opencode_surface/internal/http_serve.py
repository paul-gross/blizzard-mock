"""``serve``'s ``http.server`` socket plumbing.

Every route's body/status/headers is decided by ``domain.serve``; this module
only owns the socket, the request parsing, and the two pieces of
request-scoped mutable state a real OpenCode server would also need
(the takeover-arrived event, and the state-file read/write around
``POST .../summarize``).
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..domain import serve as serve_domain
from ..domain import state as state_domain
from ..levers import Lever
from . import state_store


def serve_forever(levers: frozenset[Lever], state_path: str) -> int:
    """Bind an ephemeral local port, announce it, and serve until killed."""
    event_arrived = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/session/") and self.path.endswith("/children"):
                self._send_json(200, json.dumps(serve_domain.children_body()).encode())
                return
            if self.path.startswith("/session/"):
                sid = self.path.split("/")[2]
                body = json.dumps(serve_domain.session_body(sid, os.getcwd(), levers)).encode()
                self._send_json(200, body)
                return
            if self.path.split("?", 1)[0] in {"/event", "/global/event"}:
                self._handle_event()
                return
            self.send_response(404)
            self.end_headers()

        def _handle_event(self) -> None:
            if Lever.TAKEOVER_EVENT_GATED in levers:
                event_arrived.wait(30)
            status, content_type, body, send_length = serve_domain.event_response(levers)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("X-Upstream-Stream", "preserved")
            if send_length:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
                self.wfile.flush()
            if Lever.TAKEOVER_IDLE_SSE in levers:
                time.sleep(30)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if self.path == "/session":
                event_arrived.set()
            if self.path.endswith("/summarize") and Lever.COMPACTION_NO_CHANGE not in levers:
                current = state_store.read_state(state_path)
                state_store.write_state(state_path, state_domain.summarized_state(current))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"true")

        def _send_json(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(f"opencode server listening on http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()
    return 0  # unreachable — serve_forever() never returns short of being killed
