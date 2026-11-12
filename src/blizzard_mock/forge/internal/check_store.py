"""In-memory commit-attached check-run store (``ICheckRunStore``)."""

from __future__ import annotations

from threading import Lock

from blizzard_mock.forge.domain.models import CheckRun


class InMemoryCheckRunStore:
    """Process-local named runs per ``(repo, sha)``. Implements ``ICheckRunStore``."""

    def __init__(self) -> None:
        self._runs: dict[tuple[str, str], dict[str, CheckRun]] = {}
        self._lock = Lock()

    def runs(self, repo: str, sha: str) -> list[CheckRun] | None:
        with self._lock:
            armed = self._runs.get((repo, sha))
            return None if armed is None else list(armed.values())

    def replace_all(self, repo: str, sha: str, runs: list[CheckRun]) -> None:
        with self._lock:
            self._runs[repo, sha] = {run.name: run for run in runs}

    def put(self, repo: str, sha: str, run: CheckRun) -> None:
        with self._lock:
            self._runs.setdefault((repo, sha), {})[run.name] = run

    def clear_all(self) -> None:
        with self._lock:
            self._runs.clear()
