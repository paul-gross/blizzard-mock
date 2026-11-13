"""Every forge answer carries a unique ``X-GitHub-Request-Id`` (``blizzard-mock:unit-test``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.forge.api.middleware import REQUEST_ID_HEADER
from blizzard_mock.forge.domain.clock import FixedClock
from tests.forge_world import REPO, START, forge

URL = f"/repos/{REPO}"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with forge(tmp_path, FixedClock(START)) as client:
        yield client


def test_an_ordinary_200_carries_a_request_id(client: TestClient) -> None:
    resp = client.get(URL)
    assert resp.status_code == 200
    assert resp.headers[REQUEST_ID_HEADER]


def test_a_token_rejected_401_carries_a_request_id(client: TestClient) -> None:
    assert client.post("/_levers/token_rejected", json={"repo": REPO}).status_code == 200
    resp = client.get(URL)
    assert resp.status_code == 401
    assert resp.headers[REQUEST_ID_HEADER]


def test_two_requests_get_different_request_ids(client: TestClient) -> None:
    first = client.get(URL)
    second = client.get(URL)
    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]
