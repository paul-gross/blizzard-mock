"""Deterministic mock usage/cost synthesis (epic #57, phase 1 of #58).

Emits illustrative ``usage``/``total_cost_usd`` figures, tied to no real
Claude pricing, as a pure deterministic function of the rendered text's
length — so a test asserting against them is reproducible.
"""

from __future__ import annotations

from collections.abc import Mapping

#: The model every mock-claude-code usage-bearing record carries, mirroring
#: the real runner adapter's pinned default.
MOCK_MODEL = "claude-opus-4-8"

#: Illustrative per-token rates used only to derive the mock's ``total_cost_usd`` —
#: not tied to any real Claude pricing (see module docstring).
_INPUT_RATE_USD = 3.0e-6
_OUTPUT_RATE_USD = 1.5e-5
_CACHE_READ_RATE_USD = 3.0e-7
_CACHE_CREATE_RATE_USD = 3.75e-6

#: A fixed cache footprint every synthesized usage object carries, so all four
#: usage classes are exercised, not just the two that scale with text length.
_CACHE_READ_TOKENS = 500
_CACHE_CREATE_TOKENS = 20


def synthesize_usage_tokens(text: str, *, base_input: int = 200, base_output: int = 50) -> dict[str, int]:
    """Token counts for ``text``, scaled by its length off the given bases.

    ``base_input``/``base_output`` let a smaller mid-turn tool-call record read as
    cheaper than the turn's final text record.
    """
    return {
        "input_tokens": base_input + len(text) // 4,
        "output_tokens": base_output + len(text) // 8,
        "cache_read_input_tokens": _CACHE_READ_TOKENS,
        "cache_creation_input_tokens": _CACHE_CREATE_TOKENS,
    }


def synthesize_cost_usd(usage: Mapping[str, int]) -> float:
    """A deterministic, illustrative dollar figure off ``usage``'s token counts.

    Rounded to the same six-decimal precision real ``total_cost_usd`` figures
    use.
    """
    cost = (
        usage["input_tokens"] * _INPUT_RATE_USD
        + usage["output_tokens"] * _OUTPUT_RATE_USD
        + usage["cache_read_input_tokens"] * _CACHE_READ_RATE_USD
        + usage["cache_creation_input_tokens"] * _CACHE_CREATE_RATE_USD
    )
    return round(cost, 6)
