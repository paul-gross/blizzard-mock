"""Driving an emitted OpenCode 2 artifact from a test: emitting it through the facade, and its managed service under
an isolated home, called over HTTP with the password it registered."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, TypeGuard

type Json = dict[str, object]


def emit_cli(*args: str, verb: str = "emit") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "blizzard_mock.harness.facades.opencode2", verb, *args],
        capture_output=True,
        text=True,
    )


def artifact(tmp_path: Path, *levers: str, version: str | None = None, verb: str = "emit") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "opencode"
    args = ["--out", str(out), *(arg for lever in levers for arg in ("--lever", lever))]
    proc = emit_cli(*args, *(["--version", version] if version else []), verb=verb)
    assert proc.returncode == 0, proc.stderr
    return out


def bare_env(home: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
    }


class Service:
    """One emitted artifact's service under an isolated home, stopped on teardown."""

    def __init__(self, binary: Path, home: Path, extra_env: Mapping[str, str] | None = None) -> None:
        self.binary = binary
        self.env = {**bare_env(home), **(extra_env or {})}
        self.registration_path = home / ".local" / "state" / "opencode" / "service.json"

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.binary), *args], env=self.env, capture_output=True, text=True, timeout=60)

    def registration(self) -> Json:
        return json.loads(self.registration_path.read_text())

    def call(self, method: str, path: str, body: object | None = None, *, auth: bool = True) -> tuple[int, object]:
        record = self.registration()
        request = urllib.request.Request(
            f"{record['url']}{path}", data=json.dumps(body).encode() if body is not None else None, method=method
        )
        if auth:
            token = base64.b64encode(f"opencode:{record['password']}".encode()).decode()
            request.add_header("authorization", f"Basic {token}")
        request.add_header("content-type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=10) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            return exc.code, None

    @contextmanager
    def events(self) -> Generator[list[Json]]:
        """Subscribe to the event stream for the block's duration; the list fills with each event as it arrives."""
        record = self.registration()
        request = urllib.request.Request(f"{record['url']}/api/event")
        token = base64.b64encode(f"opencode:{record['password']}".encode()).decode()
        request.add_header("authorization", f"Basic {token}")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        stream = opener.open(request, timeout=30)
        received: list[Json] = []

        def pump() -> None:
            try:
                for line in stream:
                    if line.startswith(b"data: "):
                        received.append(json.loads(line[len(b"data: ") :]))
            except (OSError, ValueError):
                return

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        time.sleep(0.2)
        try:
            yield received
        finally:
            time.sleep(0.5)
            stream.close()

    def data(self, method: str, path: str, body: object | None = None) -> object:
        status, answer = self.call(method, path, body)
        assert 200 <= status < 300, (method, path, status)
        assert is_json(answer)
        return answer["data"]

    def open_session(
        self, session_id: str, directory: Path, rules: Sequence[Mapping[str, object]] | None = None
    ) -> None:
        permissions = [dict(rule) for rule in rules or ()]
        self.data(
            "POST",
            "/api/session",
            {"id": session_id, "location": {"directory": str(directory)}, "metadata": {}, "permissions": permissions},
        )

    def settle(self, session_id: str, timeout: float = 30) -> list[Json]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            active = self.data("GET", "/api/session/active")
            assert isinstance(active, dict)
            if session_id not in active:
                messages = self.data("GET", f"/api/session/{session_id}/message")
                assert is_list(messages)
                rows = objects(messages)
                assert len(rows) == len(messages)
                return rows
            time.sleep(0.2)
        raise AssertionError(f"{session_id} did not settle")


class Starter(Protocol):
    def __call__(
        self, binary: Path, home: Path | None = None, *, extra_env: Mapping[str, str] | None = None
    ) -> Service: ...


def is_json(value: object) -> TypeGuard[Json]:
    return isinstance(value, dict)


def is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def objects(value: object) -> list[Json]:
    return [item for item in value if is_json(item)] if is_list(value) else []


def tool_parts(messages: list[Json]) -> list[Json]:
    return [part for message in messages for part in objects(message.get("content")) if part.get("type") == "tool"]


def output(part: Json) -> str:
    state = part["state"]
    assert is_json(state)
    content = state.get("content")
    assert content is None or is_list(content)
    return "".join(str(item["text"]) for item in objects(content))
