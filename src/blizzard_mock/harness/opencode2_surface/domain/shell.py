"""The shell tool's tiny fixed grammar: ``;``-separated ``echo``, ``echo … > file``, ``touch``, and ``sleep``, with
``${NAME:-default}``, ``$NAME``, and ``$$`` expanded from the session's environment. Anything else is refused as
unsupported; nothing is handed to a real shell."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

_BRACED = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")
_BARE = re.compile(r"\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class Echo:
    text: str
    target: str | None = None


@dataclass(frozen=True)
class Touch:
    path: str


@dataclass(frozen=True)
class Sleep:
    seconds: float


type Segment = Echo | Touch | Sleep


class UnsupportedCommandError(ValueError):
    """The command is outside the grammar the fake shell answers."""


def expand(text: str, env: Mapping[str, str], pid: int) -> str:
    text = text.replace("$$", str(pid))
    text = _BRACED.sub(lambda m: env.get(m.group("name")) or (m.group("default") or ""), text)
    return _BARE.sub(lambda m: env.get(m.group("name"), ""), text)


def parse(command: str, env: Mapping[str, str], pid: int) -> tuple[Segment, ...]:
    segments: list[Segment] = []
    for raw in (part.strip() for part in command.split(";")):
        if not raw:
            continue
        verb, _, rest = raw.partition(" ")
        if verb == "echo":
            text, redirect, target = rest.partition(">")
            words = expand(text.strip(), env, pid)
            segments.append(Echo(words, expand(target.strip(), env, pid) if redirect else None))
        elif verb == "touch" and rest.strip():
            segments.append(Touch(expand(rest.strip(), env, pid)))
        elif verb == "sleep":
            try:
                segments.append(Sleep(float(rest.strip())))
            except ValueError as exc:
                raise UnsupportedCommandError(f"unsupported sleep: {raw}") from exc
        else:
            raise UnsupportedCommandError(f"unsupported command: {raw}")
    return tuple(segments)


__all__ = ["Echo", "Segment", "Sleep", "Touch", "UnsupportedCommandError", "expand", "parse"]
