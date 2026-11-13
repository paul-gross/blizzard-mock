"""GitHub App installations and the credentials the forge tells apart.

The forge knows three kinds of credential: a **classic** personal access token (reaches every
route, echoes its scopes), a **fine-grained** one (reaches every route but the ones GitHub
withholds from fine-grained tokens), and an **installation** token minted from an App's
installation, bounded by the permissions it was minted with. Permissions are GitHub's names and
levels; ``write`` covers ``read``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

#: Permission levels, weakest first; a level covers every level at or below it.
LEVELS: dict[str, int] = {"read": 1, "write": 2, "admin": 3}

#: Every installation token holds this, whatever it was minted with.
BASELINE_PERMISSIONS: dict[str, str] = {"metadata": "read"}

#: The permissions GitHub withholds from fine-grained personal access tokens on a private
#: repository — it lists the Checks API among fine-grained token limitations. The hub holds its
#: own copy of the same rule; a change to the rule moves one constant in each repo.
FINE_GRAINED_WITHHELD: dict[str, str] = {"checks": "read"}

#: Seconds an installation token lives unless ``token_lifetime`` says otherwise.
DEFAULT_TOKEN_LIFETIME = 3600

TokenKind = Literal["classic", "fine-grained", "installation"]


@dataclass
class MockApp:
    app_id: int
    public_key_pem: str


@dataclass
class MockInstallation:
    installation_id: int
    app_id: int
    repositories: set[str]  # "owner/repo"
    permissions: dict[str, str]
    suspended_at: datetime | None = None


@dataclass
class MockToken:
    value: str
    kind: TokenKind
    installation_id: int | None = None
    permissions: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    scopes: tuple[str, ...] = ()
    expires_at: datetime | None = None
    revoked: bool = False


def covers(held: str, required: str) -> bool:
    """Whether permission level ``held`` satisfies ``required`` (``write`` covers ``read``)."""
    return LEVELS.get(held, 0) >= LEVELS.get(required, 1)


def parse_credential(authorization: str | None) -> str | None:
    """The credential in an ``Authorization`` header (``token X`` or ``Bearer X``), if any."""
    if authorization is None:
        return None
    _, _, credential = authorization.partition(" ")
    return credential.strip() or None


def effective_permissions(token: MockToken, installation: MockInstallation) -> dict[str, str]:
    """An installation token's permissions: what it was minted with, capped by what the
    installation grants now, plus the baseline every installation token holds."""
    held = dict(BASELINE_PERMISSIONS)
    for name, level in token.permissions.items():
        granted = installation.permissions.get(name)
        if granted is None:
            continue
        capped = level if LEVELS.get(level, 0) <= LEVELS.get(granted, 0) else granted
        if not covers(held.get(name, ""), capped):
            held[name] = capped
    return held


class IAppRegistry(Protocol):
    """Read/write seam over Apps, installations, and tokens (one process-wide registry)."""

    def put_app(self, app: MockApp) -> None: ...
    def get_app(self, app_id: int) -> MockApp | None: ...
    def put_installation(self, installation: MockInstallation) -> None: ...
    def get_installation(self, installation_id: int) -> MockInstallation | None: ...
    def delete_installation(self, installation_id: int) -> bool: ...
    def put_token(self, token: MockToken) -> None: ...
    def get_token(self, value: str) -> MockToken | None: ...
    def token_lifetime(self) -> int: ...
    def set_token_lifetime(self, seconds: int) -> None: ...
    def clear(self) -> None:
        """Drop every record and restore the default token lifetime."""
        ...
