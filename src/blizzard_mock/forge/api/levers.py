"""Lever routes — the forge's first-class edge-state control surface.

Namespaced under ``/_levers`` (outside the GitHub surface). State levers arm and
persist; action levers (``externally_merged``, ``comment_midflight``) fire on
POST. These routes are exempt from the request-bending levers so a test can
always clear a lever it armed.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import get_service
from blizzard_mock.forge.domain.errors import ValidationError
from blizzard_mock.forge.domain.levers import ACTION_LEVERS, STATE_LEVERS, LeverKind, LeverParams
from blizzard_mock.forge.domain.service import ForgeService

router = APIRouter(prefix="/_levers", tags=["levers"])

_CATALOG: dict[str, str] = {
    LeverKind.EXTERNALLY_MERGED.value: "action: land the PR's head on base directly (D-065)",
    LeverKind.COMMENT_MIDFLIGHT.value: "action: append a comment to a live thread (D-074)",
    LeverKind.MERGE_CONFLICT.value: "state (per PR): mergeable=false, merge → 405",
    LeverKind.MERGE_REJECTED.value: "state (per PR): merge → 405 rejected by policy",
    LeverKind.RATE_LIMITED.value: "state (global/repo): requests → 403 rate-limit",
    LeverKind.TOKEN_REJECTED.value: "state (global/repo): requests → 401 bad credentials",
    LeverKind.UNREACHABLE.value: "state (global/repo): requests → 503 unreachable",
    LeverKind.STALE_BRANCH.value: "state (per PR): mergeable_state=behind, self-heals via update-branch",
    LeverKind.CHECKS_PENDING.value: "state (per PR): mergeable_state=blocked, required checks/reviews not green yet",
    LeverKind.CHECKS_FAILED.value: "state (per PR): failed check run on the PR head; mergeable_state=blocked",
    LeverKind.BASE_CHECKS_FAILED.value: "state (per repo): one completed/failure check run on the default branch",
}


def _parse_kind(kind: str) -> LeverKind:
    try:
        return LeverKind(kind)
    except ValueError as exc:
        raise ValidationError(f"unknown lever: {kind}") from exc


@router.get("")
def list_levers(service: Annotated[ForgeService, Depends(get_service)]) -> dict[str, Any]:
    return {
        "catalog": _CATALOG,
        "state_levers": sorted(k.value for k in STATE_LEVERS),
        "action_levers": sorted(k.value for k in ACTION_LEVERS),
        "active": [ser.lever_json(lever) for lever in service.list_levers()],
    }


@router.post("/reset")
def reset_levers(service: Annotated[ForgeService, Depends(get_service)]) -> dict[str, Any]:
    service.clear_all_levers()
    return {"cleared": True}


@router.post("/{kind}")
def arm_lever(
    kind: str,
    params: LeverParams,
    service: Annotated[ForgeService, Depends(get_service)],
) -> dict[str, Any]:
    parsed = _parse_kind(kind)
    lever = service.arm_lever(parsed, params)
    if lever is None:
        return {"fired": parsed.value, "repo": params.repo, "number": params.number}
    return {"armed": ser.lever_json(lever)}


@router.delete("/{kind}")
def clear_lever(
    kind: str,
    service: Annotated[ForgeService, Depends(get_service)],
    repo: str | None = None,
    number: int | None = None,
) -> dict[str, Any]:
    parsed = _parse_kind(kind)
    service.clear_lever(parsed, repo, number)
    return {"cleared": parsed.value, "repo": repo, "number": number}
