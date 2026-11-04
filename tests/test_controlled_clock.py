"""Controlled-clock behavior across the mock fleet."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from blizzard_mock.clock import ClockBackwardsError, ControlledClock, SystemClock, resolve_clock
from blizzard_mock.forge.app import create_app as forge_app
from blizzard_mock.forge.config import ForgeConfig
from blizzard_mock.forge.domain.rate import DEFAULT_RATE_LIMIT
from blizzard_mock.mock_hub.app import create_app as hub_app
from blizzard_mock.mock_runner.app import create_app as runner_app
from blizzard_mock.mock_runner.internal.httpx_gateway import HttpxHubGateway
from tests.fleet_client import FleetClient
from tests.forge_world import REPO, build_repos


def test_clock_moves_and_concurrency() -> None:
    clock = ControlledClock()
    start = clock.now()
    assert clock.now() == start
    with pytest.raises(ClockBackwardsError):
        clock.set(start - timedelta(seconds=1))
    with pytest.raises(ValueError):
        clock.set(datetime(2030, 1, 1))
    with pytest.raises(ValueError):
        clock.advance(timedelta(seconds=-1))
    barrier = Barrier(8)

    def writer() -> list[datetime]:
        moves = []
        for _ in range(25):
            barrier.wait()
            moves.append(clock.advance(timedelta(seconds=1))[1])
            barrier.wait()
        return moves

    def reader() -> list[datetime]:
        readings = []
        for _ in range(25):
            barrier.wait()
            readings.append(clock.now())
            barrier.wait()
        return readings

    with ThreadPoolExecutor(max_workers=8) as pool:
        writers = [pool.submit(writer) for _ in range(4)]
        readers = [pool.submit(reader) for _ in range(4)]
        moves = [instant for future in writers for instant in future.result()]
        readings = [instant for future in readers for instant in future.result()]
    assert set(moves) == {start + timedelta(seconds=i) for i in range(1, 101)}
    assert set(readings) <= {start, *moves}
    assert clock.now() == start + timedelta(seconds=100)


@pytest.mark.parametrize(
    "setting,fence,controlled,warn",
    [
        (None, None, False, False),
        ("other", "1", False, False),
        ("controlled", None, False, True),
        ("controlled", "yes", False, True),
        ("controlled", "1", True, False),
    ],
)
def test_resolver(
    monkeypatch: pytest.MonkeyPatch, setting: str | None, fence: str | None, controlled: bool, warn: bool
) -> None:
    for key, value in (("BLIZZARD_CLOCK", setting), ("BLIZZARD_CLOCK_FENCE", fence)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    with capture_logs() as logs:
        clock = resolve_clock()
    assert isinstance(clock, ControlledClock if controlled else SystemClock)
    assert len(logs) == int(warn)
    if warn:
        assert logs[0]["log_level"] == "warning"
        assert "BLIZZARD_CLOCK" in logs[0]["event"] and "BLIZZARD_CLOCK_FENCE" in logs[0]["event"]


@pytest.mark.parametrize("kind", ["hub", "runner", "forge"])
def test_routes_and_fence(kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLIZZARD_CLOCK", "controlled")
    monkeypatch.setenv("BLIZZARD_CLOCK_FENCE", "1")

    def build() -> TestClient:
        if kind == "hub":
            return TestClient(hub_app())
        if kind == "runner":
            return TestClient(runner_app(gateway=HttpxHubGateway(TestClient(hub_app()), token="test")))
        return TestClient(forge_app(ForgeConfig(repos_dir=tmp_path)))

    client = build()
    start = datetime.fromisoformat(client.get("/_clock").json()["now"])
    future = start + timedelta(seconds=10)
    result = client.post("/_clock", json={"to": future.isoformat()})
    assert result.status_code == 200
    assert datetime.fromisoformat(result.json()["previous"]) == start
    assert datetime.fromisoformat(result.json()["now"]) == future
    assert client.post("/_clock", json={"advance_seconds": 0.5}).status_code == 200
    assert client.post("/_clock", json={"to": start.isoformat()}).json() == {"code": "clock_backwards"}
    for body in (
        {},
        {"to": future.isoformat(), "advance_seconds": 1},
        {"advance_seconds": -1},
        {"to": "2030-01-01T00:00:00"},
        {"to": 2000000000},
        {"to": "2000000000"},
        {"advance_seconds": True},
        {"advance_seconds": "3"},
    ):
        assert client.post("/_clock", json=body).status_code == 422
    assert datetime.fromisoformat(client.get("/_clock").json()["now"]) == future + timedelta(seconds=0.5)
    assert "/_clock" not in client.get("/openapi.json").json()["paths"]
    monkeypatch.delenv("BLIZZARD_CLOCK_FENCE")
    plain = build()
    assert plain.get("/_clock").status_code == 404
    assert plain.post("/_clock", json={"advance_seconds": 1}).status_code == 404


def test_hub_lever_and_seed_stamp() -> None:
    clock = ControlledClock()
    client = FleetClient(hub_app(clock=clock))
    client.post("/_clock", json={"advance_seconds": 42})
    now = client.get("/_clock").json()["now"]
    assert client.post("/_seed/scopes", json={"slug": "clock-test"}).status_code == 201
    assert datetime.fromisoformat(client.get("/api/fleet/scopes").json()[0]["created_at"]) == datetime.fromisoformat(
        now
    )
    client.post("/_levers/unreachable", json={})
    assert client.get("/_clock").status_code == 200
    assert client.post("/_clock", json={"advance_seconds": 0}).status_code == 200
    client.post("/_levers/reset")
    client.post("/_seed/reset")
    assert client.get("/_clock").json()["now"] == now


def test_runner_pause_uses_moved_clock() -> None:
    hub = TestClient(hub_app())
    runner = TestClient(runner_app(gateway=HttpxHubGateway(hub, token="test"), clock=ControlledClock()))
    assert runner.post("/_clock", json={"advance_seconds": 42}).status_code == 200
    now = runner.get("/_clock").json()["now"]
    runner.post("/_drive/pause", json={"by": "operator"})
    fact = hub.get("/_captured").json()["requests"][-1]["body"]["facts"][0]
    assert fact["kind"] == "runner.locally_paused"
    assert datetime.fromisoformat(fact["payload"]["at"]) == datetime.fromisoformat(now)


def test_forge_stamp_rate_and_lever(tmp_path: Path) -> None:
    client = TestClient(forge_app(ForgeConfig(repos_dir=build_repos(tmp_path / "repos")), clock=ControlledClock()))
    client.post("/_clock", json={"advance_seconds": 42})
    now = client.get("/_clock").json()["now"]
    issue = client.post(f"/repos/{REPO}/issues", json={"title": "clock"})
    assert datetime.fromisoformat(issue.json()["created_at"]) == datetime.fromisoformat(now).replace(microsecond=0)
    response = client.get(f"/repos/{REPO}")
    assert int(response.headers["X-RateLimit-Remaining"]) == DEFAULT_RATE_LIMIT - 2
    client.post("/_clock", json={"advance_seconds": 3601})
    assert int(client.get(f"/repos/{REPO}").headers["X-RateLimit-Remaining"]) == DEFAULT_RATE_LIMIT - 1
    client.post("/_levers/unreachable", json={})
    assert client.get("/_clock").status_code == 200
    assert client.post("/_clock", json={"advance_seconds": 0}).status_code == 200
    now = client.get("/_clock").json()["now"]
    client.post("/_levers/reset")
    assert client.get("/_clock").json()["now"] == now
