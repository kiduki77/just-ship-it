"""File routes: the owner uploads a file (the raw bytes as the body, its name in the query), lists a business's files,
and removes one. Every /api route is behind the console's login (`api/access.py`), so only the owner reaches them.
A refusal says why in the owner's language and never echoes the file back."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from justshipit.api.console import _act
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.services import app_settings, doc_extract, documents

router = APIRouter()


async def _body(request: Request, lang: Lang) -> bytes:
    """The upload, read no further than one byte past the limit."""
    too_big = HTTPException(413, msg("documents.refused.too_big", lang, max=doc_extract.MAX_BYTES // (1024 * 1024)))
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > doc_extract.MAX_BYTES:
        raise too_big
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > doc_extract.MAX_BYTES:
            raise too_big
    return bytes(buf)


def _upload(name: str, data: bytes, business_id: int | None, lang: Lang) -> dict:
    """Read the file first, then keep it in a short transaction of its own."""
    if business_id is not None:
        _act(documents.check_room, business_id, lang=lang)
    try:
        upload = documents.read(name, data)
    except doc_extract.DocumentRefused as exc:
        raise HTTPException(422, exc.words(lang)) from None
    return _act(documents.add, upload, business_id=business_id, lang=lang)


@router.post("/documents")
async def document_upload(request: Request, name: str, business_id: int | None = None) -> dict:
    lang = app_settings.language()
    data = await _body(request, lang)
    return await run_in_threadpool(_upload, name, data, business_id, lang)


@router.get("/businesses/{business_id}/documents")
def business_documents(business_id: int) -> list[dict]:
    with session_scope() as s:
        return documents.listing(s, business_id)


@router.delete("/documents/{document_id}")
def document_remove(document_id: int) -> dict:
    return _act(documents.remove, document_id, lang=app_settings.language())
