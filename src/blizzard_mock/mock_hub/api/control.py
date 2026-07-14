"""The mock hub's control plane — ``/_seed`` (state) and ``/_levers`` (edge states).

Namespaced outside the ``/api`` surface and exempt from the transport-edge levers so a
test can always seed a chunk and arm/clear a lever, even while the API is "unreachable".
``/_seed/chunk`` installs a scripted graph; ``/_levers`` is the first-class lever surface
(catalog + active, arm, clear, reset) — the same shape the forge established.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from blizzard_mock.levers import Lever, LeverParams
from blizzard_mock.mock_hub.api.deps import get_service
from blizzard_mock.mock_hub.domain.levers import CATALOG, HubLever
from blizzard_mock.mock_hub.domain.models import ChunkSpec
from blizzard_mock.mock_hub.domain.service import MockHubService

seed_router = APIRouter(prefix="/_seed", tags=["control"])
levers_router = APIRouter(prefix="/_levers", tags=["control"])


@seed_router.post("/chunk", status_code=201)
def seed_chunk(spec: ChunkSpec, service: Annotated[MockHubService, Depends(get_service)]) -> dict[str, str]:
    chunk = service.seed_chunk(spec)
    return {"chunk_id": chunk.chunk_id, "graph_id": chunk.graph_id}


@seed_router.post("/reset")
def reset(service: Annotated[MockHubService, Depends(get_service)]) -> dict[str, bool]:
    service.reset()
    return {"reset": True}


@levers_router.get("")
def list_levers(service: Annotated[MockHubService, Depends(get_service)]) -> dict[str, Any]:
    return {
        "catalog": CATALOG,
        "levers": sorted(k.value for k in HubLever),
        "active": [lever.model_dump() for lever in service.levers.active()],
    }


@levers_router.post("/reset")
def reset_levers(service: Annotated[MockHubService, Depends(get_service)]) -> dict[str, bool]:
    service.levers.clear_all()
    return {"cleared": True}


@levers_router.post("/{kind}")
def arm_lever(
    kind: str, params: LeverParams, service: Annotated[MockHubService, Depends(get_service)]
) -> dict[str, Any]:
    valid = {k.value for k in HubLever}
    if kind not in valid:
        return {"error": f"unknown lever {kind!r}", "levers": sorted(valid)}
    lever = Lever(kind=kind, chunk_id=params.chunk_id, remaining=params.remaining, payload=params.payload)
    service.levers.arm(lever)
    return {"armed": lever.model_dump()}


@levers_router.delete("/{kind}")
def clear_lever(
    kind: str, service: Annotated[MockHubService, Depends(get_service)], chunk_id: str | None = None
) -> dict[str, Any]:
    service.levers.clear(kind, chunk_id)
    return {"cleared": kind, "chunk_id": chunk_id}
