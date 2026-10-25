"""The stub IdP's levered control surface — namespaced ``/_levers``, outside
the OIDC/GitHub-shaped surface.

``PUT /profile`` sets the unhinted identity; ``POST /identities`` registers a
hinted identity; ``PUT /refuse_callback`` arms refusal; ``POST /reset`` clears state.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from blizzard_mock.idp.api.deps import get_state
from blizzard_mock.idp.domain.models import Profile
from blizzard_mock.idp.domain.state import IdpState

router = APIRouter(prefix="/_levers", tags=["levers"])


class RefuseCallbackParams(BaseModel):
    refuse: bool = True


@router.put("/profile")
def set_profile(profile: Profile, state_repo: Annotated[IdpState, Depends(get_state)]) -> dict[str, Any]:
    state_repo.set_profile(profile)
    return {"profile": profile.model_dump()}


@router.get("/profile")
def get_profile(state_repo: Annotated[IdpState, Depends(get_state)]) -> dict[str, Any]:
    return {"profile": state_repo.profile.model_dump()}


@router.post("/identities")
def register_identity(profile: Profile, state_repo: Annotated[IdpState, Depends(get_state)]) -> dict[str, Any]:
    if profile.email is None:
        raise HTTPException(status_code=422, detail="an identity needs a primary email")
    state_repo.register_identity(profile)
    return {"identity": profile.model_dump()}


@router.get("/identities")
def get_identities(state_repo: Annotated[IdpState, Depends(get_state)]) -> dict[str, Any]:
    return {"identities": [profile.model_dump() for profile in state_repo.identities()]}


@router.put("/refuse_callback")
def set_refuse_callback(
    params: RefuseCallbackParams, state_repo: Annotated[IdpState, Depends(get_state)]
) -> dict[str, Any]:
    state_repo.refuse_callback = params.refuse
    return {"refuse_callback": state_repo.refuse_callback}


@router.post("/reset")
def reset(state_repo: Annotated[IdpState, Depends(get_state)]) -> dict[str, Any]:
    state_repo.reset()
    return {"reset": True}
