"""Received-request capture — the header-inspection lever (issue #86b).

The mirror image of the edge-state levers: capturing what a request
presented (e.g. an ``Authorization`` header), read back via ``GET
/_captured`` rather than wiring assertion logic into the domain rules.
"""

from __future__ import annotations

from typing import Protocol


class ICaptureStore(Protocol):
    """Read/write seam over the requests received on the hub-mirror ``/api/*`` surface."""

    def record(self, *, method: str, path: str, headers: dict[str, str]) -> None: ...
    def all(self) -> list[dict[str, object]]: ...
    def clear(self) -> None: ...


class InMemoryCaptureStore:
    """Process-local received-request log, in arrival order. Implements ``ICaptureStore``."""

    def __init__(self) -> None:
        self._requests: list[dict[str, object]] = []

    def record(self, *, method: str, path: str, headers: dict[str, str]) -> None:
        self._requests.append({"method": method, "path": path, "headers": headers})

    def all(self) -> list[dict[str, object]]:
        return list(self._requests)

    def clear(self) -> None:
        self._requests.clear()
