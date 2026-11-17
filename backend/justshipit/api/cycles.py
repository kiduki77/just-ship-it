"""The owner's cycle on the console: the dashboard's board in one read, the cycle history, one cycle in full, and the
one write path for every decision the owner makes about a cycle."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException

from justshipit.api.console import _act
from justshipit.contracts.cycle import CycleBoardOut, CycleRow
from justshipit.db.models import Cycle
from justshipit.db.session import session_scope
from justshipit.services import app_settings, cycle, cycle_board
from justshipit.services.control import NotFound

router = APIRouter()


@router.get("/businesses/{business_id}/cycle-board", response_model=CycleBoardOut)
def cycle_board_view(business_id: int) -> CycleBoardOut:
    with session_scope() as s:
        return cycle_board.board(s, business_id, lang=app_settings.language())


@router.get("/businesses/{business_id}/cycles", response_model=list[CycleRow])
def cycle_history(business_id: int) -> list[CycleRow]:
    with session_scope() as s:
        return cycle_board.history(s, business_id)


@router.get("/cycles/{cycle_id}")
def cycle_detail(cycle_id: int) -> dict[str, Any]:
    lang = app_settings.language()
    with session_scope() as s:
        try:
            return cycle_board.detail(s, cycle_id, lang=lang)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc


@router.post("/cycles/{cycle_id}/decisions", response_model=CycleBoardOut)
def cycle_decide(cycle_id: int, body: Annotated[cycle.Decision, Body()]) -> CycleBoardOut:
    """The owner's decision on a card. Refused with 409 and a sentence when the card is old or the move is not open;
    the console then reads the board again."""
    lang = app_settings.language()

    def act(s, cid):
        cycle.decide(s, cid, body, by="owner", lang=lang)
        s.flush()
        return cycle_board.board(s, s.get(Cycle, cid).business_id, lang=lang)
    return _act(act, cycle_id)
