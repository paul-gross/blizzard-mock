"""Issue routes — the work-source seam (D-047 / D-074).

List/get/create issues and their comment threads, served vendor-native so the
hub's GitHub-shaped pass-through reads are exercised against a GitHub surface.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import CreateCommentBody, CreateIssueBody, get_base_url, get_service
from blizzard_mock.forge.domain.service import ForgeService

router = APIRouter(tags=["issues"])


@router.get("/repos/{owner}/{repo}/issues")
def list_issues(
    owner: str,
    repo: str,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
    state: str = "open",
) -> list[dict[str, Any]]:
    issues = service.list_issues(owner, repo, state)
    return [ser.issue_json(f"{owner}/{repo}", issue, base_url) for issue in issues]


@router.get("/repos/{owner}/{repo}/issues/{number}")
def get_issue(
    owner: str,
    repo: str,
    number: int,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    return ser.issue_json(f"{owner}/{repo}", service.get_issue(owner, repo, number), base_url)


@router.post("/repos/{owner}/{repo}/issues", status_code=201)
def create_issue(
    owner: str,
    repo: str,
    body: CreateIssueBody,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> JSONResponse:
    issue = service.create_issue(owner, repo, title=body.title, body=body.body, user=body.user, labels=body.labels)
    return JSONResponse(status_code=201, content=ser.issue_json(f"{owner}/{repo}", issue, base_url))


@router.get("/repos/{owner}/{repo}/issues/{number}/comments")
def list_comments(
    owner: str,
    repo: str,
    number: int,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> list[dict[str, Any]]:
    comments = service.list_issue_comments(owner, repo, number)
    return [ser.comment_json(f"{owner}/{repo}", number, c, base_url) for c in comments]


@router.post("/repos/{owner}/{repo}/issues/{number}/comments", status_code=201)
def create_comment(
    owner: str,
    repo: str,
    number: int,
    body: CreateCommentBody,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> JSONResponse:
    comment = service.create_issue_comment(owner, repo, number, body=body.body, user=body.user)
    return JSONResponse(status_code=201, content=ser.comment_json(f"{owner}/{repo}", number, comment, base_url))
