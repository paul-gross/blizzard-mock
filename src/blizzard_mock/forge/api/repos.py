"""Repository routes — ``GET /repos/{owner}/{repo}`` and the test-control ``POST /_repos``."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from blizzard_mock.fixture_workspace.seed import TOY_REPOS
from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import CreateRepoBody, get_base_url, get_service
from blizzard_mock.forge.domain.errors import ValidationError
from blizzard_mock.forge.domain.service import ForgeService

router = APIRouter(tags=["repos"])

#: Seed name → the files its repository starts with; ``TOY_REPOS`` taken as data and nothing more.
_SEEDS = {seed.name: seed.files for seed in TOY_REPOS}


@router.get("/repos/{owner}/{repo}")
def get_repo(
    owner: str,
    repo: str,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    return ser.repo_json(service.get_repo(owner, repo), base_url)


@router.post("/_repos", status_code=201)
def create_repo(body: CreateRepoBody, service: Annotated[ForgeService, Depends(get_service)]) -> JSONResponse:
    files = _SEEDS.get(body.seed)
    if files is None:
        raise ValidationError(f"unknown seed: {body.seed}")
    repo = service.create_repository(body.owner, body.name, files)
    return JSONResponse(
        status_code=201,
        content={
            "full_name": repo.full_name,
            "origin": service.repository_origin(repo),
            "default_branch": repo.default_branch,
        },
    )
