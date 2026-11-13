"""In-memory App registry (``IAppRegistry``). Process-local, same lifetime as the forge process."""

from __future__ import annotations

from blizzard_mock.forge.domain.apps import DEFAULT_TOKEN_LIFETIME, MockApp, MockInstallation, MockToken


class InMemoryAppRegistry:
    """Apps, installations, and tokens held in memory. Implements ``IAppRegistry``."""

    def __init__(self) -> None:
        self._apps: dict[int, MockApp] = {}
        self._installations: dict[int, MockInstallation] = {}
        self._tokens: dict[str, MockToken] = {}
        self._lifetime = DEFAULT_TOKEN_LIFETIME

    def put_app(self, app: MockApp) -> None:
        self._apps[app.app_id] = app

    def get_app(self, app_id: int) -> MockApp | None:
        return self._apps.get(app_id)

    def put_installation(self, installation: MockInstallation) -> None:
        self._installations[installation.installation_id] = installation

    def get_installation(self, installation_id: int) -> MockInstallation | None:
        return self._installations.get(installation_id)

    def delete_installation(self, installation_id: int) -> bool:
        return self._installations.pop(installation_id, None) is not None

    def put_token(self, token: MockToken) -> None:
        self._tokens[token.value] = token

    def get_token(self, value: str) -> MockToken | None:
        return self._tokens.get(value)

    def token_lifetime(self) -> int:
        return self._lifetime

    def set_token_lifetime(self, seconds: int) -> None:
        self._lifetime = seconds

    def clear(self) -> None:
        self._apps.clear()
        self._installations.clear()
        self._tokens.clear()
        self._lifetime = DEFAULT_TOKEN_LIFETIME
