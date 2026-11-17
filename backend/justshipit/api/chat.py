"""Team chat for the console dock (services/chat.py). Each route reads the owner's language once and passes it down.
A request body is never logged: the send route logs the thread and what the scrubber removed, by kind and count."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from justshipit import staffing
from justshipit.db.session import session_scope
from justshipit.i18n.said import Refusal
from justshipit.services import app_settings, chat, chat_tags

router = APIRouter()


class SubjectIn(BaseModel):
    kind: str
    ref: str | None = None
    business_id: int | None = None


class AboutIn(BaseModel):
    """A merge card's walkthrough step (`step`, `head`), or a recall of the role that worked on the subject (`role`)."""
    kind: str = Field(max_length=40)
    step: str | None = Field(default=None, max_length=64)
    head: str | None = Field(default=None, max_length=64)
    role: str | None = Field(default=None, max_length=40)


class MessageIn(BaseModel):
    subject: SubjectIn | None = None
    to_agent: str | None = None
    body: str = Field(max_length=2 * chat.body_cap())  # past the cap, send() says the cap in the owner's words
    client_id: str | None = Field(default=None, max_length=64)
    about: AboutIn | None = None  # what this line is about: a walkthrough step, or a recall


class ReadIn(BaseModel):
    seq: int


def _resolve(s, sub: SubjectIn | None, lang) -> chat_tags.Subject:
    try:
        if sub is None:
            return chat_tags.resolve(s, "general", None, None)
        return chat_tags.resolve(s, sub.kind, sub.ref, sub.business_id)
    except chat_tags.UnknownSubject as exc:
        raise HTTPException(404, chat._refuse("chat.refuse.no_subject").words(lang)) from exc


@router.get("/chat/threads")
def threads(business_id: int | None = None, archived: bool = False) -> list[dict[str, Any]]:
    lang = app_settings.language()
    with session_scope() as s:
        return chat.threads(s, business_id, lang, archived=archived)


@router.post("/chat/subject")
def subject(body: SubjectIn) -> dict[str, Any]:
    """A screen's subject, resolved without making anything: the canonical subject, its facts, who answers, and its
    thread when one exists."""
    lang = app_settings.language()
    with session_scope() as s:
        return chat.subject(s, _resolve(s, body, lang), lang)


@router.get("/chat/agents")
def agents() -> list[dict[str, Any]]:
    return chat.agents(app_settings.language())


@router.get("/chat/threads/{thread_id}/messages")
def messages(thread_id: int, before: int | None = None,
             limit: Annotated[int, Query(ge=1, le=100)] = 50) -> dict[str, Any]:
    lang = app_settings.language()
    with session_scope() as s:
        try:
            return chat.messages(s, thread_id, lang, before=before, limit=limit)
        except Refusal as exc:
            raise HTTPException(404, exc.words(lang)) from exc


@router.get("/chat/recall/{kind}/{ref}")
def recall(kind: Literal["task", "dev_job"], ref: int) -> dict[str, Any]:
    """What a recalled role answers from, for the console to open a citation: the records kept for a task or build,
    whether it is still in progress, and which roles have a turn on it and can be asked. 404 when it has no crew."""
    lang = app_settings.language()
    with session_scope() as s:
        bundle = staffing.recall_bundle(s, staffing.SubjectKind.TASK if kind == "task" else staffing.SubjectKind.BUILD,
                                        ref)
        if bundle is None:
            raise HTTPException(404, chat._refuse("chat.refuse.recall_subject").words(lang))
        return {"in_progress": bundle.in_progress, "roles": list(bundle.roles),
                "records": [{"id": r.id, "kind": r.kind.value, "body": r.body} for r in bundle.records]}


@router.post("/chat/messages")
def send(body: MessageIn) -> dict[str, Any]:
    lang = app_settings.language()
    with session_scope() as s:
        sub = _resolve(s, body.subject, lang)
        try:
            return chat.send(s, subject=sub, to_agent=body.to_agent, body=body.body, client_id=body.client_id,
                             lang=lang, about=body.about.model_dump(exclude_none=True) if body.about else None)
        except Refusal as exc:
            raise HTTPException(422, exc.words(lang)) from exc


def _thread_write(thread_id: int, fn) -> dict[str, Any]:
    lang = app_settings.language()
    with session_scope() as s:
        try:
            t = fn(s)
        except Refusal as exc:
            raise HTTPException(404, exc.words(lang)) from exc
        return {"id": t.id, "owner_read_seq": t.owner_read_seq, "archived": t.archived_at is not None}


@router.post("/chat/threads/{thread_id}/read")
def read(thread_id: int, body: ReadIn) -> dict[str, Any]:
    return _thread_write(thread_id, lambda s: chat.read(s, thread_id, body.seq))


@router.post("/chat/threads/{thread_id}/archive")
def archive(thread_id: int) -> dict[str, Any]:
    return _thread_write(thread_id, lambda s: chat.archive(s, thread_id, True))


@router.post("/chat/threads/{thread_id}/unarchive")
def unarchive(thread_id: int) -> dict[str, Any]:
    return _thread_write(thread_id, lambda s: chat.archive(s, thread_id, False))


@router.post("/chat/tasks/{message_id}/cancel")
def cancel(message_id: int) -> dict[str, Any]:
    """Cancel the work a task line started, before it starts. A round already running stops the way every build does,
    by pausing the agent or the company."""
    lang = app_settings.language()
    with session_scope() as s:
        try:
            return chat.cancel_task(s, message_id, lang)
        except Refusal as exc:
            raise HTTPException(409, exc.words(lang)) from exc
