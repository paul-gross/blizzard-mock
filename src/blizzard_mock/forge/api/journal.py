"""Journal routes — read and clear the request journal. Exempt from the request-scoped levers
and never journaled themselves."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from blizzard_mock.forge.api.deps import get_journal
from blizzard_mock.forge.domain.journal import IRequestJournal

router = APIRouter(prefix="/_journal", tags=["journal"])


@router.get("")
def list_journal(journal: Annotated[IRequestJournal, Depends(get_journal)]) -> dict[str, Any]:
    return {"entries": [entry.model_dump() for entry in journal.entries()]}


@router.delete("")
def clear_journal(journal: Annotated[IRequestJournal, Depends(get_journal)]) -> dict[str, Any]:
    journal.clear()
    return {"cleared": True}
