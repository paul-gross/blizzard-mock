"""Pinning tests for decisions that were previously defended only by comments.

Each test here is the counterpart of one trimmed comment elsewhere in the tree: the
comment names the decision and points at the test, the test fails if the decision is
reverted. Grouped by the component under test, not by tier — every case is unit or
component (no daemon, no network).
"""

from __future__ import annotations

import random
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.clock import FixedClock as RunnerFixedClock
from blizzard_mock.fixture_workspace.internal import subprocess_winter
from blizzard_mock.forge.app import create_app as create_forge_app
from blizzard_mock.forge.config import ForgeConfig
from blizzard_mock.forge.domain.clock import FixedClock as ForgeFixedClock
from blizzard_mock.harness import engine
from blizzard_mock.harness.engine import RunResult
from blizzard_mock.mock_data.domain.hub.scenario_seed import compose_board_scenario
from blizzard_mock.mock_data.internal import reflected_store
from blizzard_mock.mock_hub.app import create_app as create_hub_app
from blizzard_mock.mock_runner.app import create_app as create_runner_app
from blizzard_mock.mock_runner.domain.gateway import IHubGateway
from blizzard_mock.mock_runner.internal.httpx_gateway import HttpxHubGateway

# --------------------------------------------------------------------------- #
# harness engine: where the SessionEnd hook fires
# --------------------------------------------------------------------------- #


def test_session_end_fires_after_the_wire_render_and_before_run_prompt_returns(fenced_repo) -> None:
    """The hook is ``run_prompt``'s tail: the render lands first, and the fire has
    already happened by the time the call returns."""
    cwd, env = fenced_repo
    order: list[str] = []

    class _OrderingWire:
        def render(self, result: RunResult) -> str:
            order.append("render")
            return ""

    class _OrderingHooks:
        def on_tool_use(self, name: str, tool_input: Mapping[str, object], tool_output: str) -> None: ...

        def on_session_end(self, result: RunResult) -> None:
            order.append("session_end")

    code = engine.run_prompt(
        "verdict('approve')",
        wire=_OrderingWire(),
        cwd=cwd,
        env=env,
        out=sys.stdout,
        hooks=_OrderingHooks(),
    )

    assert code == 0
    assert order == ["render", "session_end"]


# --------------------------------------------------------------------------- #
# fixture workspace: the winter CLI subprocess's working directory
# --------------------------------------------------------------------------- #


def test_the_winter_cli_subprocess_runs_with_cwd_pinned_to_the_fixtures_own_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every winter invocation runs from ``<fixture>/tools/winter-cli``, so winter's
    own root-walk resolves the fixture rather than any enclosing workspace."""
    workspace = tmp_path / "fixture"
    (workspace / "tools" / "winter-cli").mkdir(parents=True)
    recorded: list[str | None] = []

    def _fake_run(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        recorded.append(kwargs.get("cwd"))
        return subprocess.CompletedProcess(list(cmd), 0, "", "")

    monkeypatch.setattr(subprocess_winter.subprocess, "run", _fake_run)

    cli = subprocess_winter.SubprocessWinterCli()
    cli.ensure_ready(workspace)
    cli.run(workspace, ["ws", "init"])

    expected = str(workspace / "tools" / "winter-cli")
    assert recorded == [expected, expected]


# --------------------------------------------------------------------------- #
# forge: check-run correlation
# --------------------------------------------------------------------------- #

_FORGE_REPO = "octocat/hello"


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, text=True)


@pytest.fixture
def forge_client(tmp_path: Path) -> Iterator[TestClient]:
    """A forge over a bare repo carrying ``main`` plus a clean ``feature`` branch."""
    root = tmp_path / "repos"
    work = root / "work"
    work.mkdir(parents=True)
    _git("init", "-b", "main", str(work))
    _git("-C", str(work), "config", "user.email", "seed@t")
    _git("-C", str(work), "config", "user.name", "seed")
    (work / "a.txt").write_text("hello\n")
    _git("-C", str(work), "add", "-A")
    _git("-C", str(work), "commit", "-m", "initial")
    _git("-C", str(work), "checkout", "-b", "feature")
    (work / "feature.txt").write_text("feature\n")
    _git("-C", str(work), "add", "-A")
    _git("-C", str(work), "commit", "-m", "feat")
    bare = root / "octocat" / "hello.git"
    bare.parent.mkdir(parents=True)
    _git("clone", "--bare", str(work), str(bare))

    config = ForgeConfig(repos_dir=root, host="127.0.0.1", port=4421)
    with TestClient(create_forge_app(config, clock=ForgeFixedClock(datetime(2026, 7, 13, tzinfo=UTC)))) as client:
        yield client


def test_check_runs_correlate_a_pr_by_its_head_commit_sha_not_its_branch_name(forge_client: TestClient) -> None:
    """A land script queries check-runs by the head commit sha; that query must find
    the PR's armed lever exactly as a branch-name query does."""
    pull = forge_client.post(
        f"/repos/{_FORGE_REPO}/pulls", json={"title": "feature", "head": "feature", "base": "main"}
    ).json()
    number = pull["number"]
    head_sha = pull["head"]["sha"]
    forge_client.post("/_levers/checks_failed", json={"repo": _FORGE_REPO, "number": number})

    by_sha = forge_client.get(f"/repos/{_FORGE_REPO}/commits/{head_sha}/check-runs").json()["check_runs"][0]

    assert by_sha["head_sha"] == head_sha
    assert by_sha["conclusion"] == "failure"


# --------------------------------------------------------------------------- #
# mock hub: chunk defaults and the work-items alias
# --------------------------------------------------------------------------- #

_HUB_SPEC: dict[str, Any] = {
    "entry": "build",
    "nodes": {
        "build": {
            "executor": "runner",
            "prompt": "b",
            "judgement_prompt": "j",
            "choices": [{"name": "pass", "description": "p", "to": "deliver"}],
        },
        "deliver": {"executor": "hub", "mode": "merge-to-main"},
    },
    "work_refs": [{"source": "o-r", "ref": "1"}],
}


@pytest.fixture
def hub_client() -> TestClient:
    return TestClient(create_hub_app(clock=RunnerFixedClock(datetime(2026, 7, 13, tzinfo=UTC))))


def test_a_chunk_spec_naming_neither_default_expresses_no_preference(hub_client: TestClient) -> None:
    """``default_model``/``default_effort`` default to empty/``None`` — what a real
    hub's ingest mints for a surface that declares neither."""
    chunk_id = hub_client.post("/_seed/chunk", json=_HUB_SPEC).json()["chunk_id"]

    detail = hub_client.get(f"/api/fleet/chunks/{chunk_id}").json()

    assert detail["default_model"] == []
    assert detail["default_effort"] is None


def test_a_chunk_spec_declaring_the_defaults_carries_them_through(hub_client: TestClient) -> None:
    """The positive control for the test above: the fields are genuinely seedable, so
    the empty reading is a default rather than a dropped value."""
    spec = {**_HUB_SPEC, "default_model": ["blizzard:basic"], "default_effort": "medium"}
    chunk_id = hub_client.post("/_seed/chunk", json=spec).json()["chunk_id"]

    detail = hub_client.get(f"/api/fleet/chunks/{chunk_id}").json()

    assert detail["default_model"] == ["blizzard:basic"]
    assert detail["default_effort"] == "medium"


def test_work_items_are_served_under_both_the_route_and_the_pm_items_alias(hub_client: TestClient) -> None:
    """Serving only one of the pair would let a caller pass here and 404 against the
    real hub, which is the divergence this mock exists to prevent."""
    chunk_id = hub_client.post("/_seed/chunk", json=_HUB_SPEC).json()["chunk_id"]

    canonical = hub_client.get(f"/api/fleet/chunks/{chunk_id}/work-items")
    alias = hub_client.get(f"/api/fleet/chunks/{chunk_id}/pm-items")

    assert canonical.status_code == 200
    assert alias.status_code == 200
    assert alias.json() == canonical.json()


# --------------------------------------------------------------------------- #
# mock runner: the fence-advancing lease report
# --------------------------------------------------------------------------- #


class _RecordingGateway:
    """Delegates to a real gateway, recording every lease-via-events body."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.lease_bodies: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def report_lease_via_events(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.lease_bodies.append(body)
        return self._inner.report_lease_via_events(chunk_id, body)


def test_the_lease_report_stamps_the_held_route_token_even_with_omit_route_token_armed() -> None:
    """The fence-advancing report is never lever-distorted: it carries the claim's own
    route token even while a route-token lever is armed for the driven completion."""
    clock = RunnerFixedClock(datetime(2026, 7, 13, tzinfo=UTC))
    hub = TestClient(create_hub_app(clock=clock))
    gateway = _RecordingGateway(HttpxHubGateway(hub))
    runner = TestClient(create_runner_app(gateway=cast(IHubGateway, gateway), clock=clock))

    chunk_id = hub.post("/_seed/chunk", json=_HUB_SPEC).json()["chunk_id"]
    runner.post("/_levers/lease_via_events", json={"chunk_id": chunk_id})
    runner.post("/_levers/omit_route_token", json={"chunk_id": chunk_id})

    claimed = runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()
    token = claimed["response"]["route_token"]

    assert token
    assert len(gateway.lease_bodies) == 1
    payload = gateway.lease_bodies[0]["facts"][0]["payload"]
    assert payload.get("route_token") == token


# --------------------------------------------------------------------------- #
# mock-data: the scenario board and the seed engine
# --------------------------------------------------------------------------- #


def test_the_stress_multi_question_chunk_is_an_already_waiting_on_human_chunk() -> None:
    """An open question outranks nearly every other status, so the extra question
    trails hang off a chunk that already derives ``waiting_on_human``."""
    scenario = compose_board_scenario(
        chunks=6,
        clock=RunnerFixedClock(datetime(2026, 7, 13, tzinfo=UTC)),
        rng=random.Random(1),
        stress=True,
    )
    assert scenario.census is not None
    status_by_chunk = {entry.chunk_id: entry.status for entry in scenario.census.chunk_entries}

    counts: dict[str, int] = {}
    for row in scenario.rows:
        if row.table == "questions":
            chunk_id = str(row.values["chunk_id"])
            counts[chunk_id] = counts.get(chunk_id, 0) + 1

    multi = [chunk_id for chunk_id, count in counts.items() if count >= 2]
    assert multi, "expected a chunk carrying 2+ question trails"
    assert [status_by_chunk[chunk_id] for chunk_id in multi] == ["waiting_on_human"] * len(multi)


def test_the_sqlite_seed_engine_sets_a_busy_timeout_and_postgres_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live daemon may hold the same sqlite file open, so a lock waits rather than
    raising ``database is locked`` immediately."""
    recorded: list[dict[str, Any]] = []
    real_create_engine = reflected_store.create_engine

    def _recording_create_engine(url: str, **kwargs: Any) -> Any:
        recorded.append(dict(kwargs.get("connect_args") or {}))
        return real_create_engine(url, **kwargs)

    monkeypatch.setattr(reflected_store, "create_engine", _recording_create_engine)

    reflected_store.create_seed_engine("sqlite:///:memory:")
    reflected_store.create_seed_engine("postgresql+psycopg://u:p@localhost:5432/db")

    assert recorded[0].get("timeout", 0) > 0
    assert recorded[1] == {}
