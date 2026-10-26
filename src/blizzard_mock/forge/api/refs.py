"""Git-data routes against the bare repo: commits, refs, GitHub-style comparisons,
branch creation, fast-forward, deletion, and ``POST /merges``. ``.../check-runs``
answers revision-armed or lever-derived runs; check-run and Actions job re-requests
are acknowledged without resetting anything.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import CreateRefBody, MergeBranchesBody, UpdateRefBody, get_base_url, get_service
from blizzard_mock.forge.domain.errors import ValidationError
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


@router.get("/repos/{owner}/{repo}/commits/{ref}/check-runs")
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


@router.post("/repos/{owner}/{repo}/check-runs/{check_run_id}/rerequest", status_code=201)
def rerequest_check_run(owner: str, repo: str, check_run_id: int) -> dict[str, Any]:
    # Derived check runs have nothing to reset; acknowledge with GitHub's empty 201.
    return {}


@router.post("/repos/{owner}/{repo}/actions/jobs/{job_id}/rerun", status_code=201)
def rerun_job(owner: str, repo: str, job_id: int) -> dict[str, Any]:
    # A check run's id doubles as its Actions job id; derived runs have nothing to reset.
    return {}


@router.get("/repos/{owner}/{repo}/compare/{basehead:path}")
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


@router.get("/repos/{owner}/{repo}/git/ref/{ref:path}")
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


@router.patch("/repos/{owner}/{repo}/git/refs/{ref:path}")
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


@router.post("/repos/{owner}/{repo}/git/refs", status_code=201)
def create_ref(
    owner: str,
    repo: str,
    body: CreateRefBody,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> JSONResponse:
    sha = service.create_ref(owner, repo, body.ref, sha=body.sha)
    return JSONResponse(status_code=201, content=ser.ref_json(f"{owner}/{repo}", body.ref, sha, base_url))


@router.delete("/repos/{owner}/{repo}/git/refs/heads/{branch:path}", status_code=204)
def delete_ref(
    owner: str,
    repo: str,
    branch: str,
    service: Annotated[ForgeService, Depends(get_service)],
) -> Response:
    service.delete_ref(owner, repo, branch)
    return Response(status_code=204)


@router.post("/repos/{owner}/{repo}/merges", status_code=201)
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
