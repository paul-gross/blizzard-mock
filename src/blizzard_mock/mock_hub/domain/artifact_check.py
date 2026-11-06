"""Mirrors the garden artifact formats used by stage-time checks."""

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


NonBlank = Annotated[str, AfterValidator(_nonblank)]


class Add(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    op: Literal["add"] = "add"
    class_: NonBlank = Field(alias="class")
    locus: NonBlank
    summary: NonBlank
    introduced: str | None = None
    ref: str | None = None


class Observed(BaseModel):
    op: Literal["observed"] = "observed"
    id: str


class Gone(BaseModel):
    op: Literal["gone"] = "gone"
    id: str
    note: str


Finding = Annotated[Add | Observed | Gone, Field(discriminator="op")]


class Delta(BaseModel):
    scope: str
    revisions: dict[str, str] = Field(default_factory=dict)
    measurement: str | None = None
    findings: list[Finding] = Field(default_factory=list[Finding])


class Candidate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    ref: str
    class_: str = Field(alias="class")
    title: str
    body: str
    findings: list[str] = Field(default_factory=list)


_FORMATS = {
    "finding-delta": TypeAdapter(Delta),
    "garden-proposal-candidate": TypeAdapter(list[Candidate]),
}


def refusal(name: str, content: str, formats: Sequence[str]) -> str | None:
    for shape, adapter in _FORMATS.items():
        if shape not in formats:
            continue
        try:
            adapter.validate_json(content)
        except ValidationError as exc:
            errors = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}" for error in exc.errors()
            )
            return f"artifact {name!r} does not match the {shape} shape: {errors}"
    return None
