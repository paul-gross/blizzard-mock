"""``turn``: the child process one turn's script runs in. It reads the turn from the service, runs the script its
prompt holds with the helpers bound, and reports how it ended — ``end`` when the script ran out, ``fail`` when it
raised.

The pipe to the service is moved off file descriptors 0 and 1 before the script runs, so nothing the script or a
command it starts reads or prints can touch the conversation: stdin is empty, stdout lands on stderr."""

from __future__ import annotations

import ast
import os
import sys
import traceback

from ..domain import failures
from ..domain.program import program
from . import helpers
from .channel import Channel, ServiceGoneError


def main() -> int:
    requests = os.fdopen(os.dup(1), "w", encoding="utf-8")
    replies = os.fdopen(os.dup(0), "r", encoding="utf-8")
    os.dup2(2, 1)
    with open(os.devnull, "rb") as nothing:
        os.dup2(nothing.fileno(), 0)
    sys.stdout, sys.stdin = sys.stderr, open(os.devnull, encoding="utf-8")  # noqa: SIM115 — lives as long as the turn
    channel = Channel(requests, replies)
    try:
        header = channel.header()
        helpers.bind(channel, header)
        source = program(str(header.get("script", "")))
        tree = ast.parse(source, "<behavior-script>")
        if not tree.body:
            channel.send("fail", error=failures.script_failed("the behavior script is empty"))
            return 0
        exec(compile(tree, "<behavior-script>", "exec"), helpers.namespace())
    except (helpers.TurnEnded, ServiceGoneError):
        return 0
    except Exception as exc:
        traceback.print_exc()
        channel.send("fail", error=failures.script_failed(f"{type(exc).__name__}: {exc}"))
        return 0
    channel.send("end")
    return 0


__all__ = ["main"]
