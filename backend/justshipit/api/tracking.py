"""Tracking routes: the "where people came from" numbers of a business, and the three things the owner types into them.

All of them are reversible owner writes, so none files a decision card. A refusal reads in the owner's language: 404 for a
business or tag that is not there, 400 for a value that cannot be kept, 422 for a body that does not parse."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict

from justshipit.api.owner_tasks import _body
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import activity, app_settings, tracking

router = APIRouter()


class TallyIn(BaseModel):
    """The count for a place (a channel key, friend, search or other) on `day`, the `today` the page was loaded with. The
    day's value is replaced. The owner's today or the day before is taken; any other day is refused."""
    model_config = ConfigDict(extra="forbid")
    key: str
    count: Any
    day: str


class DoorIn(BaseModel):
    """One of the owner's places. Leaving `param` out takes the kind's default; null means the place keeps no tag."""
    model_config = ConfigDict(extra="forbid")
    kind: str
    url: str
    param: str | None = None


class DoorsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doors: list[DoorIn]


class TagCountsIn(BaseModel):
    """What the owner's own analytics shows for one tag, as of today. Give either or both."""
    model_config = ConfigDict(extra="forbid")
    tag: str
    visits: Any = None
    signups: Any = None


def _fields() -> dict[str, str]:
    lang = app_settings.language()
    return {f: msg(f"tracking.field.{f}", lang)
            for model in (TallyIn, DoorIn, DoorsIn, TagCountsIn) for f in model.model_fields}


def _run(fn) -> dict[str, Any]:
    """Run an owner action in its own transaction and turn a refusal into its HTTP answer."""
    lang = app_settings.language()
    with activity.as_actor("owner"):
        try:
            with session_scope() as s:
                out = fn(s, lang)
                s.flush()
                return out
        except tracking.TrackingNotFound as exc:
            raise HTTPException(404, exc.words(lang)) from exc
        except tracking.TrackingError as exc:
            raise HTTPException(400, exc.words(lang)) from exc


@router.get("/businesses/{business_id}/tracking")
def business_tracking(business_id: int) -> dict[str, Any]:
    return _run(lambda s, lang: tracking.standing_for(s, business_id, lang=lang))


@router.post("/businesses/{business_id}/tally")
def business_tally(business_id: int, body: Annotated[Any, Body()] = None) -> dict[str, Any]:
    data = _body(TallyIn, body, _fields())
    return _run(lambda s, lang: tracking.set_tally(s, business_id, data.key, data.count, data.day))


@router.put("/businesses/{business_id}/doors")
def business_doors(business_id: int, body: Annotated[Any, Body()] = None) -> dict[str, Any]:
    data = _body(DoorsIn, body, _fields())
    return _run(lambda s, lang: {"doors": [d.view(lang) for d in tracking.set_doors(
        s, business_id, [row.model_dump(exclude_unset=True) for row in data.doors])]})


@router.post("/businesses/{business_id}/tag-counts")
def business_tag_counts(business_id: int, body: Annotated[Any, Body()] = None) -> dict[str, Any]:
    data = _body(TagCountsIn, body, _fields())
    return _run(lambda s, lang: tracking.set_tag_counts(s, business_id, data.tag, visits=data.visits, signups=data.signups))
