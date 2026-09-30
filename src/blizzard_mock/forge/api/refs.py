"""Git-data routes — commits and refs, resolved against the bare repo.

Resolves refs to commits, compares two refs GitHub-style
(``GET .../compare/{base}...{head}``), and supports an atomic compare-and-swap ref update
(``PATCH .../git/refs/{ref}``). ``.../check-runs`` derives runs
live from the active lever set.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import UpdateRefBody, get_base_url, get_service
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
