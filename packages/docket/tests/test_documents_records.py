"""A folder source's records (DR-0482, DR-0483): a golden set per format, the id, the place a hit names, and the gone
version and what anchors resolve against once a file went (DR-0487, DR-0491)."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import pytest
from documents_kit import Slide, docx, pdf, pptx, sp, wp, xlsx
from documents_scan_kit import MTIME, SOURCE, folder_vault, put, rid, run, staged
from openpyxl import Workbook

from docket.documents import records
from docket.vault.config import VaultConfig
from docket.vault.records import RawRecord

NOW = datetime(2026, 10, 16, 8, 30, tzinfo=UTC)
WHEN = datetime.fromtimestamp(MTIME, UTC)


def test_a_file_s_id_is_12_hex_of_its_nfc_path_inside_the_folder() -> None:
    nfd = unicodedata.normalize("NFD", "jelentés/ő.md")
    assert records.file_id(nfd) == records.file_id("jelentés/ő.md")
    assert len(records.file_id("a.md")) == 12
    assert records.file_id("a.md") != records.file_id("A.md")
    assert records.file_rid("documents", "a.md") == f"documents:{records.file_id('a.md')}"
    assert records.part_rid("documents:abc", "page", 3) == "documents:abc/page3"
    assert records.file_of("documents:abc/sheet12") == "documents:abc"
    assert records.file_of("documents:abc") == "documents:abc"


def books(book: Workbook) -> None:
    sheet = book.active
    assert sheet is not None
    sheet.title = "Costs"
    sheet.append(["Widget", 3, 4.5])
    hidden = book.create_sheet("Old notes")
    hidden.sheet_state = "hidden"
    hidden.append(["kept"])


def test_a_golden_record_set_per_format(tmp_path: Path, config: VaultConfig) -> None:
    work = tmp_path / "work"
    vault = folder_vault(tmp_path, config, work)
    put(work, "notes/plan.md", "# Plan\n\nWe ship in May.\n")
    put(work, "old.txt", "caf\xe9 au lait\n".encode("cp1252"))
    put(work, "brief.docx", docx(wp("A brief."), creator="Ada Example", modified_by="Ben Sample"))
    put(work, "deck.pptx", pptx([Slide(sp("Kick-off"), notes="say hello"), Slide(sp("Spare"), hidden=True)]))
    put(work, "costs.xlsx", xlsx(books))
    put(work, "scan.pdf", pdf([["page one"], []]))
    report = run(vault, now=NOW)
    assert report.outcomes == {"new": 6}
    got = {r.rid: r for r in staged(vault)}
    assert all(r.via == records.VIA and r.fetched == NOW and r.ts == WHEN for r in got.values())
    plan = got[rid("notes/plan.md")]
    assert (plan.kind, plan.title, plan.text, plan.engine) == ("document", "notes/plan.md", "# Plan\n\nWe ship in May.",
                                                               "docket-text 1")  # fmt: skip
    assert set(plan.native) == {"format", records.TEXT_HASH}
    assert got[rid("old.txt")].text == "café au lait"
    assert got[rid("old.txt")].native["note"] == "decoded as Windows-1252"
    brief = got[rid("brief.docx")]
    assert (brief.text, brief.native["creator"], brief.native["last_modified_by"]) == ("A brief.", "Ada Example",
                                                                                        "Ben Sample")  # fmt: skip
    assert brief.native["size"] == (work / "brief.docx").stat().st_size
    assert len(brief.native[records.BYTES_HASH]) == 64
    deck = rid("deck.pptx")
    assert (got[deck].text, got[deck].native["parts"]) == ("", 2)
    slide = got[f"{deck}/slide1"]
    assert (slide.kind, slide.title, slide.container, slide.thread) == ("slide", "deck.pptx, slide 1", deck, deck)
    assert slide.text == "Kick-off\n\nSpeaker notes:\n\nsay hello"
    assert got[f"{deck}/slide2"].native == {"hidden": True}
    assert got[f"{deck}/slide2"].title == "deck.pptx, slide 2 (hidden)"
    costs = rid("costs.xlsx")
    assert got[f"{costs}/sheet1"].title == "costs.xlsx, sheet Costs"
    assert got[f"{costs}/sheet1"].text == "1: Widget | 3 | 4.5"
    assert got[f"{costs}/sheet2"].title == "costs.xlsx, sheet Old notes (hidden)"
    assert (got[f"{costs}/sheet1"].engine or "").startswith("openpyxl ")
    pages = rid("scan.pdf")
    assert [got[f"{pages}/page{n}"].title for n in (1, 2)] == ["scan.pdf, page 1", "scan.pdf, page 2"]
    assert got[f"{pages}/page2"].text.startswith("No text on this page")
    assert got[pages].native["parts"] == 2
    # A part names no path and carries no hash: the file record holds the identity (DR-0483).
    assert all(SOURCE not in r.text and "sha256" not in r.native for r in got.values() if r.kind != "document")


@pytest.mark.parametrize(
    ("kind", "title", "first", "last", "place"),
    [
        ("page", "plan.pdf, page 3", None, None, "page 3"),
        ("slide", "deck.pptx, slide 4 (hidden)", 2, 2, "slide 4 (hidden)"),
        ("sheet", "costs.xlsx, sheet Costs", 41, 80, "Costs!41:80"),
        ("sheet", "costs.xlsx, sheet Old notes (hidden)", 2, 2, "'Old notes'!2:2"),
        ("sheet", "costs.xlsx, sheet Ada's", 1, 3, "'Ada''s'!1:3"),
        ("sheet", "costs.xlsx, sheet Costs", None, None, "sheet Costs"),
        ("document", "costs.xlsx", 1, 2, None),
        ("message", "hi", None, None, None),
        ("page", None, None, None, None),
    ],
)
def test_a_hit_s_place_in_its_file(
    kind: str, title: str | None, first: int | None, last: int | None, place: str | None
) -> None:
    assert records.place(kind, title, first, last) == place


def record(rid: str, text: str, **native: object) -> RawRecord:
    return RawRecord(rid=rid, source="documents", kind="document", ts=NOW, title="a.md", text=text,
                     native=dict(native), fetched=NOW, via=records.VIA)  # fmt: skip


def test_a_gone_version_keeps_the_title_and_says_since_when() -> None:
    newest = record("documents:abc", "the text", format="md", redacted_sha256="0" * 64)
    gone = records.gone_version(newest, at=NOW, day="2026-10-16", moved_to="documents:def")
    assert (gone.title, gone.text, gone.ts) == ("a.md", "Not read from the folder since 2026-10-16.", NOW)
    assert gone.native == {"gone": "2026-10-16", "format": "md", "moved_to": "documents:def"}
    assert records.is_gone(gone) and records.identity(gone) is None
    assert records.identity(newest) == "0" * 64
    page = newest.model_copy(update={"rid": "documents:abc/page1", "kind": "page", "native": {}})
    assert records.gone_version(page, at=NOW, day="2026-10-16").text == "Not read from the file since 2026-10-16."
    assert records.resolving([newest, gone]) is newest
    assert records.resolving([gone]) is gone
    assert records.resolving([gone, newest]) is newest
    assert records.readable([gone]) is None
    assert records.gone_note(gone) == (
        "the last version with text; the latest says: Not read from the folder since 2026-10-16. It moved to "
        "documents:def."
    )
    # Another source's record is never gone, whatever its native says.
    other = newest.model_copy(update={"via": "slack", "native": {"gone": "x"}})
    assert not records.is_gone(other)
