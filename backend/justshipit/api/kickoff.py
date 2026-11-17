"""Kickoff conversation routes: open a business with an idea, answer the CEO's turns, then start work from the brief.
Each route reads the owner's language once and passes it down."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from justshipit.api.console import _act
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.i18n.said import Refusal
from justshipit.services import app_settings, control, documents, existing_product, idea_review, kickoff
from justshipit.services import projects as project_service
from justshipit.services.strengths import Strength
from justshipit.settings import settings

MAX_ANSWER_CHARS = 4000
MAX_NAME_CHARS = 200
MAX_ONE_LINER_CHARS = 1000
MAX_SOURCE_CHARS = 1000
_FIELDS = ("name", "one_liner", "material", "source", "documents", "text", "choice", "action", "strengths")
_LIMITS = {"name": MAX_NAME_CHARS, "one_liner": MAX_ONE_LINER_CHARS, "text": MAX_ANSWER_CHARS,
           "source": MAX_SOURCE_CHARS}


def _describe(exc: RequestValidationError, lang: Lang) -> str:
    """The first problem, in the owner's language and without echoing the input back."""
    err = exc.errors()[0]
    field = next((str(x) for x in reversed(err["loc"]) if str(x) in _FIELDS), None)
    label = msg(f"kickoff.field.{field or 'request'}", lang)
    kind = err["type"]
    if kind == "string_too_long" and field in _LIMITS:
        return msg("kickoff.invalid.too_long", lang, field=label, max=f"{_LIMITS[field]:,}")
    if kind == "missing":
        return msg("kickoff.invalid.missing", lang, field=label)
    if kind == "literal_error" and field == "action":
        return msg("kickoff.invalid.action", lang)
    return msg("kickoff.invalid.format", lang, field=label)


class PlainValidation(APIRoute):
    """A framework's default 422 is a list of English records that echoes the input, which can be a long answer."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise HTTPException(422, _describe(exc, app_settings.language())) from None
        return guarded


router = APIRouter(route_class=PlainValidation)


class BusinessIn(BaseModel):
    name: str = Field(max_length=MAX_NAME_CHARS)
    one_liner: str = Field(max_length=MAX_ONE_LINER_CHARS)
    material: str | None = None
    documents: list[int] = Field(default_factory=list, max_length=documents.MAX_FILES)


class ImportIn(BaseModel):
    source: str = Field(max_length=MAX_SOURCE_CHARS)
    name: str = Field(default="", max_length=MAX_NAME_CHARS)
    one_liner: str = Field(default="", max_length=MAX_ONE_LINER_CHARS)
    material: str | None = None
    documents: list[int] = Field(default_factory=list, max_length=documents.MAX_FILES)


class AnswerIn(BaseModel):
    action: Literal["answer", "unknown", "later"] = "answer"
    text: str | None = Field(default=None, max_length=MAX_ANSWER_CHARS)
    choice: str | None = None
    strengths: list[Strength] | None = None  # the answer to the opening question (kickoff.STRENGTHS_TURN)


class CustomerIn(BaseModel):
    text: str | None = Field(default=None, max_length=MAX_ANSWER_CHARS)


def _read(fn, *args, **kwargs) -> dict:
    with session_scope() as s:
        try:
            return fn(s, *args, **kwargs)
        except control.ControlError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.post("/businesses")
def business_open(body: BusinessIn) -> dict:
    return _act(kickoff.open_session, name=body.name, one_liner=body.one_liner, material=body.material,
                document_ids=body.documents, lang=app_settings.language())


@router.post("/businesses/import")
def business_import(body: ImportIn) -> dict:
    """Open a business for a product the owner already has: clone it into products/, then the kickoff. The clone runs
    before the business exists, so a refusal (not a repo, a secret in it) leaves nothing behind."""
    lang = app_settings.language()
    products = settings().products_dir
    try:
        src = existing_product.parse_source(body.source, products)
        with session_scope() as s:
            if (other := existing_product.pinned_by(s, src.repo)) is not None:
                raise HTTPException(409, msg("product.import.in_use", lang, id=other, repo=src.repo))
        product = existing_product.clone(src, products)
    except Refusal as exc:
        raise HTTPException(409, exc.words(lang)) from None
    return _act(kickoff.open_session, name=body.name.strip() or src.path.name, one_liner=body.one_liner,
                material=body.material, document_ids=body.documents, product=product, lang=lang)


@router.get("/businesses/{business_id}/kickoff")
def business_kickoff(business_id: int) -> dict:
    lang = app_settings.language()

    def latest(s, bid):
        sid = kickoff.live_session_id(s, bid)
        if sid is None:
            raise control.NotFound(msg("kickoff.none_for_business", lang, id=bid))
        return kickoff.view(s, sid, lang=lang)
    return _read(latest, business_id)


@router.post("/businesses/{business_id}/start")
def business_start(business_id: int) -> dict:
    return _act(project_service.start_business, business_id)


@router.get("/businesses/{business_id}/idea-review")
def business_idea_review(business_id: int) -> dict:
    return _read(idea_review.view, business_id, lang=app_settings.language())


@router.post("/businesses/{business_id}/idea-review")
def business_idea_rereview(business_id: int) -> dict:
    return _act(idea_review.rereview, business_id, lang=app_settings.language())


@router.get("/kickoff/{session_id}")
def kickoff_view(session_id: int) -> dict:
    return _read(kickoff.view, session_id, lang=app_settings.language())


@router.post("/kickoff/{session_id}/answer")
def kickoff_answer(session_id: int, body: AnswerIn) -> dict:
    return _act(kickoff.answer, session_id, action=body.action, text=body.text, choice=body.choice,
                strengths=body.strengths,
                lang=app_settings.language())


@router.post("/kickoff/{session_id}/customer")
def kickoff_customer(session_id: int, body: CustomerIn) -> dict:
    """The answer to the customer question after the conversation is done. A blank or missing text is Skip."""
    return _act(kickoff.answer_customer, session_id, text=body.text, lang=app_settings.language())


@router.post("/kickoff/{session_id}/resume")
def kickoff_resume(session_id: int) -> dict:
    return _act(kickoff.resume, session_id, lang=app_settings.language())


@router.post("/kickoff/{session_id}/read-again")
def kickoff_read_again(session_id: int) -> dict:
    return _act(kickoff.read_again, session_id, lang=app_settings.language())


@router.post("/kickoff/{session_id}/continue")
def kickoff_continue(session_id: int) -> dict:
    return _act(kickoff.continue_on, session_id, lang=app_settings.language())


@router.post("/kickoff/{session_id}/finish")
def kickoff_finish(session_id: int) -> dict:
    return _act(kickoff.finish, session_id, lang=app_settings.language())


@router.post("/kickoff/{session_id}/abandon")
def kickoff_abandon(session_id: int) -> dict:
    return _act(kickoff.abandon, session_id, lang=app_settings.language())


@router.post("/kickoff/{session_id}/reopen")
def kickoff_reopen(session_id: int) -> dict:
    return _act(kickoff.reopen, session_id, lang=app_settings.language())
