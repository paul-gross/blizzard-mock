"""In-memory stub-IdP state — the current profile, the ``refused_callback`` lever, and
the single-use code / access-token maps both provider shapes share.

One process-wide store (mirrors ``blizzard_mock.forge.internal.lever_store``): a code
is minted at ``authorize`` and consumed exactly once at the token/access-token
exchange, mirroring the real single-use-code contract both the ``oidc`` and ``github``
conformers rely on.
"""

from __future__ import annotations

import secrets

from blizzard_mock.idp.domain.models import DEFAULT_PROFILE, Profile


class IdpState:
    """Holds the current profile, the refused-callback lever, and the code/token maps."""

    def __init__(self) -> None:
        self.profile: Profile = DEFAULT_PROFILE
        self.refuse_callback: bool = False
        self._codes: dict[str, Profile] = {}
        self._access_tokens: dict[str, Profile] = {}

    def set_profile(self, profile: Profile) -> None:
        self.profile = profile

    def reset(self) -> None:
        self.profile = DEFAULT_PROFILE
        self.refuse_callback = False
        self._codes.clear()
        self._access_tokens.clear()

    def mint_code(self) -> str:
        """A single-use authorization code bound to the **current** profile at mint
        time — a test flips the profile between two authorize calls to script two
        distinct identities without the codes' resolution racing each other."""
        code = secrets.token_urlsafe(16)
        self._codes[code] = self.profile
        return code

    def consume_code(self, code: str) -> Profile | None:
        return self._codes.pop(code, None)

    def mint_access_token(self, profile: Profile) -> str:
        token = secrets.token_urlsafe(16)
        self._access_tokens[token] = profile
        return token

    def profile_for_access_token(self, token: str) -> Profile | None:
        return self._access_tokens.get(token)
