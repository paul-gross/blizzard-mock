"""How a session's permission rules judge a tool call: the last rule whose action and resource match wins, and a tool
whose last matching rule denies every resource is withheld from the model altogether."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

type Rule = Mapping[str, object]


def wildcard(pattern: str, text: str) -> bool:
    expression = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    return re.fullmatch(expression, text, re.DOTALL) is not None


def evaluate(rules: Sequence[Rule], action: str, resource: str) -> str:
    """``allow``, ``deny``, or ``ask``; a call no rule matches is allowed."""
    effect = "allow"
    for rule in rules:
        if wildcard(str(rule.get("action", "")), action) and wildcard(str(rule.get("resource", "")), resource):
            effect = str(rule.get("effect", "allow"))
    return effect


def withheld(rules: Sequence[Rule], tool: str) -> bool:
    matching = [rule for rule in rules if wildcard(str(rule.get("action", "")), tool)]
    return bool(matching) and matching[-1].get("resource") == "*" and matching[-1].get("effect") == "deny"


def save_pattern(command: str) -> str:
    """The pattern an ``always`` reply would save: the command's first word and a wildcard."""
    return command.split(" ", 1)[0] + " *"


__all__ = ["Rule", "evaluate", "save_pattern", "wildcard", "withheld"]
