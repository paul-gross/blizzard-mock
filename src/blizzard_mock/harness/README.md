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
holds (`design/harness-adapters.md`). That preamble is
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
  A spawn also records its `spawn_credential` — what the runner exported to the
  worker: `{"lease_id", "lease_token", "runner_url"}` from `BLIZZARD_LEASE_ID`,
  `BLIZZARD_LEASE_TOKEN`, `BLIZZARD_RUNNER_URL`, `null` for one absent from the
  env. A resume that carries a token replaces the record (the runner re-mints a
  lease's token for each resume, superseding the last); one without leaves it in
  place. The record is written before the script runs and lives at `<state dir>/<session_id>.json` — `BLIZZARD_MOCK_HARNESS_STATE_DIR`,
  else `.blizzard-mock-harness/sessions/` beside the fence marker — so a test finds
  a lease's session by scanning those files for its `spawn_credential.lease_id`.
- `helpers.py` — the terse helper library bound into every behavior script's
  namespace (no import needed): `ask`, `apply_diff`, `commit`, `tool_call`,
  `verdict`, `hang`, `crash`, plus `state()` / `answer()` for reading session
  state, plus the OpenCode-only misbehaviour plane — `permission_denial`,
  `interrupt_tool`, `malformed_record` — see "Script helper API" below. Raw
  Python is available underneath for the weird cases. Its tool-call helpers
  also drive the transcript and the hook seam — see "Conversation transcripts"
  below.
- `internal/` — real git plumbing (`git.py`) and stderr-routed structlog
  (`logging.py`); kept out of the framework-free core and the script surface.
- `facades/` — one module per harness, each a thin CLI + wire over the engine:
  `claude_code.py`, `codex.py`, `opencode.py`. They share the engine and differ
  only in invocation shape / output format / exit + resume semantics.
  `facades/_transcript.py` is the claude_code-only `ITranscriptWriter`
  implementation (below), and `facades/_hooks.py` the claude_code-only
  `IHookRunner` one ("Hook execution" below); `codex.py`/`opencode.py` never
  construct either.
- `opencode_surface/` — a wholly separate concept, sharing no code with the
  engine above: `mock-opencode emit` bakes a standalone CLI-surface fake
  binary out of this package. It nests under `harness/` rather than sitting at
  the top level because `harness/` is where every coding-harness-facing
  surface lives regardless of implementation strategy — an exec-engine facade
  here, a baked out-of-process CLI artifact there — not because it shares the
  engine's approach. See "OpenCode CLI-surface mode" below.
- `opencode2_core/` — what OpenCode 2's two artifacts share, and nothing that
  executes a prompt: the managed service and its `/api/*` surface, the sessions
  and the operations a turn is built from, the permission rules, the
  corpus-shaped records, the service-level levers, the takeover TUI, the store
  migration, and `emit.py`, which bakes one artifact. It shares no code with
  `opencode_surface/` or the exec engine.
- `opencode2_surface/` — OpenCode 2's CLI-surface artifact, `mock-opencode-2
  emit`: `opencode2_core/` with a turn runner that answers a prompt's plan. See
  "OpenCode 2 CLI-surface mode" below.
- `opencode2_engine/` — OpenCode 2's engine-backed mock service,
  `mock-opencode-2 emit-engine`: `opencode2_core/` with a turn runner that runs
  each turn's prompt as a script in a child process. See "OpenCode 2
  engine-backed mode" below.

## Conversation transcripts

`mock-claude-code` and `mock-opencode` each mint a genuine harness-shaped transcript for
every run that has a known session id — the same record shapes the real runner's own
normalizer for that harness (`claude_code_normalizer`; `opencode_normalizer`)
reads, so a chunk run through the fleet produces a conversation the runner
panel can open. This is **claude_code/opencode-only**: `codex` has no reader today, so
`codex.py` never constructs a writer and the engine no-ops for it.

- `engine.ITranscriptWriter` — the protocol, mirroring `IHarnessWire`:
  `record_user` (the spawn/resume turn), `record_result` (the final assistant
  turn), and `record_tool_call` / `record_tool_result`. `engine.run_prompt` takes
  an optional `transcript` parameter and calls into it at those points; `None`
  (codex) is a total no-op. It also takes `transcript_factory`, called with the
  session id once `run_prompt` has settled on one (a fresh mint or a resume's
  own) — for a harness whose facade cannot know the id up front, unlike
  `claude_code.py` below.
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
- `facades/_opencode_transcript.OpenCodeTranscriptWriter` — OpenCode's own
  implementation, wired through `opencode.py`'s `transcript_factory` since a
  fresh `mock-opencode run` self-mints its session id (real OpenCode's own
  fresh-session handshake), unknown to the facade until `run_prompt` mints it.
  Unlike Claude Code's append-only JSONL, real `opencode export <id>` returns
  one `{info, messages}` JSON document, so this writer keeps that document in
  memory and rewrites the whole file at `<root>/mock-opencode/<session_id>.json`
  on every mutation — what makes a tool call's `pending` -> `completed`
  transition visible to a reader between the two calls. `mock-opencode export
  <id>` reads that same path back verbatim, matching the real CLI's contract.
  A `task` tool call additionally mints a second, linked child document, the
  child's own `info.parentID` naming the parent — the same
  `state.metadata.sessionId` pointer real OpenCode 1.18.32 carries, beside
  `parentSessionId` and the call's `state.time.{start,end}`. Every message
  carries `info.time.created` and every text part a `time`; the child's messages
  are created inside the spawning call's window. A `task_id` input continues
  that child's document rather than minting a new one, and each completed
  `task` part is also streamed on `run` as a `tool_use` event. A `task` input's
  reserved `child_tool_calls` key — a list of `{"tool", "input", "output"}`
  mappings, never copied into the recorded input — gives the child's reply a
  completed tool part per entry, so a mock transcript holds events inside a
  subagent: `tool_call("task", {"subagent_type": "explorer", "prompt": "...",
  "child_tool_calls": [{"tool": "read", "input": {"filePath": "a.py"}}]})`.
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
  `epic:transcripts` (`blizzard-product:/delivered/transcripts.md`) ships these
  turns somewhere a mock-fleet chunk can observe, and teaches this writer to mint
  them alongside.
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
- **Billed-zero OpenCode.** With `BLIZZARD_MOCK_OPENCODE_BILLED_ZERO=1` in its
  environment (`facades/_opencode_transcript.BILLED_ZERO_ENV_VAR`; only the exact value
  `1` turns it on), `mock-opencode` runs as a subscription-billed OpenCode: every
  `step_finish` event it streams, and every `step-finish` part and assistant message
  `info` its export document carries, reports `cost: 0` while keeping its synthesized
  tokens. Off by default, the synthesized non-zero cost stands.
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
  `os._exit`; `hang()` until it is `SIGKILL`ed; and a script raising
  `SystemExit`, which the engine's `except Exception` does not catch). The first
  two of those are deliberate and meaningful: they leave exactly the
  no-`SessionEnd` signal a `SIGKILL`ed real Claude Code leaves, which is the case
  an observer needs to tell apart from a clean exit.
- **A `SIGINT` mid-script reaches the tail.** The engine catches the interrupt
  wherever the script was — inside `hang()` included — and ends the turn as an
  `error_during_execution` run, the way real Claude Code does: the Claude Code
  facade writes a synthesized crash envelope — its usage and cost derived from the
  fixed interrupted-turn text, not accumulated from anything the script did — the
  OpenCode facade keeps every event it already streamed and closes with its error
  event, and `SessionEnd` fires for the turn. A `SIGTERM` or `SIGHUP`, unlike
  `SIGINT`, has no handler installed and kills the process outright, leaving no
  envelope at all — the same as a `SIGKILL`. This is what lets a runner's
  interrupt-then-record path be exercised against the mock.
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

## OpenCode CLI-surface mode

`mock-opencode` carries a second, wholly separate mode alongside `run` above:
`emit` writes a standalone, self-contained fake OpenCode CLI binary — a
**CLI-surface artifact** — that a real diagnostic in `blizzard`
(`blizzard.runner.harness.internal.opencode_probe` and friends) spawns as a
genuine out-of-process subprocess and drives through OpenCode's real CLI
surface (`--version`, `run`, `export`, `serve`, `attach`,
`debug config --pure`) and local HTTP control API (session `GET`, `/global/event`
SSE, `POST /session`, `POST /summarize`). It shares nothing with the exec
engine above — no `RunResult`, no `IHarnessWire`, no fence — because it never
executes a caller-supplied prompt; it answers a fixed CLI shape instead.

- **The contract:** `mock-opencode emit --out <path> [--lever NAME]...
  [--version <ver>] [--auth-read-marker <path>] [--version-touch-path <path>]`.
  `--lever` may repeat; the whole set is validated before anything touches the
  filesystem — an unknown name fails naming every bad token in one error and
  writes no file (never create-then-delete). `--version` defaults to the
  pinned `1.18.25`. `--auth-read-marker`/`--version-touch-path` are evidence-
  capture file paths, not misbehaviours, so they stay separate from `--lever`.
  On success the artifact is written to `--out`, executable, and nothing
  unusual prints; exit is `0`.
- **The package:** `opencode_surface/` (a sibling of `engine.py`/`session.py`/
  `helpers.py`/`facades/`, not an extension of any of them) — `levers.py`
  (the closed `Lever` vocabulary + `CATALOG`, the one prose home for what each
  of the 26 misbehaviours does — see it rather than a restatement here),
  `domain/` (pure decision logic: given a lever set + session state + request
  shape, what JSON/exit-code/text to produce — no file I/O, no sockets, no
  argparse), `internal/` (the I/O adapters: state-file read/write, the
  `http.server`-based `serve`, the `attach` HTTP client + stdin loop,
  subprocess), `cli.py` (the artifact's entry point, composing domain +
  internal once), and `emit.py` (runs in blizzard-mock's own venv at emit
  time only — never part of the artifact — and bakes the validated lever set
  into a generated `_baked.py` module before zipping the rest via stdlib
  `zipapp`).
- **Stdlib-only, no venv import, ever.** Everything under `domain/`,
  `internal/`, `cli.py`, `levers.py`, and the generated `_baked.py` imports
  nothing beyond the Python standard library — no `fastapi`, `pydantic`,
  `click`, `structlog`, nothing this repo depends on. The artifact runs under
  the bare **system** `python3`, with no venv and no `blizzard_mock` import
  reachable at runtime: a Landlock policy elsewhere grants an executable's
  own parent directory access, never the venv's site-packages, so an artifact
  that tried to `import blizzard_mock` would fail on a real sandboxed run.
- **Immutable once emitted.** The lever set baked into `_baked.py` is a plain
  module-level constant, fixed at emit time — there is no `/_levers` control
  plane and no store, unlike `mock_hub`'s/the IdP's lever modules (armed over
  HTTP against a long-lived service). Arming a different misbehaviour means
  emitting a different artifact.
- **Cross-invocation session state, XDG-rooted.** The diagnostic spawns the
  artifact once per surface call (version, run, export, serve, attach,
  debug), each its own process — so anything that must be observed across
  calls (`PROCESS_CONTROL`, `TRANSCRIPT_CURSOR`-shaped probes) persists to
  `$XDG_STATE_HOME/fake-opencode-state.json` (`internal.paths.state_path`,
  `internal.state_store`).
- **Never installed, never on `PATH`.** The artifact is only ever written to
  a caller-named path at emit time — `mock-opencode` itself can never become
  one. See "The fence" below for what that means for this mode specifically.

## OpenCode 2 CLI-surface mode

`mock-opencode-2 emit` writes OpenCode 2's CLI-surface artifact: a standalone,
stdlib-only fake that the 2.x compatibility diagnostic in `blizzard`
(`blizzard.runner.harness.opencode_v2.compatibility`) and its service test run
against in place of a real OpenCode 2. It shares no code with
`opencode_surface/` — the two majors' surfaces have nothing in common — and
holds to the same rules as that package: stdlib-only, immutable once emitted,
never installed, never on `PATH`. The artifact is `opencode2_core/` and
`opencode2_surface/` zipped side by side; `opencode2_engine/` is never in it.
Everything below but the turns is `opencode2_core/`'s — the surface, the
managed service, the sessions, the rules, the levers, and the takeover TUI —
and the engine-backed mock shares it; `opencode2_surface/` owns only the turns,
`domain/script.py`'s plan and `domain/shell.py`'s fake shell.

- **The contract:** `mock-opencode-2 emit --out <path> [--version <v>]
  [--lever NAME]...`. `--version` defaults to `2.1.4`. The whole lever set is
  validated before anything is written; an unknown name fails, naming every bad
  token, and writes no file.
- **The surface:** `--version` prints `opencode v<version>`; `service
  start|stop|status|restart` manage a detached service; `service get|set|unset
  disabled` keeps the one service setting the fake holds, `disabled`, in
  `$XDG_CONFIG_HOME/opencode/service.json`, and `service get disabled` prints
  `true` or `false`; `api --standalone GET
  /api/session` opens an OpenCode 1 store as 2.x does the first time; and
  `--session <id>` is the takeover TUI. The TUI reads one line from its
  terminal, switches the session to the `build` agent, prompts it, and stays
  open until it is ended.
- **The managed service:** it listens on an ephemeral loopback port and writes
  `$XDG_STATE_HOME/opencode/service.json` (`id`, `version`, `url`, `pid`,
  `password`, mode `0600`), with a new `id` and password on every start. It
  answers the non-experimental `/api/*` subset blizzard's client calls, under
  HTTP Basic auth as `opencode`, with responses shaped like the committed 2.1.4
  corpus. Sessions and messages persist in `$XDG_DATA_HOME/opencode/` across a
  restart, and a turn that was running is resumed. A session's environment does
  not survive a restart, as in 2.1.4.
- **Turns are read, never executed.** `domain/script.py` reads a prompt's
  shape: shell commands in backticks after `run`, a `read` call, a subagent's
  instructions, and the reply the prompt asks for. A shell call runs a tiny
  fixed grammar (`domain/shell.py`: `echo`, `echo … > file`, `touch`, `sleep`)
  against the session's environment and directory. Nothing reaches a real
  shell. A `sleep` holds a real stand-in process, capped at a few seconds, so
  the tool's pid can be seen and an interrupt can end it. Permission rules
  follow 2.1.4 (`opencode2_core/domain/rules.py`):
  - the last matching rule wins;
  - a tool whose last matching rule denies every resource is withheld from the model;
  - `ask` raises a request that holds the turn until it is answered.

  A subagent call runs as a child session, and a background child's finish
  wakes its root through the root's inbox.
- **Levers are service-level only:** `opencode2_core/levers.py`'s `CATALOG` is
  the one prose home for each lever, and both OpenCode 2 artifacts bake the
  same roster. Turn-level failures belong to the engine-backed mock's script
  helpers, not this artifact.
- **Held to blizzard's prompts:** a `needs_blizzard` test plans every prompt in
  blizzard's `opencode_v2/compatibility/script.py` and checks each yields the
  tool calls and reply the proof expects.

## OpenCode 2 engine-backed mode

`mock-opencode-2 emit-engine` writes OpenCode 2's engine-backed mock service:
the managed service the CLI-surface artifact answers as, with the same
registration, `/api/*` surface, rules, records, and levers, but a turn's prompt
*is* its program. It is what blizzard's OpenCode 2 driver and runner are tested
against in place of a real service and a real token.

- **The contract:** `mock-opencode-2 emit-engine --out <path> [--version <v>]
  [--lever NAME]...`, validated as `emit` is. The artifact is `opencode2_core/`
  and `opencode2_engine/` zipped side by side, stdlib-only, run under a bare
  system `python3`.
- **A turn is a child process.** Each turn's prompt runs as a Python script
  (whole-message: every line is the program) in a process of its own, started
  by the artifact's internal `turn` verb in the session's directory, under the
  session's environment (or the service's, for a session with none, as every
  child session is). The script talks to the service over a pipe, one JSON
  object per line each way: every text and reasoning part it writes, each
  step's usage, and every tool call, which the service judges before the
  script may run it. The pipe is moved off the script's stdout before it runs,
  so nothing it prints can forge a message.
- **The turn's end.** A script that runs out ends its turn `succeeded`; one that
  raises fails it, the error naming what raised; a failure helper fails it with
  its provider error, recorded on the step it ended in and on the idle. An
  interrupt ends the script's process group, and with it every command the
  script started; a steer ends the script at its next tool call or step and
  runs as the turn's next script. A restarted service runs the interrupted
  turn's script again, `resumed()` true.
- **Permission rules are enforced, not recorded.** A call a rule denies by
  pattern fails with `permission.rejected` and the script carries on with the
  refusal; a tool whose last matching rule denies every resource is withheld,
  so no call is made; an `ask` raises a permission request and holds the call
  until it is answered, an `always` reply saving its pattern as an allow rule.
- **Children run their own scripts.** `subagent(script)` starts a `general`
  child session whose turn runs `script` in a child process of its own. A
  background child that outlives its root's turn wakes the root through its
  inbox, to run the `wake` script the call named. That script lives only in
  the running service's memory: a child that finishes after a restart wakes
  its root to an empty step, and a restart during a wake turn does not resume
  it.
- **Usage is scripted.** `usage(...)` sets the current step's tokens and cost;
  a step without it gets the same generated figures the CLI-surface artifact
  reports.
- **Fenced.** Executing caller-supplied code is the feature, so a turn's script
  runs only under the exec engine's two-factor fence (below): `BLIZZARD_MOCK_HARNESS_FENCE=1`
  in the environment the script would run under, and a
  `.blizzard-mock-harness-fence` marker in the session's directory or above.
  A turn outside the fence fails without running anything.
- **Levers are service-level; turn-level failures are helpers.** The service
  bakes the same lever roster as the CLI-surface artifact. Provider refusal,
  usage limits, overload, an unknown model or variant, permission and form
  requests, and a child that wakes its root are script helpers, below.
- **Held to the corpus:** `tests/test_opencode2_corpus_conformance.py`
  (`needs_blizzard`) drives both OpenCode 2 artifacts and holds every session,
  message, part, tool state, event, model, permission request, form, and error
  they answer with to the committed 2.1.4 corpus's samples of its kind. The
  keys the mock does not mint are declared there, each with why.

The helpers bound into a turn script's namespace, in summary —
`opencode2_engine/turn/helpers.py` owns their exact signatures and defaults:

| Helper | Effect |
|--------|--------|
| `text(content)` / `reasoning(content)` | Add a text or reasoning part to the current step; the reply is the last step's text. |
| `usage(input=, output=, reasoning=, cache_read=, cache_write=, cost=)` | Set the current step's tokens and cost. |
| `step()` | End the current step, finished `tool-calls`, and start the next. |
| `shell(command, timeout=None) -> ToolResult` | Run `command` under `/bin/sh` once the rules allow it. |
| `read(path) -> ToolResult` | Read a file once the rules allow it. |
| `tool(name, tool_input=None, resource="*", output="ok") -> ToolResult` | Record a call of any other tool — an MCP tool, say — once the rules allow it. |
| `permission_request(action, resource) -> ToolResult` | Call `action` through a permission request, whatever the rules would allow. |
| `subagent(script, background=False, description="", wake=None) -> ToolResult` | Run `script` in a child session; a background child's finish runs `wake` in this session. |
| `form(title, fields=None)` | Raise a form and hold the turn until it is cancelled; returns the form as listed. |
| `provider_refusal()` / `usage_limit(retry_after_ms, kind=)` / `overload()` | End the turn with that provider error. |
| `unknown_model()` / `unknown_variant()` | End the turn as the session's model or variant not routing. |
| `hang()` | Hold the turn until it is interrupted. |
| `resumed()` / `wake_result()` / `session_id()` / `model()` | Read the turn: a restart's resume, the waking child's text, the session, its model. |

A `ToolResult`'s `status` is `completed`, `failed`, `refused`, or `withheld`;
`output`, `exit_code`, and a subagent's child `session` ride along.

## The fence

Scoped to the exec-based modes (`run_prompt`/`assert_fenced`) — `run`, on all
three facades. The OpenCode CLI-surface mode ("OpenCode CLI-surface mode"
above) never executes caller-supplied code, so it imports none of this and
runs with no `BLIZZARD_MOCK_HARNESS_FENCE` env var and no fence marker file;
what gates it instead is that an emitted artifact is only ever written to a
caller-named path, never installed, never on `PATH`. OpenCode 2's CLI-surface
artifact is gated the same way. OpenCode 2's engine-backed mock does execute
caller-supplied code, so it holds each turn's script to the same two factors,
checked by its own stdlib-only copy (`opencode2_engine/domain/fence.py`, its
constants pinned equal to the engine's by a test).

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

## Runner selftest

`blizzard runner selftest` drives the real adapter with English prompts, not scripts. Past the fence, a spawn whose
`BLIZZARD_LEASE_ID` is `selftest` (`engine.SELFTEST_LEASE_ID`) and whose prompt carries no `<behavior-script>` tag is
answered as a compliant agent would: it commits `SELFTEST.txt` (`ok`) in the acquired worktree. The session remembers
the identity, because a resume — the judge call included — arrives without it; a later prose resume answers
`<Choice>pass</Choice>` when `SELFTEST.txt` is tracked, else `<Choice>fail</Choice>`. The key is the identity, never the
prompt's wording. Any other prose prompt is a script error, as before, and the fence is checked first, so the
responder never runs unfenced.

The OpenCode 2 engine-backed mock answers the selftest the same way (`opencode2_engine/domain/selftest.py`): the first
prose turn whose session environment carries the selftest's `BLIZZARD_LEASE_ID` commits `SELFTEST.txt` and makes the
session the selftest's for the service's lifetime, and every later prose turn on it answers the choice, with or without
the identity. A tagged prompt is always the script it holds and marks nothing.

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
the `blizzard` repo's `tests/service/runner/runner.spec.ts`,
`TRANSCRIPT_BUILD`, for the pattern and the why in full).

| Helper | Effect |
|--------|--------|
| `apply_diff(diff)` | `git apply` a unified diff to the worktree (real files). |
| `commit(message) -> sha` | Real `git commit -A`; returns the new sha. |
| `tool_call(name, tool_input=None, output="ok")` | Record a tool call that does nothing else — the transcript pair and the `PostToolUse` fire, no git. For choreographing a deterministic tool timeline. |
| `verdict(choice, assessment="")` | Emit `<Choice>{choice}</Choice>` + assessment as the turn's result. |
| `ask(question, options=None)` | Record the ask, optionally shell out to `$BLIZZARD_RUNNER_ASK_CMD`, emit the tagged `<Ask …>` result, and **exit the turn**. |
| `hang()` | Block until killed (stall/heartbeat/REAP testing). A `SIGINT` ends the turn as `error_during_execution` with a synthesized envelope; a `SIGTERM`, `SIGHUP`, or `SIGKILL` leaves no envelope. |
| `crash(hard=False)` | Die without a verdict — soft (error run, exit 1) or `hard` (`os._exit`). |
| `state()` | The `SessionState` — `state().last_ask`, `state().last_answer`. |
| `answer()` | The resume message this turn was resumed with — a tagged resume's **prose**, so a script never reads its own source back; an untagged one's raw message, as always. |
| `permission_denial(name, patterns=None, *, permission_id=None)` | Stage an OpenCode `permission` event for a denied request — `patterns` defaults to a single wildcard. **OpenCode-only**, part of the misbehaviour plane below. |
| `interrupt_tool(name, tool_input=None, *, error="interrupted", call_id=None)` | Stage a `tool_use` event whose part's state is `"error"` — how an interrupted or denied tool call reads on OpenCode's wire. **OpenCode-only.** |
| `malformed_record(line=None)` | Stage a raw line in the OpenCode JSONL stream the real parser rejects — defaults to invalid JSON; pass `line` for a different rejection shape. **OpenCode-only.** |

### The misbehaviour plane

`permission_denial`, `interrupt_tool`, and `malformed_record` stage a raw,
pre-rendered JSONL line onto the active wire's own `wire_events` list — owned
by `OpenCodeRunWire` (`facades/opencode.py`), the only wire that carries one —
in call order, regardless of which control-flow path ends the turn. `render`
splices them in just before the turn's own closing text/error record. Calling
any of the three from a facade whose wire has no JSONL event stream (Claude
Code, Codex) raises loudly rather than silently no-op'ing — checked via a
duck-typed marker (`carries_opencode_wire_events`) so `helpers.py` never has
to import a facade.

## Binaries

Each facade registers a `[project.scripts]` binary:

| Binary | Facade | Surface |
|--------|--------|---------|
| `mock-claude-code` | `facades.claude_code:main` | `-p [--output-format json] [--session-id <id>] [--resume <id>] [--settings <path>] [--permission-mode <mode>] [--model <name>] [--effort <level>] "<script>"`; single `{"type":"result", …}` JSON envelope. `--permission-mode` is accepted but inert. Also `--version` — intercepted before argparse, prints the pinned `2.1.278 (Claude Code)` and exits 0, the runner's health probe's own shape. |
| `mock-codex` | `facades.codex:main` | `exec [--json] [resume <id>] "<script>"`; JSONL event stream, self-assigned session. Also `app-server` — a JSON-RPC-over-stdio double for `codex app-server`'s `initialize`/`account/read` exchange, reading and rotating `$CODEX_HOME/auth.json`; unfenced, and runs no behavior script. |
| `mock-opencode` | `facades.opencode:main` | `run [--session <id>] [--model <name>] [--variant <v>] [--auto] "<script>"`; JSONL event stream (`step_start`/`text`/`step_finish`, or `error`), server-assigned session on a fresh mint. A bare `--session <id>` with no `run` and no script is the interactive-takeover shape — answered without driving the engine. Also `emit --out <path> [--lever NAME]...` — see "OpenCode CLI-surface mode" above. |
| `mock-opencode-2` | `facades.opencode2:main` | `emit` and `emit-engine`, each `--out <path> [--version <v>] [--lever NAME]...` — see "OpenCode 2 CLI-surface mode" and "OpenCode 2 engine-backed mode" above. |

Tests: `tests/test_harness_smoke.py` (fence, verdict, real commit, ask→resume
state, crash, hang, the `<behavior-script>` tag's three cases — tagged, untagged
legacy, malformed — whole-message mode — including a tag mention inert inside a
string literal, and an empty module body failing loudly — and the Claude Code
JSON envelope + fence-refusal exit) and `tests/test_harness_hooks.py` (the hook
seam: the lifecycle fire points, the exits that fire nothing, and real
`--settings` hook execution against stub shell commands).
`tests/test_opencode_cli_surface.py` covers the CLI-surface mode instead: the
lever roster, `emit`'s validate-before-write contract, the emitted artifact
running unfenced under a bare system `python3` with no `blizzard_mock` import
reachable, and `run`'s output staying byte-identical to before `emit` existed.
`tests/test_opencode2_cli_surface.py` covers OpenCode 2's artifact: the emit contract,
stdlib-only modules, the registered service and its auth, a turn with a child, a
restart, every lever, and the prompt plans against blizzard's proof prompts.
`tests/test_opencode2_engine.py` covers the engine-backed mock: the emit
contract, the fence, a script's steps, usage, process, directory, and
environment, enforced rules, interrupt, steer, forms, child sessions, every
failure helper, the levers it acts on, and takeover.
`tests/test_opencode2_corpus_conformance.py` holds both OpenCode 2 artifacts to
the committed 2.1.4 corpus.
`tests/test_codex_app_server.py` covers `app-server` instead: the id-matched
response with an interleaved notification, a plain read, a refreshing
rotation's atomic rewrite and audit log, a missing credential file, and two
concurrent rotations racing the same lock without corrupting the file.
