"""The driver may identify a version; absent one it reports its default."""

import json

import httpx2 as httpx
import pytest

from blizzard_mock.mock_runner.config import MockRunnerConfig
from blizzard_mock.mock_runner.internal.httpx_gateway import HttpxHubGateway


@pytest.mark.parametrize("version", [None, "0.5.0"])
def test_registration_reports_the_configured_version_over_the_default(version: str | None) -> None:
    bodies: list[dict[str, object]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(201, json={"runner_id": "rn_test"})

    with httpx.Client(base_url="http://hub.test", transport=httpx.MockTransport(answer)) as client:
        gateway = HttpxHubGateway(client, token="synthetic", runner_version=version)
        assert gateway.register(name="test", workspace_id="test")[0] == 201
    assert bodies[0].get("version") == (version or "0.4.0")


def test_reported_version_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_MOCK_RUNNER_VERSION", "0.5.0")
    assert MockRunnerConfig.from_env().runner_version == "0.5.0"
