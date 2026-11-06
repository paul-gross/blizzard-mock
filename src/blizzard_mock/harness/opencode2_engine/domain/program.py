"""Which part of a turn's prompt is its program: the ``<behavior-script>`` blocks it holds on lines of their own, else
what follows the runner's preamble table, else the whole prompt. The engine artifact carries no other package, so the
split the other mock harnesses share is restated here, narrowed to what a turn's prompt can hold."""

from __future__ import annotations

import re
import textwrap

#: The header row the runner's worker preamble opens its field table with.
PREAMBLE_TABLE_HEADER = "| Field | Value |"
_OPEN = re.compile(r"^\s*<behavior-script>\s*$")
_CLOSE = re.compile(r"^\s*</behavior-script>\s*$")


def tagged(prompt: str) -> bool:
    """Whether ``prompt`` holds a ``<behavior-script>`` block opening on a line of its own."""
    return any(_OPEN.match(line) for line in prompt.splitlines())


def program(prompt: str) -> str:
    """The script ``prompt`` runs."""
    lines = prompt.splitlines()
    blocks: list[str] = []
    body: list[str] | None = None
    for line in lines:
        if body is None and _OPEN.match(line):
            body = []
        elif body is not None and _CLOSE.match(line):
            blocks.append(textwrap.dedent("\n".join(body)))
            body = None
        elif body is not None:
            body.append(line)
    if blocks:
        return "\n".join(blocks)
    start = next((i for i, line in enumerate(lines) if line.strip() == PREAMBLE_TABLE_HEADER), None)
    if start is None:
        return prompt
    end = start
    while end + 1 < len(lines) and lines[end + 1].lstrip().startswith("|"):
        end += 1
    return "\n".join(lines[end + 1 :]).lstrip("\n")


__all__ = ["PREAMBLE_TABLE_HEADER", "program", "tagged"]
