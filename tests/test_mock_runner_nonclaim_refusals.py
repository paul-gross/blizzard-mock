"""Non-claim drive calls preserve hub refusals and their contract headers."""

import httpx2 as httpx
import pytest

from blizzard_mock.clock import SystemClock
from blizzard_mock.levers import InMemoryLeverStore
from blizzard_mock.mock_runner.domain.service import MockRunnerService
from blizzard_mock.mock_runner.internal.httpx_gateway import HttpxHubGateway
from blizzard_mock.mock_runner.internal.nonclaim_refusal import classify_nonclaim_refusal


@pytest.mark.parametrize("typed", [True, False])
@pytest.mark.parametrize(
    ("status", "kind", "body", "expected"),
    [
        (401, "runner-identity", {"reason": "revoked", "runner_id": "r"}, "identity"),
        (409, "runner-version", {"reason": "old", "hub_version": "1", "floor": "1", "detail": "old"}, "version"),
    ],
)
def test_nonclaim_type_first_and_legacy(
    status: int, kind: str, body: dict[str, object], expected: str, typed: bool
) -> None:
    if typed:
        body = {**body, "type": f"urn:blizzard:problem:{kind}"}
    assert classify_nonclaim_refusal(status, body) == expected
    assert classify_nonclaim_refusal(status, {**body, "type": "urn:blizzard:problem:future"}) == "unexpected"
    assert classify_nonclaim_refusal(status, {**body, "type": None}) == "unexpected"
    assert classify_nonclaim_refusal(500, body) == "unexpected"
    assert classify_nonclaim_refusal(status, {"type": f"urn:blizzard:problem:{kind}"}) == "unexpected"


@pytest.mark.parametrize("kind", ["urn:blizzard:problem:future", None])
def test_nonclaim_refusal_is_not_reclassified(kind: str | None) -> None:
    body = {"detail": "denied"}
    if kind is not None:
        body["type"] = kind

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json=body,
            headers={"content-type": "application/problem+json", "www-authenticate": "Bearer", "x-secret": "hidden"},
        )

    with httpx.Client(transport=httpx.MockTransport(respond), base_url="http://hub") as client:
        service = MockRunnerService(
            HttpxHubGateway(client, token="token"), InMemoryLeverStore(), SystemClock(), name="r", workspace_id="w"
        )
        for result in (
            service.register(),
            service.peek(),
            service.get_chunk("c"),
            service.poll_answer("q"),
            service.pause(),
        ):
            assert result["status"] == 401
            assert result["response"] == body
            assert result["headers"] == {"content-type": "application/problem+json", "www-authenticate": "Bearer"}
            assert result["refusal"] == "unexpected"


@pytest.mark.parametrize("typed", [True, False])
def test_drive_reports_identity_and_clears_it_after_success(typed: bool) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls > 1:
            return httpx.Response(200, json={"entries": []})
        body = {"reason": "revoked", "runner_id": "r"}
        if typed:
            body["type"] = "urn:blizzard:problem:runner-identity"
        return httpx.Response(401, json=body, headers={"www-authenticate": "Bearer"})

    with httpx.Client(transport=httpx.MockTransport(respond), base_url="http://hub") as client:
        service = MockRunnerService(
            HttpxHubGateway(client, token="token"), InMemoryLeverStore(), SystemClock(), name="r", workspace_id="w"
        )
        result = service.claim_next([])
        assert result["claimed"] is False
        assert result["refusal"] == "identity"
        assert result["headers"]["www-authenticate"] == "Bearer"
        assert "refusal" not in service.peek()
