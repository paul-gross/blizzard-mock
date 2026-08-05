"""Transport-edge lever middleware — ``unreachable`` and ``delay`` — plus request capture.

Two levers bend a request before it reaches a route: ``unreachable`` answers
503; ``delay`` sleeps ``payload.ms`` first. Control-plane and liveness routes
are exempt. ``RequestCaptureMiddleware`` records every ``/api/*`` request.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from blizzard_mock.levers import ILeverStore
from blizzard_mock.mock_hub.domain.capture import ICaptureStore
from blizzard_mock.mock_hub.domain.levers import HubLever

_EXEMPT_PREFIXES = ("/_levers", "/_seed", "/_captured", "/api/health", "/api/ready", "/docs", "/openapi.json")


def _chunk_from_path(path: str) -> str | None:
    """The ``chunk_id`` a ``.../chunks/{id}/...`` path names, if any — matches both
    ``.../api/chunks/{id}`` and ``.../api/fleet/chunks/{id}`` (issue #87). Either shape's
    ``chunks/{id}`` tail resolves the same way, so a per-chunk lever (``unreachable``,
    ``delay``) still targets the right chunk."""
    parts = [p for p in path.split("/") if p]
    for i in range(len(parts) - 1):
        if parts[i] == "chunks":
            return parts[i + 1]
    return None


class HubLeverMiddleware(BaseHTTPMiddleware):
    """Applies the transport-edge levers held in ``app.state.levers``."""

    def __init__(self, app: object, levers: ILeverStore) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._levers = levers

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            return await call_next(request)
        chunk_id = _chunk_from_path(path)

        unreachable = self._levers.find(HubLever.UNREACHABLE.value, chunk_id)
        if unreachable is not None:
            self._levers.consume(unreachable)
            return JSONResponse(status_code=503, content={"detail": "the hub is unreachable"})

        delay = self._levers.find(HubLever.DELAY.value, chunk_id)
        if delay is not None:
            self._levers.consume(delay)
            import time

            time.sleep(int(delay.payload.get("ms", 0)) / 1000.0)

        return await call_next(request)


#: Excluded from capture for the same reason they're exempt from the transport-edge
#: levers above — never a runner-fleet call.
_CAPTURE_EXEMPT_PREFIXES = ("/api/health", "/api/ready")


class RequestCaptureMiddleware(BaseHTTPMiddleware):
    """Records every hub-mirror ``/api/*`` request's method, path, headers (issue #86b).

    Excludes the control plane and liveness routes.
    """

    def __init__(self, app: object, captured: ICaptureStore) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._captured = captured

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path.startswith("/api/") and not any(path.startswith(prefix) for prefix in _CAPTURE_EXEMPT_PREFIXES):
            self._captured.record(method=request.method, path=path, headers=dict(request.headers))
        return await call_next(request)
