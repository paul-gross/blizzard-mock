"""Deterministic mock usage/cost synthesis (blizzard epic #57, phase 1 of #58).

The mock stands in for a real coding harness, so it must emit the same two usage
carriers a real Claude Code run does — a result envelope's ``usage`` + ``total_cost_usd``,
and per-message ``usage`` on every assistant transcript record — so the runner
adapter's ``parse_usage``/``sum_transcript_usage`` seam (``blizzard/runner/harness/
internal/claude_code_adapter.py``) is exercised against realistic shapes with no
tokens spent.

The **figures themselves are illustrative, not a pricing table**: blizzard's own
architecture rule is that cost always comes from the harness's own ``total_cost_usd``,
never a locally-maintained schedule — this module's rates exist only so the mock's
envelope carries *a* dollar figure to prove the wire, and are deliberately **not**
shared with, or asserted equal to, any real Claude pricing. Deterministic (a pure
function of the rendered text's length, no randomness), so successive calls in one
run vary slightly rather than repeating an identical constant, and a test asserting
against them is reproducible.

Not a coding-harness facade itself; shared plumbing for :mod:`.claude_code`'s wire
and :mod:`._transcript`'s writer — the two places that mint Claude-Code-shaped
usage-bearing records.
"""

from __future__ import annotations

from collections.abc import Mapping

#: The model every mock-claude-code usage-bearing record carries — mirrors the
#: runner adapter's pinned default (``blizzard.runner.harness.internal.
#: claude_code_adapter.DEFAULT_WORKER_MODEL``).
MOCK_MODEL = "claude-opus-4-8"

#: Illustrative per-token rates used only to derive the mock's ``total_cost_usd`` —
#: not tied to any real Claude pricing (see module docstring).
_INPUT_RATE_USD = 3.0e-6
_OUTPUT_RATE_USD = 1.5e-5
_CACHE_READ_RATE_USD = 3.0e-7
_CACHE_CREATE_RATE_USD = 3.75e-6

#: A fixed cache footprint every synthesized usage object carries, so the mock's
#: token split exercises all four classes the real envelope's ``usage`` object
#: does, not just the two that scale with the rendered text.
_CACHE_READ_TOKENS = 500
_CACHE_CREATE_TOKENS = 20


def synthesize_usage_tokens(text: str, *, base_input: int = 200, base_output: int = 50) -> dict[str, int]:
    """Token counts for ``text``, scaled by its length off the given bases.

    ``base_input``/``base_output`` let a smaller mid-turn tool-call record read as
    cheaper than the turn's final text record, mirroring how a real turn's later
    tool calls see a shorter completion than its closing message.
    """
    return {
        "input_tokens": base_input + len(text) // 4,
        "output_tokens": base_output + len(text) // 8,
        "cache_read_input_tokens": _CACHE_READ_TOKENS,
        "cache_creation_input_tokens": _CACHE_CREATE_TOKENS,
    }


def synthesize_cost_usd(usage: Mapping[str, int]) -> float:
    """A deterministic, illustrative dollar figure off ``usage``'s token counts.

    Rounded to the same six-decimal precision real ``total_cost_usd`` figures use.
    Only the result-envelope wire carries a cost figure at all — a per-message
    transcript record never does (real Claude Code messages carry no per-message
    cost), so callers minting a transcript record use
    :func:`synthesize_usage_tokens` alone.
    """
    cost = (
        usage["input_tokens"] * _INPUT_RATE_USD
        + usage["output_tokens"] * _OUTPUT_RATE_USD
        + usage["cache_read_input_tokens"] * _CACHE_READ_RATE_USD
        + usage["cache_creation_input_tokens"] * _CACHE_CREATE_RATE_USD
    )
    return round(cost, 6)
