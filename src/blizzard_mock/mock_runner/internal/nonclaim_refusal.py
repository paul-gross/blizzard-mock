"""Independent mirror of runner identity and version refusal discrimination."""

from typing import Any, Literal

from pydantic import BaseModel, ValidationError

_Kind = Literal["identity", "version", "unexpected"]


class _Identity(BaseModel):
    reason: str
    runner_id: str | None = None


class _Version(BaseModel):
    reason: str
    runner_version: str | None = None
    hub_version: str
    floor: str
    detail: str


def classify_nonclaim_refusal(status: int, body: dict[str, Any]) -> _Kind:
    """Recognize known status/type pairs; only an absent type permits legacy parsing."""
    candidates: dict[int, tuple[str, _Kind, type[BaseModel]]] = {
        401: ("urn:blizzard:problem:runner-identity", "identity", _Identity),
        409: ("urn:blizzard:problem:runner-version", "version", _Version),
    }
    candidate = candidates.get(status)
    if candidate is None:
        return "unexpected"
    kind, name, model = candidate
    if "type" in body and body["type"] != kind:
        return "unexpected"
    try:
        model.model_validate(body)
    except ValidationError:
        return "unexpected"
    return name
