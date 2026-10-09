"""The hub's real GitHub conformer against this stub IdP — the provider identity a code
exchange resolves to carries every verified address the levered profile lists.

The conformer runs out of process in the sibling ``blizzard`` worktree's own venv
(``blizzard-mock`` never installs ``blizzard``); the IdP is served in process on a bound
``uvicorn`` socket, the same arrangement as ``tests/test_real_runner_against_mock_hub.py``.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn

from blizzard_mock.idp.app import create_app

#: Runs the sibling ``blizzard`` checkout's own interpreter; CI deselects the marker
#: (`mise run test -- -m "not needs_blizzard"`) since a standalone checkout has no sibling.
pytestmark = pytest.mark.needs_blizzard

#: The same sibling-resolution convention as ``test_wire_parity.py``'s ``_BLIZZARD``.
_BLIZZARD = Path(os.environ.get("BLIZZARD_SOURCE") or Path(__file__).resolve().parents[2] / "blizzard")
_BLIZZARD_PYTHON = _BLIZZARD / ".venv" / "bin" / "python"

#: Exchanges one code through the real conformer and prints the identity it resolves to.
_EXCHANGE = """
import json, sys
import httpx
from blizzard.hub.auth.oauth.internal.github_provider import GithubProvider

base, code = sys.argv[1], sys.argv[2]
with httpx.Client() as client:
    provider = GithubProvider(
        name="github", display_name="GitHub", client_id="cid", client_secret="secret",
        http_client=client, web_base=base, api_base=base,
    )
    identity = provider.exchange(code=code, redirect_uri="http://cb.test/callback")
print(json.dumps({
    "subject": identity.subject,
    "email": identity.email,
    "email_verified": identity.email_verified,
    "verified_emails": list(identity.verified_emails),
    "emails_authoritative": identity.emails_authoritative,
}))
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def _served_idp() -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10.0
        while not server.started:
            assert time.monotonic() < deadline, f"stub IdP never started on port {port}"
            time.sleep(0.05)
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _exchange_through_the_real_conformer(base: str, profile: dict[str, object]) -> dict[str, object]:
    assert _BLIZZARD_PYTHON.is_file(), (
        f"no sibling blizzard venv at {_BLIZZARD_PYTHON} — this proves the REAL GitHub conformer "
        "against the stub IdP, not a skip; set $BLIZZARD_SOURCE if the worktree lives elsewhere"
    )
    with httpx.Client(base_url=base) as client:
        assert client.put("/_levers/profile", json=profile).status_code == 200
        authorize = client.get(
            "/login/oauth/authorize",
            params={"redirect_uri": "http://cb.test/callback", "state": "s1", "client_id": "cid"},
            follow_redirects=False,
        )
    code = parse_qs(urlparse(authorize.headers["location"]).query)["code"][0]
    proc = subprocess.run(
        [str(_BLIZZARD_PYTHON), "-c", _EXCHANGE, base, code], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_real_github_conformer_reports_every_verified_address() -> None:
    with _served_idp() as base:
        identity = _exchange_through_the_real_conformer(
            base,
            {
                "subject": "42",
                "handle": "ada",
                "email": "ada@example.com",
                "email_verified": True,
                "secondary_emails": [
                    {"email": "ada@work.example", "verified": True},
                    {"email": "ada@old.example", "verified": False},
                ],
            },
        )

    assert identity == {
        "subject": "42",
        "email": "ada@example.com",
        "email_verified": True,
        "verified_emails": ["ada@example.com", "ada@work.example"],
        "emails_authoritative": True,
    }


def test_the_real_github_conformer_reports_a_verified_secondary_beside_an_unverified_primary() -> None:
    with _served_idp() as base:
        identity = _exchange_through_the_real_conformer(
            base,
            {
                "subject": "42",
                "handle": "ada",
                "email": "ada@example.com",
                "email_verified": False,
                "secondary_emails": [{"email": "ada@work.example", "verified": True}],
            },
        )

    assert identity["email"] == "ada@example.com"
    assert identity["email_verified"] is False
    assert identity["verified_emails"] == ["ada@work.example"]
