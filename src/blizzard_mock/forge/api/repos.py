"""Repository routes — ``GET /repos/{owner}/{repo}``."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import get_base_url, get_service
from blizzard_mock.forge.domain.service import ForgeService

router = APIRouter(tags=["repos"])


@router.get("/repos/{owner}/{repo}")
def get_repo(
    owner: str,
    repo: str,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    return ser.repo_json(service.get_repo(owner, repo), base_url)
