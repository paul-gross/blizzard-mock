"""Request-scoped lever middleware — the network/auth/rate edge states.

Three state levers bend a request before it reaches a route: ``unreachable``
(503), ``token_rejected`` (401), and ``rate_limited`` (403 with GitHub's
rate-limit headers). They are consulted here so every GitHub route inherits them
uniformly. The ``/_levers`` control surface and health check are exempt, so a
test can always clear a lever it armed.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from blizzard_mock.forge.domain.levers import ILeverStore, LeverKind

_EXEMPT_PREFIXES = ("/_levers", "/healthz", "/docs", "/openapi.json", "/redoc")


def _repo_from_path(path: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "repos":
        return f"{parts[1]}/{parts[2]}"
    return None


def _error(status: int, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"message": message, "documentation_url": "https://docs.github.com/rest"},
        headers=headers,
    )


class LeverMiddleware(BaseHTTPMiddleware):
    """Applies the request-scoped levers held in ``app.state.levers``."""

    def __init__(self, app: object, levers: ILeverStore) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._levers = levers

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            return await call_next(request)

        repo = _repo_from_path(path)

        if self._levers.find(LeverKind.UNREACHABLE, repo, None) is not None:
            return _error(503, "the forge is unreachable")

        if self._levers.find(LeverKind.TOKEN_REJECTED, repo, None) is not None:
            return _error(401, "Bad credentials", headers={"WWW-Authenticate": "Bearer"})

        rate = self._levers.find(LeverKind.RATE_LIMITED, repo, None)
        if rate is not None:
            self._levers.consume(rate)
            headers = {
                "X-RateLimit-Limit": "60",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "0",
                "Retry-After": "60",
            }
            return _error(403, "API rate limit exceeded", headers=headers)

        return await call_next(request)
