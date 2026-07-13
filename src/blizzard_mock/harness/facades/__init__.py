"""Per-harness CLI/wire facades over the shared exec engine.

One module per coding harness the seam accepts. Each facade is a thin surface —
invocation shape, output format, exit behavior, resume semantics — over
:mod:`blizzard_mock.harness.engine`; the facade is exactly what the runner's
adapter layer is tested against.

- ``claude_code`` — the mock Claude Code facade (binary ``mock-claude-code``).
- ``codex``, ``opencode`` — reserved for the Build step.
"""
