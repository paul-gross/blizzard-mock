"""The generic OIDC shape — discovery, authorize, token (signed ``id_token``), JWKS."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from blizzard_mock.idp.api.deps import get_signer, get_state
from blizzard_mock.idp.domain.signing import IdTokenSigner
from blizzard_mock.idp.domain.state import IdpState

router = APIRouter(tags=["oidc"])


@router.get("/.well-known/openid-configuration")
def discovery(request: Request) -> dict[str, str]:
    base = str(request.base_url).rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oidc/authorize",
        "token_endpoint": f"{base}/oidc/token",
        "jwks_uri": f"{base}/oidc/jwks",
    }


@router.get("/oidc/jwks")
def jwks(signer: Annotated[IdTokenSigner, Depends(get_signer)]) -> dict[str, object]:
    return signer.jwks()


@router.get("/oidc/authorize")
def authorize(
    request: Request,
    state_repo: Annotated[IdpState, Depends(get_state)],
    redirect_uri: str,
    state: str = "",
    client_id: str = "",
    scope: str = "",
    response_type: str = "code",
) -> RedirectResponse:
    if state_repo.refuse_callback:
        return RedirectResponse(f"{redirect_uri}?{urlencode({'error': 'access_denied', 'state': state})}")
    code = state_repo.mint_code()
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}")


@router.post("/oidc/token")
def token(
    request: Request,
    state_repo: Annotated[IdpState, Depends(get_state)],
    signer: Annotated[IdTokenSigner, Depends(get_signer)],
    grant_type: Annotated[str, Form()] = "authorization_code",
    code: Annotated[str, Form()] = "",
    redirect_uri: Annotated[str, Form()] = "",
    client_id: Annotated[str, Form()] = "",
    client_secret: Annotated[str, Form()] = "",
) -> JSONResponse:
    profile = state_repo.consume_code(code)
    if profile is None:
        return JSONResponse(status_code=400, content={"error": "invalid_grant"})
    base = str(request.base_url).rstrip("/")
    claims: dict[str, object] = {"preferred_username": profile.handle}
    if profile.email is not None:
        claims["email"] = profile.email
        claims["email_verified"] = profile.email_verified
    id_token = signer.sign_id_token(issuer=base, audience=client_id, subject=profile.subject, claims=claims)
    access_token = state_repo.mint_access_token(profile)
    return JSONResponse(
        content={"id_token": id_token, "access_token": access_token, "token_type": "bearer", "expires_in": 300}
    )
