"""Look routes: the style packs the owner picks from, the accent a main colour derives into, and a business's look
(its style pack and main colour), which its new products are built from."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException

from justshipit.api.owner_tasks import _body
from justshipit.contracts.look import Look
from justshipit.db.models import Business
from justshipit.db.session import session_scope
from justshipit.engineering import style_packs
from justshipit.i18n import Lang, msg
from justshipit.services import activity, app_settings

router = APIRouter()


def _fields(lang: Lang) -> dict[str, str]:
    return {f: msg(f"style.field.{f}", lang) for f in Look.model_fields}


def _view(look: Look, *, picked: bool | None = None) -> dict[str, Any]:
    out = {"pack": look.pack, "main": look.main, "roles": style_packs.roles(look).tokens()}
    return out if picked is None else {**out, "picked": picked}


@router.get("/styles")
def styles() -> list[dict[str, str]]:
    lang = app_settings.language()
    return [{"slug": slug, "name": msg(f"style.pack.{slug}.name", lang), "pitch": msg(f"style.pack.{slug}.pitch", lang),
             "main": pack.main} for slug, pack in style_packs.packs().items()]


@router.get("/styles/{pack}/derive")
def derive_preview(pack: str, main: str) -> dict[str, Any]:
    """The nine accent roles `main` derives into for `pack`, for the picker's preview. Nothing is kept."""
    return _view(_body(Look, {"pack": pack, "main": main}, _fields(app_settings.language())))


def _business(s, business_id: int, lang: Lang) -> Business:
    if (b := s.get(Business, business_id)) is None:
        raise HTTPException(404, msg("style.not_found", lang, id=business_id))
    return b


@router.get("/businesses/{business_id}/look")
def business_look(business_id: int) -> dict[str, Any]:
    """The business's look; `picked` is false while it is still the default."""
    with session_scope() as s:
        b = _business(s, business_id, app_settings.language())
        return _view(style_packs.business_look(b), picked=b.look is not None)


@router.post("/businesses/{business_id}/look")
def set_business_look(business_id: int, body: Annotated[Any, Body()] = None) -> dict[str, Any]:
    """Keep the owner's pick. Products already built keep the look they were built in; the next new one uses this."""
    lang = app_settings.language()
    look = _body(Look, body, _fields(lang))
    with activity.as_actor("owner"), session_scope() as s:
        _business(s, business_id, lang).look = look.model_dump()
    return _view(look, picked=True)
