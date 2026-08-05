"""Label routes — repo-level label definitions and issue label assignment.

GitHub-shaped label surface: repo-level label CRUD and issue-level label
add/remove, mirroring GitHub's REST v3 label endpoints.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import CreateLabelBody, get_service
from blizzard_mock.forge.domain.service import ForgeService

router = APIRouter(tags=["labels"])


@router.get("/repos/{owner}/{repo}/labels")
def list_labels(
    owner: str,
    repo: str,
    service: Annotated[ForgeService, Depends(get_service)],
) -> list[dict[str, Any]]:
    return [ser.label_json(label) for label in service.list_labels(owner, repo)]


@router.post("/repos/{owner}/{repo}/labels", status_code=201)
def create_label(
    owner: str,
    repo: str,
    body: CreateLabelBody,
    service: Annotated[ForgeService, Depends(get_service)],
) -> dict[str, Any]:
    label = service.create_label(owner, repo, label_name=body.name)
    return ser.label_json(label)


@router.get("/repos/{owner}/{repo}/issues/{number}/labels")
def list_issue_labels(
    owner: str,
    repo: str,
    number: int,
    service: Annotated[ForgeService, Depends(get_service)],
) -> list[dict[str, Any]]:
    issue = service.get_issue(owner, repo, number)
    return ser.issue_labels_json(issue.labels)


@router.post("/repos/{owner}/{repo}/issues/{number}/labels")
def add_issue_labels(
    owner: str,
    repo: str,
    number: int,
    body: list[str],
    service: Annotated[ForgeService, Depends(get_service)],
) -> list[dict[str, Any]]:
    issue = service.add_issue_labels(owner, repo, number, labels=body)
    return ser.issue_labels_json(issue.labels)


@router.delete("/repos/{owner}/{repo}/issues/{number}/labels/{name}")
def remove_issue_label(
    owner: str,
    repo: str,
    number: int,
    name: str,
    service: Annotated[ForgeService, Depends(get_service)],
) -> list[dict[str, Any]]:
    issue = service.remove_issue_label(owner, repo, number, label_name=name)
    return ser.issue_labels_json(issue.labels)
