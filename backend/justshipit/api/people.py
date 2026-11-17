"""People routes: the list of people the owner may meet, today's few, and what the owner does to one of them."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict

from justshipit.api.owner_tasks import _body
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.services import activity, app_settings, owner_tasks, people, people_finder, people_tasks, tracking

router = APIRouter()


class PersonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: str = "direct"
    handle: str
    display_name: str | None = None
    url: str | None = None
    quote: str | None = None
    why: str | None = None
    source: str = "warm"


class StageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str


class NextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    next_action: str | None = None
    next_action_at: datetime | None = None


class QuoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    url: str | None = None


class HeardIn(BaseModel):
    """How the person heard about the product: a channel key, friend, search or other, and their own words. Both left out
    clears the answer; words with no pick propose the channel they name."""
    model_config = ConfigDict(extra="forbid")
    heard_via: str | None = None
    heard_words: str | None = None


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    due_at: datetime | None = None


def _fields(lang: Lang) -> dict[str, str]:
    """The name of each field a request body may hold, for the refusal that names a field that does not parse."""
    fields = {*PersonIn.model_fields, *StageIn.model_fields, *NextIn.model_fields, *QuoteIn.model_fields,
              *TaskIn.model_fields, *HeardIn.model_fields}
    return {f: msg(f"people.input.{f}", lang) for f in fields}


def _run(fn, *args, **kwargs) -> dict:
    """Run an action as the owner in its own transaction. A refusal becomes 400, 404 or 409 with its message."""
    with activity.as_actor("owner"):
        try:
            with session_scope() as s:
                found = fn(s, *args, **kwargs)
                s.flush()
                return found
        except owner_tasks.OwnerTaskNotFound as exc:
            raise HTTPException(404, exc.words(app_settings.language())) from exc
        except owner_tasks.InvalidQuery as exc:
            raise HTTPException(400, exc.words(app_settings.language())) from exc
        except owner_tasks.OwnerTaskError as exc:
            raise HTTPException(409, exc.words(app_settings.language())) from exc


def _person(s, person_id: int, act, lang: Lang) -> dict:
    act(s, person_id)
    return people.get(s, person_id, lang=lang)


@router.get("/businesses/{business_id}/people")
def people_list(business_id: int, stage: str | None = None, source: str | None = None,
                include_archived: bool = False) -> dict:
    lang = app_settings.language()
    return _run(lambda s: people.listing(s, business_id, lang=lang, stage=stage, source=source,
                                         include_archived=include_archived))


@router.get("/businesses/{business_id}/people/today")
def people_today(business_id: int) -> dict:
    lang = app_settings.language()
    return _run(lambda s: {"items": people.today(s, business_id, lang=lang), "limit": people.TODAY_LIMIT})


@router.post("/businesses/{business_id}/people")
def people_add(business_id: int, body: Annotated[Any, Body()] = None) -> dict:
    lang = app_settings.language()
    data = _body(PersonIn, body, _fields(lang))
    return _run(lambda s: people.view(people.create_by_hand(
        s, business_id, platform=data.platform, handle=data.handle, display_name=data.display_name, url=data.url,
        quote=data.quote, why=data.why, source=data.source, lang=lang), lang=lang))


@router.post("/businesses/{business_id}/people/find")
def people_find(business_id: int) -> dict:
    lang = app_settings.language()
    return _run(lambda s: people_finder.request(s, business_id, lang=lang))


@router.get("/people/{person_id}")
def person_get(person_id: int) -> dict:
    lang = app_settings.language()
    return _run(lambda s: people.get(s, person_id, lang=lang))


@router.post("/people/{person_id}/stage")
def person_stage(person_id: int, body: Annotated[Any, Body()] = None) -> dict:
    lang = app_settings.language()
    data = _body(StageIn, body, _fields(lang))
    return _run(lambda s: _person(s, person_id, lambda s, i: people.move(s, i, data.stage, lang=lang), lang))


@router.post("/people/{person_id}/next")
def person_next(person_id: int, body: Annotated[Any, Body()] = None) -> dict:
    lang = app_settings.language()
    data = _body(NextIn, body, _fields(lang))
    return _run(lambda s: _person(s, person_id, lambda s, i: people.set_next(
        s, i, next_action=data.next_action, next_action_at=data.next_action_at, lang=lang), lang))


@router.post("/people/{person_id}/quote")
def person_quote(person_id: int, body: Annotated[Any, Body()] = None) -> dict:
    lang = app_settings.language()
    data = _body(QuoteIn, body, _fields(lang))
    return _run(lambda s: _person(s, person_id, lambda s, i: people.add_quote(s, i, data.text, data.url, lang=lang), lang))


@router.put("/people/{person_id}/heard")
def person_heard(person_id: int, body: Annotated[Any, Body()] = None) -> dict:
    """Record how the person heard about the product, replacing the earlier answer. The answer is the person as the list
    reads them."""
    lang = app_settings.language()
    data = _body(HeardIn, body, _fields(lang))
    return _run(lambda s: _person(s, person_id, lambda s, i: tracking.set_heard(
        s, i, data.heard_via, data.heard_words, lang=lang), lang))


@router.post("/people/{person_id}/archive")
def person_archive(person_id: int) -> dict:
    lang = app_settings.language()
    return _run(lambda s: _person(s, person_id, lambda s, i: people.archive(s, i, lang=lang), lang))


@router.post("/people/{person_id}/tasks")
def person_task(person_id: int, body: Annotated[Any, Body()] = None) -> dict:
    lang = app_settings.language()
    data = _body(TaskIn, body, _fields(lang))
    return _run(lambda s: owner_tasks.view(people_tasks.create_task(s, person_id, data.kind, lang=lang,
                                                                    due_at=data.due_at), lang))
