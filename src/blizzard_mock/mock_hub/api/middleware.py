"""Transport-edge lever middleware — ``unreachable`` and ``delay``.

These two levers bend a request before it reaches a route, uniformly across the hub
surface: ``unreachable`` answers 503 (with ``remaining=N`` it heals after N calls — the
"unreachable *mid-lease*" window), and ``delay`` sleeps ``payload.ms`` first. The control
plane (``/_levers``, ``/_seed``) and liveness (``/api/health``, ``/api/ready``) are exempt
so a test can always arm/clear a lever and gate on startup even while the API is "down".
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from blizzard_mock.levers import ILeverStore
from blizzard_mock.mock_hub.domain.levers import HubLever

_EXEMPT_PREFIXES = ("/_levers", "/_seed", "/api/health", "/api/ready", "/docs", "/openapi.json")


def _chunk_from_path(path: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "chunks":
        return parts[2]
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
