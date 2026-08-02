"""Git-data routes — commits and refs, resolved against the bare repo.

``GET /repos/{o}/{r}/commits/{ref}`` and ``GET /repos/{o}/{r}/git/ref/{ref}``
(e.g. ``heads/main``) let a delivery flow confirm a landed commit is reachable
on the base branch. ``PATCH /repos/{o}/{r}/git/refs/{ref}`` is the write
counterpart — an atomic compare-and-swap ref update that makes PR-free,
fast-forward delivery testable. ``GET /repos/{o}/{r}/commits/{ref}/check-runs``
is the check-runs surface a CI-watch delivery flow polls (blizzard#232) —
derived live from the active lever set, same as ``mergeable_state``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import UpdateRefBody, get_base_url, get_service
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


@router.get("/repos/{owner}/{repo}/git/ref/{ref:path}")
def get_ref(
    owner: str,
    repo: str,
    ref: str,
    service: Annotated[ForgeService, Depends(get_service)],
    base_url: Annotated[str, Depends(get_base_url)],
) -> dict[str, Any]:
    # GitHub addresses a ref as e.g. ``heads/main``; resolve its short name.
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
