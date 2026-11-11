"""Git-data routes against the bare repo: commits, refs, GitHub-style comparisons,
branch creation, fast-forward, deletion, and ``POST /merges``. ``.../check-runs``
answers revision-armed or lever-derived runs; check-run and Actions job re-requests
are acknowledged without resetting anything.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import (
    CreateRefBody,
    MergeBranchesBody,
    UpdateRefBody,
    get_base_url,
    get_service,
    requires,
)
from blizzard_mock.forge.domain.errors import ValidationError
from blizzard_mock.forge.domain.service import ForgeService

router = APIRouter(tags=["git"])


@router.get("/repos/{owner}/{repo}/commits/{ref}", dependencies=[requires("contents", "read")])
def get_commit(
    owner: str,
    repo: str,
    ref: str,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    return ser.commit_json(f"{owner}/{repo}", service.commit(owner, repo, ref), base_url)


@router.get("/repos/{owner}/{repo}/commits/{ref}/check-runs", dependencies=[requires("checks", "read")])
def list_check_runs(
    owner: str,
    repo: str,
    ref: str,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    runs = service.list_check_runs(owner, repo, ref)
    repo_full = f"{owner}/{repo}"
    return {
        "total_count": len(runs),
        "check_runs": [ser.check_run_json(repo_full, run, base_url) for run in runs],
    }


@router.get("/repos/{owner}/{repo}/commits/{ref}/statuses", dependencies=[requires("statuses", "read")])
def list_statuses(
    owner: str,
    repo: str,
    ref: str,
    service: Annotated[ForgeService, Depends(get_service)],
    request: Request,
    response: Response,
    page: int = 1,
    per_page: int = 30,
) -> list[dict[str, Any]]:
    _, statuses = service.list_statuses(owner, repo, ref)
    page = max(page, 1)
    per_page = min(max(per_page, 1), 100)
    last = max(1, (len(statuses) + per_page - 1) // per_page)
    if page < last:
        next_url = request.url.include_query_params(page=page + 1, per_page=per_page)
        last_url = request.url.include_query_params(page=last, per_page=per_page)
        response.headers["Link"] = f'<{next_url}>; rel="next", <{last_url}>; rel="last"'
    return [status.model_dump() for status in statuses[(page - 1) * per_page : page * per_page]]


@router.get("/repos/{owner}/{repo}/commits/{ref}/status", dependencies=[requires("statuses", "read")])
def combined_status(
    owner: str,
    repo: str,
    ref: str,
    service: Annotated[ForgeService, Depends(get_service)],
) -> dict[str, Any]:
    sha, history = service.list_statuses(owner, repo, ref)
    latest: dict[str, Any] = {}
    for status in history:
        latest.setdefault(status.context, status)
    states = [status.state for status in latest.values()]
    state = (
        "failure"
        if any(s in ("failure", "error") for s in states)
        else ("pending" if not states or "pending" in states else "success")
    )
    return {
        "state": state,
        "sha": sha,
        "total_count": len(latest),
        "statuses": [status.model_dump() for status in latest.values()],
    }


@router.post(
    "/repos/{owner}/{repo}/check-runs/{check_run_id}/rerequest",
    dependencies=[requires("checks", "write")],
    status_code=201,
)
def rerequest_check_run(owner: str, repo: str, check_run_id: int) -> dict[str, Any]:
    # Derived check runs have nothing to reset; acknowledge with GitHub's empty 201.
    return {}


@router.post(
    "/repos/{owner}/{repo}/actions/jobs/{job_id}/rerun", dependencies=[requires("actions", "write")], status_code=201
)
def rerun_job(owner: str, repo: str, job_id: int) -> dict[str, Any]:
    # A check run's id doubles as its Actions job id; derived runs have nothing to reset.
    return {}


@router.get("/repos/{owner}/{repo}/compare/{basehead:path}", dependencies=[requires("contents", "read")])
def compare(
    owner: str,
    repo: str,
    basehead: str,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    # ``:path`` so slashed branch names survive; ``...`` never occurs in a ref name.
    base, sep, head = basehead.partition("...")
    if not sep or not base or not head:
        raise ValidationError(f"compare expects base...head, got {basehead!r}")
    return ser.compare_json(f"{owner}/{repo}", service.compare(owner, repo, base, head), base_url)


@router.get("/repos/{owner}/{repo}/git/ref/{ref:path}", dependencies=[requires("contents", "read")])
def get_ref(
    owner: str,
    repo: str,
    ref: str,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    short = ref.removeprefix("heads/")
    sha = service.resolve_ref(owner, repo, short)
    return ser.ref_json(f"{owner}/{repo}", f"refs/{ref}", sha, base_url)


@router.patch("/repos/{owner}/{repo}/git/refs/{ref:path}", dependencies=[requires("contents", "write")])
def update_ref(
    owner: str,
    repo: str,
    ref: str,
    body: UpdateRefBody,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    # GitHub addresses a ref as e.g. ``heads/main``; resolve its short name.
    short = ref.removeprefix("heads/")
    sha = service.update_ref(owner, repo, short, sha=body.sha, force=body.force)
    return ser.ref_json(f"{owner}/{repo}", f"refs/{ref}", sha, base_url)


@router.post("/repos/{owner}/{repo}/git/refs", dependencies=[requires("contents", "write")], status_code=201)
def create_ref(
    owner: str,
    repo: str,
    body: CreateRefBody,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> JSONResponse:
    sha = service.create_ref(owner, repo, body.ref, sha=body.sha)
    return JSONResponse(status_code=201, content=ser.ref_json(f"{owner}/{repo}", body.ref, sha, base_url))


@router.delete(
    "/repos/{owner}/{repo}/git/refs/heads/{branch:path}", dependencies=[requires("contents", "write")], status_code=204
)
def delete_ref(
    owner: str,
    repo: str,
    branch: str,
    service: Annotated[ForgeService, Depends(get_service)],
) -> Response:
    service.delete_ref(owner, repo, branch)
    return Response(status_code=204)


@router.post("/repos/{owner}/{repo}/merges", dependencies=[requires("contents", "write")], status_code=201)
def merge_branches(
    owner: str,
    repo: str,
    body: MergeBranchesBody,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> Response:
    commit = service.merge_branches(owner, repo, base=body.base, head=body.head, message=body.commit_message)
    if commit is None:
        return Response(status_code=204)
    return JSONResponse(status_code=201, content=ser.commit_json(f"{owner}/{repo}", commit, base_url))
