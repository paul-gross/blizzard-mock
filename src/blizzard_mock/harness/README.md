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

- `engine.py` — the shared `exec()` engine + the environment fence. All three
  facades call into it; it owns the session state file and the spawn env.
- `helpers.py` — the terse helper library the behavior scripts import:
  `ask(...)`, `apply_diff(...)`, `commit(...)`, `verdict(...)`, `hang(...)`,
  `crash()` — with raw Python underneath for the weird cases.
- `facades/` — one module per harness, each a thin CLI/wire surface over the
  engine. `claude_code.py` ships now (binary `mock-claude-code`); `codex.py` and
  `opencode.py` are added by the Build step, sharing the engine and differing
  only in invocation shape / output format / exit + resume semantics.

## Binary

`mock-claude-code` → `blizzard_mock.harness.facades.claude_code:main`. The Codex
and OpenCode facade binaries are reserved for the Build step (add them to
`[project.scripts]` alongside their facade modules).

## Build-step plug points

- Fill `engine.py` (exec + fence + session state), `helpers.py` (the six-plus
  helpers), and `facades/claude_code.py:main` (real Claude-Code CLI surface).
- Add `facades/codex.py` and `facades/opencode.py` with their binaries.
- Owns test file `tests/test_harness_smoke.py`.
