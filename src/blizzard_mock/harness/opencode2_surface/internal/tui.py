"""``opencode --session <id>``: the operator's takeover, far enough to prove it composes. It reads one prompt line
from its terminal, switches the session to the ``build`` agent as the real TUI does, prompts the session through the
registered service, and stays open until it is ended."""

from __future__ import annotations

import sys
import time
from collections.abc import Mapping
from urllib.parse import quote

from . import paths, registration


def run(env: Mapping[str, str], session_id: str) -> int:
    print(f"opencode — session {session_id}", flush=True)
    line = sys.stdin.readline()
    record = registration.read(paths.registration_path(env))
    if record is None:
        print("no opencode service is registered", file=sys.stderr, flush=True)
        return 1
    prefix = f"/api/session/{quote(session_id, safe='')}"
    registration.call(record, "POST", f"{prefix}/agent", {"agent": "build"})
    registration.call(record, "POST", f"{prefix}/prompt", {"text": line.strip()})
    while True:
        time.sleep(1)


__all__ = ["run"]
