"""``AppService`` — the rules for credentials, App installations, and permission checks.

Lever-driven registration and state changes, App-JWT verification, installation read and mint,
``authenticate`` (who is calling, or 401) and ``authorize`` (may they reach this repository's
route, or 404 / 403). Authentication and authorization only bite under ``strict_tokens``.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

import jwt

from blizzard_mock.forge.domain.apps import (
    FINE_GRAINED_WITHHELD,
    LEVELS,
    IAppRegistry,
    MockApp,
    MockInstallation,
    MockToken,
    covers,
    effective_permissions,
    parse_credential,
)
from blizzard_mock.forge.domain.clock import Clock
from blizzard_mock.forge.domain.errors import (
    BadCredentials,
    InstallationNotFound,
    InstallationSuspended,
    IntegrationForbidden,
    PermissionNotGranted,
    PersonalTokenForbidden,
    RepoNotFound,
    ValidationError,
)
from blizzard_mock.forge.domain.levers import ILeverStore, LeverKind, LeverParams

#: GitHub refuses an App JWT that lives longer than this.
MAX_JWT_LIFETIME_SECONDS = 600
#: How far in the future an ``iat`` may sit.
JWT_IAT_SKEW_SECONDS = 60

_JWT_ERROR = "A JSON web token could not be decoded"
_NOT_FOUND = "Not Found"


class AppService:
    """The App / token rules over an ``IAppRegistry``, the lever store, and the clock."""

    def __init__(self, registry: IAppRegistry, levers: ILeverStore, clock: Clock) -> None:
        self._registry = registry
        self._levers = levers
        self._clock = clock

    # -- levers ------------------------------------------------------------

    def fire(self, kind: LeverKind, params: LeverParams) -> None:
        """Apply one registration or state-change action lever."""
        if kind is LeverKind.REGISTER_TOKEN:
            self._register_token(params)
        elif kind is LeverKind.REGISTER_APP:
            self._register_app(params)
        elif kind is LeverKind.REGISTER_INSTALLATION:
            self._register_installation(params)
        elif kind is LeverKind.TOKEN_LIFETIME:
            if params.seconds is None or params.seconds < 0:
                raise ValidationError("token_lifetime requires seconds")
            self._registry.set_token_lifetime(params.seconds)
        elif kind is LeverKind.REVOKE_TOKEN:
            token = self._registry.get_token(params.token or "")
            if token is None:
                raise ValidationError("revoke_token requires a registered token")
            token.revoked = True
        elif kind is LeverKind.SUSPEND_INSTALLATION:
            self._installation(params).suspended_at = self._clock.now()
        elif kind is LeverKind.DELETE_INSTALLATION:
            self._installation(params)
            assert params.installation_id is not None
            self._registry.delete_installation(params.installation_id)
        elif kind is LeverKind.REMOVE_REPOSITORY:
            if params.repo is None:
                raise ValidationError("remove_repository requires installation_id and repo")
            self._installation(params).repositories.discard(params.repo)
        elif kind is LeverKind.SET_PERMISSIONS:
            self._installation(params).permissions = _checked_permissions(params.permissions)

    def reset(self) -> None:
        self._registry.clear()

    def _register_token(self, params: LeverParams) -> None:
        if not params.token or params.token_kind is None:
            raise ValidationError("register_token requires token and token_kind")
        self._registry.put_token(MockToken(params.token, params.token_kind, scopes=tuple(params.scopes)))

    def _register_app(self, params: LeverParams) -> None:
        if params.app_id is None or not params.public_key:
            raise ValidationError("register_app requires app_id and public_key")
        self._registry.put_app(MockApp(params.app_id, params.public_key))

    def _register_installation(self, params: LeverParams) -> None:
        if params.installation_id is None or params.app_id is None:
            raise ValidationError("register_installation requires installation_id and app_id")
        self._registry.put_installation(
            MockInstallation(
                params.installation_id,
                params.app_id,
                set(params.repositories),
                _checked_permissions(params.permissions),
            )
        )

    def _installation(self, params: LeverParams) -> MockInstallation:
        installation = self._registry.get_installation(params.installation_id) if params.installation_id else None
        if installation is None:
            raise ValidationError("unknown installation_id")
        return installation

    # -- the App routes ----------------------------------------------------

    def get_installation(self, authorization: str | None, installation_id: int) -> MockInstallation:
        app = self._verify_jwt(authorization)
        return self._owned_installation(app, installation_id)

    def mint(self, authorization: str | None, installation_id: int, permissions: dict[str, str] | None) -> MockToken:
        """Mint an installation token: the full grant when ``permissions`` is absent, else exactly
        the permissions asked for, each granted at that level or above."""
        app = self._verify_jwt(authorization)
        installation = self._owned_installation(app, installation_id)
        if installation.suspended_at is not None:
            raise InstallationSuspended("This installation has been suspended")
        if permissions is None:
            minted = dict(installation.permissions)
        else:
            minted = _checked_permissions(permissions)
            for name, level in minted.items():
                if not covers(installation.permissions.get(name, ""), level):
                    raise PermissionNotGranted(
                        f"The permissions requested are not granted to this installation: {name}={level}"
                    )
        token = MockToken(
            value="ghs_" + secrets.token_hex(18),
            kind="installation",
            installation_id=installation_id,
            permissions=minted,
            expires_at=self._clock.now() + timedelta(seconds=self._registry.token_lifetime()),
        )
        self._registry.put_token(token)
        return token

    def _owned_installation(self, app: MockApp, installation_id: int) -> MockInstallation:
        installation = self._registry.get_installation(installation_id)
        if installation is None or installation.app_id != app.app_id:
            raise InstallationNotFound(_NOT_FOUND)
        return installation

    def _verify_jwt(self, authorization: str | None) -> MockApp:
        """The registered App a Bearer JWT names, its signature verified against that App's key and
        its times judged against the injected clock."""
        encoded = parse_credential(authorization)
        if encoded is None:
            raise BadCredentials(_JWT_ERROR)
        try:
            unverified = jwt.decode(encoded, options={"verify_signature": False})
            app = self._registry.get_app(int(unverified["iss"]))
            if app is None:
                raise BadCredentials(_JWT_ERROR)
            claims = jwt.decode(
                encoded,
                app.public_key_pem,
                algorithms=["RS256"],
                options={"verify_exp": False, "verify_iat": False, "verify_nbf": False, "verify_aud": False},
            )
            exp, iat = float(claims["exp"]), float(claims["iat"])
        except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
            raise BadCredentials(_JWT_ERROR) from exc
        now = self._clock.now().timestamp()
        if exp <= now or exp > now + MAX_JWT_LIFETIME_SECONDS or iat > now + JWT_IAT_SKEW_SECONDS:
            raise BadCredentials(_JWT_ERROR)
        return app

    # -- credentials -------------------------------------------------------

    @property
    def strict(self) -> bool:
        return self._levers.find(LeverKind.STRICT_TOKENS, None, None) is not None

    def authenticate(self, authorization: str | None) -> MockToken | None:
        """The registered credential behind an ``Authorization`` header; ``BadCredentials`` when it
        is absent, unregistered, revoked, expired, or minted under a suspended or deleted
        installation. ``None`` when ``strict_tokens`` is off and nothing is checked."""
        if not self.strict:
            return None
        value = parse_credential(authorization)
        token = self._registry.get_token(value) if value else None
        if token is None or token.revoked:
            raise BadCredentials()
        if token.expires_at is not None and self._clock.now() >= token.expires_at:
            raise BadCredentials()
        if token.kind == "installation":
            installation = self._registry.get_installation(token.installation_id or 0)
            if installation is None or installation.suspended_at is not None:
                raise BadCredentials()
        return token

    def authorize(self, authorization: str | None, repo: str, permission: str, level: str) -> None:
        """Let the credential through to a route needing ``permission: level`` on ``repo``, or raise
        the answer GitHub gives. Passes everything while ``strict_tokens`` is off."""
        token = self.authenticate(authorization)
        if token is None or token.kind == "classic":
            return
        if token.kind == "fine-grained":
            private = self._levers.find(LeverKind.PRIVATE_REPOSITORY, repo, None) is not None
            if private and permission in FINE_GRAINED_WITHHELD:
                raise PersonalTokenForbidden()
            return
        installation = self._registry.get_installation(token.installation_id or 0)
        if installation is None:  # pragma: no cover - authenticate refused it
            raise BadCredentials()
        if repo not in installation.repositories:
            raise RepoNotFound(_NOT_FOUND)
        held = effective_permissions(token, installation)
        if not covers(held.get(permission, ""), level):
            raise IntegrationForbidden(permission, level)


def _checked_permissions(permissions: dict[str, str]) -> dict[str, str]:
    for name, level in permissions.items():
        if level not in LEVELS:
            raise ValidationError(f"invalid permission level for {name}: {level}")
    return dict(permissions)
