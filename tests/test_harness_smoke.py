"""Unit + component coverage for the mock coding-harness engine and facades.

The engine is *the prompt is the program*: these tests mint behavior-scripts and
run them through the fenced engine and the Claude Code facade, exercising the
verdict/ask/hang/crash surface, the ask-then-resume state protocol, and the
fence refusal — no real tokens, no real coding-harness binary.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from blizzard_mock.harness import engine, helpers
from blizzard_mock.harness.engine import FenceError
from blizzard_mock.harness.facades import claude_code
from blizzard_mock.harness.facades._text import PlainTextWire

# --------------------------------------------------------------------------- #
# Fixtures: a fenced worktree that is a real git repo.
# --------------------------------------------------------------------------- #


@pytest.fixture
def fenced_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A git worktree with the fence marker dropped and a fenced env dict."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    engine.write_fence_marker(tmp_path)
    env = engine.fenced_env({"PATH": __import__("os").environ.get("PATH", "")})
    return tmp_path, env


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
# The fence
# --------------------------------------------------------------------------- #


def test_fence_refuses_without_env_var(tmp_path: Path) -> None:
    engine.write_fence_marker(tmp_path)
    with pytest.raises(FenceError):
        engine.assert_fenced(tmp_path, {})  # marker present, env var absent


def test_fence_refuses_without_marker(tmp_path: Path) -> None:
    with pytest.raises(FenceError):
        engine.assert_fenced(tmp_path, engine.fenced_env({}))  # env var present, no marker


def test_fence_passes_when_marked(fenced_repo) -> None:
    cwd, env = fenced_repo
    engine.assert_fenced(cwd, env)  # both factors present — does not raise


def test_marker_found_in_ancestor(fenced_repo) -> None:
    cwd, env = fenced_repo
    child = cwd / "envs" / "alpha"
    child.mkdir(parents=True)
    engine.assert_fenced(child, env)  # walks up to the marker at the repo root


def test_run_prompt_refuses_unfenced(tmp_path: Path) -> None:
    with pytest.raises(FenceError):
        engine.run_prompt("verdict('x')", cwd=tmp_path, env={})


# --------------------------------------------------------------------------- #
# The exec engine: verdict, apply_diff + commit
# --------------------------------------------------------------------------- #


def test_verdict_is_emitted_in_tagged_form(fenced_repo) -> None:
    code, result = _run("verdict('approve', 'looks good')", fenced_repo)
    assert code == 0
    assert result.subtype == "success"
    assert "<Choice>approve</Choice>" in result.text
    assert "looks good" in result.text


def test_script_applies_diff_and_makes_real_commit(fenced_repo) -> None:
    cwd, _ = fenced_repo
    diff = (
        "diff --git a/new.txt b/new.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/new.txt\n"
        "@@ -0,0 +1 @@\n"
        "+hello from the mock\n"
    )
    script = f"apply_diff({diff!r})\ncommit('feat: add new.txt')\nverdict('done')"
    code, result = _run(script, fenced_repo)
    assert code == 0
    # A real commit landed in the worktree's git history.
    log = subprocess.run(["git", "log", "--oneline"], cwd=cwd, capture_output=True, text=True).stdout
    assert "feat: add new.txt" in log
    assert (cwd / "new.txt").read_text() == "hello from the mock\n"
    assert "<Choice>done</Choice>" in result.text


def test_plain_script_completes_success(fenced_repo) -> None:
    code, result = _run("x = 1 + 1", fenced_repo)
    assert code == 0
    assert result.subtype == "success"
    assert result.is_error is False


# --------------------------------------------------------------------------- #
# ask-then-resume: the session-state protocol
# --------------------------------------------------------------------------- #


def test_ask_records_state_and_exits_the_turn(fenced_repo) -> None:
    code, result = _run("ask('proceed?', ['yes', 'no'])\nverdict('never reached')", fenced_repo)
    assert code == 0  # ask ends the turn normally; the ask fact is how parking is derived
    assert result.subtype == "ask"
    assert result.ask is not None
    assert result.ask.question == "proceed?"
    assert result.ask.options == ["yes", "no"]
    assert result.text == "proceed?"  # verdict after ask never ran


def test_resume_reads_prior_ask_and_answers(fenced_repo) -> None:
    sid = "sess-fixed-1"
    # Turn 1: spawn with a pre-assigned id, ask, park.
    _run("ask('pick a color', ['red', 'blue'])", fenced_repo, session_id=sid)
    # Turn 2: resume — the message arrives as code and reads what was asked.
    resume_script = (
        "prior = state().last_ask.question\nverdict('resumed', f'you asked: {prior}; answer delivered: {answer()}')"
    )
    code, result = _run(resume_script, fenced_repo, session_id=sid, is_resume=True)
    assert code == 0
    assert "<Choice>resumed</Choice>" in result.text
    assert "you asked: pick a color" in result.text
    assert "answer delivered:" in result.text  # the resume message itself is the answer-as-code


def test_ask_shells_out_when_runner_cmd_configured(fenced_repo, tmp_path: Path) -> None:
    cwd, env = fenced_repo
    sentinel = tmp_path / "ask.log"
    # A trivial "runner ask" command: append its args to a file.
    script_cmd = tmp_path / "fake_ask.py"
    script_cmd.write_text(f"import sys; open({str(sentinel)!r}, 'w').write(' '.join(sys.argv[1:]))\n")
    env = {**env, engine.ASK_CMD_ENV_VAR: f"{sys.executable} {script_cmd}"}
    _run("ask('go?', ['a', 'b'])", (cwd, env))
    assert sentinel.exists()
    logged = sentinel.read_text()
    assert "go?" in logged and "a|b" in logged  # the real ask path fired with options


# --------------------------------------------------------------------------- #
# crash and hang
# --------------------------------------------------------------------------- #


def test_crash_yields_error_run(fenced_repo) -> None:
    code, result = _run("crash()", fenced_repo)
    assert code == 1
    assert result.is_error is True
    assert result.subtype == "error_during_execution"


def test_uncaught_exception_is_an_error_run(fenced_repo) -> None:
    code, result = _run("raise ValueError('boom')", fenced_repo)
    assert code == 1
    assert result.is_error is True
    assert "boom" in result.text


def test_hang_blocks_until_killed(fenced_repo) -> None:
    cwd, env = fenced_repo
    # Drive the real binary as a subprocess so we can time it out — a hung worker
    # never returns and never emits.
    proc = subprocess.Popen(
        [sys.executable, "-m", "blizzard_mock.harness.facades.claude_code", "-p", "hang()"],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        proc.communicate(timeout=1.5)
    proc.kill()
    proc.wait(timeout=5)


# --------------------------------------------------------------------------- #
# The Claude Code facade wire
# --------------------------------------------------------------------------- #


def test_claude_facade_json_envelope(fenced_repo, capsys) -> None:
    cwd, env = fenced_repo
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "blizzard_mock.harness.facades.claude_code",
            "-p",
            "--output-format",
            "json",
            "--session-id",
            "abc-123",
            "verdict('approve')",
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    envelope = json.loads(proc.stdout)
    assert envelope["type"] == "result"
    assert envelope["is_error"] is False
    assert envelope["session_id"] == "abc-123"
    assert "<Choice>approve</Choice>" in envelope["result"]


def test_claude_facade_fence_refusal_exit_code(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "blizzard_mock.harness.facades.claude_code", "-p", "verdict('x')"],
        cwd=tmp_path,
        env={"PATH": __import__("os").environ.get("PATH", "")},  # unfenced
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2  # refused, distinct from a script error (1)
    assert "refused to run" in proc.stderr


def test_claude_facade_bare_invocation_prints_usage(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        claude_code.main([])
    assert exc.value.code == 0
    assert "mock-claude-code" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Helper + engine seams still present (kept from the scaffold's smoke).
# --------------------------------------------------------------------------- #


def test_helper_surface_is_present() -> None:
    for name in ("ask", "apply_diff", "commit", "verdict", "hang", "crash", "state", "answer"):
        assert callable(getattr(helpers, name))


def test_engine_exposes_fence_seam() -> None:
    assert engine.FENCE_ENV_VAR
    assert callable(engine.assert_fenced)
    assert callable(engine.run_prompt)


def test_helper_outside_run_context_raises() -> None:
    with pytest.raises(RuntimeError):
        helpers.commit("no context")


def test_plain_text_wire_renders_ask() -> None:
    from blizzard_mock.harness.session import Ask

    result = engine.RunResult(session_id="s", subtype="ask", ask=Ask("q?", ["a", "b"]))
    assert PlainTextWire().render(result) == '<Ask options="a|b">q?</Ask>\n'


# --------------------------------------------------------------------------- #
# The runner's spawn preamble (blizzard issue #17)
# --------------------------------------------------------------------------- #


def test_split_worker_preamble_returns_the_script_half() -> None:
    prompt = (
        "You are a fleet worker in this winter workspace.\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| runner id | `runner-local` |\n"
        "| environment workdir | `/w/e1` |\n\n"
        "verdict('pass')\n"
    )
    preamble, script = engine.split_worker_preamble(prompt)

    assert "| environment workdir | `/w/e1` |" in preamble
    assert script == "verdict('pass')"


def test_split_worker_preamble_passes_a_bare_script_through() -> None:
    # A resume message and every direct engine caller send no preamble.
    assert engine.split_worker_preamble("verdict('pass')\n") == ("", "verdict('pass')\n")


def test_preamble_prefixed_prompt_still_runs_its_script(fenced_repo) -> None:
    """The runner prepends a facts table to every spawn prompt; exec'ing it is a
    SyntaxError, which would fail the turn before the script ever runs."""
    cwd, _ = fenced_repo
    prompt = f"| Field | Value |\n|-------|-------|\n| environment workdir | `{cwd}` |\n\nverdict('pass', 'ran')\n"

    code, result = _run(prompt, fenced_repo)

    assert code == 0
    assert result.subtype == "success"
    assert engine.CHOICE_OPEN + "pass" + engine.CHOICE_CLOSE in (result.text or "")


# --------------------------------------------------------------------------- #
# The acquired worktree (blizzard issue #17: cwd is the workspace root)
# --------------------------------------------------------------------------- #


def test_acquired_worktree_prefers_the_named_env_workdir(tmp_path: Path) -> None:
    workspace, workdir = tmp_path / "ws", tmp_path / "ws" / "e1"
    workdir.mkdir(parents=True)
    env = {engine.ENV_WORKDIRS_ENV_VAR: str(workdir)}

    assert engine.acquired_worktree(env, workspace) == workdir


def test_acquired_worktree_takes_the_first_of_several(tmp_path: Path) -> None:
    first, second = tmp_path / "e1", tmp_path / "e2"
    first.mkdir()
    second.mkdir()
    env = {engine.ENV_WORKDIRS_ENV_VAR: f"{first},{second}"}

    assert engine.acquired_worktree(env, tmp_path) == first


def test_acquired_worktree_falls_back_to_cwd(tmp_path: Path) -> None:
    # Absent (direct engine callers), empty, and stale-path values all degrade to cwd.
    assert engine.acquired_worktree({}, tmp_path) == tmp_path
    assert engine.acquired_worktree({engine.ENV_WORKDIRS_ENV_VAR: ""}, tmp_path) == tmp_path
    assert engine.acquired_worktree({engine.ENV_WORKDIRS_ENV_VAR: "/nonexistent/e9"}, tmp_path) == tmp_path


def test_run_prompt_works_in_the_named_env_not_the_process_cwd(fenced_repo, tmp_path: Path) -> None:
    """A spawned worker's cwd is the workspace root; the script must still touch its env."""
    repo, env = fenced_repo
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env = {**env, engine.ENV_WORKDIRS_ENV_VAR: str(repo)}
    captured: list[engine.RunResult] = []

    class _Wire:
        def render(self, result: engine.RunResult) -> str:
            captured.append(result)
            return ""

    import os

    prior = os.getcwd()
    os.chdir(workspace)  # stand where the runner spawns the worker
    try:
        engine.run_prompt(
            "import pathlib; pathlib.Path('made-here.txt').write_text('x')\n",
            wire=_Wire(),
            env=env,
            out=sys.stdout,
        )
    finally:
        os.chdir(prior)

    assert (repo / "made-here.txt").is_file(), "the script ran in the workspace root, not its env"
    assert not (workspace / "made-here.txt").exists()
