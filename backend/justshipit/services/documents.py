"""The files the owner attaches to a business (사업 문서): a plan, a pitch deck, research.

A file arrives as bytes with its name. `doc_extract` checks its kind by content and reads its text once; the bytes go
to var/documents under their SHA-256, owner-only like the rest of var/, and the text goes on the `Document` row. The
same bytes uploaded again reuse the text already read. Every reader (the kickoff CEO, the idea panel, later runs)
reads the row's text inside the data fence, never the file. No log line carries it; the prompts that hold it are
traced like any prompt (`services/tracing.py`), as pasted notes are.

A file is the owner's own words, like the pasted notes. A plan line whose quote is found in a file is `said`, and its
source names the file and the page: `doc:<id>:p<page>`, or `doc:<id>` for a file without pages. `annotate` puts the
file's name and page beside each such line for the console. Removing a file sets `removed_at`: it stops being read
and quoted, and the lines that already cite it keep their source.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit import fileperm
from justshipit.db.models import KICKOFF_LIVE, Business, Document, KickoffSession
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.services import doc_extract
from justshipit.services.control import ControlError, NotFound
from justshipit.settings import settings

MAX_FILES = 5
MAX_NAME_CHARS = 200
_SOURCE = re.compile(r"doc:(\d+)(?::p(\d+))?")
_KIND_NAME = {"pdf": "PDF", "docx": "Word", "pptx": "PowerPoint", "md": "Markdown", "txt": "text"}


def folder() -> Path:
    path = settings().var_dir / "documents"
    path.mkdir(exist_ok=True)
    fileperm.restrict(path)
    return path


def _keep(data: bytes, sha: str) -> None:
    """The bytes under their hash, written once: a new name first, then moved into place."""
    path = folder() / sha
    if path.exists():
        return
    tmp = path.with_name(f"{sha}.{secrets.token_hex(4)}.part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    fileperm.restrict(tmp)
    os.replace(tmp, path)


def clean_name(name: str) -> str:
    """The name the owner's computer gave the file, without any folder, control characters or excess length. The
    extension is kept when the name is cut."""
    base = re.split(r"[\\/]", unicodedata.normalize("NFC", name or ""))[-1]
    base = "".join(c for c in base if unicodedata.category(c)[0] != "C").strip().strip(".").strip()
    if len(base) <= MAX_NAME_CHARS:
        return base
    stem, ext = os.path.splitext(base)
    return stem[:MAX_NAME_CHARS - len(ext)] + ext


def _business(s: Session, business_id: int, lang: Lang) -> Business:
    business = s.scalar(select(Business).where(Business.id == business_id).with_for_update())
    if business is None:
        raise NotFound(msg("documents.no_business", lang, id=business_id))
    return business


def active(s: Session, business_id: int) -> list[Document]:
    return list(s.scalars(select(Document).where(Document.business_id == business_id, Document.removed_at.is_(None))
                          .order_by(Document.id)))


def check_room(s: Session, business_id: int, *, lang: Lang) -> None:
    """Refuse before a file is read when the business has no room for it."""
    _business(s, business_id, lang)
    _room(s, business_id, 1, lang)


def _room(s: Session, business_id: int, adding: int, lang: Lang) -> None:
    if len(active(s, business_id)) + adding > MAX_FILES:
        raise ControlError(msg("documents.refused.too_many", lang, max=MAX_FILES))


class Upload(NamedTuple):
    """A file read and kept on disk, not yet a row."""
    name: str
    kind: str
    sha256: str
    size: int
    parts: list[str]
    pages: int | None
    cut: bool


def read(name: str, data: bytes) -> Upload:
    """Read an uploaded file, outside any transaction: parsing can take seconds. The same bytes read before reuse
    that text. A refusal raises `doc_extract.DocumentRefused`."""
    name = clean_name(name)
    if not name:
        raise doc_extract.DocumentRefused("no_name")
    kind = doc_extract.kind_of(name)
    sha = hashlib.sha256(data).hexdigest()
    with session_scope() as s:
        prior = s.execute(select(Document.parts, Document.pages, Document.cut)
                          .where(Document.sha256 == sha, Document.kind == kind).limit(1)).first()
    if prior is not None:
        parts, pages, cut = list(prior.parts), prior.pages, prior.cut
    else:
        got = doc_extract.extract(name, data)
        parts, pages, cut = got.parts, got.pages, got.cut
    _keep(data, sha)
    return Upload(name, kind, sha, len(data), parts, pages, cut)


def add(s: Session, upload: Upload, *, business_id: int | None = None, lang: Lang) -> dict:
    """Keep one read file as a row. With `business_id` it joins that business at once, and its live kickoff chat at
    the turn the chat is on. Without one it waits for `attach`, from the new-business form."""
    if business_id is not None:
        _business(s, business_id, lang)
        _room(s, business_id, 1, lang)
    doc = Document(name=upload.name, kind=upload.kind, size=upload.size, sha256=upload.sha256, pages=upload.pages,
                   parts=upload.parts, cut=upload.cut)
    if business_id is not None:
        doc.business_id = business_id
        live = s.scalar(select(KickoffSession).where(KickoffSession.business_id == business_id,
                                                     KickoffSession.status.in_(KICKOFF_LIVE)))
        if live is not None:
            doc.session_id, doc.after_turn = live.id, live.turns_used
    s.add(doc)
    s.flush()
    return view(doc)


def attach(s: Session, ids: list[int], *, business_id: int, session_id: int, lang: Lang) -> None:
    """The files uploaded on the new-business form join the business it opened, with the owner's first words."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return
    docs = {d.id: d for d in s.scalars(select(Document).where(Document.id.in_(ids)).with_for_update())}
    for i in ids:
        d = docs.get(i)
        if d is None or d.removed_at is not None:
            raise NotFound(msg("documents.not_found", lang, id=i))
        if d.business_id is not None:
            raise ControlError(msg("documents.taken", lang, id=i))
    _room(s, business_id, len(ids), lang)
    for d in docs.values():
        d.business_id, d.session_id, d.after_turn = business_id, session_id, 0


def remove(s: Session, doc_id: int, *, lang: Lang) -> dict:
    doc = s.scalar(select(Document).where(Document.id == doc_id).with_for_update())
    if doc is None:
        raise NotFound(msg("documents.not_found", lang, id=doc_id))
    if doc.removed_at is None:
        doc.removed_at = datetime.now(UTC)
    return view(doc)


def attached_during(s: Session, session_id: int, n: int) -> bool:
    """Whether the owner attached a file while turn n was the CEO's latest: that alone answers the turn."""
    return s.scalar(select(Document.id).where(Document.session_id == session_id, Document.after_turn == n,
                                              Document.removed_at.is_(None)).limit(1)) is not None


def view(d: Document) -> dict[str, Any]:
    return {"id": d.id, "business_id": d.business_id, "session_id": d.session_id, "after_turn": d.after_turn,
            "name": d.name, "kind": d.kind, "size": d.size, "pages": d.pages, "chars": sum(map(len, d.parts)),
            "cut": d.cut, "created_at": d.created_at, "removed": d.removed_at is not None}


def listing(s: Session, business_id: int) -> list[dict[str, Any]]:
    return [view(d) for d in active(s, business_id)]


def in_session(s: Session, session_id: int) -> list[dict[str, Any]]:
    """The files of one kickoff chat, removed ones too: the chat keeps showing what the owner sent."""
    return [view(d) for d in s.scalars(select(Document).where(Document.session_id == session_id).order_by(Document.id))]


# ------------------------------------------------------------------ the file as the owner's words

def source(doc: Document, page: int | None) -> str:
    return f"doc:{doc.id}:p{page}" if page is not None else f"doc:{doc.id}"


def words(docs: list[Document]) -> list[tuple[str, str]]:
    """Each page or slide of each file with the source a quote from it gets. A file without pages is one part."""
    paged = {d.id: d.kind in doc_extract.PAGED for d in docs}
    return [(source(d, i + 1 if paged[d.id] else None), part) for d in docs for i, part in enumerate(d.parts) if part]


def annotate(s: Session, sections: list[dict]) -> list[dict]:
    """Each line whose source is a file gets `doc`: the file's name and page, for the console to cite. A removed
    file still names its lines."""
    cited = {line["source"]: m for sec in sections for line in sec["lines"]
             if (m := _SOURCE.fullmatch(line.get("source") or ""))}
    if not cited:
        return sections
    names = dict(s.execute(select(Document.id, Document.name).where(
        Document.id.in_({int(m.group(1)) for m in cited.values()}))).all())
    for sec in sections:
        for line in sec["lines"]:
            if (m := cited.get(line.get("source") or "")) and int(m.group(1)) in names:
                line["doc"] = {"id": int(m.group(1)), "name": names[int(m.group(1))],
                               "page": int(m.group(2)) if m.group(2) else None}
    return sections


def _when(d: Document) -> str:
    if d.after_turn is None:
        return "attached on the business page"
    return "attached with the first words" if d.after_turn == 0 else f"attached during turn {d.after_turn}"


def _about(d: Document) -> str:
    size = f"{d.pages} {'slides' if d.kind == 'pptx' else 'pages'}" if d.pages is not None else \
        f"{sum(map(len, d.parts)):,} characters"
    read = ""
    if d.cut:
        read = (f"; only the first {doc_extract.MAX_PAGES} pages or {doc_extract.MAX_CHARS:,} characters were read"
                if d.pages is not None else f"; only the first {doc_extract.MAX_CHARS:,} characters were read")
    return f"{_KIND_NAME[d.kind]}, {size}, {_when(d)}{read}"


def prompt_text(docs: list[Document], *, chars: int | None = None) -> str:
    """The files as a prompt reads them: each under its name, each page marked so a quote can be placed. With
    `chars`, the files share that many characters evenly, and a line says where a file was cut short."""
    share = chars // len(docs) if chars and docs else None
    out = []
    for d in docs:
        out.append(f"### {d.name} ({_about(d)})")
        paged = d.kind in doc_extract.PAGED
        left = share
        for i, part in enumerate(d.parts):
            if not part:
                continue
            if left is not None and left <= 0:
                out.append(f"…(the rest of {d.name} is left out here)")
                break
            text = part if left is None else part[:left]
            out.append(f"[page {i + 1}]\n{text}" if paged else text)
            if left is not None:
                left -= len(text)
                if len(text) < len(part):
                    out.append(f"…(the rest of {d.name} is left out here)")
                    break
    return "\n\n".join(out)
