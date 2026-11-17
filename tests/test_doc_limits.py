"""How much work a file the owner attaches can ask of the parsers: a DOCX part's size, and the pages of a PDF read."""
import io
import zipfile
from pathlib import Path

import pytest
from justshipit.services import doc_extract
from justshipit.services.doc_extract import DocumentRefused
from pypdf import PageObject, PdfReader, PdfWriter

FIXTURES = Path(__file__).parent / "fixtures" / "documents"


def _docx_with(part: str, grow_by: int) -> bytes:
    """The fixture DOCX with one XML part padded past the cap. Spaces after the root element are valid XML and
    deflate to almost nothing, so the file stays far under the upload limit."""
    buf = io.BytesIO()
    with zipfile.ZipFile(FIXTURES / "plan.docx") as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            data = src.read(info.filename)
            out.writestr(info.filename, data + b" " * grow_by if info.filename == part else data)
    return buf.getvalue()


@pytest.mark.parametrize("part", ["word/document.xml", "word/styles.xml"])
def test_a_docx_with_an_xml_part_over_the_cap_is_not_parsed(part):
    data = _docx_with(part, doc_extract.XML_MAX_PART)
    assert len(data) < doc_extract.MAX_BYTES

    with pytest.raises(DocumentRefused) as err:
        doc_extract.extract("plan.docx", data)

    assert err.value.code == "zip_bomb"


def test_a_docx_under_the_cap_still_reads():
    assert doc_extract.extract("plan.docx", _docx_with("word/document.xml", 1024)).chars > 0


def test_a_pdf_stops_being_read_once_it_holds_more_than_the_company_keeps(monkeypatch):
    writer = PdfWriter()
    for _ in range(4):
        for page in PdfReader(io.BytesIO((FIXTURES / "plan.pdf").read_bytes())).pages:
            writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    total = len(writer.pages)
    read = []

    def a_very_long_page(page, *args, **kwargs):
        read.append(page)
        return "가나다 " * (doc_extract.MAX_CHARS * 10)
    monkeypatch.setattr(PageObject, "extract_text", a_very_long_page)

    got = doc_extract.extract("long.pdf", buf.getvalue())

    assert len(read) == 1 and total > 1, "the pages after the cap are never parsed"
    assert (got.chars, got.cut, got.pages) == (doc_extract.MAX_CHARS, True, total)
