"""A real blizzard runner against this mock hub — proves the capability-snapshot wire
round trip end to end via a genuine out-of-process ``blizzard-runner`` subprocess from
the sibling worktree (rationale: ``tests/test_wire_parity.py``'s module docstring). The
hub stays in-process (a bound ``uvicorn`` socket) so the assertion can read the stored
registry row directly — the capability snapshot has no wire-exposed read surface, the
same reason ``tests/test_runner_registration_federation.py`` reads its hub directly. The
same real runner's ``init`` and ``store reset`` verbs are driven over the mock hub's resets,
the host directory's store read back from its SQLite file."""

from __future__ import annotations

import os
import re
import socket
import sqlite3
import subprocess
import threading
import time
import tomllib
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx2 as httpx
import pytest
import uvicorn

from blizzard_mock.clock import FixedClock
from blizzard_mock.harness_identity import CLAUDE_CODE_HARNESS_ID
from blizzard_mock.mock_hub.app import create_app
from blizzard_mock.mock_hub.domain.service import MockHubService

#: Reads the sibling ``blizzard`` checkout's built runner binary; CI deselects the marker
#: (`mise run test -- -m "not needs_blizzard"`) since a standalone checkout has no sibling.
pytestmark = pytest.mark.needs_blizzard

#: The same sibling-resolution convention as ``test_wire_parity.py``'s ``_BLIZZARD``.
_BLIZZARD = Path(os.environ.get("BLIZZARD_SOURCE") or Path(__file__).resolve().parents[2] / "blizzard")
#: The sibling worktree's own venv — ``blizzard-mock`` never installs ``blizzard`` itself.
_RUNNER_BIN = Path(os.environ.get("BLIZZARD_RUNNER_BIN") or _BLIZZARD / ".venv" / "bin" / "blizzard-runner")


def _require_runner_bin() -> Path:
    assert _RUNNER_BIN.is_file(), (
        f"no real blizzard-runner binary at {_RUNNER_BIN} — this service test proves a REAL "
        "runner against this mock hub, not a skip; set $BLIZZARD_RUNNER_BIN if it lives "
        "elsewhere, or $BLIZZARD_SOURCE for the sibling blizzard worktree itself"
    )
    return _RUNNER_BIN


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _await_health(port: int, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
        while time.monotonic() < deadline:
            try:
                if client.get("/api/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
    raise AssertionError(f"mock hub never answered health on port {port}")


#: The scaffolded runner's name, and the token variable it reads (``BZ_HUB_TOKEN_<NAME>``).
_RUNNER_NAME = "runner-local"
_TOKEN_VARIABLE = "BZ_HUB_TOKEN_RUNNER_LOCAL"

#: The ``[[subscription]]`` the scaffold declares and its runner names.
_SCAFFOLD_SUBSCRIPTION = '[[subscription]]\nslug = "anthropic"\n'


def _disable_external_usage_network_call(runner_dir: Path) -> None:
    """Point the scaffolded host file's ``[[subscription]]`` sampler at a guaranteed-missing
    credentials file (the real runner's e2e helper's own precedent) — a path that never
    exists trips the sampler's missing-credentials soft failure before any request is
    built, so this tick depends on no ambient ``~/.claude/.credentials.json`` a dev
    machine happens to carry."""
    config_path = runner_dir / "blizzard-host.toml"
    text = config_path.read_text()
    patched = text.replace(
        _SCAFFOLD_SUBSCRIPTION,
        _SCAFFOLD_SUBSCRIPTION + 'credentials_path = "/nonexistent/no-such-credentials.json"\n',
        1,
    )
    assert patched != text, "the scaffolded host file's anthropic [[subscription]] moved"
    config_path.write_text(patched)


def _host_runner_id(runner_dir: Path) -> str | None:
    """The ``[[runner]] id`` ``runner init`` wrote into the host file once the runner joined."""
    raw = tomllib.loads((runner_dir / "blizzard-host.toml").read_text())
    [runner] = raw["runner"]
    return runner.get("id")


def test_a_real_runner_registers_its_capabilities_and_the_mock_hub_reads_them_back(tmp_path: Path) -> None:
    runner_bin = _require_runner_bin()

    hub_port = _free_port()
    app = create_app(clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=hub_port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _await_health(hub_port)

        runner_dir = tmp_path / "runner"
        env = {**_scrubbed_env(), "BZ_HUB_URL": f"http://127.0.0.1:{hub_port}", "BZ_RUNNER_GATES": "build,review"}
        init = subprocess.run(
            [str(runner_bin), "init", str(runner_dir)], env=env, capture_output=True, text=True, timeout=30
        )
        assert init.returncode == 0, init.stderr

        _disable_external_usage_network_call(runner_dir)

        # One synchronous tick (REAP->PULL->FILL->ADVANCE): PULL's registry sync fires
        # unconditionally, real capability snapshot included, with nothing seeded to claim.
        tick = subprocess.run(
            [str(runner_bin), "tick", "--dir", str(runner_dir)], env=env, capture_output=True, text=True, timeout=30
        )
        assert tick.returncode == 0, tick.stderr
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    service: MockHubService = app.state.service
    runners = service._state.list_runners()  # the only read-back this feature exposes
    assert len(runners) == 1, f"expected exactly one registered runner, got {runners!r}"
    row = runners[0]
    # `runner init` added the runner at this hub — the hub minted its id and token — and the
    # tick registered it under the scaffold's default name with the token init wrote.
    assert row.runner_id.startswith("rn_")
    assert (row.name, row.never_connected()) == (_RUNNER_NAME, False)
    assert _env_token(runner_dir) is not None
    assert _host_runner_id(runner_dir) == row.runner_id
    assert row.gates == ("build", "review")

    # `known_harnesses` now binds two adapters (the OpenCode binding joined
    # Claude Code's), so the snapshot carries one capability per bound harness.
    assert len(row.capabilities) == 2, f"expected exactly two capabilities, got {row.capabilities!r}"
    by_harness = {c.harness_id: c for c in row.capabilities}
    assert set(by_harness) == {CLAUDE_CODE_HARNESS_ID, "opencode"}

    claude_code = by_harness[CLAUDE_CODE_HARNESS_ID]
    assert claude_code.default is True
    assert claude_code.version is None or isinstance(claude_code.version, str)
    # The adapter's built-in tier ids, enumerated with no operator `[models.aliases]` at all.
    assert set(claude_code.tiers) == {"blizzard:frontier", "blizzard:advanced", "blizzard:basic"}

    opencode = by_harness["opencode"]
    assert opencode.default is False
    assert opencode.version is None or isinstance(opencode.version, str)
    # OpenCode ships no built-in tier table: with no operator `[opencode.models.aliases]`
    # configured, it resolves nothing yet, still registering with an empty tier set.
    assert opencode.tiers == ()


def _scrubbed_env() -> dict[str, str]:
    """The test process's environment minus any ambient runner token — a process-env token
    outranks ``<dir>/.env``, so one leaking in from a dev shell would decide what init does."""
    return {key: value for key, value in os.environ.items() if not key.startswith("BZ_HUB_TOKEN")}


def _env_token(runner_dir: Path) -> str | None:
    """The ``BZ_HUB_TOKEN_<NAME>`` line ``runner init`` wrote into ``<dir>/.env`` for its runner, if any."""
    env_file = runner_dir / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == _TOKEN_VARIABLE:
            return value.strip().strip('"')
    return None


def test_runner_init_reuses_its_token_and_re_adds_after_a_hub_reset_only_with_allow_readd(tmp_path: Path) -> None:
    """``runner init`` against the mock hub, as against a real one on ``auth.mode = "none"``: after the hub's
    data is reset a re-run stops, adding nothing and leaving ``.env`` byte-for-byte as it was, until
    ``--allow-readd`` adds a fresh runner and replaces the token."""
    runner_bin = _require_runner_bin()
    hub_port = _free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    app = create_app(clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)))
    service: MockHubService = app.state.service
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=hub_port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    runner_dir = tmp_path / "runner"
    env = {**_scrubbed_env(), "BZ_HUB_URL": hub_url}

    def init(*flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(runner_bin), "init", str(runner_dir), *flags], env=env, capture_output=True, text=True, timeout=30
        )

    try:
        _await_health(hub_port)

        first = init()
        assert first.returncode == 0, first.stderr
        token = _env_token(runner_dir)
        [added] = service._state.list_runners()
        assert token is not None and service.identity(token).runner_id == added.runner_id

        again = init()
        assert again.returncode == 0, again.stderr
        assert _env_token(runner_dir) == token
        assert [r.runner_id for r in service._state.list_runners()] == [added.runner_id]

        service.reset()  # the hub's data is reset: the token init wrote is unknown here now
        env_before = (runner_dir / ".env").read_bytes()

        refused = init()
        assert refused.returncode != 0
        assert hub_url in refused.stderr + refused.stdout
        assert "--allow-readd" in refused.stderr + refused.stdout
        assert (runner_dir / ".env").read_bytes() == env_before
        assert service._state.list_runners() == []

        readded = init("--allow-readd")
        assert readded.returncode == 0, readded.stderr
        [fresh] = service._state.list_runners()
        fresh_token = _env_token(runner_dir)
        assert fresh_token is not None and fresh_token != token
        assert service.identity(fresh_token).runner_id == fresh.runner_id != added.runner_id
        assert _host_runner_id(runner_dir) == fresh.runner_id
    finally:
        server.should_exit = True
        thread.join(timeout=10)


#: A host store moved aside: ``host.db`` under its UTC set-aside stamp.
_ASIDE_DB = re.compile(r"^host\.db\.\d{8}T\d{6}Z$")
#: The files beside a SQLite database that hold part of it, moved aside under the database's new name.
_SQLITE_SIBLINGS = ("-wal", "-shm", "-journal")


@contextmanager
def _serving_mock_hub() -> Iterator[tuple[str, MockHubService]]:
    """A mock hub bound on a free local port for the block's duration: its URL and its service."""
    hub_port = _free_port()
    app = create_app(clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=hub_port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _await_health(hub_port)
        yield f"http://127.0.0.1:{hub_port}", app.state.service
    finally:
        server.should_exit = True
        thread.join(timeout=10)


class _RealRunner:
    """The sibling checkout's runner binary, run as one-shot verbs over one host directory."""

    def __init__(self, runner_dir: Path, hub_url: str) -> None:
        self.dir = runner_dir
        self._bin = _require_runner_bin()
        self._env = {**_scrubbed_env(), "BZ_HUB_URL": hub_url}

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self._bin), *args], env=self._env, capture_output=True, text=True, timeout=30)

    def init(self, *flags: str) -> subprocess.CompletedProcess[str]:
        return self.run("init", str(self.dir), *flags)

    def tick(self) -> None:
        """One synchronous tick, whose registration records the runner the hub answers as in the store."""
        tick = self.run("tick", "--dir", str(self.dir))
        assert tick.returncode == 0, tick.stderr

    @property
    def data(self) -> Path:
        return self.dir / "data"

    @property
    def store(self) -> Path:
        return self.data / "host.db"

    def set_aside(self) -> list[Path]:
        """The databases moved aside in the data directory, oldest stamp first."""
        return sorted(p for p in self.data.iterdir() if _ASIDE_DB.match(p.name))

    def siblings(self) -> list[str]:
        """The suffixes of the SQLite files beside the live store right now."""
        return [s for s in _SQLITE_SIBLINGS if self.store.with_name(self.store.name + s).exists()]


def _stored_runner_id(database: Path) -> str | None:
    """The runner the store's identity row names — the latest registration it recorded — or ``None``."""
    with closing(sqlite3.connect(database)) as conn:
        row = conn.execute("SELECT runner_id FROM runner_identity ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row is not None else None


def _tables(database: Path) -> set[str]:
    with closing(sqlite3.connect(database)) as conn:
        return {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _added_runner_id(service: MockHubService) -> str:
    [added] = service._state.list_runners()
    return added.runner_id


def test_a_real_runner_re_added_after_a_hub_reset_sets_its_old_store_aside_before_adding(tmp_path: Path) -> None:
    """``init --allow-readd`` over a store whose token the reset hub no longer knows moves that store aside
    under a timestamped name — the database and every sibling it had, none deleted — and the fresh store in
    its place comes to name the runner the re-add minted, never the old one."""
    with _serving_mock_hub() as (hub_url, service):
        runner = _RealRunner(tmp_path / "runner", hub_url)
        first = runner.init()
        assert first.returncode == 0, first.stderr
        _disable_external_usage_network_call(runner.dir)
        runner.tick()
        old_runner_id = _added_runner_id(service)
        assert _stored_runner_id(runner.store) == old_runner_id

        service.reset()  # the hub's data is reset: the token in .env names no runner here now
        siblings_before = runner.siblings()

        readded = runner.init("--allow-readd")
        assert readded.returncode == 0, readded.stderr
        new_runner_id = _added_runner_id(service)
        assert new_runner_id != old_runner_id

        [aside] = runner.set_aside()
        assert str(aside) in readded.stdout
        for suffix in siblings_before:
            assert aside.with_name(aside.name + suffix).exists(), f"the store's {suffix} sibling was not set aside"
        assert _stored_runner_id(aside) == old_runner_id, "the set-aside store lost the old runner's identity"
        # The store was set aside before the add; init records the re-added runner in the fresh one.
        assert runner.store.is_file()
        assert _stored_runner_id(runner.store) == new_runner_id
        assert _host_runner_id(runner.dir) == new_runner_id

        runner.tick()
        assert _stored_runner_id(runner.store) == new_runner_id
        assert runner.set_aside() == [aside]
        assert _stored_runner_id(aside) == old_runner_id


def test_a_real_runner_restarted_over_a_token_its_hub_knows_keeps_its_store(tmp_path: Path) -> None:
    """A plain ``init`` over a token the hub still knows keeps the runner and its store as they are."""
    with _serving_mock_hub() as (hub_url, service):
        runner = _RealRunner(tmp_path / "runner", hub_url)
        first = runner.init()
        assert first.returncode == 0, first.stderr
        _disable_external_usage_network_call(runner.dir)
        runner.tick()
        runner_id = _added_runner_id(service)
        token = _env_token(runner.dir)

        restarted = runner.init()
        assert restarted.returncode == 0, restarted.stderr
        assert _env_token(runner.dir) == token
        assert _added_runner_id(service) == runner_id
        assert runner.set_aside() == []
        assert _stored_runner_id(runner.store) == runner_id


def test_runner_store_reset_moves_a_real_runners_store_aside_and_leaves_a_fresh_one_at_head(tmp_path: Path) -> None:
    """``blizzard runner store reset <dir>`` moves the store aside, deleting nothing, and migrates a fresh
    store in its place: one the runner's next tick opens and registers into as the same runner."""
    with _serving_mock_hub() as (hub_url, service):
        runner = _RealRunner(tmp_path / "runner", hub_url)
        first = runner.init()
        assert first.returncode == 0, first.stderr
        _disable_external_usage_network_call(runner.dir)
        runner.tick()
        runner_id = _added_runner_id(service)
        siblings_before = runner.siblings()

        reset = runner.run("store", "reset", str(runner.dir))
        assert reset.returncode == 0, reset.stderr

        [aside] = runner.set_aside()
        assert str(aside) in reset.stdout
        for suffix in siblings_before:
            assert aside.with_name(aside.name + suffix).exists(), f"the store's {suffix} sibling was not set aside"
        assert _stored_runner_id(aside) == runner_id
        assert runner.store.is_file()
        assert _stored_runner_id(runner.store) is None
        assert _tables(runner.store) == _tables(aside), "the fresh store is not migrated to the set-aside one's head"

        runner.tick()  # a store behind head is refused at startup, so this tick proves the fresh one current
        assert _stored_runner_id(runner.store) == runner_id
        assert _env_token(runner.dir) is not None
