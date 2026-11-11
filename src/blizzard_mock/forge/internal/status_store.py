"""In-memory commit status histories and read errors."""

from blizzard_mock.forge.domain.statuses import StatusSpec


class InMemoryStatusStore:
    def __init__(self) -> None:
        self._histories: dict[tuple[str, str], list[StatusSpec]] = {}
        self._errors: dict[tuple[str, str], int] = {}

    def history(self, repo: str, sha: str) -> list[StatusSpec]:
        return list(self._histories.get((repo, sha), []))

    def replace(self, repo: str, sha: str, statuses: list[StatusSpec]) -> None:
        self._histories[repo, sha] = list(statuses)

    def read_error(self, repo: str, sha: str) -> int | None:
        return self._errors.get((repo, sha))

    def set_read_error(self, repo: str, sha: str, status: int | None) -> None:
        if status is None:
            self._errors.pop((repo, sha), None)
        else:
            self._errors[repo, sha] = status

    def clear_all(self) -> None:
        self._histories.clear()
        self._errors.clear()
