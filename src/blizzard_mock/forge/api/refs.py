"""Git-data routes — commits and refs, resolved against the bare repo.

``GET /repos/{o}/{r}/commits/{ref}`` and ``GET /repos/{o}/{r}/git/ref/{ref}``
(e.g. ``heads/master``) let a delivery flow confirm a landed commit is reachable
on the base branch.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import get_base_url, get_service
from blizzard_mock.forge.domain.service import ForgeService

router = APIRouter(tags=["git"])


@router.get("/repos/{owner}/{repo}/commits/{ref}")
def get_commit(
    owner: str,
    repo: str,
    ref: str,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    return ser.commit_json(f"{owner}/{repo}", service.commit(owner, repo, ref), base_url)


@router.get("/repos/{owner}/{repo}/git/ref/{ref:path}")
def get_ref(
    owner: str,
    repo: str,
    ref: str,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    # GitHub addresses a ref as e.g. ``heads/master``; resolve its short name.
    short = ref.removeprefix("heads/")
    sha = service.resolve_ref(owner, repo, short)
    return ser.ref_json(f"{owner}/{repo}", f"refs/{ref}", sha, base_url)
