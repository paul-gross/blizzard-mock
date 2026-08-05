"""Per-harness CLI/wire facades over the shared exec engine.

One module per coding harness the seam accepts (``claude_code``, ``codex``,
``opencode``): invocation shape, output format, exit behavior, and resume
semantics over :mod:`blizzard_mock.harness.engine`.
"""
