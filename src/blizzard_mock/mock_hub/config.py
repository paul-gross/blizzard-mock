"""Mock-hub configuration — bind address, plus the host-plane-absent and IdP-offered switches.

The hub mock is pure in-memory state.

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
ENV_NO_HOST_PLANE = "BZ_MOCK_HUB_NO_HOST_PLANE"
ENV_OFFER_IDP = "BZ_MOCK_HUB_OFFER_IDP"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class MockHubConfig:
    """Resolved runtime configuration for the mock hub service."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    #: Answer every ``/api/fleet/host`` route 404 for the process's life — a hub from before hosts.
    host_plane_absent: bool = False
    #: Serve ``GET /api/auth/jwks.json`` an empty key set for the process's life — a hub offering an IdP.
    idp_offered: bool = False

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @classmethod
    def from_env(
        cls,
        *,
        host: str | None = None,
        port: int | None = None,
        host_plane_absent: bool | None = None,
        idp_offered: bool | None = None,
    ) -> MockHubConfig:
        resolved_host = host or os.environ.get(ENV_HOST, DEFAULT_HOST)
        resolved_port = port if port is not None else int(os.environ.get(ENV_PORT, DEFAULT_PORT))
        resolved_absent = host_plane_absent if host_plane_absent is not None else _env_flag(ENV_NO_HOST_PLANE)
        resolved_idp = idp_offered if idp_offered is not None else _env_flag(ENV_OFFER_IDP)
        return cls(host=resolved_host, port=resolved_port, host_plane_absent=resolved_absent, idp_offered=resolved_idp)
