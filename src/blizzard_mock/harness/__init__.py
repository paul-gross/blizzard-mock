"""Mock coding-harness engine and per-harness facades.

**The prompt is the program**: the mock turns a prompt into behavior via
``exec()`` rather than an LLM. The three per-harness mocks (Claude Code, Codex,
OpenCode) share this exec engine and differ only in their CLI/wire facade.
"""
