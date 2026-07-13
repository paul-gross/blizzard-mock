"""Pull-request routes — the delivery seam (D-057 / D-058 / D-065).

Create/get/list PRs, live mergeability, the real merge, the merged-check, and a
close-without-merge disposition — all against real refs in the backing bare repo.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import (
    CreatePullBody,
    MergeBody,
    UpdatePullBody,
    get_base_url,
    get_service,
)
from blizzard_mock.forge.domain.errors import ValidationError
from blizzard_mock.forge.domain.models import State
from blizzard_mock.forge.domain.service import ForgeService

router = APIRouter(tags=["pulls"])


@router.get("/repos/{owner}/{repo}/pulls")
def list_pulls(
    owner: str,
    repo: str,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
    state: str = "open",
) -> list[dict[str, Any]]:
    return [ser.pull_json(f"{owner}/{repo}", v, base_url) for v in service.list_pulls(owner, repo, state)]


@router.post("/repos/{owner}/{repo}/pulls", status_code=201)
def create_pull(
    owner: str,
    repo: str,
    body: CreatePullBody,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> JSONResponse:
    view = service.create_pull(
        owner, repo, title=body.title, body=body.body, head=body.head, base=body.base, user=body.user
    )
    return JSONResponse(status_code=201, content=ser.pull_json(f"{owner}/{repo}", view, base_url))


@router.get("/repos/{owner}/{repo}/pulls/{number}")
def get_pull(
    owner: str,
    repo: str,
    number: int,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    return ser.pull_json(f"{owner}/{repo}", service.get_pull(owner, repo, number), base_url)


@router.patch("/repos/{owner}/{repo}/pulls/{number}")
def update_pull(
    owner: str,
    repo: str,
    number: int,
    body: UpdatePullBody,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    if body.state not in ("open", "closed"):
        raise ValidationError(f"invalid state: {body.state}")
    view = service.set_pull_state(owner, repo, number, state=State(body.state))
    return ser.pull_json(f"{owner}/{repo}", view, base_url)


@router.put("/repos/{owner}/{repo}/pulls/{number}/merge")
def merge_pull(
    owner: str,
    repo: str,
    number: int,
    body: MergeBody,
    service: Annotated[ForgeService, Depends(get_service)],
) -> dict[str, Any]:
    result = service.merge_pull(
        owner,
        repo,
        number,
        method=body.merge_method,
        message=body.commit_message,
        sha=body.sha,
        user=body.user,
    )
    return ser.merge_result_json(result)


@router.get("/repos/{owner}/{repo}/pulls/{number}/merge")
def is_merged(
    owner: str,
    repo: str,
    number: int,
    service: Annotated[ForgeService, Depends(get_service)],
) -> Response:
    merged = service.is_merged(owner, repo, number)
    return Response(status_code=204 if merged else 404)
