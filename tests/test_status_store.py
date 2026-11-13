from concurrent.futures import ThreadPoolExecutor

from blizzard_mock.forge.domain.statuses import StatusSpec
from blizzard_mock.forge.internal.status_store import InMemoryStatusStore


def test_concurrent_replacements_allocate_unique_ids() -> None:
    store = InMemoryStatusStore()
    statuses = [StatusSpec(context="ci", state="success") for _ in range(20)]

    def replace(index: int) -> list[int]:
        sha = str(index)
        store.replace("owner/repo", sha, statuses)
        return [status.id for status in store.history("owner/repo", sha)]

    with ThreadPoolExecutor(max_workers=12) as pool:
        histories = list(pool.map(replace, range(100)))

    ids = [status_id for history in histories for status_id in history]
    assert len(ids) == len(set(ids)) == 2000
    assert all(history == sorted(history, reverse=True) for history in histories)
