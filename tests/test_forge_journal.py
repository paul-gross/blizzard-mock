"""The forge's request journal (``blizzard-mock:unit-test``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.forge.domain.journal import JournalEntry
from blizzard_mock.forge.internal.journal_store import InMemoryRequestJournal
from tests.forge_world import REPO, forge


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with forge(tmp_path) as c:
        yield c


def _entries(client: TestClient) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = client.get("/_journal").json()["entries"]
    return entries


def test_requests_are_journaled_in_order_with_their_status(client: TestClient) -> None:
    client.get(f"/repos/{REPO}")
    client.post(f"/repos/{REPO}/issues", json={"title": "t"})
    client.get("/repos/nobody/nothing")

    assert _entries(client) == [
        {"method": "GET", "path": f"/repos/{REPO}", "status": 200},
        {"method": "POST", "path": f"/repos/{REPO}/issues", "status": 201},
        {"method": "GET", "path": "/repos/nobody/nothing", "status": 404},
    ]


def test_lever_refusals_are_journaled(client: TestClient) -> None:
    client.post("/_levers/token_rejected", json={})
    client.get(f"/repos/{REPO}")

    assert _entries(client) == [{"method": "GET", "path": f"/repos/{REPO}", "status": 401}]


def test_exempt_routes_are_not_journaled(client: TestClient) -> None:
    client.get("/healthz")
    client.get("/_levers")
    client.post("/_levers/unreachable", json={"repo": "x/y"})
    client.get("/_journal")

    assert _entries(client) == []


def test_delete_clears_the_journal(client: TestClient) -> None:
    client.get(f"/repos/{REPO}")
    assert client.delete("/_journal").json() == {"cleared": True}

    assert _entries(client) == []


def test_a_delayed_request_keeps_its_arrival_position() -> None:
    journal = InMemoryRequestJournal()
    slow = journal.arrive("GET", "/repos/a/slow")
    fast = journal.arrive("GET", "/repos/a/fast")
    fast.answer(200)

    assert journal.entries() == [JournalEntry(method="GET", path="/repos/a/fast", status=200)]

    slow.answer(200)
    assert [e.path for e in journal.entries()] == ["/repos/a/slow", "/repos/a/fast"]


def test_the_journal_drops_its_oldest_entry_past_capacity() -> None:
    journal = InMemoryRequestJournal(capacity=2)
    for name in ("one", "two", "three"):
        journal.arrive("GET", f"/repos/a/{name}").answer(200)

    assert [e.path for e in journal.entries()] == ["/repos/a/two", "/repos/a/three"]
