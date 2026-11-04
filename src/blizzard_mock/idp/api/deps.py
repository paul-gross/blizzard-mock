from __future__ import annotations

from fastapi import Request

from blizzard_mock.idp.domain.signing import IdTokenSigner
from blizzard_mock.idp.domain.state import IdpState


def get_state(request: Request) -> IdpState:
    return request.app.state.idp_state


def get_signer(request: Request) -> IdTokenSigner:
    return request.app.state.signer
