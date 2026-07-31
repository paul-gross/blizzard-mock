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
# The fenced worktree fixture (`fenced_repo`) lives in `tests/conftest.py`, shared
# with `test_harness_hooks.py`.
# --------------------------------------------------------------------------- #


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


def test_claude_facade_json_envelope_carries_usage_and_cost(fenced_repo) -> None:
    """The result envelope must carry a realistic ``usage`` object + ``total_cost_usd``
    (blizzard epic #57) — what the runner adapter's ``parse_usage`` reads back."""
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
            "usage-1",
            "verdict('approve')",
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    envelope = json.loads(proc.stdout)
    assert envelope["model"]
    usage = envelope["usage"]
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert usage["cache_read_input_tokens"] > 0
    assert usage["cache_creation_input_tokens"] > 0
    assert isinstance(envelope["total_cost_usd"], float)
    assert envelope["total_cost_usd"] > 0


def test_claude_facade_resume_json_envelope_carries_usage_and_cost(fenced_repo) -> None:
    """A resume invocation with ``--output-format json`` renders the same result
    envelope (``usage`` + ``total_cost_usd``) as a spawn (blizzard runner adapter
    ``resume_with_message``, epic #57) — the mock's resume path must not degrade
    to plain text just because it's a resume."""
    cwd, env = fenced_repo
    sid = "sess-resume-usage"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "blizzard_mock.harness.facades.claude_code",
            "-p",
            "--output-format",
            "json",
            "--session-id",
            sid,
            "ask('proceed?', ['yes', 'no'])",
        ],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "blizzard_mock.harness.facades.claude_code",
            "-p",
            "--output-format",
            "json",
            "--resume",
            sid,
            "verdict('resumed')",
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    envelope = json.loads(proc.stdout)
    assert envelope["type"] == "result"
    assert envelope["session_id"] == sid
    usage = envelope["usage"]
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0
    assert isinstance(envelope["total_cost_usd"], float)
    assert envelope["total_cost_usd"] > 0


def test_claude_facade_node_entry_resume_continues_in_place(fenced_repo) -> None:
    """A **spawn-shaped** node-entry resume (blizzard issue #115, Slice 6) — the
    extended real adapter's ``--resume <sid> <preamble+script>`` form, carrying the
    *same* full node preamble + node script a fresh spawn gets, not a bare
    follow-up message — must run the entered node's script and keep the session
    **in place**: same ``session_id``, ``num_turns`` accumulating across entries.
    This exercises no new engine code — ``run_prompt`` already splits the preamble
    off the script unconditionally of ``is_resume`` (``engine.py`` line ~438) and
    ``load_or_create`` + ``turns += 1`` already continue the named session
    (``engine.py`` lines ~413-422) — this test is the proof the wiring already
    satisfies the extended adapter's contract.

    An e2e distinguishes fresh vs. resumed vs. targeted-resume exactly as this test
    does: read ``session_id`` + ``num_turns`` off the JSON result envelope (the
    same fields the real runner adapter's ``parse_usage``-neighboring code reads
    off stdout) — a resumed node-entry keeps the prior node's ``session_id`` with
    ``num_turns > 1``; a fresh entry (a new node, or the first entry into any node)
    gets its own new ``session_id`` with ``num_turns == 1``.
    """
    cwd, env = fenced_repo
    preamble = f"| Field | Value |\n|-------|-------|\n| environment workdir | `{cwd}` |\n\n"

    def run(*extra_args: str, script: str) -> dict:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "blizzard_mock.harness.facades.claude_code",
                "-p",
                "--output-format",
                "json",
                *extra_args,
                preamble + script,
            ],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    # First entry into node `build`: a fresh spawn (session_hint pre-assigns the id,
    # exactly as `_spawn_attempt` does today when `resume_from` is None).
    build_sid = "sess-node-build"
    first = run("--session-id", build_sid, script="verdict('pass', 'first build')")
    assert first["session_id"] == build_sid
    assert first["num_turns"] == 1

    # `review` runs fresh in between (a different node, its own session) — proves a
    # resume does not leak across nodes.
    review = run("--session-id", "sess-node-review", script="verdict('changes_requested')")
    assert review["session_id"] == "sess-node-review"
    assert review["num_turns"] == 1

    # Re-entering `build` (`resume:build`): the extended adapter emits
    # `--resume <resume_from>` carrying the *full* preamble + the re-entered node's
    # script — not a bare follow-up message. The session must continue in place.
    second = run("--resume", build_sid, script="verdict('pass', 'second build (resumed)')")
    assert second["session_id"] == build_sid  # in place — no fork
    assert second["num_turns"] == 2  # accumulated on the same sid, not reset

    # A third, later re-entry keeps accumulating on the same sid.
    third = run("--resume", build_sid, script="verdict('pass', 'third build (resumed again)')")
    assert third["session_id"] == build_sid
    assert third["num_turns"] == 3


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
    for name in ("ask", "apply_diff", "commit", "tool_call", "verdict", "hang", "crash", "state", "answer"):
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
# The <behavior-script> tag (blizzard-mock issue #2)
# --------------------------------------------------------------------------- #

#: Prose that is a SyntaxError if it ever reaches the interpreter — every tagged-prompt
#: test wraps its script in some, so "only the block ran" is proven by the turn not dying.
_PROSE = "You are a fleet worker. Do the work item's thing, then declare a verdict.\n"


def _tagged(*scripts: str, prose: str = _PROSE) -> str:
    """A prompt with ``prose`` around each script in its own ``<behavior-script>`` block."""
    parts = [prose]
    for script in scripts:
        parts.append(f"{engine.BEHAVIOR_SCRIPT_OPEN}\n{script}\n{engine.BEHAVIOR_SCRIPT_CLOSE}\n")
        parts.append(prose)
    return "\n".join(parts)


def _runner_preamble(workspace_prose: str, cwd: Path) -> str:
    """A stand-in for the runner's spawn preamble (``blizzard:runner/harness/preamble.py``).

    Operator-owned prose ahead of the facts table — layers the *script author does not
    control*, which is why what they happen to say about the tag must not change how a
    prompt is read.
    """
    return (
        "You are a worker in a blizzard fleet.\n\n"
        f"{workspace_prose}\n\n"
        f"| Field | Value |\n|-------|-------|\n| chunk id | `ch_1` |\n| environment workdir | `{cwd}` |\n"
    )


def test_split_behavior_script_returns_none_for_an_untagged_prompt() -> None:
    # The caller's signal to fall back to the legacy positional split.
    assert engine.split_behavior_script("verdict('pass')\n") is None


@pytest.mark.parametrize(
    "mention",
    [
        f"House rule: wrap your script in a `{engine.BEHAVIOR_SCRIPT_OPEN}` tag.",
        f"House rule: put your script between `{engine.BEHAVIOR_SCRIPT_OPEN}` and `{engine.BEHAVIOR_SCRIPT_CLOSE}`.",
    ],
    ids=["unbalanced-inline", "balanced-inline"],
)
def test_prose_that_only_mentions_the_tag_carries_no_block(mention: str) -> None:
    # A delimiter counts on a line of its own; inline, it is just words.
    assert engine.split_behavior_script(f"{mention}\nverdict('pass')\n") is None


@pytest.mark.parametrize(
    "mention",
    [
        f"House rule: wrap your script in a `{engine.BEHAVIOR_SCRIPT_OPEN}` tag.",
        f"House rule: put your script between `{engine.BEHAVIOR_SCRIPT_OPEN}` and `{engine.BEHAVIOR_SCRIPT_CLOSE}`.",
    ],
    ids=["unbalanced-inline", "balanced-inline"],
)
def test_preamble_prose_about_the_tag_leaves_a_legacy_script_alone(mention: str, fenced_repo) -> None:
    """The runner composes the preamble, not the script author: operator prose that
    merely talks about the tag must neither hijack the run (its snippet becoming the
    program while the node's real script is silently reclassified as prose) nor, when
    unpaired, kill every untagged spawn in the deployment."""
    cwd, _ = fenced_repo
    prompt = _runner_preamble(mention, cwd) + "\nverdict('pass', 'the real node script ran')\n"

    code, result = _run(prompt, fenced_repo)

    assert code == 0
    assert result.subtype == "success"
    assert "the real node script ran" in (result.text or "")


def test_split_behavior_script_separates_the_program_from_the_prose() -> None:
    tagged = engine.split_behavior_script(_tagged("verdict('pass')"))

    assert tagged is not None
    assert tagged.script == "verdict('pass')"
    assert engine.BEHAVIOR_SCRIPT_OPEN not in tagged.prose
    assert "You are a fleet worker." in tagged.prose


def test_tagged_prompt_execs_only_the_block(fenced_repo) -> None:
    code, result = _run(_tagged("verdict('pass', 'ran')"), fenced_repo)

    assert code == 0
    assert result.subtype == "success"
    assert engine.CHOICE_OPEN + "pass" + engine.CHOICE_CLOSE in (result.text or "")


def test_multiple_blocks_are_concatenated_in_order(fenced_repo) -> None:
    prompt = _tagged("marks = ['first']", "marks.append('second')", "verdict('pass', ','.join(marks))")

    code, result = _run(prompt, fenced_repo)

    assert code == 0
    assert "first,second" in (result.text or "")


def test_tagged_prompt_does_not_consult_the_preamble_split(fenced_repo) -> None:
    """A tagged prompt's prose may sit *after* the facts table — the positional split
    would feed that prose to the interpreter, the tag never does."""
    cwd, _ = fenced_repo
    prompt = (
        f"| Field | Value |\n|-------|-------|\n| environment workdir | `{cwd}` |\n\n"
        f"{_PROSE}\n{engine.BEHAVIOR_SCRIPT_OPEN}\nverdict('pass')\n{engine.BEHAVIOR_SCRIPT_CLOSE}\n"
    )

    code, result = _run(prompt, fenced_repo)

    assert code == 0
    assert result.subtype == "success"


def test_a_tagged_block_still_runs_in_the_acquired_worktree(fenced_repo) -> None:
    cwd, _ = fenced_repo
    code, _ = _run(_tagged("import pathlib; pathlib.Path('tagged.txt').write_text('x')"), fenced_repo)

    assert code == 0
    assert (cwd / "tagged.txt").read_text() == "x"


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (f"{_PROSE}{engine.BEHAVIOR_SCRIPT_OPEN}\nverdict('pass')\n", "no closing"),
        (f"{_PROSE}verdict('pass')\n{engine.BEHAVIOR_SCRIPT_CLOSE}\n", "no opening"),
        (
            f"{engine.BEHAVIOR_SCRIPT_OPEN}\nverdict('pass')\n"
            f"{engine.BEHAVIOR_SCRIPT_OPEN}\nverdict('pass')\n{engine.BEHAVIOR_SCRIPT_CLOSE}\n",
            "nested",
        ),
    ],
    ids=["unclosed", "stray-close", "nested"],
)
def test_a_malformed_tag_fails_the_turn_loudly(prompt: str, expected: str, fenced_repo) -> None:
    """Never a silent fall-through to legacy exec or a no-op turn: a typo'd tag that
    quietly succeeds with no verdict and no side effects makes tests rot invisibly."""
    with pytest.raises(engine.BehaviorScriptTagError):
        engine.split_behavior_script(prompt)

    code, result = _run(prompt, fenced_repo)

    assert code == 1
    assert result.is_error
    assert result.subtype == "error_during_execution"
    assert engine.BEHAVIOR_SCRIPT_OPEN in result.text  # names the tag problem
    assert expected in result.text


@pytest.mark.parametrize(
    "block",
    [
        f"{engine.BEHAVIOR_SCRIPT_OPEN}\n{engine.BEHAVIOR_SCRIPT_CLOSE}",
        f"{engine.BEHAVIOR_SCRIPT_OPEN}\n   \n{engine.BEHAVIOR_SCRIPT_CLOSE}",
    ],
    ids=["empty", "whitespace-only"],
)
def test_a_block_enclosing_nothing_fails_rather_than_succeeding_with_no_verdict(block: str, fenced_repo) -> None:
    """Well-formed but empty is the same rot as malformed: exit 0 with no verdict and no
    side effects is exactly what a test cannot notice."""
    code, result = _run(f"{_PROSE}\n{block}\n", fenced_repo)

    assert code == 1
    assert result.subtype == "error_during_execution"
    assert "empty behavior-script block" in result.text


def test_an_indented_block_is_dedented(fenced_repo) -> None:
    """A tag nested in a markdown list or blockquote — a natural shape once prompts read
    like prose — must yield runnable source, not an IndentationError."""
    prompt = (
        "Steps:\n\n"
        f"  {engine.BEHAVIOR_SCRIPT_OPEN}\n"
        "  marks = 'indented'\n"
        "  if marks:\n"
        "      verdict('pass', marks)\n"
        f"  {engine.BEHAVIOR_SCRIPT_CLOSE}\n"
    )

    code, result = _run(prompt, fenced_repo)

    assert code == 0
    assert "indented" in (result.text or "")


def test_a_tagged_resume_message_execs_its_block(fenced_repo) -> None:
    _run(_tagged("ask('proceed?', ['yes', 'no'])"), fenced_repo, session_id="sess-tagged-resume")

    code, result = _run(
        _tagged("verdict('pass', answer())", prose="Go ahead, ship it."),
        fenced_repo,
        session_id="sess-tagged-resume",
        is_resume=True,
    )

    assert code == 0
    assert result.subtype == "success"
    # `answer()` hands back the human's prose — never the script's own source.
    assert "Go ahead, ship it." in (result.text or "")
    assert "verdict(" not in (result.text or "")
    assert engine.BEHAVIOR_SCRIPT_OPEN not in (result.text or "")


def test_an_untagged_resume_message_still_execs_in_full(fenced_repo) -> None:
    _run("ask('proceed?')", fenced_repo, session_id="sess-untagged-resume")

    code, result = _run("verdict('pass', answer())", fenced_repo, session_id="sess-untagged-resume", is_resume=True)

    assert code == 0
    # Untagged, the resume is code end to end, and `answer()` returns it verbatim as
    # it always has — what blizzard's own ask/answer scripts read.
    assert "verdict('pass', answer())" in (result.text or "")


def test_a_tagged_prompt_records_its_prose_as_the_transcript_user_turn(fenced_repo) -> None:
    """Genuine prose with the script blocks elided, replacing the synthetic placeholder."""
    recorded: list[str] = []

    class _Transcript:
        def record_user(self, text: str) -> None:
            recorded.append(text)

        def record_result(self, result: engine.RunResult) -> None: ...

        def record_tool_call(self, name: str, tool_input: dict) -> str:
            return "toolu_x"

        def record_tool_result(self, tool_use_id: str, output: str) -> None: ...

    code, _ = _run(_tagged("verdict('pass')"), fenced_repo, transcript=_Transcript())

    assert code == 0
    assert len(recorded) == 1
    assert "You are a fleet worker." in recorded[0]
    assert "verdict(" not in recorded[0]
    assert engine.BEHAVIOR_SCRIPT_OPEN not in recorded[0]
    assert engine.BEHAVIOR_SCRIPT_CLOSE not in recorded[0]
    assert "is not shown here" not in recorded[0]  # not the synthetic placeholder


# --------------------------------------------------------------------------- #
# Whole-message mode (blizzard-mock issue #8): the entire message is the script
# --------------------------------------------------------------------------- #


def test_whole_message_execs_the_entire_body_with_no_tag(fenced_repo) -> None:
    code, result = _run("verdict('pass', 'ran')", fenced_repo, whole_message=True)

    assert code == 0
    assert result.subtype == "success"
    assert engine.CHOICE_OPEN + "pass" + engine.CHOICE_CLOSE in (result.text or "")


def test_whole_message_ignores_a_behavior_script_tag_mention_on_its_own_line(fenced_repo) -> None:
    """Whole-message mode does no sentinel scanning at all: a `<behavior-script>`
    mention alone on a line — the exact shape that ends a *tagged* block early — is
    just more script text here, even though it is itself invalid as a standalone
    statement outside of a string."""
    script = (
        "marks = []\n"
        "text = '''\n"
        f"{engine.BEHAVIOR_SCRIPT_OPEN}\n"
        "marks.append('should not run as code')\n"
        f"{engine.BEHAVIOR_SCRIPT_CLOSE}\n"
        "'''\n"
        "marks.append('real')\n"
        "verdict('pass', ','.join(marks))\n"
    )

    code, result = _run(script, fenced_repo, whole_message=True)

    assert code == 0
    assert result.subtype == "success"
    # The tag lines inside the string literal are inert data, not delimiters — the
    # whole body ran as one program, including the line right after the string.
    assert "real" in (result.text or "")
    assert "should not run as code" not in (result.text or "")


def test_whole_message_does_not_consult_the_preamble_split(fenced_repo) -> None:
    """A message that happens to contain a preamble-shaped facts table is still
    exec'd wholesale — whole-message mode never calls `split_worker_preamble`."""
    cwd, _ = fenced_repo
    script = (
        f"table = '| Field | Value |\\n|-------|-------|\\n| environment workdir | `{cwd}` |'\n"
        "verdict('pass', 'ran despite the table-shaped string')\n"
    )

    code, result = _run(script, fenced_repo, whole_message=True)

    assert code == 0
    assert "ran despite the table-shaped string" in (result.text or "")


def test_whole_message_resume_execs_the_entire_body_wholesale(fenced_repo) -> None:
    _run("ask('proceed?')", fenced_repo, session_id="sess-whole-resume", whole_message=True)

    code, result = _run(
        "verdict('pass', answer())",
        fenced_repo,
        session_id="sess-whole-resume",
        is_resume=True,
        whole_message=True,
    )

    assert code == 0
    # Untagged and whole-message alike, a resume is code end to end — `answer()`
    # returns the resume message verbatim, never inferred prose.
    assert "verdict('pass', answer())" in (result.text or "")


@pytest.mark.parametrize(
    "script",
    ["", "   \n\n", "# just a comment\n# nothing to run\n"],
    ids=["empty", "whitespace-only", "comments-only"],
)
def test_a_whole_message_script_with_an_empty_module_body_fails_loudly(script: str, fenced_repo) -> None:
    """Well-formed-but-empty is the same rot as malformed: exit 0 with no verdict and
    no side effects is exactly what a test cannot notice."""
    code, result = _run(script, fenced_repo, whole_message=True)

    assert code == 1
    assert result.is_error
    assert result.subtype == "error_during_execution"
    assert "empty whole-message behavior script" in result.text


def test_whole_message_defaults_to_off_and_leaves_tagged_prompts_alone(fenced_repo) -> None:
    # Without whole_message=True, a <behavior-script>-tagged prompt still splits.
    code, result = _run(_tagged("verdict('pass', 'ran')"), fenced_repo)

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


# --------------------------------------------------------------------------- #
# Conversation transcripts: the claude_code-only ITranscriptWriter (blizzard#29)
# --------------------------------------------------------------------------- #


def test_transcripts_root_never_defaults_to_the_real_home_claude_projects(tmp_path: Path) -> None:
    """No ``BZ_TRANSCRIPTS_ROOT`` override must fall back to a path *under the
    fence*, never the runner-side default (``~/.claude/projects``) — that is the
    developer's real Claude Code session store."""
    from blizzard_mock.harness.facades._transcript import transcripts_root

    root = transcripts_root({}, fence_dir=tmp_path)
    assert root == tmp_path / ".blizzard-mock-harness" / "transcripts"
    assert str(Path.home() / ".claude" / "projects") not in str(root)


def test_transcripts_root_respects_the_override(tmp_path: Path) -> None:
    from blizzard_mock.harness.facades._transcript import TRANSCRIPTS_ROOT_ENV_VAR, transcripts_root

    override = tmp_path / "elsewhere"
    root = transcripts_root({TRANSCRIPTS_ROOT_ENV_VAR: str(override)}, fence_dir=tmp_path)
    assert root == override


def _real_home_claude_projects_mtime() -> float | None:
    path = Path.home() / ".claude" / "projects"
    return path.stat().st_mtime if path.exists() else None


def test_claude_facade_mints_a_transcript_with_matched_tool_turns(fenced_repo, tmp_path: Path) -> None:
    """A real spawn + apply_diff + commit + verdict, driven through the actual
    ``mock-claude-code`` binary, mints Claude-shaped JSONL: one spawn ``user``
    record, a matched ``tool_use``/``tool_result`` pair per ``apply_diff``/
    ``commit`` call, and a final ``assistant`` text record — with nothing landing
    in the developer's real ``~/.claude/projects``."""
    cwd, env = fenced_repo
    transcripts_dir = tmp_path / "transcripts"
    env = {**env, "BZ_TRANSCRIPTS_ROOT": str(transcripts_dir)}
    before = _real_home_claude_projects_mtime()

    diff = (
        "diff --git a/new.txt b/new.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/new.txt\n"
        "@@ -0,0 +1 @@\n"
        "+hello from the mock\n"
    )
    script = f"apply_diff({diff!r})\ncommit('feat: add new.txt')\nverdict('approve', 'looks good')"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "blizzard_mock.harness.facades.claude_code",
            "-p",
            "--output-format",
            "json",
            "--session-id",
            "sess-transcript-1",
            script,
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr

    assert _real_home_claude_projects_mtime() == before, "must never write to the real ~/.claude/projects"

    matches = list(transcripts_dir.glob("*/sess-transcript-1.jsonl"))
    assert len(matches) == 1
    records = [json.loads(line) for line in matches[0].read_text().splitlines() if line.strip()]

    # One spawn user turn — never the raw exec'd Python.
    user_records = [r for r in records if r["type"] == "user" and isinstance(r["message"]["content"], str)]
    assert len(user_records) == 1
    assert "apply_diff(" not in user_records[0]["message"]["content"]
    assert user_records[0]["sessionId"] == "sess-transcript-1"

    # A matched tool_use/tool_result pair per apply_diff/commit call.
    tool_use_records = [
        r
        for r in records
        if r["type"] == "assistant" and any(b.get("type") == "tool_use" for b in r["message"]["content"])
    ]
    tool_result_records = [
        r
        for r in records
        if r["type"] == "user"
        and isinstance(r["message"]["content"], list)
        and any(b.get("type") == "tool_result" for b in r["message"]["content"])
    ]
    assert len(tool_use_records) == 2  # apply_diff, commit
    assert len(tool_result_records) == 2
    tool_use_ids = {r["message"]["content"][0]["id"] for r in tool_use_records}
    tool_result_ids = {r["message"]["content"][0]["tool_use_id"] for r in tool_result_records}
    assert tool_use_ids == tool_result_ids  # every tool_use has its matching tool_result

    # The final assistant text record carries the rendered verdict.
    text_records = [
        r for r in records if r["type"] == "assistant" and any(b.get("type") == "text" for b in r["message"]["content"])
    ]
    assert len(text_records) == 1
    assert "<Choice>approve</Choice>" in text_records[0]["message"]["content"][0]["text"]
    assert "looks good" in text_records[0]["message"]["content"][0]["text"]

    # Every assistant-type record — the two tool-call turns and the final text
    # turn — carries `model` + `usage` (blizzard epic #57): what the runner
    # adapter's transcript-summation fallback sums (``sum_transcript_usage``).
    assistant_records = tool_use_records + text_records
    assert len(assistant_records) == 3
    for record in assistant_records:
        assert record["message"]["model"]
        usage = record["message"]["usage"]
        assert usage["input_tokens"] > 0
        assert usage["output_tokens"] > 0
        assert usage["cache_read_input_tokens"] > 0
        assert usage["cache_creation_input_tokens"] > 0


def test_claude_facade_resume_mints_another_user_and_assistant_turn(fenced_repo, tmp_path: Path) -> None:
    cwd, env = fenced_repo
    transcripts_dir = tmp_path / "transcripts"
    env = {**env, "BZ_TRANSCRIPTS_ROOT": str(transcripts_dir)}
    sid = "sess-transcript-resume"

    subprocess.run(
        [
            sys.executable,
            "-m",
            "blizzard_mock.harness.facades.claude_code",
            "-p",
            "--session-id",
            sid,
            "ask('proceed?', ['yes', 'no'])",
        ],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "blizzard_mock.harness.facades.claude_code",
            "-p",
            "--resume",
            sid,
            "verdict('resumed')",
        ],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    matches = list(transcripts_dir.glob(f"*/{sid}.jsonl"))
    assert len(matches) == 1
    records = [json.loads(line) for line in matches[0].read_text().splitlines() if line.strip()]
    user_text_records = [r for r in records if r["type"] == "user" and isinstance(r["message"]["content"], str)]
    assistant_text_records = [
        r for r in records if r["type"] == "assistant" and any(b.get("type") == "text" for b in r["message"]["content"])
    ]
    assert len(user_text_records) == 2  # spawn + resume
    assert len(assistant_text_records) == 2  # the ask + the resumed verdict


def test_claude_facade_without_session_id_writes_no_transcript(fenced_repo, tmp_path: Path) -> None:
    """No ``--session-id``/``--resume`` means no session id is known up front, so
    the facade skips transcript writing rather than guessing at a file name."""
    cwd, env = fenced_repo
    transcripts_dir = tmp_path / "transcripts"
    env = {**env, "BZ_TRANSCRIPTS_ROOT": str(transcripts_dir)}

    proc = subprocess.run(
        [sys.executable, "-m", "blizzard_mock.harness.facades.claude_code", "-p", "verdict('x')"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert not transcripts_dir.exists()


def test_codex_facade_writes_no_transcript(fenced_repo, tmp_path: Path) -> None:
    """Only claude_code constructs a transcript writer; codex/opencode are a no-op."""
    cwd, env = fenced_repo
    transcripts_dir = tmp_path / "transcripts"
    env = {**env, "BZ_TRANSCRIPTS_ROOT": str(transcripts_dir)}

    proc = subprocess.run(
        [sys.executable, "-m", "blizzard_mock.harness.facades.codex", "exec", "verdict('x')"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert not transcripts_dir.exists()


# --------------------------------------------------------------------------- #
# Deterministic usage/cost synthesis (blizzard epic #57, phase 1 of #58)
# --------------------------------------------------------------------------- #


def test_synthesize_usage_tokens_scales_with_text_length() -> None:
    from blizzard_mock.harness.facades._usage import synthesize_usage_tokens

    short = synthesize_usage_tokens("hi")
    long = synthesize_usage_tokens("x" * 1000)
    assert long["input_tokens"] > short["input_tokens"]
    assert long["output_tokens"] > short["output_tokens"]
    # The cache footprint is fixed, not scaled by text length.
    assert long["cache_read_input_tokens"] == short["cache_read_input_tokens"]
    assert long["cache_creation_input_tokens"] == short["cache_creation_input_tokens"]


def test_synthesize_usage_tokens_is_deterministic() -> None:
    from blizzard_mock.harness.facades._usage import synthesize_usage_tokens

    assert synthesize_usage_tokens("same text") == synthesize_usage_tokens("same text")


def test_synthesize_usage_tokens_respects_lower_bases_for_tool_calls() -> None:
    from blizzard_mock.harness.facades._usage import synthesize_usage_tokens

    default_bases = synthesize_usage_tokens("x")
    lower_bases = synthesize_usage_tokens("x", base_input=50, base_output=10)
    assert lower_bases["input_tokens"] < default_bases["input_tokens"]
    assert lower_bases["output_tokens"] < default_bases["output_tokens"]


def test_synthesize_cost_usd_is_positive_and_deterministic() -> None:
    from blizzard_mock.harness.facades._usage import synthesize_cost_usd, synthesize_usage_tokens

    usage = synthesize_usage_tokens("a realistic assistant reply")
    cost = synthesize_cost_usd(usage)
    assert cost > 0
    assert cost == synthesize_cost_usd(usage)


def test_synthesize_cost_usd_grows_with_token_counts() -> None:
    from blizzard_mock.harness.facades._usage import synthesize_cost_usd, synthesize_usage_tokens

    small = synthesize_cost_usd(synthesize_usage_tokens("hi"))
    large = synthesize_cost_usd(synthesize_usage_tokens("x" * 5000))
    assert large > small


# --- recorded model/effort flags (issue #144) --------------------------------


def _run_claude(cwd: Path, env: dict, session: str, script: str, *, resume: bool = False, **flags) -> None:
    """One `mock-claude-code` turn, with whichever of `--model`/`--effort` was given."""
    session_flag = ["--resume", session] if resume else ["--session-id", session]
    extra = [arg for name, value in flags.items() if value for arg in (f"--{name}", value)]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "blizzard_mock.harness.facades.claude_code",
            "-p",
            "--output-format",
            "json",
            *session_flag,
            *extra,
            script,
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def _session_state(cwd: Path, session: str) -> dict:
    path = engine.fence_base_dir(cwd) / ".blizzard-mock-harness" / "sessions" / f"{session}.json"
    return json.loads(path.read_text())


def test_claude_facade_records_the_model_and_effort_flags_each_turn_received(fenced_repo) -> None:
    """The observable behind blizzard's mint-only model contract: a mint carries the
    resolved model, and a resume carries none — the harness restores the session's own.

    Recorded, never acted on: the mock is model-agnostic. And it is a check of the
    **flag**, not of the effective model — the facade only ever sees argv.
    """
    cwd, env = fenced_repo
    _run_claude(cwd, env, "sess-1", "verdict('pass')", model="sonnet", effort="high")
    _run_claude(cwd, env, "sess-1", "verdict('pass')", resume=True, effort="high")

    state = _session_state(cwd, "sess-1")

    assert state["invocations"] == [
        {"kind": "spawn", "model": "sonnet", "effort": "high"},
        {"kind": "resume", "model": None, "effort": "high"},
    ]


def test_a_turn_launched_with_neither_flag_records_both_as_absent(fenced_repo) -> None:
    cwd, env = fenced_repo
    _run_claude(cwd, env, "sess-2", "verdict('pass')")

    assert _session_state(cwd, "sess-2")["invocations"] == [{"kind": "spawn", "model": None, "effort": None}]
