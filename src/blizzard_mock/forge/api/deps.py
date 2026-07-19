"""Shared FastAPI dependencies and request bodies for the forge routers.

The composition root stashes the wired ``ForgeService`` and ``base_url`` on
``app.state``; routers reach them through these dependencies rather than
constructing collaborators themselves (``bzh:dependency-injection``).
"""

from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel, Field

from blizzard_mock.forge.domain.models import MergeMethod
from blizzard_mock.forge.domain.service import ForgeService


def get_service(request: Request) -> ForgeService:
    service: ForgeService = request.app.state.service
    return service


def get_base_url(request: Request) -> str:
    base_url: str = request.app.state.base_url
    return base_url


class CreateIssueBody(BaseModel):
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    user: str = "octocat"


class CreateCommentBody(BaseModel):
    body: str
    user: str = "octocat"


class CreatePullBody(BaseModel):
    title: str
    head: str
    base: str
    body: str = ""
    user: str = "octocat"


class MergeBody(BaseModel):
    commit_message: str | None = None
    sha: str | None = None
    merge_method: MergeMethod = MergeMethod.MERGE
    user: str = "octocat"


class UpdatePullBody(BaseModel):
    state: str


class UpdateBranchBody(BaseModel):
    expected_head_sha: str | None = None
