"""Request-scoped middleware — the network/auth/rate edge states, conditional reads,
and the rate headers.

``LeverMiddleware`` is the outer layer. Four state levers bend a request before it
reaches a route: ``unreachable`` (503), ``token_rejected`` (401), ``rate_limited``
(403), and ``secondary_rate_limited`` (403). ``/_levers`` and the health check are
exempt, so a test can always clear a lever it armed.

``RateHeaderMiddleware`` sits inside it, so a lever refusal short-circuits before
anything is counted or hashed. It owns the ``/repos/`` surface only: every 200 ``GET``
answers a strong ``ETag`` and a matching ``If-None-Match`` answers ``304``, uncounted;
every other answer that reaches a route spends one request from the credential's
window and carries ``X-RateLimit-*``.
"""

from __future__ import annotations

import hashlib

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from blizzard_mock.forge.domain.levers import ILeverStore, LeverKind
from blizzard_mock.forge.domain.rate import ANONYMOUS_BUCKET, IRateLedger, RateWindow

_EXEMPT_PREFIXES = ("/_levers", "/healthz", "/docs", "/openapi.json", "/redoc")

_SECONDARY_LIMIT_MESSAGE = "You have exceeded a secondary rate limit. Please wait a few minutes before you try again."


def _repo_from_path(path: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "repos":
        return f"{parts[1]}/{parts[2]}"
    return None


def _bucket(request: Request) -> str:
    """The credential in ``Authorization`` (``token X`` or ``Bearer X``); one anonymous
    bucket when there is none."""
    _, _, credential = request.headers.get("authorization", "").partition(" ")
    return credential.strip() or ANONYMOUS_BUCKET


def _rate_headers(window: RateWindow, remaining: int | None = None) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(window.limit),
        "X-RateLimit-Remaining": str(window.remaining if remaining is None else remaining),
        "X-RateLimit-Reset": str(window.reset),
        "X-RateLimit-Resource": "core",
    }


def _error(status: int, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"message": message, "documentation_url": "https://docs.github.com/rest"},
        headers=headers,
    )


class LeverMiddleware(BaseHTTPMiddleware):
    """Applies the request-scoped levers held in ``app.state.levers``."""

    def __init__(self, app: object, levers: ILeverStore, rate: IRateLedger) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._levers = levers
        self._rate = rate

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            return await call_next(request)

        repo = _repo_from_path(path)

        if self._levers.find(LeverKind.UNREACHABLE, repo, None) is not None:
            return _error(503, "the forge is unreachable")

        if self._levers.find(LeverKind.TOKEN_REJECTED, repo, None) is not None:
            return _error(401, "Bad credentials", headers={"WWW-Authenticate": "Bearer"})

        window = self._rate.peek(_bucket(request))

        rate = self._levers.find(LeverKind.RATE_LIMITED, repo, None)
        if rate is not None:
            self._levers.consume(rate)
            headers = {**_rate_headers(window, remaining=0), "Retry-After": "60"}
            return _error(403, "API rate limit exceeded", headers=headers)

        secondary = self._levers.find(LeverKind.SECONDARY_RATE_LIMITED, repo, None)
        if secondary is not None:
            self._levers.consume(secondary)
            headers = {**_rate_headers(window), "Retry-After": "60"}
            return _error(403, secondary.message or _SECONDARY_LIMIT_MESSAGE, headers=headers)

        return await call_next(request)


class RateHeaderMiddleware(BaseHTTPMiddleware):
    """Conditional reads and the rate headers, over the ``/repos/`` surface only."""

    def __init__(self, app: object, rate: IRateLedger) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._rate = rate

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not request.url.path.startswith("/repos/"):
            return await call_next(request)

        response = await call_next(request)
        body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        bucket = _bucket(request)

        if request.method == "GET" and response.status_code == 200:
            etag = f'"{hashlib.sha256(body).hexdigest()}"'
            headers["ETag"] = etag
            if _matches(request.headers.get("if-none-match"), etag):
                # Uncounted: the window is read, not spent.
                return Response(status_code=304, headers={"ETag": etag, **_rate_headers(self._rate.peek(bucket))})

        headers.update(_rate_headers(self._rate.count(bucket)))
        return Response(content=body, status_code=response.status_code, headers=headers)


def _matches(if_none_match: str | None, etag: str) -> bool:
    if if_none_match is None:
        return False
    candidates = [c.strip().removeprefix("W/") for c in if_none_match.split(",")]
    return "*" in candidates or etag in candidates
