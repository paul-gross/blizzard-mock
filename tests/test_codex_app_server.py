"""``mock-codex app-server`` (blizzard#504) — the JSON-RPC-over-stdio double for ``codex
app-server``'s ``initialize``/``account/read`` exchange, driven the way blizzard's renewer seam
drives it: both requests written to stdin, then closed. Not the exec-engine's "prompt is the
program" mode (``test_harness_smoke.py``): this verb answers a fixed protocol against
``$CODEX_HOME/auth.json`` and never runs a script, so it is unfenced."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from pathlib import Path

_INITIALIZE = '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "x"}}}\n'


def _account_read(*, refresh_token: bool) -> str:
    return (
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "account/read", "params": {"refreshToken": refresh_token}})
        + "\n"
    )


def _run_app_server(codex_home: Path, stdin: str, *, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "blizzard_mock.harness.facades.codex", "app-server"],
        input=stdin,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "CODEX_HOME": str(codex_home)},
        timeout=timeout,
    )


def _messages(stdout: str) -> list[dict]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


def _write_auth(path: Path, *, access_token: str = "old-access", refresh_token: str = "old-refresh") -> None:
    path.write_text(
        json.dumps({"tokens": {"access_token": access_token, "refresh_token": refresh_token, "account_id": "acct-1"}})
    )


def test_initialize_gets_an_id_matched_response_with_an_interleaved_notification(tmp_path: Path) -> None:
    _write_auth(tmp_path / "auth.json")

    proc = _run_app_server(tmp_path, _INITIALIZE)

    assert proc.returncode == 0
    messages = _messages(proc.stdout)
    assert messages[0] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    # A notification carries no id at all — never mistakable for the awaited response.
    assert "id" not in messages[1]
    assert messages[1]["method"]


def test_account_read_without_refresh_token_reports_the_account_and_writes_nothing(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path)
    before = auth_path.read_text()

    proc = _run_app_server(tmp_path, _INITIALIZE + _account_read(refresh_token=False))

    assert proc.returncode == 0
    response = next(m for m in _messages(proc.stdout) if m.get("id") == 2)
    assert response["result"]["requiresOpenaiAuth"] is True
    assert response["result"]["account"] == {"account_id": "acct-1"}
    assert auth_path.read_text() == before  # no rotation on a plain read
    assert not (tmp_path / "auth.json.audit.log").exists()


def test_account_read_with_refresh_token_rotates_both_tokens_atomically(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path)

    proc = _run_app_server(tmp_path, _INITIALIZE + _account_read(refresh_token=True))

    assert proc.returncode == 0
    response = next(m for m in _messages(proc.stdout) if m.get("id") == 2)
    assert response["result"]["requiresOpenaiAuth"] is True

    rotated = json.loads(auth_path.read_text())
    assert rotated["tokens"]["access_token"] != "old-access"
    assert rotated["tokens"]["refresh_token"] != "old-refresh"
    assert rotated["tokens"]["account_id"] == "acct-1"
    assert "last_refresh" in rotated

    audit_lines = (tmp_path / "auth.json.audit.log").read_text().splitlines()
    assert len(audit_lines) == 1
    entry = json.loads(audit_lines[0])
    assert entry["content_digest"]
    # Never the token itself — only a digest.
    assert rotated["tokens"]["refresh_token"] not in json.dumps(entry)
    # The final file's own digest matches the logged write byte-for-byte.
    assert entry["content_digest"] == hashlib.sha256(auth_path.read_bytes()).hexdigest()[:16]


def test_a_missing_auth_file_reports_a_null_account_without_crashing(tmp_path: Path) -> None:
    proc = _run_app_server(tmp_path, _INITIALIZE + _account_read(refresh_token=True))

    assert proc.returncode == 0
    response = next(m for m in _messages(proc.stdout) if m.get("id") == 2)
    assert response["result"]["requiresOpenaiAuth"] is True
    assert response["result"]["account"] is None


def test_two_concurrent_refreshes_never_corrupt_the_file_and_both_are_audited(tmp_path: Path) -> None:
    """The concurrent-writer proof (blizzard#504 Phase 2): two rotations racing the same lock
    never leave the file unparseable, and each lands its own audit line — proof the lock
    serializes the two read-modify-write cycles rather than merely looking like it does."""
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path)
    request = _INITIALIZE + _account_read(refresh_token=True)

    results: list[subprocess.CompletedProcess[str]] = []
    errors: list[BaseException] = []

    def _drive() -> None:
        try:
            results.append(_run_app_server(tmp_path, request))
        except BaseException as exc:  # surfaced to the main thread below
            errors.append(exc)

    threads = [threading.Thread(target=_drive) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert errors == []
    assert len(results) == 2
    assert all(r.returncode == 0 for r in results)

    # The file is valid JSON, throughout — never half-written by one writer mid-replace.
    final_bytes = auth_path.read_bytes()
    final = json.loads(final_bytes)
    assert final["tokens"]["refresh_token"] not in ("old-refresh",)
    assert final["tokens"]["account_id"] == "acct-1"

    audit_lines = (tmp_path / "auth.json.audit.log").read_text().splitlines()
    assert len(audit_lines) == 2
    entries = [json.loads(line) for line in audit_lines]
    digests = {entry["content_digest"] for entry in entries}
    assert len(digests) == 2  # two distinct rotations, not one writer clobbering the other silently
    # Every write matches a mock-codex pid, and the final file's digest matches whichever
    # entry was appended last — the lock serialized the cycles rather than merely seeming to.
    assert all("pid" in entry for entry in entries)
    last_logged = max(entries, key=lambda entry: entry["at"])
    assert last_logged["content_digest"] == hashlib.sha256(final_bytes).hexdigest()[:16]
