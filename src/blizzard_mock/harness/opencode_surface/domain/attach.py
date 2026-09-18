"""``attach``'s client-side validation of the ``serve`` process's ``/global/event`` reply.

The HTTP round-trip is I/O (``internal.attach_client``); this module only
decides, given the response's headers/body, whether the connection counts as
"the real event stream, preserved end to end".
"""

from __future__ import annotations

from ..levers import Lever


def idle_response_ok(upstream_header: str | None, content_type: str | None) -> bool:
    """Validation used when ``TAKEOVER_IDLE_SSE`` is armed — the body is never read."""
    if upstream_header != "preserved":
        return False
    return content_type == "text/event-stream"


def stream_response_ok(levers: frozenset[Lever], upstream_header: str | None, body: bytes) -> bool:
    """Validation used for every other lever combination — the body is read and compared."""
    if upstream_header != "preserved":
        return False
    exempt = Lever.TAKEOVER_IMMEDIATE_EOF in levers or Lever.TAKEOVER_STREAM_FAILURE in levers
    return body == b": upstream\n\n" or exempt
