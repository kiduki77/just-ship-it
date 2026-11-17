"""First 10 kit routes: the kit as the console reads it, and the owner's click that has the company write it. The company
prepares texts for the owner to copy and sends nothing. A refusal reads in the owner's language: 404 for a business or
person that is not there, 400 for a request that cannot be written, 409 while a write is running or there is no plan,
422 for a body that does not parse."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict

from justshipit.api.owner_tasks import _body
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import activity, app_settings, kit

router = APIRouter()


class PrepareIn(BaseModel):
    """Which parts to write (all of them when left out) and, for a message alone, which person. Both left out writes the
    whole kit."""
    model_config = ConfigDict(extra="forbid")
    parts: list[str] | None = None
    person_id: int | None = None


def _fields() -> dict[str, str]:
    lang = app_settings.language()
    return {f: msg(f"kit.field.{f}", lang) for f in PrepareIn.model_fields}


def _run(fn) -> dict[str, Any]:
    """Run an owner action in its own transaction and turn a refusal into its HTTP answer."""
    lang = app_settings.language()
    with activity.as_actor("owner"):
        try:
            with session_scope() as s:
                out = fn(s, lang)
                s.flush()
                return out
        except kit.KitNotFound as exc:
            raise HTTPException(404, exc.words(lang)) from exc
        except kit.KitConflict as exc:
            raise HTTPException(409, exc.words(lang)) from exc
        except kit.KitError as exc:
            raise HTTPException(400, exc.words(lang)) from exc


@router.get("/businesses/{business_id}/kit")
def business_kit(business_id: int) -> dict[str, Any]:
    return _run(lambda s, lang: kit.read(s, business_id, lang=lang))


@router.post("/businesses/{business_id}/kit/prepare")
def business_kit_prepare(business_id: int, body: Annotated[Any, Body()] = None) -> dict[str, Any]:
    """Queue the company's write of the kit. It runs in the background; `writing` on the kit says when it is done."""
    data = _body(PrepareIn, body, _fields())
    return _run(lambda s, lang: kit.request(s, business_id, parts=data.parts, person_id=data.person_id))
