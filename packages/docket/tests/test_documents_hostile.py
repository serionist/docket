"""Hostile and broken files (DR-0485): each is kept out whole with Docket's own reason, never read past a cap, never
parsed with a DTD, and never named by an error's message. Every file is built here; nothing binary is committed."""

from __future__ import annotations

import zipfile

import pytest
from documents_kit import (
    DOC_REL,
    Slide,
    docx,
    encrypted,
    package,
    pdf,
    pptx,
    rels,
    replaced,
    sheet_xml,
    sp,
    wp,
    wroot,
    xlsx,
)
from openpyxl import Workbook

from docket.documents import formats, ooxml
from docket.documents.formats import extract
from docket.documents.limits import MIB, Limits, NotReadError
from docket.documents.ooxml import OLE, UNSAFE_XML, Package

BILLION_LAUGHS = (
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">'
    '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
)
EXTERNAL = '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY secret SYSTEM "file:///etc/hostname">]>'


def refused(data: bytes, fmt: formats.Format, limits: Limits | None = None) -> NotReadError:
    with pytest.raises(NotReadError) as caught:
        extract(data, fmt, limits or Limits())
    return caught.value


def _sheet(book: Workbook) -> None:
    sheet = book.active
    assert sheet is not None
    sheet["A1"] = "Northwind"


# -- the package ------------------------------------------------------------------------------------------------------


def test_an_ole_file_is_protected_whatever_its_extension() -> None:
    fmts: tuple[formats.Format, ...] = ("docx", "xlsx", "pptx")
    for fmt in fmts:
        error = refused(OLE + bytes(512), fmt)
        assert (error.reason, error.detail) == ("protected", "protected, or in an old binary format")


def test_something_that_isn_t_a_zip_is_damaged() -> None:
    assert refused(b"not a zip at all", "docx").detail == "damaged (BadZipFile)"


def test_too_many_parts_is_refused_before_any_is_read() -> None:
    data = package({f"part{n}.xml": "<x/>" for n in range(6)})
    error = refused(data, "docx", Limits(zip_members=5))
    assert (error.reason, error.detail) == ("zip", "6 parts in its package, over the cap of 5")


def test_a_part_declaring_more_than_the_cap_is_refused() -> None:
    data = docx(wp("x" * 4000))
    error = refused(data, "docx", Limits(member_bytes=2000))
    assert error.reason == "zip"
    assert error.detail.startswith("a part of 1 MB in its package, over the cap of 1 MB")


def test_a_part_compressed_past_the_ratio_is_a_zip_bomb() -> None:
    data = package({"bomb.xml": b"0" * (2 * MIB)})
    error = refused(data, "xlsx")
    assert error.reason == "zip"
    assert "compressed" in error.detail
    assert error.detail.endswith("to 1 in its package, over the cap of 100 to 1")


def test_parts_over_the_package_s_cap_together_are_refused() -> None:
    data = package({"a.bin": b"a" * 3000, "b.bin": b"b" * 3000}, compression=zipfile.ZIP_STORED)
    error = refused(data, "docx", Limits(package_bytes=5000))
    assert (error.reason, error.detail) == ("zip", "1 MB of parts in its package, over the cap of 1 MB")


def test_an_encrypted_zip_part_is_protected() -> None:
    data = bytearray(docx(wp("x")))
    central = data.find(b"PK\x01\x02")
    while central >= 0:
        data[central + 8] |= 0x1
        central = data.find(b"PK\x01\x02", central + 4)
    assert refused(bytes(data), "docx").reason == "protected"


def test_a_part_whose_bytes_don_t_match_its_crc_is_damaged() -> None:
    members: dict[str, str | bytes] = {
        "_rels/.rels": rels(("rId1", DOC_REL, "word/document.xml")),
        "word/document.xml": wroot("document", "<w:body>" + wp("Northwind budget") + "</w:body>"),
    }
    broken = package(members, compression=zipfile.ZIP_STORED).replace(b"Northwind", b"Southwind")
    assert refused(broken, "docx").detail == "damaged (BadZipFile)"


def test_a_missing_main_part_or_none_at_all_is_damaged() -> None:
    no_main = package({"_rels/.rels": rels()})
    assert refused(no_main, "docx").detail == "damaged (no main part)"
    gone = package({"_rels/.rels": rels(("rId1", DOC_REL, "word/document.xml"))})
    assert refused(gone, "docx").detail == "damaged (no main part)"
    named = Package(package({"a.xml": "<a/>"}), Limits())
    with pytest.raises(NotReadError, match="a part it names is missing"):
        named.read("b.xml")


def test_relationships_resolve_from_the_root_and_leave_external_targets_out() -> None:
    data = package(
        {
            "_rels/.rels": rels(("rId1", DOC_REL, "/main.xml")),
            "main.xml": "<root/>",
            "_rels/main.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="x" Type="t/web" Target="https://example.invalid/" TargetMode="External"/>'
            '<Relationship Id="y" Type="t/part" Target="sub/my%20part.xml"/></Relationships>',
            "sub/my part.xml": "<p/>",
        }
    )
    found = Package(data, Limits())
    assert found.main() == "main.xml"
    assert found.rels("main.xml") == [ooxml.Rel("y", "t/part", "sub/my part.xml")]
    assert found.related("main.xml", "part") == ["sub/my part.xml"]
    assert found.core() == (None, None)


# -- XML --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", [BILLION_LAUGHS, EXTERNAL, '<?xml version="1.0"?><!DOCTYPE w:document>'])
def test_a_docx_part_with_a_dtd_or_entities_is_never_parsed(prefix: str) -> None:
    data = replaced(docx(wp("x")), "word/document.xml", prefix + wroot("document", "<w:body/>").split("?>", 1)[1])
    error = refused(data, "docx")
    assert (error.reason, error.detail) == ("xml", UNSAFE_XML)


def test_a_workbook_with_entities_is_never_parsed() -> None:
    sheet = (
        BILLION_LAUGHS
        + sheet_xml('<row r="1"><c r="A1" t="inlineStr"><is><t>&lol3;</t></is></c></row>').split("?>", 1)[1]
    )
    data = replaced(xlsx(_sheet), "xl/worksheets/sheet1.xml", sheet)
    error = refused(data, "xlsx")
    assert (error.reason, error.detail) == ("xml", UNSAFE_XML)


def test_malformed_xml_is_damaged() -> None:
    data = replaced(docx(wp("x")), "word/document.xml", "<w:document><unclosed>")
    assert refused(data, "docx").detail == "damaged (ParseError)"


def test_a_deeply_nested_part_is_damaged_not_a_crash() -> None:
    depth = 20_000
    body = "<w:r>" * depth + "<w:t>deep</w:t>" + "</w:r>" * depth
    data = replaced(docx(wp("x")), "word/document.xml", wroot("document", f"<w:body><w:p>{body}</w:p></w:body>"))
    assert refused(data, "docx").detail == "damaged (RecursionError)"


# -- caps -------------------------------------------------------------------------------------------------------------


def test_a_file_over_the_byte_cap_is_never_read() -> None:
    error = refused(b"x" * 2000, "txt", Limits(file_bytes=1000))
    assert (error.reason, error.detail) == ("size", "1 MB, over the cap of 1 MB")


def test_rows_past_the_cap_keep_the_whole_workbook_out_and_empty_ones_don_t() -> None:
    def rows(book: Workbook) -> None:
        sheet = book.active
        assert sheet is not None
        sheet["A1"], sheet["A5"] = "first", "past the cap"

    error = refused(xlsx(rows), "xlsx", Limits(rows=4))
    assert (error.reason, error.detail) == ("rows", "sheet 1 has rows past the cap of 4")

    def trailing(book: Workbook) -> None:
        sheet = book.active
        assert sheet is not None
        sheet["A1"] = "first"
        sheet["B9"].number_format = "0.00"

    assert extract(xlsx(trailing), "xlsx", Limits(rows=4)).parts[0].text == "1: first"


def test_a_cell_past_the_column_cap_keeps_the_workbook_out() -> None:
    def wide(book: Workbook) -> None:
        sheet = book.active
        assert sheet is not None
        sheet["A1"], sheet["E1"] = "a", "past"

    error = refused(xlsx(wide), "xlsx", Limits(columns=3))
    assert (error.reason, error.detail) == ("columns", "sheet 1 has cells past column 3")


def test_too_many_sheets_slides_or_pages_keep_the_file_out() -> None:
    def two(book: Workbook) -> None:
        book.create_sheet("Second")

    assert refused(xlsx(two), "xlsx", Limits(sheets=1)).detail == "2 sheets, over the cap of 1"
    deck = pptx([Slide(sp("one")), Slide(sp("two"))])
    assert refused(deck, "pptx", Limits(slides=1)).detail == "2 slides, over the cap of 1"
    assert refused(pdf([["a"], ["b"]]), "pdf", Limits(pages=1)).detail == "2 pages, over the cap of 1"


def test_text_past_a_part_s_or_a_file_s_cap_keeps_the_file_out() -> None:
    part = refused(pdf([["x" * 200]]), "pdf", Limits(part_text=100))
    assert (part.reason, part.detail) == ("text", "page 1 holds 1 MB of text, over the cap of 1 MB")
    whole = refused(b"y" * 200, "txt", Limits(file_text=100))
    assert (whole.reason, whole.detail) == ("text", "1 MB of text, over the cap of 1 MB")


# -- pdfs -------------------------------------------------------------------------------------------------------------


def test_a_pdf_with_a_real_password_is_encrypted() -> None:
    error = refused(encrypted(pdf([["Northwind salaries"]]), "a-real-one"), "pdf")
    assert (error.reason, error.detail) == ("encrypted", "encrypted with a password")


@pytest.mark.parametrize("data", [b"%PDF-1.4\n", pdf([["cut short"]])[:200], b"%PDF-1.4\n1 0 obj << /Type"])
def test_a_broken_pdf_is_damaged(data: bytes) -> None:
    assert refused(data, "pdf").reason == "damaged"


# -- errors -----------------------------------------------------------------------------------------------------------


def test_a_reader_s_own_error_is_named_by_its_type_never_its_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(data: bytes, limits: Limits) -> formats.Extracted:
        raise ValueError("the text of a secret clause")

    monkeypatch.setattr(formats, "read_docx", boom)
    error = refused(docx(wp("x")), "docx")
    assert error.detail == "damaged (ValueError)"
    assert "secret" not in str(error)


def test_running_out_of_memory_isn_t_called_damage(monkeypatch: pytest.MonkeyPatch) -> None:
    def hungry(data: bytes, limits: Limits) -> formats.Extracted:
        raise MemoryError

    monkeypatch.setattr(formats, "read_pdf", hungry)
    with pytest.raises(MemoryError):
        extract(pdf([["x"]]), "pdf")


def test_a_blank_core_property_names_nobody() -> None:
    data = docx(wp("x"), creator=" ", modified_by="Ben\n Sample")
    got = extract(data, "docx")
    assert (got.creator, got.last_modified_by) == (None, "Ben Sample")
