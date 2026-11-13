"""Shared FastAPI dependencies and request bodies for the forge routers.

The composition root stashes the wired ``ForgeService`` and ``base_url`` on
``app.state``; routers reach them through these dependencies rather than
constructing collaborators themselves (``bzh:dependency-injection``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.params import Depends as DependsMarker
from pydantic import BaseModel, Field

from blizzard_mock.forge.domain.app_service import AppService
from blizzard_mock.forge.domain.models import MergeMethod
from blizzard_mock.forge.domain.service import ForgeService


def get_service(request: Request) -> ForgeService:
    service: ForgeService = request.app.state.service
    return service


def get_app_service(request: Request) -> AppService:
    apps: AppService = request.app.state.apps
    return apps


def requires(permission: str, level: str) -> DependsMarker:
    """A route's declared permission: under ``strict_tokens`` the credential must hold
    ``permission: level`` on the route's repository. Every ``/repos/{owner}/{repo}/…`` route
    declares one; the declaration is readable off the dependency as ``.requires``."""

    def dependency(
        owner: str, repo: str, request: Request, apps: Annotated[AppService, Depends(get_app_service)]
    ) -> None:
        apps.authorize(request.headers.get("authorization"), f"{owner}/{repo}", permission, level)

    setattr(dependency, "requires", (permission, level))  # noqa: B010
    return Depends(dependency)


def get_base_url(request: Request) -> str:
    base_url: str = request.app.state.base_url
    return base_url


class CreateIssueBody(BaseModel):
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    user: str = "octocat"


class CreateLabelBody(BaseModel):
    name: str


class CreateCommentBody(BaseModel):
    body: str
    user: str = "octocat"


class UpdateIssueStateBody(BaseModel):
    state: str
    state_reason: str | None = None


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


class UpdateRefBody(BaseModel):
    sha: str
    force: bool = False


class CreateRefBody(BaseModel):
    ref: str
    sha: str


class MergeBranchesBody(BaseModel):
    base: str
    head: str
    commit_message: str | None = None


class CreateRepoBody(BaseModel):
    owner: str
    name: str
    seed: str = "toy-api"


class AppTokenBody(BaseModel):
    permissions: dict[str, str] | None = None
