"""In-memory commit status histories and read errors."""

from threading import Lock

from blizzard_mock.forge.domain.statuses import Status, StatusSpec


class InMemoryStatusStore:
    def __init__(self) -> None:
        self._histories: dict[tuple[str, str], list[Status]] = {}
        self._errors: dict[tuple[str, str], int] = {}
        self._next_id = 1
        self._lock = Lock()

    def history(self, repo: str, sha: str) -> list[Status]:
        return list(self._histories.get((repo, sha), []))

    def replace(self, repo: str, sha: str, statuses: list[StatusSpec]) -> None:
        with self._lock:
            history = [
                Status(**status.model_dump(), id=self._next_id + len(statuses) - i - 1)
                for i, status in enumerate(statuses)
            ]
            self._next_id += len(history)
            self._histories[repo, sha] = history

    def read_error(self, repo: str, sha: str) -> int | None:
        return self._errors.get((repo, sha))

    def set_read_error(self, repo: str, sha: str, status: int | None) -> None:
        if status is None:
            self._errors.pop((repo, sha), None)
        else:
            self._errors[repo, sha] = status

    def clear_all(self) -> None:
        with self._lock:
            self._histories.clear()
            self._errors.clear()
