"""A folder source in search, read, verify and doc_put: a hit gives the path and the page or the sheet's rows, `#41-80`
reads rows 41 to 80, and a file that went is read, verified and cited from its last version with text (DR-0483,
DR-0487, DR-0491)."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import copied, make_config, make_record, note
from documents_kit import docx, pdf, wp, xlsx
from documents_scan_kit import SOURCE, cycle, folder_vault, name, put, rid
from openpyxl import Workbook

from docket import recall, render
from docket.documents.ooxml import OLE
from docket.index import rebuild, search
from docket.index.query import Hit
from docket.vault import spans
from docket.vault.anchors import format_anchor
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.vault import Vault
from docket.web import views

TZ = ZoneInfo("Europe/Budapest")
REVIEW = "reports/q3 review.pdf"


def rows(book: Workbook) -> None:
    sheet = book.active
    assert sheet is not None
    sheet.title = "Costs"
    for n in range(1, 101):
        words = " ".join(f"cost{n}w{k}" for k in range(12))
        sheet.append([f"item {n}", "turbine bearing" if n == 50 else words])


@pytest.fixture
def library(tmp_path: Path) -> tuple[Vault, Path]:
    """A copy of the folder and its vault, scanned, promoted and indexed once for every test, which all start there."""
    root = copied(tmp_path, built, "documents-library")
    vault = Vault.open(root)
    name(vault, tmp_path / "work")
    return vault, tmp_path / "work"


def built(folder: Path) -> Path:
    config = make_config()
    config.sources[SOURCE] = SourceConfig(folder="~/Work")
    vault = Vault.init(folder / "vault", config, "test-box")
    work = folder / "work"
    put(work, REVIEW, pdf([["an opening page"], ["the gearbox overheated twice"], ["closing words"]]))
    put(work, "costs.xlsx", xlsx(rows))
    name(vault, work)
    cycle(vault)
    rebuild(vault)
    return vault.root


def test_a_hit_gives_the_file_s_path_and_its_page_or_rows(library: tuple[Vault, Path]) -> None:
    vault, _ = library
    found = search(vault, "gearbox")
    group = found.groups[0]
    page = f"{rid(REVIEW)}/page2"
    assert (group.kind, group.title, group.key) == ("document", REVIEW, rid(REVIEW))
    assert (group.hits[0].rid, group.hits[0].place) == (page, "page 2")
    shown = render.search(found, TZ)
    assert "  page 2" in next(line for line in shown if line.strip().startswith("1."))
    bearing = search(vault, "turbine bearing").groups[0]
    assert (bearing.kind, bearing.title) == ("document", "costs.xlsx")
    place = bearing.hits[0].place
    assert place is not None and place.startswith("Costs!")
    first, last = (int(n) for n in place.removeprefix("Costs!").split(":"))
    assert first <= 50 <= last


def test_rows_41_to_80_read_as_the_anchor_says(library: tuple[Vault, Path]) -> None:
    vault, _ = library
    sheet = f"{rid('costs.xlsx')}/sheet1"
    read = recall.read(vault, f"r:{sheet}#41-80", view="raw")
    span = read.spans[0]
    assert span.paragraphs == (41, 80)
    lines = span.text.split("\n\n")
    assert (len(lines), lines[0].split(" | ")[0], lines[-1].split(" | ")[0]) == (40, "41: item 41", "80: item 80")


def test_a_file_that_went_is_read_verified_and_cited_from_its_last_text(library: tuple[Vault, Path]) -> None:
    vault, work = library
    page = f"{rid(REVIEW)}/page2"
    doc = note("brief:q3-review", covers=[page], lines=[f"- The gearbox overheated. {format_anchor(page, 'part', 1)}"])
    assert recall.doc_put(vault, doc).notes == []
    (work / "reports" / "q3 review.pdf").rename(work / "q3 final.pdf")
    cycle(vault)
    read = recall.read(vault, f"r:{page}", view="raw")
    assert [s.text for s in read.spans] == ["Not read from the file since " + read.spans[0].text[29:],
                                           "the gearbox overheated twice"]  # fmt: skip
    assert read.spans[1].note is not None and "the latest says: Not read from the file" in read.spans[1].note
    shown = render.read(read)
    assert any(line.startswith("  (the last version with text") for line in shown)
    checked = recall.verify(vault, "brief:q3-review")
    span = checked.lines[0].spans[0]
    assert (span.problem, span.text) == (None, "the gearbox overheated twice")
    assert checked.lines[0].problems == []
    moved = rid("q3 final.pdf")
    whole = recall.read(vault, f"r:{rid(REVIEW)}", view="raw")
    assert whole.spans[1].note is not None and f"It moved to {moved}." in whole.spans[1].note
    again = doc.replace("overheated.", "overheated, twice.").replace("type: brief", "type: brief\nrev: 1")
    result = recall.doc_put(vault, again)
    assert (result.rev, len(result.notes)) == (2, 1)
    assert result.notes[0].startswith(f"{page}: the last version with text")
    assert render.put(result)[-1].startswith("note: ")


def test_the_web_shows_a_document_hit_s_place() -> None:
    hit = Hit(locator="r:documents:abc/page2#1", kind="record", place="page 2")
    assert views._stretch(hit) == "page 2"  # pyright: ignore[reportPrivateUsage]
    assert views.KIND_ICON["document"] == "doc"


# -- the review's findings ----------------------------------------------------------------------------------------

DECK = "deck.pdf"


def pages(edited: int | None = None) -> bytes:
    return pdf([[f"page {n} text" + (" edited" if n == edited else "")] for n in range(1, 13)])


def around(vault: Vault, n: int) -> list[str]:
    found = recall.read(vault, f"r:{rid(DECK)}/page{n}", view="raw", around=1)
    return [s.rid.rsplit("/", 1)[-1] for s in found.spans]


@pytest.mark.parametrize("indexed", [False, True], ids=["loaded", "placed"])
def test_around_on_a_page_reads_its_file_s_pages_in_their_order(
    tmp_path: Path, config: VaultConfig, indexed: bool
) -> None:
    work = tmp_path / "work"
    vault = folder_vault(tmp_path, config, work)
    put(work, DECK, pages())
    cycle(vault)
    # Page 5 edited: a newer ts, and still between pages 4 and 6.
    put(work, DECK, pages(edited=5), mtime=1_792_000_000)
    cycle(vault)
    if indexed:
        rebuild(vault)
    assert around(vault, 2) == ["page1", "page2", "page3"]
    assert around(vault, 1) == ["page1", "page2"]
    assert around(vault, 12) == ["page11", "page12"]
    assert around(vault, 5) == ["page4", "page5", "page6"]
    assert around(vault, 4) == ["page3", "page4", "page5"]
    (work / DECK).unlink()
    cycle(vault)
    if indexed:
        rebuild(vault)
    found = recall.read(vault, f"r:{rid(DECK)}/page3", view="raw", around=1)
    shown = [(s.rid.rsplit("/", 1)[-1], s.text, s.note is not None) for s in found.spans]
    assert shown[0][0] == "page3" and shown[0][1].startswith("Not read from the file since ")
    assert shown[1:] == [("page2", "page 2 text", True), ("page3", "page 3 text", True), ("page4", "page 4 text", True)]


def test_a_mapper_s_page_or_document_is_neither_placed_nor_grouped_as_a_document(
    library: tuple[Vault, Path],
) -> None:
    vault, _ = library
    page = make_record(1, rid="chat:C0WIKI/roadmap", kind="page", title="Roadmap, page 3", text="the zebra plan")
    shaped = make_record(2, rid="chat:0123456789ab", kind="document", title="notes.md", text="the zebra notes",
                         container=None, thread=None)  # fmt: skip
    Gate(vault).stage([page, shaped])
    Gate(vault).promote("chat")
    rebuild(vault)
    found = search(vault, "zebra")
    assert found.groups
    assert all(group.kind != "document" for group in found.groups)
    assert all(hit.place is None for group in found.groups for hit in group.hits)


def test_a_file_that_turns_protected_is_verified_and_cited_from_its_last_text(
    tmp_path: Path, config: VaultConfig
) -> None:
    work = tmp_path / "work"
    vault = folder_vault(tmp_path, config, work)
    put(work, "plan.docx", docx(wp("one") + wp("two") + wp("three")))
    cycle(vault)
    plan = rid("plan.docx")
    doc = note("brief:plan", covers=[plan], lines=[f"- Three. {format_anchor(plan, 'part', 3)}"])
    recall.doc_put(vault, doc)
    # A sensitivity label wraps the docx in an OLE container: the file is now one Docket can't read.
    put(work, "plan.docx", OLE + bytes(4096), mtime=1_792_000_000)
    cycle(vault)
    checked = recall.verify(vault, "brief:plan")
    span = checked.lines[0].spans[0]
    assert (span.problem, span.text) == (None, "three")
    assert span.note is not None and "the latest says: Not read: protected" in span.note
    again = doc.replace("Three.", "Three, still.").replace("type: brief", "type: brief\nrev: 1")
    result = recall.doc_put(vault, again)
    assert (result.rev, len(result.notes)) == (2, 1)
    read = recall.read(vault, f"r:{plan}", view="raw")
    assert [s.text for s in read.spans] == ["Not read: protected, or in an old binary format.", "one\n\ntwo\n\nthree"]


def test_doc_put_reads_a_covered_source_s_raw_once(
    library: tuple[Vault, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, _ = library
    page = f"{rid(REVIEW)}/page2"
    loads: list[str] = []
    real = spans.RawLookup._source  # pyright: ignore[reportPrivateUsage]

    def counting(self: spans.RawLookup, source: str) -> dict[str, list[RawRecord]]:
        if source not in self._sources:  # pyright: ignore[reportPrivateUsage]
            loads.append(source)
        return real(self, source)

    monkeypatch.setattr(spans.RawLookup, "_source", counting)
    recall.doc_put(vault, note("brief:q3", covers=[page], lines=[f"- Hot. {format_anchor(page, 'part', 1)}"]))
    assert loads == [SOURCE]
