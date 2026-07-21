"""RS256 ``id_token`` signing + the JWKS document the hub's ``oidc`` conformer fetches.

A fresh keypair is generated once per process (``blizzard-mock-idp`` restarts between
scenario runs, never mid-scenario) — no persistence, mirroring every other mock's
in-memory-only state.
"""

from __future__ import annotations

import json
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from jwt.algorithms import RSAAlgorithm

_KID = "stub-idp-key-1"
_ID_TOKEN_TTL_SECONDS = 300


class IdTokenSigner:
    """Mints RS256-signed ``id_token``s and serves the matching JWKS."""

    def __init__(self) -> None:
        self._private_key: RSAPrivateKey = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwks(self) -> dict[str, object]:
        jwk = json.loads(RSAAlgorithm.to_jwk(self._private_key.public_key()))
        jwk["kid"] = _KID
        jwk["alg"] = "RS256"
        jwk["use"] = "sig"
        return {"keys": [jwk]}

    def sign_id_token(self, *, issuer: str, audience: str, subject: str, claims: dict[str, object]) -> str:
        now = int(time.time())
        payload: dict[str, object] = {
            "iss": issuer,
            "aud": audience,
            "sub": subject,
            "iat": now,
            "exp": now + _ID_TOKEN_TTL_SECONDS,
            **claims,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256", headers={"kid": _KID})
