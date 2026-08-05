"""The GitHub-style shape — ``/login/oauth/authorize``, ``/login/oauth/access_token``,
``GET /user``, ``GET /user/emails``.

Served at the same origin as the OIDC shape: a scenario points a single
``api_base`` override at this process for both.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse

from blizzard_mock.idp.api.deps import get_state
from blizzard_mock.idp.domain.models import Profile
from blizzard_mock.idp.domain.state import IdpState

router = APIRouter(tags=["github"])

_TOKEN_PREFIX = "token "


@router.get("/login/oauth/authorize")
def authorize(
    request: Request,
    state_repo: Annotated[IdpState, Depends(get_state)],
    redirect_uri: str,
    state: str = "",
    client_id: str = "",
    scope: str = "",
) -> RedirectResponse:
    if state_repo.refuse_callback:
        return RedirectResponse(f"{redirect_uri}?{urlencode({'error': 'access_denied', 'state': state})}")
    code = state_repo.mint_code()
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}")


@router.post("/login/oauth/access_token")
def access_token(
    state_repo: Annotated[IdpState, Depends(get_state)],
    code: Annotated[str, Form()] = "",
    redirect_uri: Annotated[str, Form()] = "",
    client_id: Annotated[str, Form()] = "",
    client_secret: Annotated[str, Form()] = "",
) -> JSONResponse:
    profile = state_repo.consume_code(code)
    if profile is None:
        return JSONResponse(status_code=400, content={"error": "bad_verification_code"})
    token = state_repo.mint_access_token(profile)
    return JSONResponse(content={"access_token": token, "token_type": "bearer", "scope": "user:email"})


def _profile_for_bearer(state_repo: IdpState, authorization: str | None) -> Profile | None:
    if authorization is None or not authorization.startswith(_TOKEN_PREFIX):
        return None
    token = authorization[len(_TOKEN_PREFIX) :]
    return state_repo.profile_for_access_token(token)


_UNAUTHORIZED = JSONResponse(status_code=401, content={"message": "Bad credentials"})


@router.get("/user")
def user(
    state_repo: Annotated[IdpState, Depends(get_state)], authorization: Annotated[str | None, Header()] = None
) -> JSONResponse:
    profile = _profile_for_bearer(state_repo, authorization)
    if profile is None:
        return _UNAUTHORIZED
    return JSONResponse(content={"id": int(profile.subject), "login": profile.handle})


@router.get("/user/emails")
def user_emails(
    state_repo: Annotated[IdpState, Depends(get_state)], authorization: Annotated[str | None, Header()] = None
) -> JSONResponse:
    profile = _profile_for_bearer(state_repo, authorization)
    if profile is None:
        return _UNAUTHORIZED
    if profile.email is None:
        return JSONResponse(content=[])
    return JSONResponse(content=[{"email": profile.email, "primary": True, "verified": profile.email_verified}])
