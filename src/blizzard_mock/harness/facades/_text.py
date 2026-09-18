"""A plain-text wire — the engine's default when no facade wire is supplied.

Not a coding-harness facade; a minimal renderer for direct
:func:`~blizzard_mock.harness.engine.run_prompt` use and tests. Real facades
supply their own vendor-shaped wire.
"""

from __future__ import annotations

from blizzard_mock.harness.engine import RunResult


def render_ask_text(result: RunResult) -> str:
    """The tagged ask shape adapters parse, symmetric with the ``<Choice>`` verdict."""
    ask = result.ask
    if ask is None:
        return result.text
    options = "|".join(ask.options)
    return f'<Ask options="{options}">{ask.question}</Ask>'


class PlainTextWire:
    """Render just the result text (or the tagged ask), one trailing newline."""

    def render_identity(self, session_id: str) -> str | None:
        """No identity preamble — this wire has no vendor handshake to stand in for."""
        return None

    def render(self, result: RunResult) -> str:
        text = render_ask_text(result) if result.subtype == "ask" else result.text
        return f"{text}\n"
