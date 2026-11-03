"""The registration file a listening service writes, readable only by its owner, and removed when it stops."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def write(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".tmp")
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(record, handle)
    os.replace(staged, path)


def read(path: Path) -> dict[str, object] | None:
    try:
        loaded = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def remove_if_owned(path: Path, registration_id: str) -> None:
    current = read(path)
    if current is not None and current.get("id") == registration_id:
        path.unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def call(record: dict[str, object], method: str, path: str, body: object | None = None) -> object:
    """One authenticated request to the registered service; raises ``OSError`` when it does not answer."""
    token = base64.b64encode(f"opencode:{record['password']}".encode()).decode()
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{record['url']}{path}", data=data, method=method)
    request.add_header("authorization", f"Basic {token}")
    if data is not None:
        request.add_header("content-type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise OSError(f"{method} {path} answered {exc.code}") from exc
    return json.loads(raw) if raw else None


__all__ = ["call", "pid_alive", "read", "remove_if_owned", "write"]
