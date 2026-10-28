"""The IdP-offered switch: ``GET /api/auth/jwks.json`` answers an empty key set while on, and is an absent route
(404) while off — the default."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.mock_hub.app import create_app
from blizzard_mock.mock_hub.config import ENV_OFFER_IDP, MockHubConfig

_JWKS = "/api/auth/jwks.json"


def test_off_by_default_the_jwks_route_is_absent() -> None:
    assert TestClient(create_app()).get(_JWKS).status_code == 404


def test_the_lever_serves_an_empty_key_set_until_cleared() -> None:
    client = TestClient(create_app())
    assert client.post("/_levers/idp_offered", json={}).status_code == 200

    resp = client.get(_JWKS)
    assert (resp.status_code, resp.json()) == (200, {"keys": []})

    client.delete("/_levers/idp_offered")
    assert client.get(_JWKS).status_code == 404


def test_the_startup_switch_serves_it_past_a_lever_reset() -> None:
    client = TestClient(create_app(MockHubConfig(idp_offered=True)))
    client.post("/_levers/reset")

    resp = client.get(_JWKS)
    assert (resp.status_code, resp.json()) == (200, {"keys": []})


def test_the_startup_switch_reads_its_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OFFER_IDP, "1")
    assert MockHubConfig.from_env().idp_offered is True
    monkeypatch.delenv(ENV_OFFER_IDP)
    assert MockHubConfig.from_env().idp_offered is False
