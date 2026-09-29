"""Browser destinations for the PR and commit URLs emitted by the forge API."""

from __future__ import annotations

from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from blizzard_mock.forge.api.deps import get_service
from blizzard_mock.forge.domain.errors import BranchNotFound
from blizzard_mock.forge.domain.service import ForgeService

router = APIRouter(tags=["web"])


def _page(title: str, *lines: str) -> HTMLResponse:
    heading = escape(title)
    paragraphs = "".join(f"<p>{escape(line)}</p>" for line in lines)
    return HTMLResponse(
        f"<!doctype html><html lang='en'><meta charset='utf-8'><title>{heading}</title>"
        f"<main><h1>{heading}</h1>{paragraphs}</main></html>"
    )


@router.get("/{owner}/{repo}/pull/{number}", response_class=HTMLResponse)
def pull_page(
    owner: str, repo: str, number: int, service: Annotated[ForgeService, Depends(get_service)]
) -> HTMLResponse:
    pull = service.get_pull(owner, repo, number).pull
    return _page(
        f"{owner}/{repo} PR #{number}: {pull.title}",
        "Merged" if pull.merged else pull.state.value.title(),
        f"{pull.head} → {pull.base}",
        *([f"Merged commit: {pull.merge_commit_sha}"] if pull.merge_commit_sha else []),
    )


@router.get("/{owner}/{repo}/commit/{sha}", response_class=HTMLResponse)
def commit_page(
    owner: str, repo: str, sha: str, service: Annotated[ForgeService, Depends(get_service)]
) -> HTMLResponse:
    try:
        commit = service.commit(owner, repo, sha)
    except BranchNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _page(f"{owner}/{repo} commit {commit.sha}", commit.message)
