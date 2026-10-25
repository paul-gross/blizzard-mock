"""Keyed identities across both stub-IdP authorization shapes."""

from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from blizzard_mock.idp.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _authorize(client: TestClient, route: str, hint: str | None, state: str = "s") -> dict[str, str]:
    params = {"redirect_uri": "http://cb.test/callback", "state": state, "client_id": "cid"}
    if hint is not None:
        params["login_hint"] = hint
    response = client.get(route, params=params, follow_redirects=False)
    assert response.status_code in (302, 307)
    return {key: values[0] for key, values in parse_qs(urlparse(response.headers["location"]).query).items()}


def _oidc_claims(client: TestClient, code: str) -> tuple[dict[str, object], str]:
    response = client.post("/oidc/token", data={"code": code, "client_id": "cid"})
    assert response.status_code == 200
    key = RSAAlgorithm.from_jwk(client.get("/oidc/jwks").json()["keys"][0])
    claims = jwt.decode(response.json()["id_token"], key=key, algorithms=["RS256"], audience="cid")  # type: ignore[arg-type]
    return claims, response.json()["access_token"]


def _github_token(client: TestClient, code: str) -> str:
    response = client.post("/login/oauth/access_token", data={"code": code})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_hinted_oidc_binds_two_outstanding_codes_to_their_own_identities(client: TestClient) -> None:
    ada = {"subject": "42", "handle": "ada", "email": "Ada@Example.com", "email_verified": False}
    bob = {"subject": "7", "handle": "bob", "email": "bob@example.com"}
    assert client.post("/_levers/identities", json=ada).json()["identity"]["email"] == ada["email"]
    client.post("/_levers/identities", json=bob)

    ada_code = _authorize(client, "/oidc/authorize", "ADA@example.COM", "ada")["code"]
    bob_code = _authorize(client, "/oidc/authorize", "bob@example.com", "bob")["code"]
    bob_claims, _ = _oidc_claims(client, bob_code)
    ada_claims, ada_token = _oidc_claims(client, ada_code)
    assert (bob_claims["sub"], bob_claims["email"]) == ("7", "bob@example.com")
    assert (ada_claims["sub"], ada_claims["email"], ada_claims["email_verified"]) == ("42", "Ada@Example.com", False)
    assert client.app.state.idp_state.profile_for_access_token(ada_token).subject == "42"  # type: ignore[attr-defined]


def test_hinted_github_binds_user_and_emails_to_each_code(client: TestClient) -> None:
    client.post(
        "/_levers/identities",
        json={
            "subject": "42",
            "handle": "ada",
            "email": "ada@example.com",
            "secondary_emails": [{"email": "ada@work.example", "verified": False}],
        },
    )
    client.post("/_levers/identities", json={"subject": "7", "handle": "bob", "email": "bob@example.com"})
    ada_code = _authorize(client, "/login/oauth/authorize", "ADA@EXAMPLE.COM")["code"]
    bob_code = _authorize(client, "/login/oauth/authorize", "bob@example.com")["code"]
    bob_token = _github_token(client, bob_code)
    ada_token = _github_token(client, ada_code)
    assert client.get("/user", headers={"Authorization": f"token {bob_token}"}).json() == {"id": 7, "login": "bob"}
    headers = {"Authorization": f"token {ada_token}"}
    assert client.get("/user", headers=headers).json() == {"id": 42, "login": "ada"}
    assert client.get("/user/emails", headers=headers).json() == [
        {"email": "ada@example.com", "primary": True, "verified": True},
        {"email": "ada@work.example", "primary": False, "verified": False},
    ]


@pytest.mark.parametrize("route", ["/oidc/authorize", "/login/oauth/authorize"])
def test_unknown_hint_and_refusal_never_mint_a_code(client: TestClient, route: str) -> None:
    assert _authorize(client, route, "missing@example.com", "unknown") == {"error": "access_denied", "state": "unknown"}
    client.post("/_levers/identities", json={"email": "ada@example.com"})
    client.put("/_levers/refuse_callback", json={"refuse": True})
    assert _authorize(client, route, "ada@example.com", "refused") == {"error": "access_denied", "state": "refused"}


def test_registration_replaces_in_place_and_reset_clears_identities(client: TestClient) -> None:
    client.post("/_levers/identities", json={"subject": "42", "email": "Ada@Example.com"})
    client.post("/_levers/identities", json={"subject": "7", "email": "bob@example.com"})
    client.post("/_levers/identities", json={"subject": "99", "email": "ada@example.COM"})
    identities = client.get("/_levers/identities").json()["identities"]
    assert [identity["subject"] for identity in identities] == ["99", "7"]
    assert client.post("/_levers/identities", json={"email": None}).status_code == 422
    assert client.get("/_levers/identities").json()["identities"] == identities
    client.post("/_levers/reset")
    assert client.get("/_levers/identities").json() == {"identities": []}
    assert _authorize(client, "/oidc/authorize", "ada@example.com")["error"] == "access_denied"


@pytest.mark.parametrize("route", ["/oidc/authorize", "/login/oauth/authorize"])
def test_unhinted_authorization_still_uses_profile(client: TestClient, route: str) -> None:
    client.post("/_levers/identities", json={"subject": "42", "email": "ada@example.com"})
    client.put("/_levers/profile", json={"subject": "7", "handle": "bob", "email": "bob@example.com"})
    code = _authorize(client, route, None)["code"]
    if route == "/oidc/authorize":
        claims, _ = _oidc_claims(client, code)
        assert (claims["sub"], claims["email"]) == ("7", "bob@example.com")
    else:
        token = _github_token(client, code)
        assert client.get("/user", headers={"Authorization": f"token {token}"}).json() == {"id": 7, "login": "bob"}
