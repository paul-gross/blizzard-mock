"""Independent mirror of the runner's claim-refusal discriminator."""

from typing import Any, Literal

from pydantic import BaseModel, ValidationError

ClaimRefusal = Literal["terminal", "dependency", "incompatible", "conflict", "paused", "unexpected"]


class _Claim(BaseModel):
    chunk_id: str
    detail: str = ""


class _Terminal(_Claim):
    status: str


class _Dependency(_Claim):
    prerequisite_chunk_id: str


class _Incompatible(_Claim):
    incompatible_runner_id: str
    reason: str = "capability"


class _Conflict(_Claim):
    held_by_runner_id: str


class _Paused(_Claim):
    runner_id: str


_MODELS: dict[str, type[_Claim]] = {
    "terminal": _Terminal,
    "dependency": _Dependency,
    "incompatible": _Incompatible,
    "conflict": _Conflict,
    "paused": _Paused,
}
_TYPES: dict[tuple[int, str], ClaimRefusal] = {
    (409, "urn:blizzard:problem:claim-terminal"): "terminal",
    (409, "urn:blizzard:problem:claim-not-ready"): "terminal",
    (409, "urn:blizzard:problem:claim-dependency"): "dependency",
    (409, "urn:blizzard:problem:claim-incompatible"): "incompatible",
    (409, "urn:blizzard:problem:claim-conflict"): "conflict",
    (403, "urn:blizzard:problem:claim-paused"): "paused",
    (403, "urn:blizzard:problem:claim-runner-retired"): "paused",
    (403, "urn:blizzard:problem:claim-runner-unregistered"): "paused",
    (403, "urn:blizzard:problem:claim-runner-version-refused"): "paused",
}


def classify_claim_refusal(status: int, body: dict[str, Any]) -> ClaimRefusal:
    """Classify only a recognized status/type pair; legacy fallback requires no type."""
    if status not in (403, 409):
        return "unexpected"
    if "type" in body:
        kind = body["type"]
        refusal = _TYPES.get((status, kind), "unexpected") if isinstance(kind, str) else "unexpected"
    elif status == 403:
        refusal = "paused"
    elif "status" in body:
        refusal = "terminal"
    elif "prerequisite_chunk_id" in body:
        refusal = "dependency"
    elif "incompatible_runner_id" in body:
        refusal = "incompatible"
    else:
        refusal = "conflict"
    if refusal != "unexpected":
        try:
            _MODELS[refusal].model_validate(body)
        except ValidationError:
            return "unexpected"
    return refusal
