"""Mock-runner configuration — its own bind address and the hub it drives.

Sourced from CLI flags with env fallbacks. ``hub_url`` is the hub the
driver's outbound protocol targets; ``host``/``port`` is where the driver's
own control surface listens.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8431  # the real runner's band (+3); a test usually injects a free port
DEFAULT_HUB_URL = "http://127.0.0.1:8421"
DEFAULT_RUNNER_ID = "runner-mock"
DEFAULT_WORKSPACE_ID = "workspace-mock"

ENV_HOST = "BZ_MOCK_RUNNER_HOST"
ENV_PORT = "BZ_MOCK_RUNNER_PORT"
ENV_HUB_URL = "BZ_HUB_URL"
ENV_RUNNER_ID = "BZ_MOCK_RUNNER_ID"
ENV_WORKSPACE_ID = "BZ_MOCK_WORKSPACE_ID"


@dataclass(frozen=True)
class MockRunnerConfig:
    """Resolved runtime configuration for the mock runner driver."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    hub_url: str = DEFAULT_HUB_URL
    runner_id: str = DEFAULT_RUNNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @classmethod
    def from_env(
        cls,
        *,
        host: str | None = None,
        port: int | None = None,
        hub_url: str | None = None,
        runner_id: str | None = None,
        workspace_id: str | None = None,
    ) -> MockRunnerConfig:
        return cls(
            host=host or os.environ.get(ENV_HOST, DEFAULT_HOST),
            port=port if port is not None else int(os.environ.get(ENV_PORT, DEFAULT_PORT)),
            hub_url=hub_url or os.environ.get(ENV_HUB_URL, DEFAULT_HUB_URL),
            runner_id=runner_id or os.environ.get(ENV_RUNNER_ID, DEFAULT_RUNNER_ID),
            workspace_id=workspace_id or os.environ.get(ENV_WORKSPACE_ID, DEFAULT_WORKSPACE_ID),
        )
