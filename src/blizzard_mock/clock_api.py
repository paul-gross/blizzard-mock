"""Shared test-control HTTP surface for controlled clocks."""

import re
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blizzard_mock.clock import ClockBackwardsError, ControlledClock


class ClockMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: datetime | None = None
    advance_seconds: Annotated[float | None, Field(strict=True, ge=0, allow_inf_nan=False)] = None

    @field_validator("to", mode="before")
    @classmethod
    def validate_instant_format(cls, value: object) -> object:
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})", value
        ):
            raise ValueError("instant must be an ISO 8601 datetime with an offset")
        return value

    @model_validator(mode="after")
    def validate_move(self) -> "ClockMove":
        if self.model_fields_set not in ({"to"}, {"advance_seconds"}) or (
            self.to is None and self.advance_seconds is None
        ):
            raise ValueError("provide exactly one clock move")
        if self.to is not None and self.to.utcoffset() is None:
            raise ValueError("instant must have a timezone")
        return self


def clock_router(clock: ControlledClock) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    @router.get("/_clock")
    def read_clock() -> dict[str, datetime]:
        return {"now": clock.now()}

    @router.post("/_clock", response_model=None)
    def move_clock(move: ClockMove) -> dict[str, datetime] | JSONResponse:
        try:
            if move.to is not None:
                previous, now = clock.set(move.to)
            else:
                assert move.advance_seconds is not None
                previous, now = clock.advance(timedelta(seconds=move.advance_seconds))
        except ClockBackwardsError:
            return JSONResponse(status_code=409, content={"code": "clock_backwards"})
        except OverflowError as exc:
            raise HTTPException(status_code=422, detail="clock move out of range") from exc
        return {"previous": previous, "now": now}

    return router
