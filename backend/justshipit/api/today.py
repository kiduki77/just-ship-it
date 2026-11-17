"""The home's read model: what only the owner can do today, for one business."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from justshipit.api import console
from justshipit.db.session import session_scope
from justshipit.services import app_settings, today
from justshipit.services.control import NotFound

router = APIRouter()


@router.get("/businesses/{business_id}/today")
def business_today(business_id: int, all: bool = False) -> dict[str, Any]:
    """The owner's list, most urgent first: the top three and the total, or every item with `all`."""
    with session_scope() as s:
        try:
            lang = app_settings.language()
            return today.build(s, business_id, lang=lang, everything=all, failed=console.failed_by_business(s, lang))
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
