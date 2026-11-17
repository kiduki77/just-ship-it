"""Owner task routes: the list of things only the owner can do, and what the owner does to one: finish, skip,
reopen, and tick one of its steps. Also the owner's word that they sent an outbox item by hand, which closes or makes
its to-do."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError, Field

from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import activity, app_settings, owner_tasks, people_tasks

router = APIRouter()


class DoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    link: str | None = None
    numbers: dict[str, Any] | None = None
    note: str | None = None
    stage: str | None = None
    quote: str | None = None
    next_action: str | None = None
    next_action_at: datetime | None = None
    heard_via: str | None = None
    heard_words: str | None = None


class StepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(max_length=64)
    done: bool


class SentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: datetime | None = None
    link: str | None = None
    note: str | None = None


class SkipIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = None


def _body[T: BaseModel](model: type[T], raw: Any, field_names: dict[str, str] | None = None) -> T:
    """The body as `model`, or a 422 in the owner's language that names the field and never repeats what was sent: a
    framework's default 422 echoes the input, which here may be a link or a note. `field_names` are a caller's own
    names for its fields; without them a field is named by the catalog (`owner_task.field.<name>`)."""
    lang = app_settings.language()

    def name(field: str) -> str:
        if field_names is not None:
            return field_names.get(field, msg("owner_task.field.body", lang))
        key = f"owner_task.field.{field}"
        return msg(key if key in _FIELDS else "owner_task.field.body", lang)

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise HTTPException(422, msg("owner_task.error.not_object", lang))
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        fields = sorted({name(str(e["loc"][0])) if e["loc"] else name("body") for e in exc.errors()})
        raise HTTPException(422, msg("owner_task.error.bad_fields", lang, fields=", ".join(fields))) from None


_FIELDS = {f"owner_task.field.{f}" for f in (*DoneIn.model_fields, "at", "body")}


def _run(fn) -> dict:
    """Run a task action as the owner in its own transaction, with the owner's language read once: `fn(s, lang)`. A
    refusal becomes 400, 404 or 409 in that language."""
    lang = app_settings.language()
    with activity.as_actor("owner"):
        try:
            with session_scope() as s:
                task = fn(s, lang)
                s.flush()
                return task if isinstance(task, dict) else owner_tasks.view(task, lang)
        except owner_tasks.OwnerTaskNotFound as exc:
            raise HTTPException(404, exc.words(lang)) from exc
        except owner_tasks.InvalidQuery as exc:
            raise HTTPException(400, exc.words(lang)) from exc
        except owner_tasks.OwnerTaskError as exc:
            raise HTTPException(409, exc.words(lang)) from exc


@router.get("/owner-tasks")
def owner_task_list(status: str | None = None, business_id: int | None = None) -> dict:
    return _run(lambda s, lang: owner_tasks.listing(s, status=status, business_id=business_id, lang=lang))


@router.get("/owner-tasks/{task_id}")
def owner_task_get(task_id: int) -> dict:
    return _run(lambda s, lang: owner_tasks.get(s, task_id, lang=lang))


@router.post("/owner-tasks/{task_id}/done")
def owner_task_done(task_id: int, body: Annotated[Any, Body()] = None) -> dict:
    data = _body(DoneIn, body)
    outcome = people_tasks.Outcome(data.stage, data.quote, data.next_action, data.next_action_at, data.heard_via,
                                   data.heard_words)
    return _run(lambda s, lang: owner_tasks.done(s, task_id, link=data.link, numbers=data.numbers, note=data.note,
                                                outcome=outcome))


@router.post("/actions/{action_id}/sent-by-owner")
def action_sent_by_owner(action_id: int, body: Annotated[Any, Body()] = None) -> dict:
    """The owner sent an outbox item by hand: record it (when, an optional link and note) on the to-do it closes or
    makes. Nothing is sent. The answer is that to-do."""
    data = _body(SentIn, body)
    return _run(lambda s, lang: owner_tasks.sent_by_owner(s, action_id, at=data.at, link=data.link, note=data.note))


@router.post("/owner-tasks/{task_id}/skip")
def owner_task_skip(task_id: int, body: Annotated[Any, Body()] = None) -> dict:
    note = _body(SkipIn, body).note
    return _run(lambda s, lang: owner_tasks.skip(s, task_id, note=note))


@router.post("/owner-tasks/{task_id}/steps")
def owner_task_step(task_id: int, body: Annotated[Any, Body()] = None) -> dict:
    data = _body(StepIn, body)
    return _run(lambda s, lang: owner_tasks.tick(s, task_id, data.key, data.done))


@router.post("/owner-tasks/{task_id}/reopen")
def owner_task_reopen(task_id: int) -> dict:
    return _run(lambda s, lang: owner_tasks.reopen(s, task_id))
