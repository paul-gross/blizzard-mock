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

**Whole-message mode is the preferred contract for a caller that composes the
whole message itself** — test tiers, scripted journeys, fixture scenarios.
`engine.run_prompt(..., whole_message=True)` execs the entire prompt (or, on
resume, the entire resume message) with **no sentinel scanning at all**: no
`<behavior-script>` tag lookup, no preamble split. A mention of the tag anywhere
in the script — including inside a string literal a script builds or inspects —
is just more script text, never a delimiter, which closes the residual the tag
approach below cannot: a tag-shaped line inside a string still ends a tagged
block early, because the line-anchored scan below has no notion of Python syntax.
A whole-message script that parses to an empty module body (blank, or
comments-only) fails the turn loudly (`EmptyBehaviorScriptError`) rather than
exiting 0 with no verdict. Use whole-message mode whenever the caller controls
the entire message; reach for the tag below only when a message mixes
script-author-owned code with prose the caller does not fully control (an
operator preamble, a quoted work item) and cannot cleanly separate the two
before calling the engine.

**…or, for mixed prose+script composition, say which part of it.** A
`<behavior-script>…</behavior-script>` tag marks
the program — the same tagged-text idiom the mock uses on the *output* side
(`<Choice>`, `<Ask>`), turned around onto the input. Only the tagged blocks are
`exec`'d, several concatenating in order; everything around them is prose the
engine never runs, so a mock-targeted prompt can read like a real node prompt
instead of being pure code. `engine.split_behavior_script()` owns the split and
lives in the shared engine, so all three facades get it, on spawn and on resume
alike. A tagged prompt does not consult the preamble split below at all. Blocks
are dedented, so a tag nested in a markdown list or blockquote runs.

**A delimiter counts only on a line of its own** (leading/trailing whitespace
aside). The worker's prompt is not all script-author-owned: the runner composes
operator prose ahead of the envelope (`blizzard:runner/harness/preamble.py`), and
that prose may well *mention* the tag — a house rule about it, or a quoted work
item. Matched as a bare substring, such a mention would either hijack a legacy run
(the mention's snippet becoming the program while the node's real script is
silently reclassified as prose) or, unpaired, hard-fail every untagged spawn in
the deployment. Line-anchoring makes an inline mention — ``a `<behavior-script>`
tag`` — inert, so a prompt with no *block* keeps behaving exactly as it did before
the tag existed. **Quote the tag inline, never as a standalone block:** prose that
sets both delimiters alone on their own lines *is* a block, since nothing
distinguishes an illustration from the real thing.

**A malformed tag fails the turn loudly.** An opening tag with no close, a close
with no open, a nested open, or a block enclosing nothing executable ends the run
as `error_during_execution` (exit 1) with a message naming the tag problem — never
a silent fall-through to the legacy path, and never a no-op "text turn". Silent
degradation is the failure mode designed out: a tag that quietly succeeds with no
verdict and no side effects makes tests rot invisibly.

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
`IHarnessWire` owns *how* it is rendered. Two further optional facade seams follow
the same shape: `ITranscriptWriter` owns *whether and how a conversation is
recorded* — see "Conversation transcripts" below — and `IHookRunner` owns *whether
and how a settings document's hook commands are executed* — see "Hook execution".

- `engine.py` — the shared `exec()` engine, the fence, the `<behavior-script>`
  split, and `run_prompt()`; owns the `RunResult`, the `IHarnessWire`,
  `ITranscriptWriter` and `IHookRunner` protocols facades implement, and the
  `RunContext` the helpers read. Framework-free — it spawns nothing itself.
- `session.py` — `SessionState` / `SessionStore`: the per-session JSON file
  (keyed by session id) that lets a resumed script read what it asked. It also
  records an `Invocation` per turn — the `--model`/`--effort` flags that turn was
  launched with, `None` for a flag absent from argv. The mock acts on neither
  (it is model- and effort-agnostic); they are recorded because *what each turn
  was launched with* is the observable a fleet-tier scenario asserts blizzard's
  mint-only model contract against. Note what that does and does not prove: the
  facade sees argv, so this checks the **flag**, never the effective model.
- `helpers.py` — the terse helper library bound into every behavior script's
  namespace (no import needed): `ask`, `apply_diff`, `commit`, `tool_call`,
  `verdict`, `hang`, `crash`, plus `state()` / `answer()` for reading session
  state. Raw Python is available underneath for the weird cases. Its tool-call
  helpers also drive the transcript and the hook seam — see "Conversation
  transcripts" below.
- `internal/` — real git plumbing (`git.py`) and stderr-routed structlog
  (`logging.py`); kept out of the framework-free core and the script surface.
- `facades/` — one module per harness, each a thin CLI + wire over the engine:
  `claude_code.py`, `codex.py`, `opencode.py`. They share the engine and differ
  only in invocation shape / output format / exit + resume semantics.
  `facades/_transcript.py` is the claude_code-only `ITranscriptWriter`
  implementation (below), and `facades/_hooks.py` the claude_code-only
  `IHookRunner` one ("Hook execution" below); `codex.py`/`opencode.py` never
  construct either.

## Conversation transcripts

`mock-claude-code` mints a genuine Claude-Code-shaped JSONL transcript for every
run that has a known session id — the same record shapes the real runner's
transcript normalizer (`blizzard.runner.harness.internal.claude_code_normalizer`,
blizzard#245) reads, so a chunk run through the fleet produces a conversation the
runner panel can open. This is **claude_code-only**: only Claude Code has a
reader today, so `codex.py` and `opencode.py` never construct a writer and the
engine no-ops for them.

- `engine.ITranscriptWriter` — the protocol, mirroring `IHarnessWire`:
  `record_user` (the spawn/resume turn), `record_result` (the final assistant
  turn), and `record_tool_call` / `record_tool_result`. `engine.run_prompt` takes
  an optional `transcript` parameter and calls into it at those points; `None`
  (every facade but claude_code) is a total no-op.
- **The tool-call call sites — stated here and nowhere else.** Three helpers
  mint a matched `tool_use`/`tool_result` pair: `apply_diff` (as `Edit`), `commit`
  (as `Bash`), and `tool_call(name)`, which mints one and touches nothing else.
  All three go through a single combined path in `helpers.py` that writes the
  transcript pair **when a writer is wired** and fires the `PostToolUse` hooks
  (`engine.IHookRunner`) **regardless** — the two seams are independent, so a run
  with hooks and no transcript still fires.
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
- **The sidechain/thinking-fidelity gap — stated here and nowhere else.** This is
  the one place to update if the gap's shape changes. Beyond `sessionId`/`cwd`/
  `timestamp` per record — `facades/_transcript.py`'s module docstring is the one
  place that owns why each of those three is minted — this writer mints none of:
  a `uuid`/`parentUuid` DAG, `isSidechain` subagent sidecar files,
  `type: "thinking"` content blocks, a `<persisted-output>` offload wrapper, or
  byte-exact ANSI fidelity. The first three are a **documented gap**, not a claim
  the normalizer doesn't want them — it added inline-sidechain threading (the
  `uuid`/`parentUuid` chain), sidecar-file discovery (`isSidechain` subagent
  conversations, `<session-id>/subagents/agent-<agentId>.jsonl`), and
  thinking-turn redaction, none of which this writer mints. It closes once
  `epic:transcripts` (`blizzard-product:/plans/transcripts.md`) ships these turns
  somewhere a mock-fleet chunk can observe, and teaches this writer to mint them
  alongside.
- The user turn's text is never the raw exec'd Python — that would misrepresent
  code as "what the user said" — it is a tagged prompt's own prose with its
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
  Blizzard also reads `message.usage` **per turn** rather than summed, for the session
  context behind `rotate.max_context_tokens` (`ClaudeCodeTranscriptSource.context_tokens`);
  this writer mints one usage-bearing record per message, so that read is faithful here.
- **The `message.id` gap.** This writer mints no `message.id`, and one record per
  message. The real harness splits a multi-content-block reply across several records
  that each repeat their message's one `usage`, which is why `sum_transcript_usage`
  collapses by `message.id` — against this writer that collapse never engages, and the
  id-less fallback branch counts each record instead. Equivalent totals here, but the
  dedupe itself is exercised only by `blizzard:unit-test` fixtures, never mock-driven.
  Minting a repeated `message.id` across split records would close it.

## Hook execution

Real Claude Code executes the hook commands declared in its `--settings`
document, and the runner depends on two of them: a `PostToolUse` command that
signals progress, and a `SessionEnd` command that signals "this process declared
itself done". `mock-claude-code` executes them for real — the command runs as a
subprocess and does whatever it does — so those signals travel their actual path
instead of being synthesized by whoever wanted them. This is **claude_code-only**:
it is the only facade the runner passes `--settings`, so `codex.py` and
`opencode.py` never construct a runner and the engine no-ops for them.

- `engine.IHookRunner` — the protocol, mirroring `ITranscriptWriter`:
  `on_tool_use(name, tool_input, tool_output)` and `on_session_end(result)`.
  `engine.run_prompt` takes an optional `hooks` parameter; `None` is a total
  no-op. The engine constructs no payload and spawns nothing itself.
- **The two lifecycle points.** `PostToolUse` fires from the tool-call helpers —
  "Conversation transcripts" above owns that list — so a working mock worker
  signals progress as a side effect of working, and `tool_call(name)` gives a
  script a deterministic timeline with no git in it. `SessionEnd` fires once at
  the tail of `run_prompt`, after the wire render.
- **Why the tail.** The hook subprocess runs to completion while the mock process
  is still alive, so a "declared done" signal is durable *before* an observer can
  see the process exit. Firing and forgetting, or firing after exit, would make
  those two orderings race — and telling them apart is the whole point of the
  signal.
- `facades/_hooks.SettingsHookRunner` — the one implementation. It reads
  `hooks.PostToolUse[].hooks[]` and `hooks.SessionEnd[].hooks[]` from the settings
  JSON (inner entries with `type: "command"`) and runs each command with
  `shell=True` in the acquired worktree, **inheriting the spawn environment** —
  which is the point, since it is how a hook command finds the identity its caller
  injected. A Claude-Code-shaped JSON payload goes in on stdin:
  `hook_event_name`, `session_id`, `cwd`, `transcript_path`, plus
  `tool_name`/`tool_input`/`tool_response` on `PostToolUse` and `reason` on
  `SessionEnd`. Output is **captured, never inherited** — a chatty hook writing to
  the facade's own stdout would interleave with the `{"type":"result", …}` envelope
  the adapter parses.
- **Nothing a hook does can fail the turn.** A missing file, unparseable JSON, an
  absent `hooks` key, a malformed entry, a nonzero exit, or a command that outruns
  the per-hook timeout (60s, real Claude Code's default) is logged to the
  stderr-routed structlog and ignored. The runner writes this document; a mock that
  hard-failed on a bad one would turn a settings typo into a dead fleet.
- **A missing hook binary degrades silently, by construction.** If the command
  names a binary that is not on the child's `PATH`, the shell exits 127 and that
  exit is swallowed like any other — the turn is green and no signal was sent.
  Debugging "why did no hook fire", start there: the `hook command exited nonzero`
  warning on stderr is the only trace it leaves.
- **The three invocation shapes the runner makes, and what each expects.** Spawn
  (`-p --output-format json --model … --session-id … --settings …`) fires. Resume
  with a message (`-p --output-format json --resume … --settings …`) fires —
  `--resume` inherits nothing from the original spawn, so the flag rides again, and
  **each turn is its own process, so each fires its own `SessionEnd`**. The
  synchronous verdict elicitation (`-p --output-format json --resume …`) carries
  **no** `--settings`, deliberately: it must not record a done-signal for a turn
  the worker did not finish. `--model` is accepted and ignored.
- **Which exits fire no `SessionEnd`** — by class, not by list. The tail is skipped
  when **no session ever started** (a fence refusal; a pre-session argument error)
  and when **the process never reached its tail** (`crash(hard=True)`, which is
  `os._exit`; `hang()`, which blocks until it is killed; and a script raising
  `SystemExit`, which the engine's `except Exception` does not catch). The first
  two of those are deliberate and meaningful: they leave exactly the
  no-`SessionEnd` signal a `SIGKILL`ed real Claude Code leaves, which is the case
  an observer needs to tell apart from a clean exit.
- **`cwd` is fixed at construction and does not follow a script's repointing.** The
  hook's working directory and the payload's `cwd` are the acquired worktree
  resolved when the run started — matching real Claude Code, whose hooks also run
  in the fixed session cwd. A fleet-tier script that repoints `ctx.cwd` at one repo
  (see "Script helper API" for the convention and why) still fires its hooks in the
  environment directory, not the repo it just committed in.
- **`session_id` and `reason`.** `SessionEnd`'s `session_id` is the run's own, so
  both resume shapes are right. `PostToolUse` reports the id the facade resolved
  from `--session-id`/`--resume`; a bare direct invocation that supplies neither
  (never how the runner drives this facade) reports `""`. `reason` is always
  `"other"` — Claude Code's vocabulary is `clear`/`logout`/`prompt_input_exit`/
  `other`, and only `other` describes a headless `-p` process exiting. Deriving it
  from the run's outcome would emit values real Claude Code never sends; the
  outcome already rides the wire envelope.
- **Wired: `PostToolUse` and `SessionEnd`, nothing else.** No other event, and no
  matchers or per-tool filters — the runner's document declares bare command hooks.
  The payload's `hook_event_name` discriminates, so another event drops in later
  without a payload redesign.

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
| `tool_call(name, tool_input=None, output="ok")` | Record a tool call that does nothing else — the transcript pair and the `PostToolUse` fire, no git. For choreographing a deterministic tool timeline. |
| `verdict(choice, assessment="")` | Emit `<Choice>{choice}</Choice>` + assessment as the turn's result. |
| `ask(question, options=None)` | Record the ask, optionally shell out to `$BLIZZARD_RUNNER_ASK_CMD`, emit the tagged `<Ask …>` result, and **exit the turn**. |
| `hang()` | Block forever (stall/heartbeat/REAP testing). |
| `crash(hard=False)` | Die without a verdict — soft (error run, exit 1) or `hard` (`os._exit`). |
| `state()` | The `SessionState` — `state().last_ask`, `state().last_answer`. |
| `answer()` | The resume message this turn was resumed with — a tagged resume's **prose**, so a script never reads its own source back; an untagged one's raw message, as always. |

## Binaries

Each facade registers a `[project.scripts]` binary:

| Binary | Facade | Surface |
|--------|--------|---------|
| `mock-claude-code` | `facades.claude_code:main` | `-p [--output-format json] [--session-id <id>] [--resume <id>] [--settings <path>] [--model <name>] [--effort <level>] "<script>"`; single `{"type":"result", …}` JSON envelope. |
| `mock-codex` | `facades.codex:main` | `exec [--json] [resume <id>] "<script>"`; JSONL event stream, self-assigned session. |
| `mock-opencode` | `facades.opencode:main` | `run [--session <id>] [--attach] "<script>"`; message text + JSON trailer. |

Tests: `tests/test_harness_smoke.py` (fence, verdict, real commit, ask→resume
state, crash, hang, the `<behavior-script>` tag's three cases — tagged, untagged
legacy, malformed — whole-message mode — including a tag mention inert inside a
string literal, and an empty module body failing loudly — and the Claude Code
JSON envelope + fence-refusal exit) and `tests/test_harness_hooks.py` (the hook
seam: the lifecycle fire points, the exits that fire nothing, and real
`--settings` hook execution against stub shell commands).
