# `blizzard_mock.harness` — mock coding-harness engine + facades

## Contract

The harness seam accepts any coding harness (Claude Code, Codex, OpenCode).
This package ships **three mock coding-harness applications**, one mimicking
each, so the runner's adapter layer is exercised against realistic per-harness
surfaces in every test without spending real tokens.

**The prompt is the program.** A real harness turns a prompt string into
behavior via an LLM; the mock turns it into behavior via `exec()` — the prompt
it receives *is* Python code, run in the acquired worktree with the spawn
environment. A test mints a workflow graph whose node prompts are behavior
scripts, and the code rides the real pipeline (hub envelope → runner → adapter →
spawn), including per-attempt variation via judgement `prompt_addendum` (D-071).

**…but only the envelope half of it.** The runner spawns a worker at the winter
**workspace root** and prepends a machine-local preamble — the operator's
workspace prose plus a facts table naming the environments the chunk holds
(blizzard issue #17, `design/harness-adapters.md`). That preamble is addressed to
an agent's judgement, not to `exec`, and the cwd it arrives with is the
workspace, not the worktree the node must touch. A real agent reads the prose and
goes to its env; the mock does the analogous thing without an LLM:
`engine.split_worker_preamble()` drops the preamble so only the script runs, and
`engine.acquired_worktree()` reads the env's workdir off `BLIZZARD_ENV_WORKDIRS`
(injected alongside the prompt) so the script still runs *in the acquired
worktree*. Both no-op for a direct caller: a bare script with no preamble, and no
`BLIZZARD_ENV_WORKDIRS`, runs in cwd as before.

A script can do anything an agent can: apply a diff and `git commit` (**real
commits** — everything downstream of the harness seam runs for real), fire the
ask/answer protocol by invoking `blizzard ask` and exiting (park + resume, the
resume message again arriving as code), emit a verdict or a malformed one, hang,
or crash. **Sessions persist a state file** so a resumed script can read what it
asked and act on the answer it was resumed with.

**Fenced.** Arbitrary code execution is the feature, so the engine refuses to
run unless test scaffolding marks the environment — it can never pass as a real
harness binding.

## Structure

The engine/facade split is what lets a new harness be added without touching the
engine: the engine owns *what* is emitted (a `RunResult`), a facade's
`IHarnessWire` owns *how* it is rendered.

- `engine.py` — the shared `exec()` engine, the fence, and `run_prompt()`; owns
  the `RunResult`, the `IHarnessWire` protocol facades implement, and the
  `RunContext` the helpers read. Framework-free.
- `session.py` — `SessionState` / `SessionStore`: the per-session JSON file
  (keyed by session id) that lets a resumed script read what it asked.
- `helpers.py` — the terse helper library bound into every behavior script's
  namespace (no import needed): `ask`, `apply_diff`, `commit`, `verdict`,
  `hang`, `crash`, plus `state()` / `answer()` for reading session state. Raw
  Python is available underneath for the weird cases.
- `internal/` — real git plumbing (`git.py`) and stderr-routed structlog
  (`logging.py`); kept out of the framework-free core and the script surface.
- `facades/` — one module per harness, each a thin CLI + wire over the engine:
  `claude_code.py`, `codex.py`, `opencode.py`. They share the engine and differ
  only in invocation shape / output format / exit + resume semantics.

## The fence

Arbitrary code execution is the feature, so the engine refuses to run unless
**both** factors mark the environment as test scaffolding — it can never pass as
a real harness binding:

1. env var `BLIZZARD_MOCK_HARNESS_FENCE=1` (`engine.FENCE_ENV_VAR`), and
2. a `.blizzard-mock-harness-fence` marker file (`engine.FENCE_MARKER_FILENAME`)
   in the worktree tree — cwd or any ancestor.

`engine.assert_fenced(cwd, env)` enforces it; a refusal raises `FenceError`, and
the facades map that to exit code `2` (distinct from a script error's `1`).
`engine.write_fence_marker(cwd)` / `engine.fenced_env(base)` mark an environment
from test scaffolding. Session state defaults to a `.blizzard-mock-harness/`
directory beside the marker, overridable via `BLIZZARD_MOCK_HARNESS_STATE_DIR`.

## Script helper API

A behavior-script is Python; these names are pre-bound in its namespace:

| Helper | Effect |
|--------|--------|
| `apply_diff(diff)` | `git apply` a unified diff to the worktree (real files). |
| `commit(message) -> sha` | Real `git commit -A`; returns the new sha. |
| `verdict(choice, assessment="")` | Emit `<Choice>{choice}</Choice>` + assessment as the turn's result. |
| `ask(question, options=None)` | Record the ask, optionally shell out to `$BLIZZARD_RUNNER_ASK_CMD`, emit the tagged `<Ask …>` result, and **exit the turn**. |
| `hang()` | Block forever (stall/heartbeat/REAP testing). |
| `crash(hard=False)` | Die without a verdict — soft (error run, exit 1) or `hard` (`os._exit`). |
| `state()` | The `SessionState` — `state().last_ask`, `state().last_answer`. |
| `answer()` | The resume message this turn was resumed with. |

## Binaries

Each facade registers a `[project.scripts]` binary:

| Binary | Facade | Surface |
|--------|--------|---------|
| `mock-claude-code` | `facades.claude_code:main` | `-p [--output-format json] [--session-id <id>] [--resume <id>] "<script>"`; single `{"type":"result", …}` JSON envelope. |
| `mock-codex` | `facades.codex:main` | `exec [--json] [resume <id>] "<script>"`; JSONL event stream, self-assigned session. |
| `mock-opencode` | `facades.opencode:main` | `run [--session <id>] [--attach] "<script>"`; message text + JSON trailer. |

Tests: `tests/test_harness_smoke.py` (fence, verdict, real commit, ask→resume
state, crash, hang, and the Claude Code JSON envelope + fence-refusal exit).
