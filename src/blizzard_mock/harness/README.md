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

**…but say which part of it.** A `<behavior-script>…</behavior-script>` tag marks
the program — the same tagged-text idiom the mock uses on the *output* side
(`<Choice>`, `<Ask>`), turned around onto the input. Only the tagged blocks are
`exec`'d, several concatenating in order; everything around them is prose the
engine never runs, so a mock-targeted prompt can read like a real node prompt
instead of being pure code. `engine.split_behavior_script()` owns the split and
lives in the shared engine, so all three facades get it, on spawn and on resume
alike. A tagged prompt does not consult the preamble split below at all.

**A malformed tag fails the turn loudly.** An opening tag with no close, a close
with no open, or a nested open ends the run as `error_during_execution` (exit 1)
with a message naming the tag problem — never a silent fall-through to the legacy
path, and never a no-op "text turn". Silent degradation is the failure mode
designed out: a typo'd tag that quietly succeeds with no verdict and no side
effects makes tests rot invisibly.

The tag is orthogonal to the fence: it marks *which part* of a prompt is the
program, while `assert_fenced` (below) decides whether the binary may execute
anything at all. A tag in prompt content is never on its own sufficient to
trigger execution.

**Untagged, only the envelope half of it (legacy).** The runner spawns a worker at
the winter **workspace root** and prepends a machine-local preamble — the
operator's workspace prose plus a facts table naming the environments the chunk
holds (blizzard issue #17, `design/harness-adapters.md`). That preamble is
addressed to an agent's judgement, not to `exec`, and the cwd it arrives with is
the workspace, not the worktree the node must touch. A real agent reads the prose
and goes to its env; the mock does the analogous thing without an LLM:
`engine.split_worker_preamble()` drops the preamble positionally — everything
through the last row of the facts table — so only the script that follows runs,
and `engine.acquired_worktree()` reads the env's workdir off
`BLIZZARD_ENV_WORKDIRS` (injected alongside the prompt) so the script still runs
*in the acquired worktree*. This path is the **fallback for a prompt carrying no
`<behavior-script>` block**, and it is unchanged: every pre-tag behavior script
keeps working. Both no-op for a direct caller: a bare script with no preamble, and
no `BLIZZARD_ENV_WORKDIRS`, runs in cwd as before.

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
`IHarnessWire` owns *how* it is rendered. A second, optional facade seam follows
the same shape: `ITranscriptWriter` owns *whether and how a conversation is
recorded* — see "Conversation transcripts" below.

- `engine.py` — the shared `exec()` engine, the fence, the `<behavior-script>`
  split, and `run_prompt()`; owns the `RunResult`, the `IHarnessWire` and
  `ITranscriptWriter` protocols facades implement, and the `RunContext` the
  helpers read. Framework-free.
- `session.py` — `SessionState` / `SessionStore`: the per-session JSON file
  (keyed by session id) that lets a resumed script read what it asked.
- `helpers.py` — the terse helper library bound into every behavior script's
  namespace (no import needed): `ask`, `apply_diff`, `commit`, `verdict`,
  `hang`, `crash`, plus `state()` / `answer()` for reading session state. Raw
  Python is available underneath for the weird cases. `apply_diff`/`commit` each
  also mint a matched tool-call turn on the transcript, if one is wired.
- `internal/` — real git plumbing (`git.py`) and stderr-routed structlog
  (`logging.py`); kept out of the framework-free core and the script surface.
- `facades/` — one module per harness, each a thin CLI + wire over the engine:
  `claude_code.py`, `codex.py`, `opencode.py`. They share the engine and differ
  only in invocation shape / output format / exit + resume semantics.
  `facades/_transcript.py` is the claude_code-only `ITranscriptWriter`
  implementation (below); `codex.py`/`opencode.py` never construct one.

## Conversation transcripts

`mock-claude-code` mints a genuine Claude-Code-shaped JSONL transcript for every
run that has a known session id — the same record shapes the real runner's
transcript parser (`blizzard/runner/transcripts/parser.py`) reads, so a chunk
run through the fleet produces a conversation the runner panel can open. This is
**claude_code-only**: only Claude Code has a reader today, so `codex.py` and
`opencode.py` never construct a writer and the engine no-ops for them.

- `engine.ITranscriptWriter` — the protocol, mirroring `IHarnessWire`:
  `record_user` (the spawn/resume turn), `record_result` (the final assistant
  turn), and `record_tool_call` / `record_tool_result` (a matched pair, called by
  `helpers.apply_diff` / `helpers.commit`). `engine.run_prompt` takes an optional
  `transcript` parameter and calls into it at those points; `None` (every facade
  but claude_code) is a total no-op.
- `facades/_transcript.ClaudeTranscriptWriter` — the one implementation. Appends
  JSONL records to `<root>/mock-claude-code/<session_id>.jsonl`. The directory
  name is a fixed constant, not a mangled-cwd replica of real Claude Code's
  naming: the real reader locates a transcript by globbing
  `<root>/*/<session_id>.jsonl` and only falls back to the directory name as a
  multi-match tie-break, which a UUID4 session id never triggers.
- `facades/_transcript.transcripts_root` resolves `BZ_TRANSCRIPTS_ROOT` — the
  same env var the runner reads (`blizzard.runner.config.ENV_TRANSCRIPTS_ROOT`),
  so writer and reader agree on where files live. **When unset it falls back to a
  path under the fence** (beside the session-state directory,
  `engine.fence_base_dir`) — never the runner-side default of
  `~/.claude/projects`, which is the developer's real Claude Code session store.
- `claude_code.py` only constructs a writer when a session id is already known
  (`--session-id` on spawn, or `--resume`) — the runner always pre-assigns one at
  spawn and honors it, so this covers the fleet-driven path in full; a bare
  direct invocation that lets the engine self-assign a uuid skips transcript
  writing.
- Minted deliberately narrow for realism a human reading the file benefits from
  (`sessionId`/`cwd`/`timestamp` per record) without cost the parser doesn't
  need: no `uuid`/`parentUuid` DAG, no `isSidechain` subagent sidecars, no
  `<persisted-output>` offload wrapper, no byte-exact ANSI fidelity. The user
  turn's text is never the raw exec'd Python — that would misrepresent code as
  "what the user said" — it is a tagged prompt's own prose with its
  `<behavior-script>` blocks elided, else the real preamble prose when an
  untagged spawn carried one (`split_worker_preamble`), else a short synthetic
  line.
- Every **assistant** record carries `message.model` + `message.usage` (tokens by
  class), and the final result envelope additionally carries top-level `usage` +
  `total_cost_usd` — the same fields the real harness reports, so the runner's
  cost-telemetry capture path has something to parse: `parse_usage` reads the
  envelope, and `sum_transcript_usage` sums the per-message `usage` on the
  envelope-less fallback. The figures are synthesized deterministically by output
  length in `facades/_usage.py` and are **illustrative, not a pricing table** —
  blizzard never derives cost from one; `total_cost_usd` is the harness's own number.

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

A behavior-script is Python — inside a `<behavior-script>` block, or the whole
untagged prompt past the preamble. These names are pre-bound in its namespace:

`apply_diff`/`commit` act on `current_context().cwd`, which `run_prompt` sets
to `engine.acquired_worktree()` — the **environment's** directory, not any
one repo's. In the single-repo fixtures most tests use, that directory *is*
the repo, so this is invisible; in a real multi-repo winter env it holds
every acquired repo as a child and is **not itself a git repo**, so `git
apply`/`git commit` fail there. A fleet-tier script targeting one repo must
repoint the ambient context first — `ctx = current_context(); ctx.cwd =
pathlib.Path(ctx.cwd) / "<repo-name>"` — before calling either helper (see
the `blizzard` repo's `tests/service/test_runner_service.py`,
`_TRANSCRIPT_BUILD_SCRIPT`, for the pattern and the why in full).

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
state, crash, hang, the `<behavior-script>` tag's three cases — tagged, untagged
legacy, malformed — and the Claude Code JSON envelope + fence-refusal exit).
