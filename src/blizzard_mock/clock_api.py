"""Shared test-control HTTP surface for controlled clocks."""

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from blizzard_mock.clock import ClockBackwardsError, ControlledClock


class ClockMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: datetime | None = None
    advance_seconds: Annotated[float | None, Field(ge=0, allow_inf_nan=False)] = None

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
