"""``serve``'s local HTTP control API — decisions for every route the handler answers.

Socket handling lives in ``internal.http_serve``; this module only decides what
each route's body/status/headers should be.
"""

from __future__ import annotations

from ..levers import Lever


def children_body() -> dict:
    """``GET /session/{id}/children``'s body — always empty."""
    return {"children": []}


def session_body(sid: str, cwd: str, levers: frozenset[Lever]) -> dict:
    """``GET /session/{id}``'s body."""
    return {
        "id": "ses_wrong" if Lever.TAKEOVER_WRONG_SESSION in levers else sid,
        "directory": "/wrong-directory" if Lever.TAKEOVER_WRONG_DIRECTORY in levers else cwd,
        "title": "proof",
    }


def event_response(levers: frozenset[Lever]) -> tuple[int, str, bytes, bool]:
    """``GET /event`` / ``GET /global/event``'s ``(status, content_type, body, send_content_length)``."""
    if Lever.TAKEOVER_STREAM_FAILURE in levers:
        body, status, content_type = b"upstream failure", 503, "text/event-stream"
    else:
        non_sse = Lever.TAKEOVER_NON_SSE in levers
        body = b"{}" if non_sse else b": upstream\n\n"
        status = 200
        content_type = "application/json" if non_sse else "text/event-stream"

    if Lever.TAKEOVER_IMMEDIATE_EOF in levers:
        body, status, content_type = b"", 200, "text/event-stream"
    elif Lever.TAKEOVER_IDLE_SSE in levers:
        body = b""

    send_content_length = Lever.TAKEOVER_IDLE_SSE not in levers
    return status, content_type, body, send_content_length
