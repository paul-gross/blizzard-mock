"""Coverage for the OpenCode CLI-surface mode (`mock-opencode emit`).

Wholly separate from the exec-engine mode (`test_harness_smoke.py`'s
"prompt is the program"): these tests prove the *artifact's own contract* —
`emit`'s validate-before-write behavior, the lever roster changing observable
output, the artifact running unfenced under a bare system `python3` with no
`blizzard_mock` import reachable, and `run`'s own dispatch path staying
unperturbed by `emit`'s presence in the same module. Not a re-tiering of
`blizzard`'s 51 diagnostic tests (a later phase, in a different repo).
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from blizzard_mock.harness.opencode_surface import _baked
from blizzard_mock.harness.opencode_surface import emit as surface_emit
from blizzard_mock.harness.opencode_surface import levers as surface_levers

# --------------------------------------------------------------------------- #
# A bare system python3, with nothing from this repo's venv reachable.
# --------------------------------------------------------------------------- #

#: Deliberately excludes this repo's venv `bin/` (no `PYTHONPATH`, no
#: `VIRTUAL_ENV`, and a `PATH` that never resolves back to the venv's own
#: `python3`) — `env python3` in the emitted artifact's shebang must land on
#: the real system interpreter, matching how the Landlock-sandboxed real
#: diagnostic spawns it.
_BARE_SYSTEM_ENV = {"PATH": "/usr/bin:/bin"}


def test_bare_system_env_cannot_import_blizzard_mock() -> None:
    """Proves the stripped env itself carries no path back to this repo's venv —
    the premise every "runs under a bare system python3" test below relies on."""
    proc = subprocess.run(
        ["/usr/bin/python3", "-c", "import blizzard_mock"],
        env=_BARE_SYSTEM_ENV,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "ModuleNotFoundError" in proc.stderr


# --------------------------------------------------------------------------- #
# `mock-opencode emit`'s CLI contract
# --------------------------------------------------------------------------- #


def _run_emit_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "blizzard_mock.harness.facades.opencode", "emit", *args],
        capture_output=True,
        text=True,
    )


def test_emit_rejects_an_unknown_lever_and_writes_no_file(tmp_path: Path) -> None:
    out = tmp_path / "fake-opencode"
    proc = _run_emit_cli("--out", str(out), "--lever", "NOT_A_REAL_LEVER")

    assert proc.returncode != 0
    assert "NOT_A_REAL_LEVER" in proc.stderr
    assert not out.exists()


def test_emit_rejects_every_unknown_lever_in_one_message_not_just_the_first(tmp_path: Path) -> None:
    out = tmp_path / "fake-opencode"
    proc = _run_emit_cli("--out", str(out), "--lever", "BOGUS_ONE", "--lever", "BOGUS_TWO")

    assert proc.returncode != 0
    assert "BOGUS_ONE" in proc.stderr
    assert "BOGUS_TWO" in proc.stderr
    assert not out.exists()


@pytest.mark.parametrize(
    "lever_args",
    [
        [],
        ["--lever", "PERMISSION_REQUEST_ONLY"],
        ["--lever", "PERMISSION_REQUEST_ONLY", "--lever", "TAKEOVER_EXIT_EARLY"],
    ],
    ids=["no-levers", "one-lever", "two-levers"],
)
def test_emit_with_known_levers_produces_an_executable_artifact(tmp_path: Path, lever_args: list[str]) -> None:
    out = tmp_path / "fake-opencode"
    proc = _run_emit_cli("--out", str(out), *lever_args)

    assert proc.returncode == 0, proc.stderr
    assert out.is_file()
    assert out.stat().st_mode & 0o111  # executable


def test_emitted_artifact_runs_under_a_bare_system_python3_unfenced(tmp_path: Path) -> None:
    """No `blizzard_mock` importable, no fence env var, no fence marker file —
    and it still answers `--version` correctly."""
    out = tmp_path / "fake-opencode"
    surface_emit.emit(out, levers=frozenset())
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    assert "BLIZZARD_MOCK_HARNESS_FENCE" not in _BARE_SYSTEM_ENV
    assert not (scratch / ".blizzard-mock-harness-fence").exists()

    proc = subprocess.run([str(out), "--version"], cwd=scratch, env=_BARE_SYSTEM_ENV, capture_output=True, text=True)

    assert proc.returncode == 0
    assert proc.stdout == "opencode 1.18.25\n"


def test_emit_version_default_is_the_pinned_version(tmp_path: Path) -> None:
    out = tmp_path / "fake-opencode"
    surface_emit.emit(out, levers=frozenset())

    proc = subprocess.run([str(out), "--version"], env=_BARE_SYSTEM_ENV, cwd=tmp_path, capture_output=True, text=True)

    assert proc.stdout == "opencode 1.18.25\n"


def test_emit_version_override_is_baked_in(tmp_path: Path) -> None:
    out = tmp_path / "fake-opencode"
    surface_emit.emit(out, levers=frozenset(), version="9.9.9")

    proc = subprocess.run([str(out), "--version"], env=_BARE_SYSTEM_ENV, cwd=tmp_path, capture_output=True, text=True)

    assert proc.stdout == "opencode 9.9.9\n"


def test_baked_module_fields_match_emits_generated_template() -> None:
    """The checked-in `_baked.py` (kept for import/type-checking during development) and
    `emit._render_baked`'s generated replacement must declare the same fields — a
    hand-added field to one with nothing tying it to the other would silently drift."""

    def module_level_names(source: str) -> set[str]:
        return {
            node.target.id
            for node in ast.parse(source).body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }

    checked_in_fields = module_level_names(Path(_baked.__file__).read_text())
    generated_fields = module_level_names(surface_emit._render_baked(frozenset(), "1.0.0", None, None))

    assert checked_in_fields == generated_fields


# --------------------------------------------------------------------------- #
# The default (no-lever) emission is misbehaviour-free
# --------------------------------------------------------------------------- #


def test_default_emission_completes_a_fresh_turn_cleanly_with_no_misbehaviour(tmp_path: Path) -> None:
    out = tmp_path / "fake-opencode"
    surface_emit.emit(out, levers=frozenset())

    proc = subprocess.run(
        # A leading `run` matches the real CLI's own argv shape — `--session` as
        # argv[0] is a distinct, deliberately-hanging invocation this fake also answers.
        [str(out), "run", "--session", "sess-default-1", "do the task"],
        cwd=tmp_path,
        env=_BARE_SYSTEM_ENV,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert [event["type"] for event in events] == ["step_start", "text", "step_finish"]
    assert all(event["sessionID"] == "sess-default-1" for event in events)
    text_event = next(event for event in events if event["type"] == "text")
    assert "<Choice>pass</Choice>" in text_event["part"]["text"]
    # No denial, refusal, or wrong-session behavior anywhere in the turn.
    assert "error" not in " ".join(json.dumps(e) for e in events)
    assert "ses_wrong" not in proc.stdout


# --------------------------------------------------------------------------- #
# A representative spread of individual levers, each changing observable
# output relative to the default — every one against the real emitted binary.
# --------------------------------------------------------------------------- #


def test_permission_request_only_lever_changes_the_permission_turn_error(tmp_path: Path) -> None:
    default_out = tmp_path / "fake-opencode-default"
    lever_out = tmp_path / "fake-opencode-lever"
    surface_emit.emit(default_out, levers=frozenset())
    surface_emit.emit(lever_out, levers=surface_levers.parse(["PERMISSION_REQUEST_ONLY"]))

    def run(binary: Path) -> dict:
        proc = subprocess.run(
            [str(binary), "run a permission"], cwd=tmp_path, env=_BARE_SYSTEM_ENV, capture_output=True, text=True
        )
        assert proc.returncode == 0
        return json.loads(proc.stdout)

    default_event = run(default_out)
    lever_event = run(lever_out)

    assert default_event["part"]["state"]["error"] == (
        "The user has specified a rule which prevents you from using this specific tool call."
    )
    assert lever_event["part"]["state"]["error"] == "unrelated tool failure"
    assert default_event != lever_event


def test_takeover_wrong_session_lever_changes_the_served_session_id(tmp_path: Path) -> None:
    default_out = tmp_path / "fake-opencode-default"
    lever_out = tmp_path / "fake-opencode-lever"
    surface_emit.emit(default_out, levers=frozenset())
    surface_emit.emit(lever_out, levers=surface_levers.parse(["TAKEOVER_WRONG_SESSION"]))

    def served_session_id(binary: Path) -> str:
        proc = subprocess.Popen(
            [str(binary), "serve"],
            cwd=tmp_path,
            env=_BARE_SYSTEM_ENV,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert proc.stdout is not None
            line = proc.stdout.readline()
            match = re.search(r":(\d+)$", line.strip())
            assert match is not None
            port = int(match.group(1))
            import urllib.request

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/session/ses_real", timeout=5) as response:
                body = json.loads(response.read())
        finally:
            proc.terminate()
            proc.wait(timeout=5)
        return body["id"]

    assert served_session_id(default_out) == "ses_real"
    assert served_session_id(lever_out) == "ses_wrong"


def test_drop_config_shell_lever_changes_debug_config_pure(tmp_path: Path) -> None:
    default_out = tmp_path / "fake-opencode-default"
    lever_out = tmp_path / "fake-opencode-lever"
    surface_emit.emit(default_out, levers=frozenset())
    surface_emit.emit(lever_out, levers=surface_levers.parse(["DROP_CONFIG_SHELL"]))
    env = {**_BARE_SYSTEM_ENV, "OPENCODE_CONFIG_CONTENT": json.dumps({"shell": "bash", "compaction": True})}

    def effective_config(binary: Path) -> dict:
        proc = subprocess.run(
            [str(binary), "debug", "config", "--pure"], cwd=tmp_path, env=env, capture_output=True, text=True
        )
        assert proc.returncode == 0
        return json.loads(proc.stdout)

    assert effective_config(default_out) == {"shell": "bash", "compaction": True}
    assert effective_config(lever_out) == {"compaction": True}


def test_process_control_no_live_state_lever_suppresses_the_live_state_write(tmp_path: Path) -> None:
    default_out = tmp_path / "fake-opencode-default"
    lever_out = tmp_path / "fake-opencode-lever"
    surface_emit.emit(default_out, levers=frozenset())
    surface_emit.emit(lever_out, levers=surface_levers.parse(["PROCESS_CONTROL_NO_LIVE_STATE"]))

    def state_after_process_control_turn(binary: Path, state_home: Path) -> dict | None:
        state_home.mkdir()
        env = {**_BARE_SYSTEM_ENV, "XDG_STATE_HOME": str(state_home)}
        proc = subprocess.Popen(
            [str(binary), "run the process-control turn"],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert proc.stdout is not None
            proc.stdout.readline()  # the step_start event, printed before it sleeps
            time.sleep(0.3)
        finally:
            proc.terminate()
            proc.wait(timeout=5)
        state_file = state_home / "fake-opencode-state.json"
        return json.loads(state_file.read_text()) if state_file.exists() else None

    assert state_after_process_control_turn(default_out, tmp_path / "state-default") == {"phase": "process_control"}
    assert state_after_process_control_turn(lever_out, tmp_path / "state-lever") is None


# --------------------------------------------------------------------------- #
# `run`'s own wire shape is unperturbed by `emit`'s presence in the same module
# --------------------------------------------------------------------------- #
#
# `run` moved off the `<Choice>` + JSON-trailer wire onto the JSONL `step_start`/
# `text`/`step_finish` event stream in a separate, unrelated change (the OpenCode
# adapter's own real-protocol work — see `test_harness_smoke.py` for that shape's
# full coverage, including identity streaming and resume). These two tests never
# pinned that wire shape itself; they only ever proved `emit`'s own addition to
# this file doesn't reach into `run`'s dispatch path, which still holds.


def test_run_mode_output_is_unperturbed_by_emit(fenced_repo: tuple[Path, dict[str, str]]) -> None:
    """`run` still dispatches through the exec engine end to end — `emit` living in
    the same module changes none of `run`'s own JSONL event structure."""
    cwd, env = fenced_repo
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "blizzard_mock.harness.facades.opencode",
            "run",
            "--session",
            "sess-opencode-1",
            "verdict('approve', 'note')",
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0
    lines = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [event["type"] for event in lines] == ["step_start", "text", "step_finish"]
    assert all(event["sessionID"] == "sess-opencode-1" for event in lines)
    assert lines[1]["part"]["text"] == "<Choice>approve</Choice>\nnote"


def test_run_mode_bare_invocation_usage_is_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    from blizzard_mock.harness.facades import opencode

    with pytest.raises(SystemExit) as exc:
        opencode.main([])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "mock-opencode" in out
    assert "Also: mock-opencode emit" not in out  # the emit mention is -h/--help-only


def test_run_mode_no_longer_accepts_the_old_attach_flag() -> None:
    """``--attach`` was retired with the JSONL rewrite (a bare ``--session`` now
    implies resume) — an invocation using it is an ordinary unrecognized-argument
    error, not the old flag's own ``--attach requires --session`` validation."""
    from blizzard_mock.harness.facades import opencode

    with pytest.raises(SystemExit) as exc:
        opencode.main(["run", "--attach", "resume script"])

    assert exc.value.code == 2
