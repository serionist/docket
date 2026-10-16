"""Hostile and broken files (DR-0485): each is kept out whole with Docket's own reason, never read past a cap, never
parsed with a DTD, and never named by an error's message. Every file is built here; nothing binary is committed."""

from __future__ import annotations

import io
import time
import tracemalloc
import zipfile

import pytest
from documents_kit import (
    DOC_REL,
    Slide,
    docx,
    encrypted,
    overlapping,
    package,
    patched,
    pdf,
    pptx,
    rels,
    replaced,
    sheet_xml,
    sp,
    streamed,
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


@pytest.mark.parametrize(("method", "name"), [(zipfile.ZIP_BZIP2, "bzip2"), (zipfile.ZIP_LZMA, "lzma")])
def test_a_part_packed_with_bzip2_or_lzma_is_refused_before_it_is_inflated(method: int, name: str) -> None:
    # Both inflate a whole compressed chunk at once, so a few hundred bytes can become hundreds of MiB in one call.
    data = package({"_rels/.rels": rels(("rId1", DOC_REL, "word/document.xml")), "word/document.xml": "<x/>"},
                   compression=method)  # fmt: skip
    error = refused(data, "docx")
    assert (error.reason, error.detail) == (
        "zip",
        f"a part in its package packed with {name}, which Docket doesn't read",
    )


def test_a_part_that_lies_about_its_size_is_counted_by_what_it_holds() -> None:
    members: dict[str, str | bytes] = {"_rels/.rels": rels(("rId1", DOC_REL, "word/document.xml"))}
    data = patched(
        streamed(members, "word/document.xml", 2, b"<" + b" " * (MIB - 1)), "word/document.xml", declared=1000
    )
    assert zipfile.ZipFile(io.BytesIO(data)).getinfo("word/document.xml").file_size == 1000
    error = refused(data, "docx", Limits(member_bytes=MIB, ratio=10**9))
    assert (error.reason, error.detail) == ("zip", "a part of 2 MB in its package, over the cap of 1 MB")


def test_a_lying_zip_bomb_is_refused_without_inflating_it_whole() -> None:
    # 80 MiB of spaces deflate to about 80 KB and declare 1,000 bytes; zipfile's own read would inflate all of it.
    members: dict[str, str | bytes] = {"_rels/.rels": rels(("rId1", DOC_REL, "word/document.xml"))}
    data = patched(streamed(members, "word/document.xml", 80, b" " * MIB), "word/document.xml", declared=1000)
    tracemalloc.start()
    try:
        error = refused(data, "docx")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert error.reason == "zip"
    assert peak < 16 * MIB


def test_a_part_without_its_header_or_cut_short_or_not_deflate_is_damaged() -> None:
    data = docx(wp("Northwind"))
    headless = data.replace(b"PK\x03\x04", b"PK\x00\x00", 1)
    assert refused(headless, "docx").detail == "damaged (a part's header is missing)"
    short = patched(data, "word/document.xml", compressed=10**6)
    assert refused(short, "docx").detail == "damaged (a part is cut short)"
    info = zipfile.ZipFile(io.BytesIO(data)).getinfo("word/document.xml")
    begin = info.header_offset + 30 + len(info.filename) + len(info.extra)
    garbled = data[:begin] + bytes([0xFF]) * info.compress_size + data[begin + info.compress_size :]
    assert refused(garbled, "docx").detail == "damaged (error)"


@pytest.mark.perf
def test_entries_that_share_one_stream_are_refused_before_any_is_inflated() -> None:
    # 200 entries over one 64 MiB stream took 9 s to inflate one by one before the package's cap stopped them.
    data = overlapping(200, 64 * MIB, 700 * 1024)
    assert len(zipfile.ZipFile(io.BytesIO(data)).infolist()) == 200
    started = time.perf_counter()
    assert refused(data, "docx").detail == "damaged (its parts overlap)"
    assert time.perf_counter() - started < 2.0


def test_what_is_left_of_the_package_s_cap_caps_the_next_part() -> None:
    members: dict[str, str | bytes] = {f"part{n}.bin": bytes(2 * MIB) for n in range(3)}
    error = refused(package(members), "docx", Limits(package_bytes=3 * MIB, ratio=10**9))
    assert (error.reason, error.detail) == ("zip", "4 MB of parts in its package, over the cap of 3 MB")


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


@pytest.mark.parametrize(
    ("prefix", "text"),
    [
        (BILLION_LAUGHS, "&lol3;"),
        (EXTERNAL, "&secret;"),
        # A bare DTD: openpyxl's own parse would take it, so the package's check refuses it first.
        ('<?xml version="1.0"?><!DOCTYPE worksheet>', "plain"),
    ],
    ids=["billion-laughs", "external-entity", "bare-dtd"],
)
def test_a_workbook_part_with_a_dtd_or_entities_is_never_parsed(prefix: str, text: str) -> None:
    sheet = prefix + sheet_xml(f'<row r="1"><c r="A1" t="inlineStr"><is><t>{text}</t></is></c></row>').split("?>", 1)[1]
    data = replaced(xlsx(_sheet), "xl/worksheets/sheet1.xml", sheet)
    error = refused(data, "xlsx")
    assert (error.reason, error.detail) == ("xml", UNSAFE_XML)


def test_a_dtd_is_refused_in_a_part_whatever_its_name_and_an_image_isn_t_xml() -> None:
    data = replaced(docx(wp("x")), "word/media/notes.data", '<?xml version="1.0"?><!DOCTYPE x><x/>')
    assert refused(data, "docx").detail == UNSAFE_XML
    image = replaced(docx(wp("Beside a picture")), "word/media/image1.png", b"\x89PNG\r\n\x1a\n" + bytes(64))
    assert extract(image, "docx").text == "Beside a picture"


SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
    '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
)
TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"


def test_an_image_s_dtd_never_keeps_the_file_out_by_its_name_or_its_content_type() -> None:
    # The SVG 1.1 DOCTYPE that draw.io and matplotlib write; nothing parses an image.
    named = replaced(docx(wp("Northwind plan")), "word/media/image1.svg", SVG)
    assert extract(named, "docx").text == "Northwind plan"
    types = (
        f'<?xml version="1.0"?><Types xmlns="{TYPES}"><Default Extension="IMG" ContentType="image/svg+xml"/>'
        '<Override PartName="/word/media/chart.bin" ContentType="image/svg+xml"/>'
        '<Override PartName="/word/media/drawing.img" ContentType="application/xml"/></Types>'
    )
    typed = replaced(replaced(docx(wp("Northwind plan")), "[Content_Types].xml", types), "word/media/pic.img", SVG)
    typed = replaced(typed, "word/media/chart.bin", SVG)
    assert extract(typed, "docx").text == "Northwind plan"
    # Its override says it isn't an image, whatever its extension's default says.
    xml_part = replaced(typed, "word/media/drawing.img", SVG)
    assert refused(xml_part, "docx").detail == UNSAFE_XML


def test_content_types_with_a_dtd_are_refused() -> None:
    data = replaced(
        docx(wp("x")), "[Content_Types].xml", f'<?xml version="1.0"?><!DOCTYPE Types><Types xmlns="{TYPES}"/>'
    )
    assert refused(data, "docx").detail == UNSAFE_XML


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
