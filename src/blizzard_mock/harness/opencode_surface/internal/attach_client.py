"""``attach``'s HTTP client + the stdin loop that records the operator's takeover line."""

from __future__ import annotations

import sys
import threading
import time
import urllib.request

from ..domain import attach as attach_domain
from ..domain import state as state_domain
from ..levers import Lever
from . import state_store


def run(args: list[str], levers: frozenset[Lever], state_path: str) -> int:
    """``attach <url> --session <id> --dir <dir>``: connect, validate, then relay stdin."""
    attach_url = args[1]
    session = args[args.index("--session") + 1]
    headers = {"X-OpenCode-Directory": args[args.index("--dir") + 1]}

    with urllib.request.urlopen(
        urllib.request.Request(f"{attach_url}/session/{session}", headers=headers), timeout=10
    ) as response:
        response.read()

    with urllib.request.urlopen(
        urllib.request.Request(f"{attach_url}/global/event", headers=headers), timeout=10
    ) as response:
        upstream = response.headers.get("X-Upstream-Stream")
        if Lever.TAKEOVER_IDLE_SSE in levers:
            ok = attach_domain.idle_response_ok(upstream, response.headers.get("Content-Type"))
        else:
            ok = attach_domain.stream_response_ok(levers, upstream, response.read())
        if not ok:
            return 8

    if Lever.TAKEOVER_EXIT_EARLY in levers:
        return 0
    if Lever.TAKEOVER_IDLE_SSE in levers:
        threading.Thread(target=_record_takeover_input, args=(state_path,), daemon=True).start()
        time.sleep(30)
    else:
        _record_takeover_input(state_path)
    print("interactive attach connected", flush=True)
    time.sleep(30)
    return 0


def _record_takeover_input(state_path: str) -> None:
    value = sys.stdin.readline().strip()
    if not value:
        return
    current = state_store.read_state(state_path)
    state_store.write_state(state_path, state_domain.takeover_continued_state(current, value))
