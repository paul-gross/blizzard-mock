"""Mock-hub configuration — bind address only (the hub mock is pure in-memory state).

Sourced from CLI flags with env fallbacks. The mock stands in for the real hub on the
winter service band's hub port; a service-tier test that runs it out of process points
the runner's ``BZ_HUB_URL`` at ``http://{host}:{port}``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8421  # the real hub's band (+2); a test usually injects a free port

ENV_HOST = "BZ_MOCK_HUB_HOST"
ENV_PORT = "BZ_MOCK_HUB_PORT"


@dataclass(frozen=True)
class MockHubConfig:
    """Resolved runtime configuration for the mock hub service."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @classmethod
    def from_env(cls, *, host: str | None = None, port: int | None = None) -> MockHubConfig:
        resolved_host = host or os.environ.get(ENV_HOST, DEFAULT_HOST)
        resolved_port = port if port is not None else int(os.environ.get(ENV_PORT, DEFAULT_PORT))
        return cls(host=resolved_host, port=resolved_port)
