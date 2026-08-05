"""The stub IdP's levered control surface — namespaced ``/_levers``, outside
the OIDC/GitHub-shaped surface.

``PUT /profile`` sets the next authorize dance's identity; ``PUT
/refuse_callback`` arms the refused-callback lever; ``POST /reset`` restores defaults.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
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
