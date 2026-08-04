"""Unit + component coverage for the stub IdP (``blizzard-mock:unit-test``, issue #92).

Drives both provider shapes — OIDC (discovery, authorize, signed ``id_token``, JWKS)
and GitHub-style (authorize, access-token exchange, ``/user``, ``/user/emails``) —
over a ``TestClient`` (in-process, no network), plus the ``/_levers`` control surface
(profile, handle-rename, unverified-email, refuse-callback).
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from blizzard_mock.idp.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _redirect_query(resp) -> dict[str, str]:  # type: ignore[no-untyped-def]
    location = resp.headers["location"]
    return {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}


# --- oidc ------------------------------------------------------------------------


def test_oidc_discovery_document(client: TestClient) -> None:
    resp = client.get("/.well-known/openid-configuration")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authorization_endpoint"].endswith("/oidc/authorize")
    assert body["token_endpoint"].endswith("/oidc/token")
    assert body["jwks_uri"].endswith("/oidc/jwks")
    assert body["issuer"] == str(client.base_url).rstrip("/")


def test_oidc_full_dance_mints_a_verifiable_id_token(client: TestClient) -> None:
    resp = client.get(
        "/oidc/authorize",
        params={"redirect_uri": "http://cb.test/callback", "state": "st1", "client_id": "cid"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    query = _redirect_query(resp)
    assert query["state"] == "st1"
    code = query["code"]

    token_resp = client.post(
        "/oidc/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://cb.test/callback",
            "client_id": "cid",
            "client_secret": "secret",
        },
    )
    assert token_resp.status_code == 200
    id_token = token_resp.json()["id_token"]

    jwks = client.get("/oidc/jwks").json()
    key = RSAAlgorithm.from_jwk(jwks["keys"][0])
    claims = jwt.decode(id_token, key=key, algorithms=["RS256"], audience="cid")  # type: ignore[arg-type]
    assert claims["sub"] == "1001"
    assert claims["preferred_username"] == "octocat"
    assert claims["email"] == "octocat@example.com"
    assert claims["email_verified"] is True


def test_oidc_token_code_is_single_use(client: TestClient) -> None:
    resp = client.get(
        "/oidc/authorize",
        params={"redirect_uri": "http://cb.test/callback", "state": "st1", "client_id": "cid"},
        follow_redirects=False,
    )
    code = _redirect_query(resp)["code"]
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://cb.test/callback",
        "client_id": "cid",
        "client_secret": "secret",
    }
    assert client.post("/oidc/token", data=data).status_code == 200
    second = client.post("/oidc/token", data=data)
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


def test_oidc_refuse_callback_lever(client: TestClient) -> None:
    client.put("/_levers/refuse_callback", json={"refuse": True})
    resp = client.get(
        "/oidc/authorize",
        params={"redirect_uri": "http://cb.test/callback", "state": "st1", "client_id": "cid"},
        follow_redirects=False,
    )
    query = _redirect_query(resp)
    assert query["error"] == "access_denied"
    assert "code" not in query


def test_oidc_unverified_email_profile_lever(client: TestClient) -> None:
    client.put(
        "/_levers/profile",
        json={"subject": "42", "handle": "ada", "email": "ada@example.com", "email_verified": False},
    )
    resp = client.get(
        "/oidc/authorize",
        params={"redirect_uri": "http://cb.test/callback", "state": "st1", "client_id": "cid"},
        follow_redirects=False,
    )
    code = _redirect_query(resp)["code"]
    token_resp = client.post(
        "/oidc/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://cb.test/callback",
            "client_id": "cid",
            "client_secret": "secret",
        },
    )
    jwks = client.get("/oidc/jwks").json()
    key = RSAAlgorithm.from_jwk(jwks["keys"][0])
    claims = jwt.decode(token_resp.json()["id_token"], key=key, algorithms=["RS256"], audience="cid")  # type: ignore[arg-type]
    assert claims["email_verified"] is False


# --- github ------------------------------------------------------------------------


def test_github_full_dance_resolves_user_and_verified_email(client: TestClient) -> None:
    resp = client.get(
        "/login/oauth/authorize",
        params={"redirect_uri": "http://cb.test/callback", "state": "st1", "client_id": "cid"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    code = _redirect_query(resp)["code"]

    token_resp = client.post(
        "/login/oauth/access_token",
        data={"code": code, "redirect_uri": "http://cb.test/callback", "client_id": "cid", "client_secret": "s"},
    )
    assert token_resp.status_code == 200
    access_token = token_resp.json()["access_token"]

    user_resp = client.get("/user", headers={"Authorization": f"token {access_token}"})
    assert user_resp.status_code == 200
    assert user_resp.json() == {"id": 1001, "login": "octocat"}

    emails_resp = client.get("/user/emails", headers={"Authorization": f"token {access_token}"})
    assert emails_resp.json() == [{"email": "octocat@example.com", "primary": True, "verified": True}]


def test_github_user_rejects_a_missing_or_bad_bearer(client: TestClient) -> None:
    assert client.get("/user").status_code == 401
    assert client.get("/user", headers={"Authorization": "token bogus"}).status_code == 401


def test_github_handle_rename_via_the_profile_lever(client: TestClient) -> None:
    client.put(
        "/_levers/profile", json={"subject": "1001", "handle": "octocat", "email": None, "email_verified": False}
    )
    first_resp = client.get(
        "/login/oauth/authorize",
        params={"redirect_uri": "http://cb.test/callback", "state": "s1", "client_id": "cid"},
        follow_redirects=False,
    )
    first_code = _redirect_query(first_resp)["code"]
    first_token = client.post(
        "/login/oauth/access_token",
        data={"code": first_code, "redirect_uri": "http://cb.test/callback", "client_id": "cid", "client_secret": "s"},
    ).json()["access_token"]
    assert client.get("/user", headers={"Authorization": f"token {first_token}"}).json()["login"] == "octocat"

    client.put(
        "/_levers/profile", json={"subject": "1001", "handle": "octo-renamed", "email": None, "email_verified": False}
    )
    second_resp = client.get(
        "/login/oauth/authorize",
        params={"redirect_uri": "http://cb.test/callback", "state": "s2", "client_id": "cid"},
        follow_redirects=False,
    )
    second_code = _redirect_query(second_resp)["code"]
    second_token = client.post(
        "/login/oauth/access_token",
        data={"code": second_code, "redirect_uri": "http://cb.test/callback", "client_id": "cid", "client_secret": "s"},
    ).json()["access_token"]
    second_user = client.get("/user", headers={"Authorization": f"token {second_token}"}).json()
    assert second_user == {"id": 1001, "login": "octo-renamed"}


def test_github_user_emails_is_empty_with_no_email(client: TestClient) -> None:
    client.put("/_levers/profile", json={"subject": "1", "handle": "a", "email": None, "email_verified": False})
    resp = client.get(
        "/login/oauth/authorize",
        params={"redirect_uri": "http://cb.test/callback", "state": "s1", "client_id": "cid"},
        follow_redirects=False,
    )
    code = _redirect_query(resp)["code"]
    token = client.post(
        "/login/oauth/access_token",
        data={"code": code, "redirect_uri": "http://cb.test/callback", "client_id": "cid", "client_secret": "s"},
    ).json()["access_token"]
    assert client.get("/user/emails", headers={"Authorization": f"token {token}"}).json() == []


# --- levers ---------------------------------------------------------------------


def test_levers_reset_restores_the_default_profile(client: TestClient) -> None:
    client.put("/_levers/profile", json={"subject": "9", "handle": "z", "email": None, "email_verified": False})
    client.put("/_levers/refuse_callback", json={"refuse": True})
    client.post("/_levers/reset")
    profile = client.get("/_levers/profile").json()["profile"]
    assert profile == {
        "subject": "1001",
        "handle": "octocat",
        "email": "octocat@example.com",
        "email_verified": True,
        "role": None,
    }


def test_levers_profile_accepts_and_defaults_the_role_field(client: TestClient) -> None:
    default_profile = client.get("/_levers/profile").json()["profile"]
    assert default_profile["role"] is None

    resp = client.put(
        "/_levers/profile",
        json={"subject": "42", "handle": "ada", "email": "ada@example.com", "email_verified": True, "role": "guest"},
    )
    assert resp.json()["profile"]["role"] == "guest"
    assert client.get("/_levers/profile").json()["profile"]["role"] == "guest"


def test_levers_profile_role_round_trips_through_a_subsequent_authorize_dance(client: TestClient) -> None:
    client.put(
        "/_levers/profile",
        json={
            "subject": "42",
            "handle": "ada",
            "email": "ada@example.com",
            "email_verified": True,
            "role": "contributor",
        },
    )
    resp = client.get(
        "/oidc/authorize",
        params={"redirect_uri": "http://cb.test/callback", "state": "s1", "client_id": "cid"},
        follow_redirects=False,
    )
    code = _redirect_query(resp)["code"]
    token_resp = client.post(
        "/oidc/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://cb.test/callback",
            "client_id": "cid",
            "client_secret": "secret",
        },
    )
    access_token = token_resp.json()["access_token"]

    idp_state = client.app.state.idp_state  # type: ignore[attr-defined]
    resolved_profile = idp_state.profile_for_access_token(access_token)
    assert resolved_profile.role == "contributor"

    # additive only — the role never surfaces in the signed id_token, which stays
    # provider-shaped
    jwks = client.get("/oidc/jwks").json()
    key = RSAAlgorithm.from_jwk(jwks["keys"][0])
    claims = jwt.decode(token_resp.json()["id_token"], key=key, algorithms=["RS256"], audience="cid")  # type: ignore[arg-type]
    assert "role" not in claims


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
