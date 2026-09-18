"""``serve``'s local HTTP control API — decisions for every route the handler answers.

Socket handling lives in ``internal.http_serve``; this module only decides what
each route's body/status/headers should be.
"""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class EventResponse:
    """``GET /event`` / ``GET /global/event``'s response, named so the one caller
    (``internal.http_serve``) doesn't have to remember tuple positions."""

    status: int
    content_type: str
    body: bytes
    send_content_length: bool


def event_response(levers: frozenset[Lever]) -> EventResponse:
    """``GET /event`` / ``GET /global/event``'s response, by lever priority
    (``TAKEOVER_IMMEDIATE_EOF`` overrides everything else, including a 503)."""
    if Lever.TAKEOVER_IMMEDIATE_EOF in levers:
        status, content_type, body = 200, "text/event-stream", b""
    elif Lever.TAKEOVER_STREAM_FAILURE in levers:
        status, content_type, body = 503, "text/event-stream", b"upstream failure"
    elif Lever.TAKEOVER_NON_SSE in levers:
        status, content_type, body = 200, "application/json", b"{}"
    else:
        status, content_type, body = 200, "text/event-stream", b": upstream\n\n"

    if Lever.TAKEOVER_IDLE_SSE in levers:
        body = b""

    send_content_length = Lever.TAKEOVER_IDLE_SSE not in levers
    return EventResponse(status, content_type, body, send_content_length)


def should_apply_summarize(levers: frozenset[Lever]) -> bool:
    """Whether ``POST .../summarize`` should actually advance session state
    (``COMPACTION_NO_CHANGE`` suppresses it)."""
    return Lever.COMPACTION_NO_CHANGE not in levers
