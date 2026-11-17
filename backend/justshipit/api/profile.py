"""The customer description: what the console shows for a business, and the owner's edits to it. An edit is
reversible, so it files no decision card; the activity row it writes is what Undo reads. Each route reads the owner's
language once."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from justshipit.db.session import session_scope
from justshipit.services import app_settings
from justshipit.services.control import NotFound
from justshipit.services.profile import edit, store, view

router = APIRouter()


class EditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    if_live: list[int] = Field(default_factory=list)
    codes: list[str] = Field(default_factory=list)
    sentence: str | None = None
    text: str | None = None


class UndoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: int


def _fail(exc: Exception, lang) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(404, str(exc))
    status = 409 if isinstance(exc, store.StaleWrite) else 422
    return HTTPException(status, exc.words(lang))  # type: ignore[attr-defined]


@router.get("/businesses/{business_id}/profile")
def profile_view(business_id: int) -> dict[str, Any]:
    lang = app_settings.language()
    with session_scope() as s:
        try:
            return view.build(s, business_id, lang=lang)
        except NotFound as exc:
            raise _fail(exc, lang) from exc


@router.post("/businesses/{business_id}/profile/undo")
def profile_undo(business_id: int, body: UndoIn) -> dict[str, Any]:
    lang = app_settings.language()
    try:
        with session_scope() as s:
            store.undo(s, business_id, body.event_id, lang=lang)
            return view.build(s, business_id, lang=lang)
    except (NotFound, store.ProfileError) as exc:
        raise _fail(exc, lang) from exc


@router.post("/businesses/{business_id}/profile/{line}")
def profile_edit(business_id: int, line: str, body: EditIn) -> dict[str, Any]:
    lang = app_settings.language()
    try:
        with session_scope() as s:
            event_id = edit.apply(s, business_id, line, body.action, if_live=body.if_live, codes=body.codes,
                                  sentence=body.sentence, text=body.text, lang=lang)
            return {"profile": view.build(s, business_id, lang=lang), "event_id": event_id}
    except (NotFound, store.ProfileError) as exc:
        raise _fail(exc, lang) from exc
