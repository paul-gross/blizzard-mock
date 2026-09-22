"""The ``mock-codex app-server`` verb (blizzard#504) — a JSON-RPC-over-stdio double for
``codex app-server``, answering ``initialize`` and ``account/read`` the same shape the
real binary does (confirmed live against Codex 0.149.0, D1's tested assumption): the
matching response for an id-carrying request, an unsolicited notification interleaved in
between, exactly like the real server's own reply ordering. Lets blizzard's renewer
binding (``blizzard.runner.subscriptions.internal.openai_credential_renewer``) be proven
against a double, never a real login.

A refreshing ``account/read`` takes the same lock a concurrent vendor-style writer would
(``auth.json.lock``, ``fcntl.flock``) and rewrites ``auth.json`` atomically — a temp file
in the same directory, renamed over the target — so two writers racing this file never
leave a reader with a half-written one. Every rotation appends one audit line: the
rotating process's pid and a digest of the exact bytes it wrote, never a token itself —
proof, after a race, that the file's final digest matches the last logged write rather
than a third, unaccounted party's."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path

_METHOD_INITIALIZE = "initialize"
_METHOD_ACCOUNT_READ = "account/read"

# The rotated access token's own lifetime — long enough that a caller sampling right
# after a renewal never races it, short enough to look like a real one.
_ACCESS_TOKEN_LIFETIME_SECONDS = 3600


def run_app_server(*, auth_path: Path) -> int:
    """Read newline-delimited JSON-RPC messages from stdin until EOF, answering
    ``initialize`` and ``account/read`` on stdout — every other message a caller might
    send is echoed back with an empty result when it carries an ``id`` (never left
    unanswered), and ignored otherwise. Exits 0 once stdin closes: this mock never
    outlives its caller's own EOF, matching the one-shot seam that drives it."""
    audit_log_path = auth_path.with_name(auth_path.name + ".audit.log")
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        _handle(message, auth_path=auth_path, audit_log_path=audit_log_path)
    return 0


def _handle(message: dict[str, object], *, auth_path: Path, audit_log_path: Path) -> None:
    method = message.get("method")
    request_id = message.get("id")
    if method == _METHOD_INITIALIZE:
        _reply(request_id, {})
        # The real server interleaves unsolicited notifications between a request and
        # its own id-matched response — a caller must skip this, never mistake it for
        # the reply it is waiting on.
        _notify("sessionConfigured", {})
        return
    if method == _METHOD_ACCOUNT_READ:
        params = message.get("params")
        refresh = isinstance(params, dict) and bool(params.get("refreshToken"))
        account = _rotate(auth_path, audit_log_path) if refresh else _read_account(auth_path)
        _reply(request_id, {"account": account, "requiresOpenaiAuth": account is None})
        return
    if request_id is not None:
        _reply(request_id, {})


def _reply(request_id: object, result: dict[str, object]) -> None:
    _emit({"jsonrpc": "2.0", "id": request_id, "result": result})


def _notify(method: str, params: dict[str, object]) -> None:
    _emit({"jsonrpc": "2.0", "method": method, "params": params})


def _emit(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _read_account(auth_path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(auth_path.read_text())
    except (OSError, ValueError):
        return None
    tokens = data.get("tokens") if isinstance(data, dict) else None
    account_id = tokens.get("account_id") if isinstance(tokens, dict) else None
    return {"account_id": account_id} if isinstance(account_id, str) and account_id else None


def _rotate(auth_path: Path, audit_log_path: Path) -> dict[str, object] | None:
    lock_path = auth_path.with_name(auth_path.name + ".lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            return _rotate_locked(auth_path, audit_log_path)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _rotate_locked(auth_path: Path, audit_log_path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(auth_path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        return None
    account_id = tokens.get("account_id")
    new_refresh_token = secrets.token_hex(16)
    tokens["access_token"] = _issue_access_token(account_id if isinstance(account_id, str) else None)
    tokens["refresh_token"] = new_refresh_token
    data["tokens"] = tokens
    data["last_refresh"] = time.time()
    written = _write_atomic(auth_path, data)
    _append_audit(audit_log_path, written=written)
    return {"account_id": account_id} if isinstance(account_id, str) else None


def _write_atomic(path: Path, data: dict[str, object]) -> str:
    """A temp file in the same directory, renamed over the target — a concurrent reader
    (or a second writer racing the same lock) never observes a half-written file. Returns
    the exact text written, so the audit log can record its digest rather than re-reading
    the file back (which a still-later writer could have already replaced)."""
    text = json.dumps(data)
    directory = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=path.name + ".")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp_path, path)
    return text


def _append_audit(audit_log_path: Path, *, written: str) -> None:
    """One line per rotation: the writer's pid and a digest of the exact bytes it just
    wrote — never the token itself. After a race between two writers, the final file's
    own digest must equal whichever entry was appended last, proof the lock actually
    serialized the two read-modify-write cycles rather than merely looking like it did."""
    digest = hashlib.sha256(written.encode()).hexdigest()[:16]
    line = json.dumps({"pid": os.getpid(), "content_digest": digest, "at": time.time()})
    with open(audit_log_path, "a") as fh:
        fh.write(line + "\n")


def _issue_access_token(account_id: str | None) -> str:
    """A minimally-shaped unsigned JWT carrying a fresh ``exp`` — only the claim the
    real sampler and renewer bindings ever read."""
    header = _b64({"alg": "none"})
    payload = _b64({"sub": account_id or "mock-account", "exp": time.time() + _ACCESS_TOKEN_LIFETIME_SECONDS})
    return f"{header}.{payload}.mock-signature"


def _b64(obj: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
