"""A real blizzard runner against this mock hub (blizzard#433's ``blizzard:service-test``
acceptance line for Phase 1) — proves the capability-snapshot wire round trip end to end
using genuinely real runner-side code: the real ``blizzard-runner`` binary (which
assembles its capability snapshot from a real harness registry at composition, then
sends it through the real ``RunnerRegistrationRequest`` wire model) against this repo's
own mock hub.

``blizzard-mock`` cannot import ``blizzard`` at all — no dependency is declared, and the
two repos even keep separate venvs (this is why ``tests/test_wire_parity.py`` reads the
sibling worktree's committed OpenAPI spec by *path* rather than importing the models it
describes). So the runner side here is driven as a genuine out-of-process subprocess of
the sibling worktree's own installed ``blizzard-runner`` binary — exactly the shape
``src/blizzard_mock/mock_hub/cli.py`` already documents: "a service-tier test that runs
the real runner out of process points ``BZ_HUB_URL`` at this address." The hub side stays
in-process — served for real over a bound ``uvicorn`` socket, not a subprocess — so the
assertion can reach the stored registry row directly: the capability snapshot carries no
wire-exposed read surface by design (blizzard#433 renders nothing onto ``RunnerView``),
the same reason blizzard's own component test for this feature
(``tests/test_runner_registration_federation.py``) reads its hub's registry directly
rather than over the wire.

Not a skip: an unresolvable sibling checkout refuses a green rather than reporting a round
trip it never drove, mirroring ``test_wire_parity.py``'s own refusal-not-skip stance.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import uvicorn

from blizzard_mock.clock import FixedClock
from blizzard_mock.harness_identity import CLAUDE_CODE_HARNESS_ID
from blizzard_mock.mock_hub.app import create_app
from blizzard_mock.mock_hub.domain.service import MockHubService

#: The same sibling-resolution convention as ``test_wire_parity.py``'s ``_BLIZZARD``.
_BLIZZARD = Path(os.environ.get("BLIZZARD_SOURCE") or Path(__file__).resolve().parents[2] / "blizzard")
#: The sibling worktree's own venv, not this one's — ``blizzard-mock`` never installs
#: ``blizzard``, so the binary can only be found there (or at an explicit override).
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


def _disable_external_usage_network_call(runner_dir: Path) -> None:
    """Point the freshly scaffolded config's legacy subscription sampler at a
    guaranteed-missing credentials file — the real runner's own e2e helper's precedent
    (``blizzard/tests/e2e/test_acceptance_loop.py``'s ``external_usage_credentials_path``):
    a path that never exists trips the sampler's missing-credentials soft failure before
    any request is built, so this tick depends on no ambient
    ``~/.claude/.credentials.json`` a dev machine happens to carry."""
    config_path = runner_dir / "blizzard-runner.toml"
    text = config_path.read_text()
    patched = text.replace(
        "[external_subscription_usage]\n",
        '[external_subscription_usage]\ncredentials_path = "/nonexistent/no-such-credentials.json"\n',
        1,
    )
    assert patched != text, "the scaffolded config's [external_subscription_usage] header moved"
    config_path.write_text(patched)


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
        env = {**os.environ, "BZ_HUB_URL": f"http://127.0.0.1:{hub_port}"}
        init = subprocess.run(
            [str(runner_bin), "init", str(runner_dir)], env=env, capture_output=True, text=True, timeout=30
        )
        assert init.returncode == 0, init.stderr

        _disable_external_usage_network_call(runner_dir)

        # One synchronous reconciliation tick (REAP -> PULL -> FILL -> ADVANCE): PULL's
        # registry sync fires unconditionally, real capability snapshot included, with no
        # chunk seeded and nothing to claim.
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

    assert len(row.capabilities) == 1, f"expected exactly one capability, got {row.capabilities!r}"
    capability = row.capabilities[0]
    # `known_harnesses` binds exactly one harness by default (blizzard#433's own stated
    # exclusion: "no second adapter is bound"), so this single entry is also the default.
    assert capability.harness_id == CLAUDE_CODE_HARNESS_ID
    assert capability.default is True
    assert capability.version is None or isinstance(capability.version, str)
    # The adapter's built-in tier ids, enumerated with no operator `[models.aliases]` at all.
    assert set(capability.tiers) == {"blizzard:frontier", "blizzard:advanced", "blizzard:basic"}
