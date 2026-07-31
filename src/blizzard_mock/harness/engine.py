"""The shared mock-harness exec engine — *the prompt is the program*.

A real coding harness turns a prompt string into behavior via an LLM; the mock
turns it into behavior via :func:`exec` — the prompt it receives *is* Python
code, run in the acquired worktree with the spawn environment
(``implementation/mocking.md``). All three per-harness facades
(:mod:`blizzard_mock.harness.facades`) call into this one engine and differ only
in their CLI/wire surface, so a new facade never touches the engine.

Responsibilities owned here:

- **The fence.** Arbitrary code execution is the feature, so it is fenced: the
  engine refuses to run unless test scaffolding marks the environment (a
  :data:`FENCE_ENV_VAR` env var *and* a :data:`FENCE_MARKER_FILENAME` marker file
  in the worktree tree). It can never pass as a real harness binding.
- **The exec.** Treat the prompt's behavior script (or, on resume, the resume
  message's) as Python source and :func:`exec` it in the worktree with the
  :mod:`blizzard_mock.harness.helpers` surface bound as globals. A caller that
  controls the whole message declares **whole-message mode** (``run_prompt(...,
  whole_message=True)``) and the entire body execs with no sentinel scanning at
  all — the preferred contract for a driver that composes the message itself.
  Otherwise, which part of the prompt *is* the program is said explicitly by a
  ``<behavior-script>`` tag (:func:`split_behavior_script`); an untagged prompt
  falls back to the positional :func:`split_worker_preamble` and execs everything
  after the preamble.
- **Session state.** Persist a per-session file so a resumed script can read what
  it asked and act on the answer it was resumed with.

The engine owns *what* is emitted (a :class:`RunResult`); a facade's
:class:`IHarnessWire` owns *how* it is rendered on the wire.
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
#: The runner-injected, comma-separated workdirs of the environments the chunk holds.
#: The runner spawns a worker at the *workspace root* and names its env(s) in the
#: preamble prompt instead (blizzard issue #17), so cwd is no longer the worktree to
#: work in. A real agent reads that prose and goes there; the mock reads this.
ENV_WORKDIRS_ENV_VAR = "BLIZZARD_ENV_WORKDIRS"

#: Header of the machine-local facts table the runner prepends to every spawn prompt
#: (``design/harness-adapters.md``: the delivered prompt is "the hub's node envelope
#: plus the runner's machine-local preamble"). It is prose, not program — see
#: :func:`split_worker_preamble`.
PREAMBLE_TABLE_HEADER = "| Field | Value |"

#: Delimiters that say outright which part of a prompt is the program — the same
#: tagged-text idiom the wire already uses on the *output* side (:data:`CHOICE_OPEN`,
#: ``<Ask>``), turned around onto the input. A prompt carrying them **on lines of their
#: own** is exec'd from its tagged blocks alone and everything outside them is prose the
#: engine never runs; a prompt without such a line — including one that only mentions a
#: tag inline, as operator preamble prose may — keeps the positional
#: :func:`split_worker_preamble` behavior. See :func:`split_behavior_script`.
BEHAVIOR_SCRIPT_OPEN = "<behavior-script>"
BEHAVIOR_SCRIPT_CLOSE = "</behavior-script>"

#: Structured markers the facade wire embeds so a dumb adapter can parse the two
#: reply shapes out of the harness-native output (mirrors Claude Code's tagged
#: text; see ``design/harness-adapters.md``'s ``<Choice>{name}</Choice>``).
CHOICE_OPEN = "<Choice>"
CHOICE_CLOSE = "</Choice>"

#: The transcript's user-turn text when no preamble prose is available to show. A
#: transcript reader must never see the raw exec'd Python as "what the user said"
#: (it would misrepresent the script as prose an agent read) — these stand in for
#: both the received prompt and a resume message, which also arrives as code.
_TRANSCRIPT_SPAWN_TEXT = "(mock harness spawn — the behavior script it executed is not shown here)"
_TRANSCRIPT_RESUME_TEXT = "(mock harness resume — the behavior script it executed is not shown here)"


class FenceError(RuntimeError):
    """The engine refused to run because the environment is not test-marked."""


class HarnessCrash(RuntimeError):
    """A behavior script called ``crash()`` — the worker died without a verdict."""


class BehaviorScriptTagError(ValueError):
    """A prompt's ``<behavior-script>`` tags are unbalanced or nested.

    Raised out of :func:`split_behavior_script` and re-raised inside the run so the
    turn ends as an ``error_during_execution``. Silent degradation is the failure
    mode designed out here: a typo'd tag must never fall back to the legacy
    exec-everything path or quietly succeed as a no-op turn, because a script that
    stops running while its tests keep passing rots them invisibly.
    """


class EmptyBehaviorScriptError(ValueError):
    """A whole-message behavior script parses to an empty module body.

    Raised inside :func:`run_prompt`'s whole-message path so the turn ends as an
    ``error_during_execution`` — the same silent-no-op rot
    :class:`BehaviorScriptTagError` closes for an empty tagged block, closed here
    for a message that is blank or comments-only (which parses to no statements at
    all, so an unguarded exec would exit 0 with no verdict).
    """


class _AskExit(Exception):
    """Internal control-flow signal: a script called ``ask()`` and must exit now."""

    def __init__(self, ask: Ask) -> None:
        super().__init__(ask.question)
        self.ask = ask


@dataclass
class RunResult:
    """What one turn produced, before a facade renders it to the wire.

    ``subtype`` is ``"success"`` (a verdict or plain completion), ``"ask"`` (the
    worker parked on a question), or ``"error_during_execution"`` (the script
    raised / crashed). ``exit_code`` is what the facade returns to the OS.
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
    """A facade's wire surface — the engine's only outward dependency (owned inward).

    Each per-harness facade implements this to render a :class:`RunResult` in its
    coding harness's native output format. The engine constructs the result and
    writes ``render(result)`` to the output stream; it never formats anything.
    """

    def render(self, result: RunResult) -> str:
        """Return the exact bytes-as-text this harness would print for ``result``."""
        ...


class ITranscriptWriter(Protocol):
    """A facade's optional conversation-transcript sink — mirrors :class:`IHarnessWire`.

    Only the claude_code facade constructs one (only Claude Code has a transcript
    reader today — ``blizzard/runner/transcripts/parser.py``); every other facade
    passes ``None`` and the engine no-ops. The engine calls into it at two defined
    points — the spawn/resume user turn and the final result — and never renders
    anything itself; the helper surface drives ``record_tool_call`` /
    ``record_tool_result`` off the run context in between (the package README's
    "Conversation transcripts" owns which helpers those are).
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

    Real Claude Code runs the hook commands declared in its ``--settings``
    document; this is the seam that lets the mock do the same, so a runner-owned
    hook travels its real path instead of being synthesized. Only the claude_code
    facade constructs one (it is the only facade the runner passes ``--settings``
    to); every other facade and every direct engine caller passes ``None`` and the
    engine no-ops. The engine calls into it at two defined lifecycle points and
    never constructs a hook payload or spawns anything itself.
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

    Set by :func:`run_prompt` for the duration of the ``exec`` and read by the
    helper functions through :func:`current_context`. Helpers mutate ``result``
    (verdict/ask) and ``session`` (asks/answers) rather than touching stdout.
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


# --------------------------------------------------------------------------- #
# The fence
# --------------------------------------------------------------------------- #


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

    Both factors are required so neither a stray env var nor a stray file alone
    can unfence the mock: ``FENCE_ENV_VAR`` must equal ``FENCE_ENV_VALUE`` *and*
    a ``FENCE_MARKER_FILENAME`` marker must exist in the worktree tree. Every
    facade guards through this one function before executing anything.
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


# --------------------------------------------------------------------------- #
# Worktree resolution, state location & ask dispatch
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TaggedPrompt:
    """A tagged prompt's two halves: the program, and the prose around it.

    ``script`` is every ``<behavior-script>`` block's contents, dedented and
    concatenated in order; ``prose`` is the rest of the prompt with the blocks (and
    their delimiter lines) elided — real prose, which is both what a transcript shows
    as "what the user said" and what a resumed script reads back from
    :func:`~blizzard_mock.harness.helpers.answer`, where an untagged prompt has only
    its preamble, a synthetic placeholder, or the raw message.
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

    The tag says *this part is the program*: only the tagged blocks reach the
    interpreter, and everything around them is prose the engine treats as data. That
    decouples the mock from the exact shape of the runner's spawn preamble — a
    tagged prompt does not consult :func:`split_worker_preamble` at all — and lets a
    mock-targeted prompt read like a real node prompt instead of being pure code.

    **A delimiter counts only on a line of its own** (leading/trailing whitespace
    aside). The prompt a worker receives is not all script-author-owned: the runner
    prepends operator prose it composes itself
    (``blizzard:runner/harness/preamble.py``), and that prose may well *mention* the
    tag — a house rule about it, or a quoted work item like the one this feature came
    from. Matched as a bare substring, such a mention would either hijack a legacy
    run (an illustrative block becomes the whole program and the node's real script
    silently never runs) or, unpaired, hard-fail every untagged spawn in the
    deployment. Line-anchoring makes an inline mention — ``a `<behavior-script>`
    tag`` — inert, so a prompt with no *block* keeps behaving exactly as it did
    before the tag existed.

    Blocks are :func:`~textwrap.dedent`\\ ed, so a tag nested in a markdown list or a
    blockquote yields runnable source rather than an ``IndentationError``.

    Returns ``None`` when the prompt carries no delimiter line at all, which is the
    caller's signal to fall back to the legacy positional split. Raises
    :class:`BehaviorScriptTagError` when the delimiter lines are unbalanced or
    nested, or when they enclose nothing executable — never a silent fall-through to
    legacy exec, and never a quiet no-op turn, because a tag that succeeds with no
    verdict and no side effects makes tests rot invisibly.

    The tag is orthogonal to the fence: it marks *which part* of a prompt is the
    program, while :func:`assert_fenced` decides whether anything may run at all. A
    tag in prompt content is never on its own sufficient to trigger execution.
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

    # Blocks join on a newline so two of them never fuse into one line; the prose
    # keeps its own shape, minus the gaps the elided blocks left behind.
    script = "\n".join(blocks)
    if not script.strip():
        raise BehaviorScriptTagError(
            f"empty behavior-script block: the {BEHAVIOR_SCRIPT_OPEN} block(s) enclose nothing to execute"
        )
    return TaggedPrompt(script=script, prose=re.sub(r"\n{3,}", "\n\n", "\n".join(prose)).strip())


def _parses_to_empty_module(source: str) -> bool:
    """True iff ``source`` compiles to a module with no statements at all.

    Blank or whitespace-only source is the obvious case; comments-only source also
    parses to an empty body, which is exactly the silent no-verdict rot
    :class:`EmptyBehaviorScriptError` exists to catch for whole-message mode. A
    genuine syntax error is *not* reported as empty — that already fails loudly of
    its own accord once :func:`exec` reaches it.
    """
    try:
        return len(ast.parse(source).body) == 0
    except SyntaxError:
        return False


def split_worker_preamble(prompt: str) -> tuple[str, str]:
    """Split a spawn prompt into ``(preamble, behavior_script)``.

    The **legacy, untagged** discrimination: positional, and consulted only for a prompt
    with no ``<behavior-script>`` tag (:func:`split_behavior_script`, which supersedes it).

    The prompt is the program — but only the *envelope* half of it. The runner prepends a
    machine-local preamble to every spawn: the operator's workspace prose, then a facts
    table naming the held environments (blizzard issue #17, ``design/harness-adapters.md``).
    That preamble is addressed to an agent's judgement, not to ``exec`` — feeding it to the
    interpreter is a ``SyntaxError`` on the table's first pipe, which would fail the turn
    before the script it precedes ever runs.

    The preamble always ends with its facts table, and the table's rows are contiguous, so
    the script is whatever follows the last row of the table opened by
    :data:`PREAMBLE_TABLE_HEADER`. Returns ``("", prompt)`` when no preamble is present —
    a resume message and every direct engine caller pass a bare script.
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

    The runner spawns a worker at the **workspace root** and names the environments it
    holds in the preamble prompt (blizzard issue #17) — so the process cwd is the
    workspace, not the worktree the node is supposed to touch. A real agent reads the
    preamble and works in the named env; this is the mock's equivalent of obeying it,
    reading the same fact off :data:`ENV_WORKDIRS_ENV_VAR` (which the adapter injects
    alongside the prompt). The first workdir is the one chosen, matching the adapter's
    own single-env fallback.

    Falls back to ``cwd`` when the variable is absent or empty — direct engine callers
    (the mock's own tests, ``blizzard-mock:e2e``, manual runs) drive it from inside the
    worktree and never set it. A named workdir that does not exist is ignored rather
    than trusted, so a stale value degrades to today's behavior instead of erroring.
    """
    raw = env.get(ENV_WORKDIRS_ENV_VAR, "")
    for candidate in (part.strip() for part in raw.split(",")):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return cwd


def fence_base_dir(cwd: Path) -> Path:
    """The directory session/transcript state is rooted under.

    The fence marker's parent, else ``cwd`` when no marker is found in the tree —
    the same fallback :func:`_state_root` has always used, pulled out so the
    transcript writer's own fenced fallback (``facades/_transcript.py``) shares it
    rather than re-deriving the rule.
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

    The terse helper surface is bound by name so a script writes ``commit("…")``
    with no imports; ``__builtins__`` is left intact — the mock is a full Python
    interpreter on purpose, so a script can reach for raw Python for weird cases.
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


# --------------------------------------------------------------------------- #
# The exec engine
# --------------------------------------------------------------------------- #


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
    whole_message: bool = False,
) -> int:
    """Execute a behavior-script ``prompt`` and return the process exit code.

    On **spawn** (``is_resume=False``) ``session_id`` is the harness-honored
    pre-assignment (Claude Code) or ``None`` to self-assign a uuid (Codex,
    OpenCode). On **resume** ``session_id`` is required and ``prompt`` is the
    resume message — which *also arrives as code* and is what gets executed,
    with the persisted session state (prior asks) available to it.

    Spawn and resume read the prompt the same way: under ``whole_message=True``
    the entire body execs with no sentinel scanning at all (no
    ``<behavior-script>`` scan, no preamble split) — the mode for a caller that
    composes the whole message itself (test tiers, scripted journeys, fixture
    scenarios), where a mention of the tag anywhere in the script, including
    inside a string literal, must stay inert. Otherwise, a ``<behavior-script>``-
    tagged prompt execs its blocks alone (:func:`split_behavior_script`), an
    untagged one execs everything past the preamble
    (:func:`split_worker_preamble`), and a malformed tag fails the turn as an
    ``error_during_execution``. A whole-message script that parses to an empty
    module body (blank, or comments-only) fails the same way instead of exiting 0
    with no verdict (:class:`EmptyBehaviorScriptError`).

    ``model``/``effort`` (issue #144) are the flags this invocation was launched with —
    recorded onto the session state as an :class:`~blizzard_mock.harness.session.Invocation`
    and otherwise ignored (the mock is model- and effort-agnostic). ``None`` means the flag
    was absent from argv, which on a resume is precisely what a fleet-tier scenario asserts.

    Refuses to run unless :func:`assert_fenced` passes. Renders the resulting
    :class:`RunResult` through ``wire`` to ``out`` exactly once (a ``hang`` never
    reaches that line; a ``crash`` renders an error result).

    ``transcript``, when supplied (only the claude_code facade constructs one — a
    genuine Claude-shaped conversation is only useful where a reader exists), mints
    the user turn for this run and the assistant turn for its result; it is also
    bound onto the run context, which the helper surface's tool calls write through
    mid-script (the package README's "Conversation transcripts" owns that list).
    ``None`` (every other facade) is a total no-op.

    ``hooks``, when supplied (only the claude_code facade constructs one, from the
    ``--settings`` document the runner hands it), executes the hook commands that
    document declares. ``on_session_end`` fires once at the tail of this function,
    after the wire render and before the return, so the hook completes while the
    process is still alive. ``None`` (every other facade, and every direct caller)
    is a total no-op.
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

    # Which part of the prompt is the program? Under whole-message mode the caller
    # has already answered that question — the entire body is the program, no
    # scanning at all, so a `<behavior-script>` mention anywhere inside it (even in
    # a string literal) is inert data rather than a delimiter. Otherwise, a
    # `<behavior-script>` block says so outright; without one, the runner's preamble
    # still rides ahead of the envelope on a spawn — prose for an agent to read, not
    # code to run — so the positional split decides. A malformed tag, or a
    # whole-message script with an empty module body, is neither: it is carried
    # into the run below and raised there, so the turn fails loudly instead of
    # degrading to a quiet path.
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
    # What the *human* said this turn: a tagged prompt's prose, else the whole raw
    # message (an untagged resume, and a whole-message resume alike, are code end to
    # end, and `answer()` has always returned it verbatim). This is what a resumed
    # script reads back as its answer.
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
    # What this turn was actually launched with (issue #144) — the observable behind the
    # mint-only model contract's fleet-tier proof.
    state.invocations.append(Invocation(kind="resume" if is_resume else "spawn", model=model, effort=effort))

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
        # Never the raw script (it would misrepresent code as "what the user said"):
        # the tagged prompt's own prose, else the real preamble prose, else a short
        # synthetic line.
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
        # The tail, after the render: the hook subprocess runs to completion while
        # this process is still alive, so a "declared done" signal is durable before
        # the runner can observe the exit it is compared against. The exits that do
        # *not* reach here carry meaning of their own — see the package README's
        # "Hook execution".
        hooks.on_session_end(result)
    return result.exit_code
