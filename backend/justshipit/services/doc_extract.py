"""The words in a file the owner attaches: what kind of file it is, read from its bytes, and its text page by page.

Pure: bytes and a file name in, an `Extracted` out, or a `DocumentRefused` that says why in the owner's words. The
name's extension must be one this module reads and the bytes must be that kind of file, so an .exe renamed to .pdf is
refused. Nothing here runs, opens or fetches anything the file names: a link is text, a macro is never loaded, an
external relationship in a DOCX or PPTX is never followed. A DOCX or PPTX is a zip, and its entries are counted and
sized from the zip's own directory before any part is read (`zipfile` never reads past an entry's declared size).
"""
from __future__ import annotations

import posixpath
import unicodedata
import zipfile
from io import BytesIO
from typing import Literal, NamedTuple

from justshipit.i18n.said import SaidError

Kind = Literal["pdf", "docx", "pptx", "md", "txt"]
EXTENSIONS: dict[str, Kind] = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".md": "md", ".markdown": "md",
                               ".txt": "txt"}
PAGED: frozenset[Kind] = frozenset({"pdf", "pptx"})

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 50
MAX_CHARS = 30_000
# What one PDF page's raw text is cut to before it is cleaned: room for the control characters cleaning drops.
MAX_PAGE_RAW_CHARS = 4 * MAX_CHARS
ZIP_MAX_ENTRIES = 3000
ZIP_MAX_UNPACKED = 100 * 1024 * 1024
XML_MAX_PART = 10 * 1024 * 1024
# Fewer letters than this in every page read means the PDF is pictures of pages, not text.
SCAN_MIN_LETTERS = 20

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class DocumentRefused(SaidError):
    """A file the company will not keep, with the reason as a `documents.refused.*` message."""

    def __init__(self, code: str, /, **vars: object) -> None:
        self.code = code
        super().__init__(f"documents.refused.{code}", **vars)


class Extracted(NamedTuple):
    """`parts` is the text of each page or slide read, in order, or one part for a file without pages. `pages` is how
    many pages or slides the file has, None for a file without pages. `cut` says the company read less than all of
    it: only the first `MAX_PAGES` pages, or only the first `MAX_CHARS` characters."""
    kind: Kind
    parts: list[str]
    pages: int | None
    cut: bool

    @property
    def chars(self) -> int:
        return sum(len(p) for p in self.parts)


def kind_of(name: str) -> Kind:
    """The kind the file's name claims, or a refusal for a name this module does not read."""
    kind = EXTENSIONS.get(posixpath.splitext(name.lower())[1])
    if kind is None:
        raise DocumentRefused("unknown_type")
    return kind


def extract(name: str, data: bytes) -> Extracted:
    if not data:
        raise DocumentRefused("empty")
    if len(data) > MAX_BYTES:
        raise DocumentRefused("too_big", max=MAX_BYTES // (1024 * 1024))
    kind = kind_of(name)
    ext = posixpath.splitext(name.lower())[1]
    match kind:
        case "pdf":
            if not data.startswith(b"%PDF-"):
                raise DocumentRefused("mismatch", ext=ext)
            parts, pages = _pdf(data)
        case "docx":
            # python-docx parses every XML part of the package, not only the body.
            if any(i.file_size > XML_MAX_PART for i in _zip(data, "word/document.xml", ext).infolist()
                   if i.filename.lower().endswith((".xml", ".rels"))):
                raise DocumentRefused("zip_bomb")
            parts, pages = _docx(data), None
        case "pptx":
            parts, pages = _pptx(_zip(data, "ppt/presentation.xml", ext))
        case _:
            parts, pages = [_text(data, ext)], None
    if not any(p.strip() for p in parts):
        raise DocumentRefused("no_text")
    parts, over_chars = _capped(parts)
    return Extracted(kind, parts, pages, over_chars or (pages or 0) > MAX_PAGES)


def _clean(text: str) -> str:
    """NFC, line breaks kept, other control and format characters dropped, blank runs squeezed."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co"))
    lines = [ln.rstrip() for ln in text.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def _capped(parts: list[str]) -> tuple[list[str], bool]:
    out, left = [], MAX_CHARS
    for p in parts:
        out.append(p[:left])
        left -= len(out[-1])
        if left <= 0:
            return out, sum(map(len, parts)) > MAX_CHARS
    return out, False


def _letters(parts: list[str]) -> int:
    return sum(1 for p in parts for c in p if c.isalnum())


def _pdf(data: bytes) -> tuple[list[str], int]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            try:
                opened = reader.decrypt("")
            except Exception as exc:
                raise DocumentRefused("encrypted") from exc
            if not opened:
                raise DocumentRefused("encrypted")
        total = len(reader.pages)
        # pypdf has no time limit, so the work is bounded by what is asked of it: at most MAX_PAGES pages, and none
        # after the pages read already hold more than the company keeps. One page past the cap is what tells
        # `extract` the file was cut. (pypdf caps what one stream unpacks to on its own.)
        parts, chars = [], 0
        for i in range(min(total, MAX_PAGES)):
            if chars > MAX_CHARS:
                break
            parts.append(_clean((reader.pages[i].extract_text() or "")[:MAX_PAGE_RAW_CHARS]))
            chars += len(parts[-1])
    except DocumentRefused:
        raise
    except Exception as exc:
        raise DocumentRefused("broken") from exc
    if _letters(parts) < SCAN_MIN_LETTERS:
        raise DocumentRefused("scan")
    return parts, total


def _zip(data: bytes, main_part: str, ext: str) -> zipfile.ZipFile:
    """The file as a zip, once its directory shows the part a DOCX or PPTX must have and sizes that cannot bomb."""
    if not data.startswith(b"PK\x03\x04"):
        raise DocumentRefused("mismatch", ext=ext)
    try:
        z = zipfile.ZipFile(BytesIO(data))
        infos = z.infolist()
    except (zipfile.BadZipFile, ValueError) as exc:
        raise DocumentRefused("broken") from exc
    if len(infos) > ZIP_MAX_ENTRIES or sum(i.file_size for i in infos) > ZIP_MAX_UNPACKED:
        raise DocumentRefused("zip_bomb")
    if main_part not in {i.filename for i in infos}:
        raise DocumentRefused("mismatch", ext=ext)
    return z


def _xml(z: zipfile.ZipFile, part: str):
    """A part parsed without entities, DTDs or network: python-docx's own parser settings."""
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    try:
        if z.getinfo(part).file_size > XML_MAX_PART:
            raise DocumentRefused("zip_bomb")
        return etree.fromstring(z.read(part), parser)
    except DocumentRefused:
        raise
    except Exception as exc:
        raise DocumentRefused("broken") from exc


def _runs(el, tag: str) -> str:
    return "".join(t.text or "" for t in el.iter(tag))


def _docx(data: bytes) -> list[str]:
    """Paragraphs and table rows in body order. A table row reads as its cells joined by " | "."""
    import docx

    try:
        body = docx.Document(BytesIO(data)).element.body
    except Exception as exc:
        raise DocumentRefused("broken") from exc
    lines = []
    for el in body.iterchildren():
        if el.tag == f"{_W}p":
            lines.append(_runs(el, f"{_W}t"))
        elif el.tag == f"{_W}tbl":
            lines += [" | ".join(_runs(tc, f"{_W}t") for tc in tr.iterchildren(f"{_W}tc"))
                      for tr in el.iter(f"{_W}tr")]
    return [_clean("\n".join(lines))]


def _pptx(z: zipfile.ZipFile) -> tuple[list[str], int]:
    """Each slide's text in the deck's order, one paragraph a line. Speaker notes are left out."""
    rels = _xml(z, "ppt/_rels/presentation.xml.rels")
    target = {r.get("Id"): r.get("Target") or "" for r in rels.iter(f"{_PR}Relationship")}
    ids = [el.get(f"{_R}id") for el in _xml(z, "ppt/presentation.xml").iter(f"{_P}sldId")]
    names = {i.filename for i in z.infolist()}
    paths = list(dict.fromkeys(t[1:] if t.startswith("/") else posixpath.normpath(posixpath.join("ppt", t))
                               for t in (target.get(i, "") for i in ids)))
    parts = []
    for path in paths[:MAX_PAGES]:
        if path not in names:
            raise DocumentRefused("broken")
        slide = _xml(z, path)
        parts.append(_clean("\n".join(_runs(p, f"{_A}t") for p in slide.iter(f"{_A}p"))))
    return parts, len(paths)


def _text(data: bytes, ext: str) -> str:
    """UTF-8 (with or without a BOM), UTF-16 with a BOM, or the Korean code page older Windows editors save in. A
    NUL byte or a run of control characters means the file is not text."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ["utf-16"]
    elif b"\x00" in data:
        raise DocumentRefused("mismatch", ext=ext)
    else:
        encodings = ["utf-8-sig", "cp949"]
    for enc in encodings:
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise DocumentRefused("mismatch", ext=ext)
    controls = sum(1 for c in text if unicodedata.category(c) == "Cc" and c not in "\n\r\t\f")
    if controls > max(8, len(text) // 100):
        raise DocumentRefused("mismatch", ext=ext)
    return _clean(text)
