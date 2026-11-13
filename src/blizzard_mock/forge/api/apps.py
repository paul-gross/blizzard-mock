"""GitHub App routes — an installation read and the installation-token mint, both authenticated by
an App JWT rather than a token. Controllers hold only ``AppService`` (``bzh:controller-read-only``)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse

from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.deps import AppTokenBody, get_app_service
from blizzard_mock.forge.domain.app_service import AppService

router = APIRouter(tags=["apps"])


@router.get("/app/installations/{installation_id}")
def get_installation(
    installation_id: int,
    apps: Annotated[AppService, Depends(get_app_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    return ser.installation_json(apps.get_installation(authorization, installation_id))


@router.post("/app/installations/{installation_id}/access_tokens", status_code=201)
def create_access_token(
    installation_id: int,
    apps: Annotated[AppService, Depends(get_app_service)],
    body: AppTokenBody | None = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    token = apps.mint(authorization, installation_id, body.permissions if body is not None else None)
    return JSONResponse(status_code=201, content=ser.access_token_json(token))
