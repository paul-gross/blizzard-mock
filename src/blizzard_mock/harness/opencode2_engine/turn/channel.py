"""The pipe a turn's child process talks to the service over: one JSON object per line each way. The child writes a
request on stdout; a request that needs an answer — a tool call, a child session, a form — blocks on the service's
reply on stdin. The service never writes unasked."""

from __future__ import annotations

import json
import sys
from typing import IO, cast

type Json = dict[str, object]


class ServiceGoneError(RuntimeError):
    """The service closed the pipe: it ended the turn, and the child should exit."""


class Channel:
    def __init__(self, requests: IO[str] | None = None, replies: IO[str] | None = None) -> None:
        self._requests = requests if requests is not None else sys.stdout
        self._replies = replies if replies is not None else sys.stdin

    def header(self) -> Json:
        return self._read()

    def send(self, op: str, **fields: object) -> None:
        self._requests.write(json.dumps({"op": op, **fields}) + "\n")
        self._requests.flush()

    def ask(self, op: str, **fields: object) -> Json:
        self.send(op, **fields)
        return self._read()

    def _read(self) -> Json:
        line = self._replies.readline()
        if not line:
            raise ServiceGoneError("the service closed the turn")
        decoded: object = json.loads(line)
        if not isinstance(decoded, dict):
            raise ServiceGoneError(f"the service answered a non-object: {line!r}")
        return cast("dict[str, object]", decoded)


__all__ = ["Channel", "ServiceGoneError"]
