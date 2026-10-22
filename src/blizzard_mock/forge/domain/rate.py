"""The rate-limit ledger seam.

Per-credential windows of ``(limit, remaining, reset)``, read through the injected
``Clock`` (``bzh:injected-clock``) so a test ticks past a reset. Counting down never
refuses: only the ``rate_limited`` and ``secondary_rate_limited`` levers do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

#: The limit every bucket starts each window with.
DEFAULT_RATE_LIMIT = 5000

#: How long a window lasts, in seconds.
RATE_WINDOW_SECONDS = 3600

#: The bucket a request carrying no credential falls into.
ANONYMOUS_BUCKET = ""


@dataclass(frozen=True)
class RateWindow:
    limit: int
    remaining: int
    reset: int  # epoch seconds, as ``X-RateLimit-Reset``


class IRateLedger(Protocol):
    def count(self, bucket: str) -> RateWindow:
        """Spend one request from ``bucket``'s window, opening or restoring it first."""
        ...

    def peek(self, bucket: str) -> RateWindow:
        """The window ``bucket`` would answer with, without spending anything."""
        ...

    def set_budget(self, token: str | None, *, remaining: int | None, reset: int | None) -> None:
        """Set the remaining and the reset of ``token``'s window, or of every bucket when
        ``token`` is ``None``."""
        ...

    def clear(self) -> None: ...
