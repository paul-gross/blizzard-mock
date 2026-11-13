"""Token kinds, GitHub App installations, and per-route permissions (``blizzard-mock:unit-test``)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, cast

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from blizzard_mock.forge.domain.clock import FixedClock
from tests.forge_world import REPO, START, forge

URL = f"/repos/{REPO}"
APP_ID = 4242
INSTALLATION = 77


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY = _key()
OTHER_KEY = _key()
PUBLIC_PEM = (
    KEY.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
)


def _jwt(clock: FixedClock, *, key: rsa.RSAPrivateKey = KEY, iat: int = -60, exp: int = 540, iss: int = APP_ID) -> str:
    now = int(clock.now().timestamp())
    return jwt.encode({"iss": str(iss), "iat": now + iat, "exp": now + exp}, key, algorithm="RS256")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _tok(token: str) -> dict[str, str]:
    return {"Authorization": f"token {token}"}


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[TestClient, FixedClock]]:
    clock = FixedClock(START)
    with forge(tmp_path, clock) as client:
        yield client, clock


def _lever(client: TestClient, kind: str, **body: Any) -> None:
    assert client.post(f"/_levers/{kind}", json=body).status_code == 200


def _install(
    client: TestClient,
    permissions: dict[str, str] | None = None,
    repositories: list[str] | None = None,
) -> None:
    _lever(client, "register_app", app_id=APP_ID, public_key=PUBLIC_PEM)
    _lever(
        client,
        "register_installation",
        installation_id=INSTALLATION,
        app_id=APP_ID,
        repositories=[REPO] if repositories is None else repositories,
        permissions=permissions if permissions is not None else {"metadata": "read", "contents": "read"},
    )


def _mint(client: TestClient, clock: FixedClock, permissions: dict[str, str] | None = None) -> str:
    body = {} if permissions is None else {"permissions": permissions}
    resp = client.post(f"/app/installations/{INSTALLATION}/access_tokens", json=body, headers=_bearer(_jwt(clock)))
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


def test_every_answer_carries_a_request_id_and_off_by_default_nothing_authenticates(
    world: tuple[TestClient, FixedClock],
) -> None:
    client, _ = world
    ok = client.get(URL)
    assert ok.status_code == 200
    assert ok.json()["private"] is False
    assert ok.headers["X-GitHub-Request-Id"]
    assert client.get(URL, headers=_tok("anything")).status_code == 200
    ids = {client.get("/healthz").headers["X-GitHub-Request-Id"], client.get("/healthz").headers["X-GitHub-Request-Id"]}
    assert len(ids) == 2
    _lever(client, "unreachable")
    assert client.get(URL).headers["X-GitHub-Request-Id"]


def test_strict_tokens_refuse_unregistered_absent_and_revoked(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    _lever(client, "strict_tokens")
    assert client.get(URL).status_code == 401
    bad = client.get(URL, headers=_tok("nope"))
    assert bad.status_code == 401
    assert bad.json()["message"] == "Bad credentials"
    _lever(client, "register_token", token="c1", token_kind="classic", scopes=["repo", "read:org"])
    ok = client.get(URL, headers=_tok("c1"))
    assert ok.status_code == 200
    assert ok.headers["X-OAuth-Scopes"] == "repo, read:org"
    _lever(client, "revoke_token", token="c1")
    assert client.get(URL, headers=_tok("c1")).status_code == 401
    assert client.get("/healthz").status_code == 200


def test_strict_tokens_clear_like_any_state_lever(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    _lever(client, "strict_tokens")
    assert client.get(URL).status_code == 401
    client.delete("/_levers/strict_tokens")
    assert client.get(URL).status_code == 200


def test_classic_reaches_every_route_and_fine_grained_is_withheld_checks_on_a_private_repo(
    world: tuple[TestClient, FixedClock],
) -> None:
    client, _ = world
    _lever(client, "strict_tokens")
    _lever(client, "register_token", token="classic", token_kind="classic", scopes=["repo"])
    _lever(client, "register_token", token="fine", token_kind="fine-grained")
    checks = f"{URL}/commits/main/check-runs"
    assert client.get(checks, headers=_tok("classic")).status_code == 200
    assert client.get(checks, headers=_tok("fine")).status_code == 200
    _lever(client, "private_repository", repo=REPO)
    assert client.get(URL, headers=_tok("fine")).json()["private"] is True
    assert client.get(checks, headers=_tok("classic")).status_code == 200
    denied = client.get(checks, headers=_tok("fine"))
    assert denied.status_code == 403
    assert denied.json()["message"] == "Resource not accessible by personal access token"
    assert client.get(f"{URL}/pulls", headers=_tok("fine")).status_code == 200


def test_the_app_routes_check_the_jwt(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client)
    path = f"/app/installations/{INSTALLATION}"
    ok = client.get(path, headers=_bearer(_jwt(clock)))
    assert ok.status_code == 200
    assert ok.json() == {
        "id": INSTALLATION,
        "app_id": APP_ID,
        "permissions": {"metadata": "read", "contents": "read"},
        "suspended_at": None,
    }
    bad = {
        "other key": _jwt(clock, key=OTHER_KEY),
        "expired": _jwt(clock, iat=-700, exp=-100),
        "too long": _jwt(clock, exp=601),
        "future iat": _jwt(clock, iat=120),
        "unknown app": _jwt(clock, iss=1),
    }
    for name, token in bad.items():
        assert client.get(path, headers=_bearer(token)).status_code == 401, name
        assert client.post(f"{path}/access_tokens", json={}, headers=_bearer(token)).status_code == 401, name
    assert client.get(path).status_code == 401
    assert client.get("/app/installations/999", headers=_bearer(_jwt(clock))).status_code == 404


def test_a_jwt_is_judged_against_the_injected_clock(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client)
    token = _jwt(clock)
    clock.tick(600)
    assert client.get(f"/app/installations/{INSTALLATION}", headers=_bearer(token)).status_code == 401


def test_another_apps_installation_is_not_found(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client)
    _lever(client, "register_app", app_id=9, public_key=PUBLIC_PEM)
    resp = client.get(f"/app/installations/{INSTALLATION}", headers=_bearer(_jwt(clock, iss=9)))
    assert resp.status_code == 404


def test_mint_narrows_to_what_was_asked_and_refuses_what_is_not_granted(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client, {"metadata": "read", "contents": "write"})
    path = f"/app/installations/{INSTALLATION}/access_tokens"
    full = client.post(path, json={}, headers=_bearer(_jwt(clock)))
    assert full.json()["permissions"] == {"metadata": "read", "contents": "write"}
    assert full.json()["token"].startswith("ghs_")
    assert full.json()["expires_at"] == "2026-07-13T01:00:00Z"
    narrowed = client.post(path, json={"permissions": {"contents": "read"}}, headers=_bearer(_jwt(clock)))
    assert narrowed.status_code == 201
    assert narrowed.json()["permissions"] == {"contents": "read"}
    refused = client.post(path, json={"permissions": {"issues": "read"}}, headers=_bearer(_jwt(clock)))
    assert refused.status_code == 422
    assert "issues" in refused.json()["message"]
    assert (
        client.post(path, json={"permissions": {"contents": "admin"}}, headers=_bearer(_jwt(clock))).status_code == 422
    )


def test_a_suspended_installation_reads_but_will_not_mint(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client)
    _lever(client, "suspend_installation", installation_id=INSTALLATION)
    read = client.get(f"/app/installations/{INSTALLATION}", headers=_bearer(_jwt(clock)))
    assert read.json()["suspended_at"] == "2026-07-13T00:00:00Z"
    mint = client.post(f"/app/installations/{INSTALLATION}/access_tokens", json={}, headers=_bearer(_jwt(clock)))
    assert mint.status_code == 403
    _install(client)  # registering again replaces the record, restoring it
    assert _mint(client, clock)


def test_a_deleted_installation_is_not_found(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client)
    _lever(client, "delete_installation", installation_id=INSTALLATION)
    assert client.get(f"/app/installations/{INSTALLATION}", headers=_bearer(_jwt(clock))).status_code == 404


def test_a_minted_token_is_bounded_by_its_permissions(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client, {"metadata": "read", "contents": "write", "issues": "read"})
    _lever(client, "strict_tokens")
    token = _mint(client, clock, {"contents": "read"})
    assert client.get(URL, headers=_tok(token)).status_code == 200
    assert client.get(f"{URL}/commits/main", headers=_tok(token)).status_code == 200
    pulls = client.get(f"{URL}/pulls", headers=_tok(token))
    assert pulls.status_code == 403
    assert pulls.headers["X-Accepted-GitHub-Permissions"] == "pull_requests=read"
    assert pulls.json()["message"] == "Resource not accessible by integration"
    write = client.post(f"{URL}/git/refs", json={"ref": "refs/heads/x", "sha": "0" * 40}, headers=_tok(token))
    assert write.status_code == 403
    assert write.headers["X-Accepted-GitHub-Permissions"] == "contents=write"
    assert "X-OAuth-Scopes" not in pulls.headers


def test_write_covers_read_and_metadata_is_always_held(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client, {"issues": "write"})
    _lever(client, "strict_tokens")
    token = _mint(client, clock)
    assert client.get(f"{URL}/issues", headers=_tok(token)).status_code == 200
    assert client.get(URL, headers=_tok(token)).status_code == 200


def test_a_minted_token_expires_on_the_clock_and_honours_token_lifetime(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client)
    _lever(client, "strict_tokens")
    _lever(client, "token_lifetime", seconds=120)
    token = _mint(client, clock)
    clock.tick(119)
    assert client.get(URL, headers=_tok(token)).status_code == 200
    clock.tick(1)
    assert client.get(URL, headers=_tok(token)).status_code == 401


def test_default_lifetime_is_an_hour(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client)
    _lever(client, "strict_tokens")
    token = _mint(client, clock)
    clock.tick(3599)
    assert client.get(URL, headers=_tok(token)).status_code == 200
    clock.tick(1)
    assert client.get(URL, headers=_tok(token)).status_code == 401


@pytest.mark.parametrize("lever", ["suspend_installation", "delete_installation"])
def test_minted_tokens_die_with_their_installation(world: tuple[TestClient, FixedClock], lever: str) -> None:
    client, clock = world
    _install(client)
    _lever(client, "strict_tokens")
    token = _mint(client, clock)
    _lever(client, lever, installation_id=INSTALLATION)
    assert client.get(URL, headers=_tok(token)).status_code == 401


def test_a_removed_repository_is_not_found_to_the_installations_tokens(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client)
    _lever(client, "strict_tokens")
    token = _mint(client, clock)
    _lever(client, "remove_repository", installation_id=INSTALLATION, repo=REPO)
    assert client.get(URL, headers=_tok(token)).status_code == 404


def test_withdrawing_a_permission_refuses_tokens_already_minted(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client, {"metadata": "read", "contents": "read"})
    _lever(client, "strict_tokens")
    token = _mint(client, clock)
    assert client.get(f"{URL}/commits/main", headers=_tok(token)).status_code == 200
    _lever(client, "set_permissions", installation_id=INSTALLATION, permissions={"metadata": "read"})
    assert client.get(f"{URL}/commits/main", headers=_tok(token)).status_code == 403
    assert client.get(URL, headers=_tok(token)).status_code == 200


def test_a_revoked_minted_token_answers_401(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client)
    _lever(client, "strict_tokens")
    token = _mint(client, clock)
    _lever(client, "revoke_token", token=token)
    assert client.get(URL, headers=_tok(token)).status_code == 401


def test_reset_clears_the_registry_and_the_lifetime(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _install(client)
    _lever(client, "token_lifetime", seconds=5)
    client.post("/_levers/reset")
    assert client.get(f"/app/installations/{INSTALLATION}", headers=_bearer(_jwt(clock))).status_code == 401
    _install(client)
    assert _mint(client, clock)
    _lever(client, "strict_tokens")
    # default lifetime is back
    token = _mint(client, clock)
    clock.tick(60)
    assert client.get(URL, headers=_tok(token)).status_code == 200


def test_the_levers_validate_their_params(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    for kind, body in [
        ("register_token", {"token": "x"}),
        ("register_app", {"app_id": 1}),
        ("register_installation", {"installation_id": 1}),
        ("token_lifetime", {}),
        ("revoke_token", {"token": "ghost"}),
        ("suspend_installation", {"installation_id": 99}),
        ("private_repository", {}),
    ]:
        assert client.post(f"/_levers/{kind}", json=body).status_code == 422, kind


def _api_routes(routes: Iterable[Any]) -> Iterator[APIRoute]:
    """Every ``APIRoute`` under the app, through the routers it includes."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        else:
            included = getattr(route, "original_router", None)
            if included is not None:
                yield from _api_routes(included.routes)


def test_every_repos_route_declares_its_permission(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    app = cast(FastAPI, client.app)
    undeclared: list[str] = []
    seen = 0
    for route in _api_routes(app.routes):
        if not route.path.startswith("/repos/"):
            continue
        seen += 1
        declared = [getattr(d.dependency, "requires", None) for d in route.dependencies]
        if not any(declared):
            undeclared.append(f"{sorted(route.methods or ())} {route.path}")
    assert seen >= 25
    assert undeclared == []
