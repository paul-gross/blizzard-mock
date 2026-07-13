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
- **The exec.** Treat the prompt (or, on resume, the resume message) as Python
  source and :func:`exec` it in the worktree with the
  :mod:`blizzard_mock.harness.helpers` surface bound as globals.
- **Session state.** Persist a per-session file so a resumed script can read what
  it asked and act on the answer it was resumed with.

The engine owns *what* is emitted (a :class:`RunResult`); a facade's
:class:`IHarnessWire` owns *how* it is rendered on the wire.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from blizzard_mock.harness.internal.logging import get_logger
from blizzard_mock.harness.session import Ask, SessionState, SessionStore

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

#: Structured markers the facade wire embeds so a dumb adapter can parse the two
#: reply shapes out of the harness-native output (mirrors Claude Code's tagged
#: text; see ``design/harness-adapters.md``'s ``<Choice>{name}</Choice>``).
CHOICE_OPEN = "<Choice>"
CHOICE_CLOSE = "</Choice>"


class FenceError(RuntimeError):
    """The engine refused to run because the environment is not test-marked."""


class HarnessCrash(RuntimeError):
    """A behavior script called ``crash()`` — the worker died without a verdict."""


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
# State location & ask dispatch
# --------------------------------------------------------------------------- #


def _state_root(env: Mapping[str, str], cwd: Path) -> Path:
    """Where session files live: the override env var, else beside the fence marker."""
    override = env.get(STATE_DIR_ENV_VAR)
    if override:
        return Path(override)
    marker = find_fence_marker(cwd)
    base = marker.parent if marker is not None else cwd
    return base / ".blizzard-mock-harness" / "sessions"


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
) -> int:
    """Execute a behavior-script ``prompt`` and return the process exit code.

    On **spawn** (``is_resume=False``) ``session_id`` is the harness-honored
    pre-assignment (Claude Code) or ``None`` to self-assign a uuid (Codex,
    OpenCode). On **resume** ``session_id`` is required and ``prompt`` is the
    resume message — which *also arrives as code* and is what gets executed,
    with the persisted session state (prior asks) available to it.

    Refuses to run unless :func:`assert_fenced` passes. Renders the resulting
    :class:`RunResult` through ``wire`` to ``out`` exactly once (a ``hang`` never
    reaches that line; a ``crash`` renders an error result).
    """
    import sys

    from blizzard_mock.harness.facades._text import PlainTextWire

    cwd = Path(cwd) if cwd is not None else Path.cwd()
    env = env if env is not None else os.environ
    wire = wire if wire is not None else PlainTextWire()
    stream: IO[str] = out if out is not None else sys.stdout

    assert_fenced(cwd, env)

    store = SessionStore(_state_root(env, cwd))
    if is_resume:
        if not session_id:
            raise ValueError("resume requires a session_id")
        state = store.load_or_create(session_id)
        state.resumes.append(prompt)
    else:
        session_id = session_id or str(uuid.uuid4())
        state = store.load_or_create(session_id)
    state.turns += 1

    ctx = RunContext(
        session=state,
        wire=wire,
        cwd=cwd,
        env=env,
        store=store,
        is_resume=is_resume,
        resume_message=prompt if is_resume else None,
        ask_cmd=_ask_cmd(env),
    )

    _log.info("mock harness run", session_id=session_id, resume=is_resume, cwd=str(cwd))
    token = _CURRENT.set(ctx)
    started = time.monotonic()
    try:
        with _chdir(cwd):
            exec(compile(prompt, "<behavior-script>", "exec"), _script_globals(ctx))
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
    stream.write(wire.render(result))
    stream.flush()
    return result.exit_code
