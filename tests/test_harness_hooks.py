"""Hook-seam coverage for the mock coding-harness engine and the claude_code facade.

Real Claude Code executes the hook commands its ``--settings`` document declares;
`engine.IHookRunner` is the seam that lets the mock do the same. These tests pin
*where* the engine fires — the lifecycle points, and the exits that deliberately
fire nothing — against a recording fake, and (phase 3) the real subprocess
execution against stub shell commands. Nothing here depends on `blizzard`: the
mock executes whatever command string a settings document names.

Two properties can only be observed from a process that never returns normally —
``crash(hard=True)`` is ``os._exit`` and ``hang()`` blocks forever — so this module
also owns :func:`run_out_of_process`, a driver that binds the **engine** directly
(no facade) and records through a durably-writing hook runner.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from blizzard_mock.harness import engine, helpers
from blizzard_mock.harness.engine import FenceError, RunResult
from blizzard_mock.harness.facades._text import PlainTextWire
from blizzard_mock.harness.session import SessionState, SessionStore

# --------------------------------------------------------------------------- #
# In-process recording fake
# --------------------------------------------------------------------------- #


class _RecordingHookRunner:
    """An ``IHookRunner`` that records every fire, in call order."""

    def __init__(self) -> None:
        self.tool_uses: list[tuple[str, dict[str, object], str]] = []
        self.session_ends: list[RunResult] = []
        self.calls: list[str] = []

    def on_tool_use(self, name: str, tool_input: Mapping[str, object], tool_output: str) -> None:
        self.tool_uses.append((name, dict(tool_input), tool_output))
        self.calls.append(f"tool:{name}")

    def on_session_end(self, result: RunResult) -> None:
        self.session_ends.append(result)
        self.calls.append("session_end")


def _run(script: str, repo_env: tuple[Path, dict[str, str]], **kw) -> tuple[int, engine.RunResult]:
    """Run a script through the engine with a capturing wire; return (code, result)."""
    cwd, env = repo_env
    captured: list[engine.RunResult] = []

    class _CapturingWire:
        def render(self, result: engine.RunResult) -> str:
            captured.append(result)
            return ""

    code = engine.run_prompt(script, wire=_CapturingWire(), cwd=cwd, env=env, out=sys.stdout, **kw)
    return code, captured[-1]


# --------------------------------------------------------------------------- #
# The out-of-process driver: for the exits that never return normally
# --------------------------------------------------------------------------- #

#: Source of the generated driver. It binds :func:`engine.run_prompt` **directly**
#: rather than a facade, so the assertions it carries hold from the phase the seam
#: lands in — no facade constructs a hook runner until the ``--settings`` wiring
#: exists, and a hard crash is a property of where the engine puts the fire point.
_DRIVER_SOURCE = '''\
"""Generated driver — one behavior script through the engine, with a durably
writing hook runner, in a process that may never return normally.

Written and run by ``run_out_of_process`` in ``tests/test_harness_hooks.py``.
"""

import os
import sys
from pathlib import Path

from blizzard_mock.harness import engine


class _FileAppendingHookRunner:
    """An ``IHookRunner`` that appends one line per fire and *durably* flushes.

    Open-append-flush-fsync-close on every call, deliberately. This driver exists
    to observe processes that never return normally: ``crash(hard=True)`` is
    ``os._exit``, which skips interpreter buffer flushing, and a hung child is
    killed outright. A buffered fake would leave an empty log whether or not the
    hook fired — so a negative assertion could not fail, and a positive one would
    lose every entry.
    """

    def __init__(self, path):
        self._path = Path(path)

    def _append(self, line):
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\\n")
            fh.flush()
            os.fsync(fh.fileno())

    def on_tool_use(self, name, tool_input, tool_output):
        self._append("tool:" + name)

    def on_session_end(self, result):
        self._append("session_end:" + result.subtype)


engine.run_prompt(Path(sys.argv[1]).read_text(), hooks=_FileAppendingHookRunner(sys.argv[2]))
'''


def run_out_of_process(
    script: str,
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float = 15.0,
) -> tuple[Path, int | None]:
    """Run ``script`` through the engine in a separate process; return ``(log, code)``.

    ``code`` is the child's exit status, or ``None`` when it had to be killed at
    ``timeout`` (a ``hang()``). The log holds one durably-flushed line per hook
    fire — read it with :func:`log_entries`. Scratch files land beside the worktree,
    never inside it, so a script's ``git add -A`` cannot pick the driver up.
    """
    work = Path(tempfile.mkdtemp(prefix="hook-driver-", dir=cwd.parent))
    driver = work / "driver.py"
    driver.write_text(_DRIVER_SOURCE)
    script_file = work / "script.py"
    script_file.write_text(script)
    log = work / "hooks.log"

    proc = subprocess.Popen(
        [sys.executable, str(driver), str(script_file), str(log)],
        cwd=cwd,
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        proc.communicate(timeout=timeout)
        return log, proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        return log, None


def log_entries(log: Path) -> list[str]:
    """The hook fires recorded by :func:`run_out_of_process`, in order."""
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# SessionEnd: the soft termination paths
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("script", "subtype"),
    [
        ("pass", "success"),
        ("verdict('approve', 'looks good')", "success"),
        ("ask('which way?')", "ask"),
        ("raise ValueError('boom')", "error_during_execution"),
        ("crash()", "error_during_execution"),
    ],
    ids=["plain", "verdict", "ask-park", "raised-exception", "soft-crash"],
)
def test_session_end_fires_exactly_once_on_every_soft_termination_path(fenced_repo, script: str, subtype: str) -> None:
    """All five exits that unwind normally converge on ``run_prompt``'s tail."""
    hooks = _RecordingHookRunner()
    _run(script, fenced_repo, hooks=hooks)
    assert hooks.calls == ["session_end"]
    assert hooks.session_ends[0].subtype == subtype


def test_a_script_raising_system_exit_fires_no_session_end(fenced_repo) -> None:
    """``sys.exit()`` skips the tail — ``SystemExit`` is not an ``Exception``.

    Pre-existing engine behavior (the ``except Exception`` boundary already skips
    the wire render too); pinned here as *known*, so the README's account of which
    exits fire nothing is true rather than approximately true.
    """
    hooks = _RecordingHookRunner()
    with pytest.raises(SystemExit):
        _run("import sys\nsys.exit()", fenced_repo, hooks=hooks)
    assert hooks.calls == []


def test_a_fence_refusal_fires_no_hook(tmp_path: Path) -> None:
    """The session never started, so there is no session to end."""
    hooks = _RecordingHookRunner()
    with pytest.raises(FenceError):
        engine.run_prompt("pass", cwd=tmp_path, env={}, hooks=hooks)
    assert hooks.calls == []


def test_hooks_default_to_none_and_change_nothing(fenced_repo) -> None:
    """The seam is opt-in: a caller that passes no runner gets today's behavior."""
    code, result = _run("verdict('approve')", fenced_repo)
    assert code == 0
    assert result.subtype == "success"


# --------------------------------------------------------------------------- #
# The exits that never return normally (out-of-process)
# --------------------------------------------------------------------------- #


def test_a_hard_crash_fires_no_session_end(fenced_repo) -> None:
    """``crash(hard=True)`` is ``os._exit`` — the same no-signal a SIGKILL'd real
    Claude Code leaves, which is precisely the case crash recovery exists to tell
    apart from a clean exit."""
    cwd, env = fenced_repo
    log, code = run_out_of_process("crash(hard=True)", cwd=cwd, env=env)
    assert code == 137
    assert log_entries(log) == []


def test_the_hard_crash_negative_is_meaningful(fenced_repo) -> None:
    """The positive control for the test above: the same driver, a clean exit, one
    durable entry. Without it, an empty log would prove nothing."""
    cwd, env = fenced_repo
    log, code = run_out_of_process("verdict('approve')", cwd=cwd, env=env)
    assert code == 0
    assert log_entries(log) == ["session_end:success"]


def test_the_out_of_process_driver_binds_the_engine_directly() -> None:
    """No facade anywhere in the driver: the assertions it carries must not depend
    on the ``--settings`` wiring a later phase adds."""
    assert "engine.run_prompt(" in _DRIVER_SOURCE
    assert "facades" not in _DRIVER_SOURCE


# --------------------------------------------------------------------------- #
# PostToolUse: the tool-call fire points
# --------------------------------------------------------------------------- #

_DIFF = (
    "diff --git a/new.txt b/new.txt\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/new.txt\n"
    "@@ -0,0 +1 @@\n"
    "+hello from the mock\n"
)


class _RecordingTranscript:
    """An ``ITranscriptWriter`` that records the calls the engine and helpers make."""

    def __init__(self) -> None:
        self.tool_calls: list[tuple[str, dict[str, object]]] = []
        self.tool_results: list[tuple[str, str]] = []

    def record_user(self, text: str) -> None: ...

    def record_result(self, result: RunResult) -> None: ...

    def record_tool_call(self, name: str, tool_input: Mapping[str, object]) -> str:
        self.tool_calls.append((name, dict(tool_input)))
        return f"toolu_{len(self.tool_calls)}"

    def record_tool_result(self, tool_use_id: str, output: str) -> None:
        self.tool_results.append((tool_use_id, output))


@pytest.fixture
def fenced_dir(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A fenced directory that is deliberately **not** a git repo."""
    engine.write_fence_marker(tmp_path)
    return tmp_path, engine.fenced_env({"PATH": os.environ.get("PATH", "")})


def test_apply_diff_and_commit_each_fire_post_tool_use_and_still_mint_their_pair(fenced_repo) -> None:
    """The two real tool calls fire once each, with Claude's own tool names, and the
    transcript pair the existing tests assert is unaffected by the split."""
    hooks = _RecordingHookRunner()
    transcript = _RecordingTranscript()
    _run(
        f"apply_diff({_DIFF!r})\ncommit('feat: add new.txt')",
        fenced_repo,
        hooks=hooks,
        transcript=transcript,
    )

    assert [name for name, _, _ in hooks.tool_uses] == ["Edit", "Bash"]
    assert hooks.calls == ["tool:Edit", "tool:Bash", "session_end"]
    assert [name for name, _ in transcript.tool_calls] == ["Edit", "Bash"]
    assert len(transcript.tool_results) == 2


def test_the_hook_fires_with_no_transcript_wired(fenced_repo) -> None:
    """The two seams are independent: the early return that used to skip everything
    when no writer was bound must not skip the hooks."""
    hooks = _RecordingHookRunner()
    _run(f"apply_diff({_DIFF!r})\ncommit('feat: add new.txt')", fenced_repo, hooks=hooks, transcript=None)
    assert [name for name, _, _ in hooks.tool_uses] == ["Edit", "Bash"]


def test_tool_call_fires_once_mints_one_pair_and_touches_no_git(fenced_dir) -> None:
    """Run in a directory that is not a git repo: any git call would fail the turn,
    so a clean success is itself the proof that nothing was committed."""
    hooks = _RecordingHookRunner()
    transcript = _RecordingTranscript()
    code, result = _run("tool_call('Read', {'file_path': '/tmp/x'})", fenced_dir, hooks=hooks, transcript=transcript)

    assert code == 0
    assert result.subtype == "success"
    assert hooks.tool_uses == [("Read", {"file_path": "/tmp/x"}, "ok")]
    assert transcript.tool_calls == [("Read", {"file_path": "/tmp/x"})]
    assert len(transcript.tool_results) == 1


def test_a_scripted_tool_timeline_beats_before_it_stalls(fenced_repo) -> None:
    """Three beats, then ``hang()``: the choreography the helper exists for, observed
    from outside the process that never returns."""
    cwd, env = fenced_repo
    script = "tool_call('Read')\ntool_call('Read')\ntool_call('Read')\nhang()"
    log, code = run_out_of_process(script, cwd=cwd, env=env, timeout=5.0)

    assert code is None, "the child must still be hung when we kill it"
    assert log_entries(log) == ["tool:Read", "tool:Read", "tool:Read"]


def test_tool_call_is_bound_into_the_script_namespace(tmp_path: Path) -> None:
    """The binding itself, asserted against ``_script_globals`` — a script calls
    ``tool_call(...)`` with no import, exactly as it calls ``commit(...)``."""
    ctx = engine.RunContext(
        session=SessionState(session_id="s"),
        wire=PlainTextWire(),
        cwd=tmp_path,
        env={},
        store=SessionStore(tmp_path),
        is_resume=False,
    )
    ns = engine._script_globals(ctx)
    assert ns["tool_call"] is helpers.tool_call
