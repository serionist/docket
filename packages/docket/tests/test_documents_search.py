"""A folder source in search, read, verify and doc_put: a hit gives the path and the page or the sheet's rows, `#41-80`
reads rows 41 to 80, and a file that went is read, verified and cited from its last version with text (DR-0483,
DR-0487, DR-0491)."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import copied, make_config, note
from documents_kit import pdf, xlsx
from documents_scan_kit import SOURCE, cycle, name, put, rid
from openpyxl import Workbook

from docket import recall, render
from docket.index import rebuild, search
from docket.index.query import Hit
from docket.vault.anchors import format_anchor
from docket.vault.config import SourceConfig
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
