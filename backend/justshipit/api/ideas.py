"""Idea box routes: drop an idea in, read the list the CEO has worked through, reverse a disposition, retry a failure."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.services import activity, app_settings, ideas

router = APIRouter()


class IdeaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    links: list[str] | None = None


class OverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disposition: str
    note: str | None = None


class DiscardIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = None


_FIELDS = ("text", "links", "disposition", "note")


def _body[T: BaseModel](model: type[T], raw: Any, lang: Lang) -> T:
    """The body as `model`, or a 422 that names the fields and never repeats what was sent: a framework's default 422
    echoes the input, which here may be a link or a note."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise HTTPException(422, msg("ideas.body.not_object", lang))
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        keys = {str(e["loc"][0]) if e["loc"] and str(e["loc"][0]) in _FIELDS else "body" for e in exc.errors()}
        fields = ", ".join(msg(f"ideas.field.{k}", lang) for k in sorted(keys))
        raise HTTPException(422, msg("ideas.body.invalid", lang, fields=fields)) from None


def _run(fn, *args, **kwargs) -> dict:
    """Run an idea action as the owner in its own transaction; a refusal becomes 404, 400, 422 or 409 with the service's
    message, which never repeats what was sent."""
    with activity.as_actor("owner"):
        try:
            with session_scope() as s:
                result = fn(s, *args, **kwargs)
                s.flush()
                return result
        except ideas.IdeaNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except ideas.InvalidQuery as exc:
            raise HTTPException(400, str(exc)) from exc
        except ideas.IdeaInvalid as exc:
            raise HTTPException(422, str(exc)) from exc
        except ideas.IdeaError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post("/businesses/{business_id}/ideas")
def idea_post(business_id: int, body: Annotated[Any, Body()] = None) -> dict:
    lang = app_settings.language()
    data = _body(IdeaIn, body, lang)
    return _run(ideas.post, business_id, text=data.text, links=data.links, lang=lang)


@router.get("/ideas")
def idea_list(business_id: int | None = None, status: str | None = None) -> dict:
    lang = app_settings.language()
    return _run(lambda s: ideas.listing(s, business_id=business_id, status=status, lang=lang))


@router.get("/ideas/{idea_id}")
def idea_get(idea_id: int) -> dict:
    return _run(ideas.get, idea_id, lang=app_settings.language())


@router.post("/ideas/{idea_id}/override")
def idea_override(idea_id: int, body: Annotated[Any, Body()] = None) -> dict:
    lang = app_settings.language()
    data = _body(OverrideIn, body, lang)
    return _run(ideas.override, idea_id, disposition=data.disposition, note=data.note, lang=lang)


@router.post("/ideas/{idea_id}/discard")
def idea_discard(idea_id: int, body: Annotated[Any, Body()] = None) -> dict:
    lang = app_settings.language()
    return _run(ideas.confirm_discard, idea_id, note=_body(DiscardIn, body, lang).note, lang=lang)


@router.post("/ideas/{idea_id}/retry")
def idea_retry(idea_id: int) -> dict:
    return _run(ideas.retry, idea_id, lang=app_settings.language())
