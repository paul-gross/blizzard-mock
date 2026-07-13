"""Smoke coverage for the mock coding-harness engine + facades. Grow (Build step)."""

from __future__ import annotations

from blizzard_mock.harness import engine, helpers
from blizzard_mock.harness.facades import claude_code


def test_claude_code_facade_prints_usage(capsys) -> None:
    claude_code.main()
    out = capsys.readouterr().out
    assert "mock-claude-code" in out


def test_helper_surface_is_present() -> None:
    # The terse helper library the behavior scripts import (stubs for now).
    for name in ("ask", "apply_diff", "commit", "verdict", "hang", "crash"):
        assert callable(getattr(helpers, name))


def test_engine_exposes_fence_seam() -> None:
    assert engine.FENCE_ENV_VAR
    assert callable(engine.assert_fenced)
    assert callable(engine.run_prompt)
