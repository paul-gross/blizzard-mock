"""Mock coding-harness engine and per-harness facades.

**The prompt is the program.** A real coding harness turns a prompt string into
behavior via an LLM; the mock turns it into behavior via ``exec()`` — the prompt
it receives *is* Python code, run in the acquired worktree with the spawn
environment. That makes agent behavior fully simulable with no injection side
channel and no real tokens.

The three per-harness mocks (Claude Code, Codex, OpenCode) **share this exec
engine** and differ only in their CLI/wire facade — the facade being exactly
what the runner's adapter layer is tested against (invocation shape, output
format, exit behavior, resume semantics).

Arbitrary code execution is the feature, and it is **fenced**: the engine
refuses to run unless test scaffolding marks the environment, so it can never
pass as a real harness binding.

See ``README.md`` for the full contract.
"""
