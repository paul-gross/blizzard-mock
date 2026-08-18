"""The shared mock-harness exec engine — *the prompt is the program*.

A real coding harness turns a prompt into behavior via an LLM; the mock turns
it via :func:`exec` — the prompt it receives *is* Python code. Execution is
fenced (:func:`assert_fenced`).
"""

from __future__ import annotations

import ast
import contextlib
import contextvars
import os
import re
import textwrap
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from blizzard_mock.harness.internal.logging import get_logger
from blizzard_mock.harness.session import Ask, Invocation, SessionState, SessionStore

_log = get_logger(__name__)

#: Environment variable the test scaffolding sets to unfence the engine.
FENCE_ENV_VAR = "BLIZZARD_MOCK_HARNESS_FENCE"
#: The exact value :data:`FENCE_ENV_VAR` must carry — presence alone is not enough.
FENCE_ENV_VALUE = "1"
#: Marker file the fence looks for in the worktree tree (cwd or an ancestor).
FENCE_MARKER_FILENAME = ".blizzard-mock-harness-fence"
#: Optional override for where session state files live; defaults beside the marker.
STATE_DIR_ENV_VAR = "BLIZZARD_MOCK_HARNESS_STATE_DIR"
#: Optional command the ``ask`` helper shells out to (the real runner ask path),
#: e.g. ``"blizzard runner ask"``. Absent in unit tests — the ask is emit-only.
ASK_CMD_ENV_VAR = "BLIZZARD_RUNNER_ASK_CMD"
#: The runner-injected, comma-separated workdirs of the environments the chunk
#: holds (issue #17); see :func:`acquired_worktree`.
ENV_WORKDIRS_ENV_VAR = "BLIZZARD_ENV_WORKDIRS"

#: Header of the machine-local facts table the runner prepends to every spawn prompt.
#: It is prose, not program — see :func:`split_worker_preamble`.
PREAMBLE_TABLE_HEADER = "| Field | Value |"

#: Delimiters that say outright which part of a prompt is the program. They count only
#: **on lines of their own** — see :func:`split_behavior_script`.
BEHAVIOR_SCRIPT_OPEN = "<behavior-script>"
BEHAVIOR_SCRIPT_CLOSE = "</behavior-script>"

#: Structured markers the facade wire embeds so a dumb adapter can parse the two
#: reply shapes out of the harness-native output (``design/harness-adapters.md``).
CHOICE_OPEN = "<Choice>"
CHOICE_CLOSE = "</Choice>"

#: Placeholder user-turn text when no preamble prose is available: a transcript
#: reader must never see the raw exec'd Python as "what the user said".
_TRANSCRIPT_SPAWN_TEXT = "(mock harness spawn — the behavior script it executed is not shown here)"
_TRANSCRIPT_RESUME_TEXT = "(mock harness resume — the behavior script it executed is not shown here)"


class FenceError(RuntimeError):
    """The engine refused to run because the environment is not test-marked."""


class HarnessCrash(RuntimeError):
    """A behavior script called ``crash()`` — the worker died without a verdict."""


class BehaviorScriptTagError(ValueError):
    """A prompt's ``<behavior-script>`` tags are unbalanced or nested.

    Ends the turn as ``error_during_execution`` rather than a silent no-op.
    """


class EmptyBehaviorScriptError(ValueError):
    """A whole-message behavior script parses to an empty module body.

    Ends the turn as ``error_during_execution`` rather than exiting 0.
    """


class _AskExit(Exception):
    """Internal control-flow signal: a script called ``ask()`` and must exit now."""

    def __init__(self, ask: Ask) -> None:
        super().__init__(ask.question)
        self.ask = ask


@dataclass
class RunResult:
    """What one turn produced, before a facade renders it to the wire.

    ``subtype`` is ``"success"``, ``"ask"``, or ``"error_during_execution"``.
    """

    session_id: str
    text: str = ""
    is_error: bool = False
    subtype: str = "success"
    num_turns: int = 1
    duration_ms: int = 0
    exit_code: int = 0
    ask: Ask | None = None


class IHarnessWire(Protocol):
    """A facade's wire surface — the engine's only outward dependency.

    Writes ``render(result)`` to the output stream; never formats anything.
    """

    def render(self, result: RunResult) -> str:
        """Return the exact bytes-as-text this harness would print for ``result``."""
        ...


class ITranscriptWriter(Protocol):
    """A facade's optional conversation-transcript sink — mirrors :class:`IHarnessWire`.

    ``None`` is a total no-op; called at defined points only.
    """

    def record_user(self, text: str) -> None:
        """Append a user turn (the spawn envelope text or a resume message)."""
        ...

    def record_result(self, result: RunResult) -> None:
        """Append the assistant turn carrying the run's final rendered text."""
        ...

    def record_tool_call(self, name: str, tool_input: Mapping[str, object]) -> str:
        """Append a ``tool_use`` turn; return its id for the matching result."""
        ...

    def record_tool_result(self, tool_use_id: str, output: str) -> None:
        """Append the ``tool_result`` turn matching a prior ``record_tool_call``."""
        ...


class IHookRunner(Protocol):
    """A facade's optional hook-execution sink — mirrors :class:`ITranscriptWriter`.

    ``None`` is a total no-op; called at two defined lifecycle points only.
    """

    def on_tool_use(self, name: str, tool_input: Mapping[str, object], tool_output: str) -> None:
        """Fire the ``PostToolUse`` hooks for one tool call the script just made."""
        ...

    def on_session_end(self, result: RunResult) -> None:
        """Fire the ``SessionEnd`` hooks once, as this turn's process exits."""
        ...


@dataclass
class RunContext:
    """Ambient state for the currently-executing behavior script.

    Set by :func:`run_prompt`; read via :func:`current_context`.
    """

    session: SessionState
    wire: IHarnessWire
    cwd: Path
    env: Mapping[str, str]
    store: SessionStore
    is_resume: bool
    resume_message: str | None = None
    ask_cmd: Sequence[str] | None = None
    result: RunResult | None = None
    transcript: ITranscriptWriter | None = None
    hooks: IHookRunner | None = None


_CURRENT: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar("blizzard_mock_run_context", default=None)


def current_context() -> RunContext:
    """Return the context of the running behavior script, or raise if none is active.

    The helper library calls this; it fails loudly when a helper is used outside
    an engine-driven ``exec`` (e.g. imported and called directly).
    """
    ctx = _CURRENT.get()
    if ctx is None:
        raise RuntimeError("no active mock-harness run context — call a helper only from a behavior script")
    return ctx


# -- The fence -------------------------------------------------------------- #


def find_fence_marker(cwd: Path) -> Path | None:
    """Return the marker file at ``cwd`` or the nearest ancestor, else ``None``."""
    for directory in (cwd, *cwd.parents):
        candidate = directory / FENCE_MARKER_FILENAME
        if candidate.is_file():
            return candidate
    return None


def is_fenced(cwd: Path, env: Mapping[str, str]) -> bool:
    """True iff both fence factors are present: the env var *and* the marker file."""
    return env.get(FENCE_ENV_VAR) == FENCE_ENV_VALUE and find_fence_marker(cwd) is not None


def assert_fenced(cwd: Path | None = None, env: Mapping[str, str] | None = None) -> None:
    """Raise :class:`FenceError` unless the environment is test-marked.

    Both factors are required: ``FENCE_ENV_VAR`` must equal ``FENCE_ENV_VALUE``
    *and* a ``FENCE_MARKER_FILENAME`` marker must exist in the worktree tree.
    """
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    env = env if env is not None else os.environ
    if env.get(FENCE_ENV_VAR) != FENCE_ENV_VALUE:
        _log.warning("fence refused: env var missing", var=FENCE_ENV_VAR, cwd=str(cwd))
        raise FenceError(
            f"mock harness refused to run: {FENCE_ENV_VAR}={FENCE_ENV_VALUE} not set. "
            "The mock executes arbitrary code and only runs under test scaffolding."
        )
    if find_fence_marker(cwd) is None:
        _log.warning("fence refused: marker file missing", marker=FENCE_MARKER_FILENAME, cwd=str(cwd))
        raise FenceError(
            f"mock harness refused to run: no {FENCE_MARKER_FILENAME} marker in {cwd} or its parents. "
            "The mock executes arbitrary code and only runs under test scaffolding."
        )


def write_fence_marker(cwd: Path) -> Path:
    """Test-scaffolding helper: drop the marker file at ``cwd`` and return its path."""
    marker = Path(cwd) / FENCE_MARKER_FILENAME
    marker.write_text("blizzard-mock harness fence marker — this tree is test scaffolding\n")
    return marker


def fenced_env(base: Mapping[str, str] | None = None, **extra: str) -> dict[str, str]:
    """Test-scaffolding helper: an env dict with the fence variable set."""
    env = dict(base if base is not None else os.environ)
    env[FENCE_ENV_VAR] = FENCE_ENV_VALUE
    env.update(extra)
    return env


# -- Worktree resolution, state location & ask dispatch ---------------------- #


@dataclass(frozen=True)
class TaggedPrompt:
    """A tagged prompt's two halves: the program, and the prose around it.

    ``script`` is the dedented, concatenated block contents; ``prose`` the rest.
    """

    script: str
    prose: str


def _tag_line(tag: str) -> re.Pattern[str]:
    """A delimiter matcher: that tag *alone* on its line, bar surrounding whitespace."""
    return re.compile(rf"^[ \t]*{re.escape(tag)}[ \t]*$")


_OPEN_LINE = _tag_line(BEHAVIOR_SCRIPT_OPEN)
_CLOSE_LINE = _tag_line(BEHAVIOR_SCRIPT_CLOSE)


def split_behavior_script(prompt: str) -> TaggedPrompt | None:
    """Split a ``<behavior-script>``-tagged prompt into its program and its prose.

    A delimiter counts only on a line of its own. Returns ``None`` when absent;
    raises :class:`BehaviorScriptTagError` on unbalanced or empty blocks.
    """
    blocks: list[str] = []
    prose: list[str] = []
    body: list[str] = []
    open_line: int | None = None

    for number, line in enumerate(prompt.splitlines(), start=1):
        if _OPEN_LINE.match(line):
            if open_line is not None:
                raise BehaviorScriptTagError(
                    f"malformed behavior-script tags: a nested {BEHAVIOR_SCRIPT_OPEN} on line {number} "
                    f"inside the block opened on line {open_line} — blocks do not nest"
                )
            open_line, body = number, []
        elif _CLOSE_LINE.match(line):
            if open_line is None:
                raise BehaviorScriptTagError(
                    f"unbalanced behavior-script tags: a {BEHAVIOR_SCRIPT_CLOSE} on line {number} "
                    f"with no opening {BEHAVIOR_SCRIPT_OPEN}"
                )
            blocks.append(textwrap.dedent("\n".join(body)))
            open_line = None
        elif open_line is None:
            prose.append(line)
        else:
            body.append(line)

    if open_line is not None:
        raise BehaviorScriptTagError(
            f"unbalanced behavior-script tags: the {BEHAVIOR_SCRIPT_OPEN} on line {open_line} "
            f"has no closing {BEHAVIOR_SCRIPT_CLOSE}"
        )
    if not blocks:
        # No delimiter *line* anywhere — whatever the prompt says about the tag in
        # passing, it carries no block, so it is a legacy prompt.
        return None

    # Blocks join on a newline so two of them never fuse into one line.
    script = "\n".join(blocks)
    if not script.strip():
        raise BehaviorScriptTagError(
            f"empty behavior-script block: the {BEHAVIOR_SCRIPT_OPEN} block(s) enclose nothing to execute"
        )
    return TaggedPrompt(script=script, prose=re.sub(r"\n{3,}", "\n\n", "\n".join(prose)).strip())


def _parses_to_empty_module(source: str) -> bool:
    """True iff ``source`` compiles to a module with no statements at all.

    Blank, whitespace-only, or comments-only source all qualify. A genuine
    syntax error is not reported as empty.
    """
    try:
        return len(ast.parse(source).body) == 0
    except SyntaxError:
        return False


def split_worker_preamble(prompt: str) -> tuple[str, str]:
    """Split a spawn prompt into ``(preamble, behavior_script)``.

    Legacy, untagged, positional split: the script follows the last row of the
    table opened by :data:`PREAMBLE_TABLE_HEADER` (issue #17).
    """
    lines = prompt.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == PREAMBLE_TABLE_HEADER)
    except StopIteration:
        return "", prompt
    end = start
    while end + 1 < len(lines) and lines[end + 1].lstrip().startswith("|"):
        end += 1
    return "\n".join(lines[: end + 1]), "\n".join(lines[end + 1 :]).lstrip("\n")


def acquired_worktree(env: Mapping[str, str], cwd: Path) -> Path:
    """The worktree a behavior-script runs in: the first held env's workdir, else ``cwd``.

    Read off :data:`ENV_WORKDIRS_ENV_VAR` (issue #17); a workdir that does not
    exist is ignored, falling back to ``cwd``.
    """
    raw = env.get(ENV_WORKDIRS_ENV_VAR, "")
    for candidate in (part.strip() for part in raw.split(",")):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return cwd


def fence_base_dir(cwd: Path) -> Path:
    """The directory session/transcript state is rooted under.

    The fence marker's parent, else ``cwd`` when no marker is found in the
    tree.
    """
    marker = find_fence_marker(cwd)
    return marker.parent if marker is not None else cwd


def _state_root(env: Mapping[str, str], cwd: Path) -> Path:
    """Where session files live: the override env var, else beside the fence marker."""
    override = env.get(STATE_DIR_ENV_VAR)
    if override:
        return Path(override)
    return fence_base_dir(cwd) / ".blizzard-mock-harness" / "sessions"


def _ask_cmd(env: Mapping[str, str]) -> Sequence[str] | None:
    """Parse the optional real-runner ask command from the environment."""
    raw = env.get(ASK_CMD_ENV_VAR)
    return raw.split() if raw else None


@contextlib.contextmanager
def _chdir(target: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(previous)


def _script_globals(ctx: RunContext) -> dict[str, object]:
    """Build the namespace a behavior script executes in.

    The helper surface is bound by name so a script writes ``commit("…")`` with
    no imports; ``__builtins__`` is left intact.
    """
    from blizzard_mock.harness import helpers

    ns: dict[str, object] = {
        "__name__": "__behavior_script__",
        "ask": helpers.ask,
        "apply_diff": helpers.apply_diff,
        "commit": helpers.commit,
        "tool_call": helpers.tool_call,
        "verdict": helpers.verdict,
        "hang": helpers.hang,
        "crash": helpers.crash,
        "state": helpers.state,
        "answer": helpers.answer,
    }
    return ns


# -- The exec engine ---------------------------------------------------------- #


def run_prompt(
    prompt: str,
    *,
    wire: IHarnessWire | None = None,
    session_id: str | None = None,
    is_resume: bool = False,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    out: IO[str] | None = None,
    transcript: ITranscriptWriter | None = None,
    hooks: IHookRunner | None = None,
    model: str | None = None,
    effort: str | None = None,
    compaction_window: str | None = None,
    whole_message: bool = False,
) -> int:
    """Execute a behavior-script ``prompt`` and return the process exit code.

    Refuses to run unless :func:`assert_fenced` passes. Renders the resulting
    :class:`RunResult` through ``wire`` to ``out`` exactly once.
    """
    import sys

    from blizzard_mock.harness.facades._text import PlainTextWire

    env = env if env is not None else os.environ
    # An explicit cwd wins; otherwise work in the env the runner handed us, which is not
    # the process cwd once the worker is spawned at the workspace root (issue #17).
    cwd = Path(cwd) if cwd is not None else acquired_worktree(env, Path.cwd())
    wire = wire if wire is not None else PlainTextWire()
    stream: IO[str] = out if out is not None else sys.stdout

    assert_fenced(cwd, env)

    # Which part of the prompt is the program? A malformed tag or an empty
    # whole-message body is raised below, never silently degraded.
    tag_error: BehaviorScriptTagError | None = None
    empty_error: EmptyBehaviorScriptError | None = None
    tagged: TaggedPrompt | None = None
    preamble, script = "", ""
    if whole_message:
        script = prompt
        if _parses_to_empty_module(script):
            empty_error = EmptyBehaviorScriptError(
                "empty whole-message behavior script: the message parses to an empty module body"
            )
    else:
        try:
            tagged = split_behavior_script(prompt)
        except BehaviorScriptTagError as exc:
            tag_error = exc

        if tagged is not None:
            script = tagged.script
        elif tag_error is None:
            preamble, script = split_worker_preamble(prompt)
    script_error: Exception | None = tag_error or empty_error
    # What the *human* said this turn — what a resumed script reads back as its
    # answer: a tagged prompt's prose, else the whole raw message.
    message = tagged.prose if tagged is not None else prompt

    store = SessionStore(_state_root(env, cwd))
    if is_resume:
        if not session_id:
            raise ValueError("resume requires a session_id")
        state = store.load_or_create(session_id)
        state.resumes.append(message)
    else:
        session_id = session_id or str(uuid.uuid4())
        state = store.load_or_create(session_id)
    state.turns += 1
    # What this turn was actually launched with (issue #144, blizzard#343).
    state.invocations.append(
        Invocation(
            kind="resume" if is_resume else "spawn",
            model=model,
            effort=effort,
            compaction_window=compaction_window,
        )
    )

    ctx = RunContext(
        session=state,
        wire=wire,
        cwd=cwd,
        env=env,
        store=store,
        is_resume=is_resume,
        resume_message=message if is_resume else None,
        ask_cmd=_ask_cmd(env),
        transcript=transcript,
        hooks=hooks,
    )

    if transcript is not None:
        # Never the raw script — the tagged prompt's own prose, else the real
        # preamble prose, else a short synthetic line.
        prose = tagged.prose if tagged is not None else preamble
        user_text = prose if prose else (_TRANSCRIPT_RESUME_TEXT if is_resume else _TRANSCRIPT_SPAWN_TEXT)
        transcript.record_user(user_text)
    _log.info(
        "mock harness run",
        session_id=session_id,
        resume=is_resume,
        cwd=str(cwd),
        whole_message=whole_message,
        tagged=tagged is not None,
        preamble_stripped=bool(preamble),
    )
    token = _CURRENT.set(ctx)
    started = time.monotonic()
    try:
        if script_error is not None:
            raise script_error  # a bad tag or an empty whole-message script fails the turn, never silently
        with _chdir(cwd):
            exec(compile(script, "<behavior-script>", "exec"), _script_globals(ctx))
        result = ctx.result or RunResult(session_id=session_id, subtype="success")
    except _AskExit as exc:
        result = ctx.result or RunResult(session_id=session_id, subtype="ask", ask=exc.ask, text=exc.ask.question)
    except HarnessCrash as exc:
        _log.warning("behavior script crashed", session_id=session_id, error=str(exc))
        result = RunResult(
            session_id=session_id, is_error=True, subtype="error_during_execution", text=str(exc), exit_code=1
        )
    except Exception as exc:  # a script blew up — the harness reports an error run
        _log.warning("behavior script raised", session_id=session_id, error=repr(exc))
        result = RunResult(
            session_id=session_id,
            is_error=True,
            subtype="error_during_execution",
            text=f"{type(exc).__name__}: {exc}",
            exit_code=1,
        )
    finally:
        _CURRENT.reset(token)
        store.save(state)

    result.session_id = session_id
    result.num_turns = state.turns
    result.duration_ms = int((time.monotonic() - started) * 1000)
    if transcript is not None:
        transcript.record_result(result)
    stream.write(wire.render(result))
    stream.flush()
    if hooks is not None:
        # After the render, so the hook subprocess runs while this process is
        # still alive (pinned by tests/test_pin_mock.py).
        hooks.on_session_end(result)
    return result.exit_code
