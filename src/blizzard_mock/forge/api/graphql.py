"""The GraphQL batch read — ``POST /graphql``, one restricted query shape.

The hub's GitHub work source reads many issues in one request: aliased
``issueOrPullRequest(number:)`` fields under one ``repository(owner:, name:)``. This route is not a GraphQL
engine; it recognizes exactly that shape, so an unrecognized query answers a GraphQL ``errors`` entry rather than
a guess. Each alias answers from the in-memory issue state (a pull request answers as an issue, its title and body
from the shared number space); a missing number answers ``null`` for its alias with a ``NOT_FOUND`` error, as GitHub
does. Comments come only when the query selects ``comments``.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from blizzard_mock.forge.api.deps import get_service
from blizzard_mock.forge.api.serialization import pull_node_id
from blizzard_mock.forge.domain.errors import ForgeError, HeadMismatch, IssueNotFound
from blizzard_mock.forge.domain.models import Issue, PullRequest
from blizzard_mock.forge.domain.service import ForgeService

router = APIRouter(tags=["graphql"])

_ALIAS_RE = re.compile(r"(?P<alias>[A-Za-z_]\w*)\s*:\s*issueOrPullRequest\(\s*number\s*:\s*(?P<number>\d+)\s*\)")


def _graphql_error(message: str, kind: str = "BAD_REQUEST", path: list[str] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"type": kind, "message": message}
    if path is not None:
        error["path"] = path
    return error


def _update_branch(variables: dict[str, Any], service: ForgeService, request: Request) -> JSONResponse:
    data = variables.get("input", variables)
    if not isinstance(data, dict):
        return JSONResponse(content={"data": None, "errors": [_graphql_error("invalid input")]})
    data = cast(dict[str, Any], data)
    node_id = data.get("pullRequestId")
    expected = data.get("expectedHeadOid")
    method = data.get("updateMethod")
    try:
        decoded = base64.b64decode(node_id, validate=True).decode() if isinstance(node_id, str) else ""
        prefix, full = decoded.split(":", 1)
        repo, number = full.rsplit("#", 1)
        owner, name = repo.split("/", 1)
        if prefix != "PullRequest" or method not in ("REBASE", "MERGE") or not isinstance(expected, str):
            raise ValueError("invalid input")
        request.app.state.apps.authorize(request.headers.get("authorization"), repo, "pull_requests", "write")
        service.update_branch(owner, name, int(number), expected_head_sha=expected, rebase=method == "REBASE")
        view = service.get_pull(owner, name, int(number))
    except HeadMismatch as exc:
        return JSONResponse(
            content={
                "data": {"updatePullRequestBranch": None},
                "errors": [_graphql_error(f"Expected head OID does not match: {exc}", "UNPROCESSABLE")],
            }
        )
    except (ValueError, UnicodeError, binascii.Error, ForgeError) as exc:
        kind = "INTERNAL_SERVER_ERROR" if isinstance(exc, ForgeError) and exc.status >= 500 else "UNPROCESSABLE"
        return JSONResponse(
            content={"data": {"updatePullRequestBranch": None}, "errors": [_graphql_error(str(exc), kind)]}
        )
    return JSONResponse(
        content={"data": {"updatePullRequestBranch": {"pullRequest": {"id": node_id, "headRefOid": view.head_sha}}}}
    )


@router.post("/graphql")
def graphql(
    payload: dict[str, Any],
    service: Annotated[ForgeService, Depends(get_service)],
    request: Request,
) -> JSONResponse:
    query = str(payload.get("query") or "")
    variables: dict[str, Any] = payload.get("variables") or {}
    if "updatePullRequestBranch" in query:
        return _update_branch(variables, service, request)
    if "pullRequest" in query and "issueOrPullRequest" not in query:
        owner, name, number = variables.get("owner"), variables.get("name"), variables.get("number")
        if isinstance(owner, str) and isinstance(name, str) and isinstance(number, int):
            try:
                service.get_pull(owner, name, number)
                return JSONResponse(
                    content={"data": {"repository": {"pullRequest": {"id": pull_node_id(f"{owner}/{name}", number)}}}}
                )
            except ForgeError as exc:
                return JSONResponse(
                    content={
                        "data": {"repository": {"pullRequest": None}},
                        "errors": [_graphql_error(exc.message, "NOT_FOUND")],
                    }
                )
    owner: object = variables.get("owner")
    name: object = variables.get("name")
    aliases = _ALIAS_RE.findall(query)
    if not isinstance(owner, str) or not isinstance(name, str) or not aliases:
        message = "unsupported query: expected repository(owner:, name:) with aliased issueOrPullRequest(number:)"
        return JSONResponse(content={"data": None, "errors": [_graphql_error(message)]})
    with_comments = "comments" in query
    repo: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    for alias, number in aliases:
        try:
            item: Issue | PullRequest = service.get_issue(owner, name, int(number))
        except IssueNotFound:
            try:
                item = service.get_pull(owner, name, int(number)).pull
            except ForgeError as exc:
                repo[alias] = None
                errors.append(_graphql_error(exc.message, "NOT_FOUND", ["repository", alias]))
                continue
        except ForgeError as exc:
            repo[alias] = None
            errors.append(_graphql_error(exc.message, "NOT_FOUND", ["repository", alias]))
            continue
        node: dict[str, Any] = {"title": item.title, "body": item.body}
        if with_comments:
            node["comments"] = {"nodes": [{"body": c.body} for c in item.comments]}
        repo[alias] = node
    answer: dict[str, Any] = {"data": {"repository": repo}}
    if errors:
        answer["errors"] = errors
    return JSONResponse(content=answer)
