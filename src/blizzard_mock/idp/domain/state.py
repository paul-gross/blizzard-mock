"""In-memory stub-IdP state — the current profile, keyed identities, the
``refuse_callback`` lever, and the code / access-token maps both shapes share.

One process-wide store: a code is minted at ``authorize`` and consumed
exactly once at the token exchange.
"""

from __future__ import annotations

import secrets

from blizzard_mock.idp.domain.models import DEFAULT_PROFILE, Profile


class IdpState:
    """Holds the profile, keyed identities, refusal lever, and code/token maps."""

    def __init__(self) -> None:
        self.profile: Profile = DEFAULT_PROFILE
        self.refuse_callback: bool = False
        self._identities: dict[str, Profile] = {}
        self._codes: dict[str, Profile] = {}
        self._access_tokens: dict[str, Profile] = {}

    def set_profile(self, profile: Profile) -> None:
        self.profile = profile

    def register_identity(self, profile: Profile) -> None:
        if profile.email is None:
            raise ValueError("an identity needs a primary email")
        self._identities[profile.email.lower()] = profile

    def identities(self) -> list[Profile]:
        return list(self._identities.values())

    def identity_for_hint(self, hint: str) -> Profile | None:
        return self._identities.get(hint.lower())

    def reset(self) -> None:
        self.profile = DEFAULT_PROFILE
        self.refuse_callback = False
        self._identities.clear()
        self._codes.clear()
        self._access_tokens.clear()

    def mint_code(self, profile: Profile) -> str:
        """Mint a single-use code bound to the chosen identity at authorize time."""
        code = secrets.token_urlsafe(16)
        self._codes[code] = profile
        return code

    def consume_code(self, code: str) -> Profile | None:
        return self._codes.pop(code, None)

    def mint_access_token(self, profile: Profile) -> str:
        token = secrets.token_urlsafe(16)
        self._access_tokens[token] = profile
        return token

    def profile_for_access_token(self, token: str) -> Profile | None:
        return self._access_tokens.get(token)
