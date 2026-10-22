"""In-memory rate-limit ledger (``IRateLedger``)."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard_mock.forge.domain.clock import Clock
from blizzard_mock.forge.domain.rate import ANONYMOUS_BUCKET, DEFAULT_RATE_LIMIT, RATE_WINDOW_SECONDS, RateWindow


@dataclass
class _Window:
    remaining: int
    reset: int


class InMemoryRateLedger:
    """Process-local windows keyed by credential. Implements ``IRateLedger``.

    A window opens at a bucket's first counted answer and restores to the limit at
    its reset; the remaining never goes below zero.
    """

    def __init__(self, clock: Clock, limit: int = DEFAULT_RATE_LIMIT) -> None:
        self._clock = clock
        self._limit = limit
        self._windows: dict[str, _Window] = {}
        #: A budget set for "every bucket" — buckets opened later start from it.
        self._template: _Window | None = None

    def _now(self) -> int:
        return int(self._clock.now().timestamp())

    def _live(self, bucket: str) -> _Window | None:
        window = self._windows.get(bucket)
        if window is not None and self._now() >= window.reset:
            del self._windows[bucket]
            return None
        return window

    def _fresh(self) -> _Window:
        template = self._template
        if template is not None and self._now() < template.reset:
            return _Window(template.remaining, template.reset)
        self._template = None
        return _Window(self._limit, self._now() + RATE_WINDOW_SECONDS)

    def count(self, bucket: str) -> RateWindow:
        window = self._live(bucket)
        if window is None:
            window = self._windows[bucket] = self._fresh()
        window.remaining = max(window.remaining - 1, 0)
        return RateWindow(self._limit, window.remaining, window.reset)

    def peek(self, bucket: str) -> RateWindow:
        window = self._live(bucket) or self._fresh()
        return RateWindow(self._limit, window.remaining, window.reset)

    def set_budget(self, token: str | None, *, remaining: int | None, reset: int | None) -> None:
        if token is not None:
            self._apply(self._live(token) or self._windows.setdefault(token, self._fresh()), remaining, reset)
            return
        self._windows.setdefault(ANONYMOUS_BUCKET, self._fresh())
        for bucket in list(self._windows):
            if self._live(bucket) is not None:
                self._apply(self._windows[bucket], remaining, reset)
        self._template = _Window(
            self._limit if remaining is None else remaining,
            self._now() + RATE_WINDOW_SECONDS if reset is None else reset,
        )

    def _apply(self, window: _Window, remaining: int | None, reset: int | None) -> None:
        if remaining is not None:
            window.remaining = remaining
        if reset is not None:
            window.reset = reset

    def clear(self) -> None:
        self._windows.clear()
        self._template = None
