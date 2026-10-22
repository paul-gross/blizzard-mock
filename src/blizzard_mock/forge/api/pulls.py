"""Pull-request routes — the delivery seam (D-057 / D-058 / D-065).

Create/get/list PRs, live mergeability, the real merge, the merged-check, and a
close-without-merge disposition — all against real refs in the backing bare repo.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import (
    CreatePullBody,
    MergeBody,
    UpdateBranchBody,
    UpdatePullBody,
    get_base_url,
    get_service,
)
from blizzard_mock.forge.domain.errors import ValidationError
from blizzard_mock.forge.domain.models import State
from blizzard_mock.forge.domain.service import ForgeService, PullPage

router = APIRouter(tags=["pulls"])


@router.get("/repos/{owner}/{repo}/pulls")
def list_pulls(
    owner: str,
    repo: str,
    request: Request,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
    state: str = "open",
    base: str | None = None,
    head: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    page: int = 1,
    per_page: int | None = None,
) -> JSONResponse:
    result = service.list_pulls(
        owner, repo, state, base=base, head=head, sort=sort, direction=direction, page=page, per_page=per_page
    )
    link = _link_header(f"{base_url}{request.url.path}", dict(request.query_params), result)
    return JSONResponse(
        content=[ser.pull_json(f"{owner}/{repo}", v, base_url) for v in result.views],
        headers={"Link": link} if link else None,
    )


def _link_header(url: str, query: dict[str, str], result: PullPage) -> str:
    """GitHub's ``Link`` header for a paged listing: ``next``/``last`` while a later page
    exists, ``prev``/``first`` past page 1. Empty when the answer is not paged or is the only page."""
    if result.per_page is None:
        return ""

    def page_url(number: int) -> str:
        return f"{url}?{urlencode({**query, 'page': number, 'per_page': result.per_page})}"

    last = result.last_page
    rels: list[tuple[str, int]] = []
    if result.page > 1:
        rels += [("prev", min(result.page - 1, last)), ("first", 1)]
    if result.page < last:
        rels += [("next", result.page + 1), ("last", last)]
    return ", ".join(f'<{page_url(number)}>; rel="{rel}"' for rel, number in rels)


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


@router.put("/repos/{owner}/{repo}/pulls/{number}/update-branch", status_code=202)
def update_branch(
    owner: str,
    repo: str,
    number: int,
    body: UpdateBranchBody,
    service: Annotated[ForgeService, Depends(get_service)],
) -> JSONResponse:
    message = service.update_branch(owner, repo, number, expected_head_sha=body.expected_head_sha)
    return JSONResponse(status_code=202, content={"message": message})


@router.get("/repos/{owner}/{repo}/pulls/{number}/merge")
def is_merged(
    owner: str,
    repo: str,
    number: int,
    service: Annotated[ForgeService, Depends(get_service)],
) -> Response:
    merged = service.is_merged(owner, repo, number)
    return Response(status_code=204 if merged else 404)
