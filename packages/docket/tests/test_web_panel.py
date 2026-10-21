"""The Search page's panel read, signed in, on the index fixture (DR-0709, DR-0775): a hit's raw beside the summary that
covers it, the raw around it widened and narrowed, a doc verified line by line, the Recording page's link, and the
panel's own errors. Each link is the Search page's URL for the state it opens."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from docket_testkit import PLAUD, POLISHED, index_template, make_record, note, plaud_records
from docket_webkit import signed_in
from starlette.testclient import TestClient

from docket import recall
from docket.documents import records
from docket.vault.processed import DocMeta, ProcessedDoc
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

CALL = "r:calls:rec-0100@01:50-03:20"
OFFER = "r:mail:<offer-1@northwind.example>#1-4"
MESSAGE = "r:chat:C0OPS/1791400800.000100"
API = "/api/web/search/panel"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


def panel(client: TestClient, hit: str, extra: str = "") -> dict[str, Any]:
    answer = client.get(f"{API}?hit={quote(hit)}{extra}")
    assert answer.status_code == 200, answer.text
    found = answer.json()["panel"]
    assert found is not None
    return found


def cited(lines: list[dict[str, Any]]) -> list[int]:
    return [line["n"] for line in lines if line["cited"]]


def segments(found: dict[str, Any]) -> list[dict[str, Any]]:
    return [seg for span in found["spans"] for seg in span["segments"]]


def text_of(found: dict[str, Any]) -> str:
    """Everything the panel says, as one text, to find a sentence in."""
    parts = [found["title"], found["no_summary"], *found["citing"], *found["legacy"], *found["problems"]]
    for span in found["spans"]:
        parts += [span["head"], span["versions"], span["note"] or "", span["problem"] or ""]
        parts += [s["text"] for s in span["segments"]] + [p["text"] for p in span["paragraphs"]]
    if found["summary"]:
        parts += [line["body"] for line in found["summary"]["lines"]]
    return "\n".join(parts)


def test_a_transcript_hit_puts_its_raw_beside_its_summary(client: TestClient) -> None:
    found = panel(client, CALL)
    summary = found["summary"]
    assert (summary["id"], summary["type"], summary["rev"]) == ("minutes:2026-10-07-supplier-call", "minutes", 1)
    assert cited(summary["lines"]) == [14]
    line = next(line for line in summary["lines"] if line["cited"])
    anchor = line["anchors"][0]
    assert anchor["text"] == "@01:50-02:30"
    assert anchor["url"] == "/search?hit=r:calls:rec-0100@01:50-02:30"
    assert "recording-note:2026-10-07-supplier-call rev 1, line 16" in found["citing"]
    marked = [s["at"] for s in segments(found) if s["mark"]]
    assert marked[0] == "01:50"
    assert all("01:50" <= t <= "03:20" for t in marked)
    assert any(s["at"] < "01:50" for s in segments(found) if not s["mark"])
    assert segments(found)[0]["locator"].startswith("r:calls:rec-0100@")
    assert found["around_text"] == "\N{PLUS-MINUS SIGN} 1 min"
    assert (found["verify_label"], found["verify"]) == ("Verify the summary", f"/search?hit={CALL}&verify=1")


def test_a_recording_hit_links_to_the_recording_page_and_another_hit_doesn_t(
    client: TestClient, tmp_path: Path
) -> None:
    assert panel(client, CALL)["recording"] == "/recording?rid=calls:rec-0100&at=01:50-03:20#at"
    assert panel(client, OFFER)["recording"] is None
    # A recording kept without segments of its own, as Plaud's mapper keeps one, opens there whole.
    Vault.open(tmp_path / "vault").write_run("plaud", plaud_records("transaction_polish"))
    assert panel(client, f"r:{PLAUD}")["recording"] == f"/recording?rid={PLAUD}"


def test_around_widens_the_raw(client: TestClient) -> None:
    def count(around: int) -> int:
        return len(segments(panel(client, CALL, f"&around={around}")))

    assert count(0) < count(1) < count(3)
    least = panel(client, CALL, "&around=0")
    assert least["less"] is None
    # One minute is the default, so the URL leaves it out.
    assert least["more"] == f"/search?hit={CALL}"
    top = panel(client, CALL, "&around=30")
    assert top["more"] is None
    assert top["less"] == f"/search?hit={CALL}&around=29"


def test_a_message_hit_shows_its_neighbours_with_itself_marked(client: TestClient) -> None:
    found = panel(client, MESSAGE)
    assert [span["mark"] for span in found["spans"]] == [False, True, False]
    assert any("cold-rated" in p["text"] for p in found["spans"][1]["paragraphs"])
    assert found["around_text"] == "\N{PLUS-MINUS SIGN} 1 message"
    assert panel(client, MESSAGE, "&around=2")["around_text"] == "\N{PLUS-MINUS SIGN} 2 messages"


def test_a_mail_paragraph_hit_numbers_the_paragraphs_and_marks_its_own(client: TestClient) -> None:
    found = panel(client, OFFER, "&around=0")
    paras = [p for span in found["spans"] for p in span["paragraphs"]]
    assert [p["n"] for p in paras] == [1, 2, 3, 4]
    assert all(p["mark"] for p in paras)
    assert found["around_text"] == "\N{PLUS-MINUS SIGN} 0 paragraphs"
    assert "day rate" in text_of(found)


def test_a_doc_hit_verifies_its_lines_and_then_every_line(client: TestClient) -> None:
    doc = "recording-note:2026-10-07-supplier-call"
    found = panel(client, doc, "&lines=21-22")
    assert found["mode"] == "verify"
    assert [p["line"]["n"] for p in found["pairs"]] == [21, 22]
    assert all(p["spans"] for p in found["pairs"])
    assert (found["verify_label"], found["verify"]) == ("Verify every line", f"/search?hit={doc}&lines=21-22&verify=1")
    every = panel(client, doc, "&lines=21-22&verify=1")
    assert len(every["pairs"]) > 2
    assert every["verify_label"] == "Only the hit's lines"
    whole = panel(client, doc)
    assert len(whole["pairs"]) == len(every["pairs"])
    assert whole["verify"] is None


def test_verify_on_a_record_hit_verifies_its_summary(client: TestClient) -> None:
    found = panel(client, CALL, "&verify=1")
    assert [p["line"]["n"] for p in found["pairs"]] == [14]
    assert found["title"] == "Minutes: supplier call"
    assert (found["verify_label"], found["verify"]) == ("Back to the raw", f"/search?hit={CALL}")


def test_a_line_whose_anchor_doesnt_resolve_shows_its_problem(client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "vault" / "processed" / "minutes" / "2026" / "10"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026-10-15-hand-written.md").write_text(
        "---\nid: minutes:2026-10-15-hand-written\ntype: minutes\ncovers:\n  - calls:rec-0101\nby: hand\n---\n\n"
        "# Hand written\n\n- Right here. @00:10\n- Far past the end. @59:00\n- No anchor at all.\n",
        encoding="utf-8",
        newline="\n",
    )
    pairs = panel(client, "minutes:2026-10-15-hand-written")["pairs"]
    flagged = [p for p in pairs if p["flagged"]]
    assert len(pairs) == 3
    assert len(flagged) == 2
    assert "is outside calls:rec-0101" in " ".join(s["problem"] or "" for s in flagged[0]["spans"])
    assert "no anchors at the end of the line" in " ".join(flagged[1]["problems"])


def test_a_record_no_summary_covers(client: TestClient) -> None:
    hit = "r:chat:C0SIDE/1791500000.000100"
    found = panel(client, hit)
    assert found["no_summary"] == "No summary covers this yet."
    assert found["verify"] is None
    assert "nothing to verify" in panel(client, hit, "&verify=1")["no_summary"]


def test_a_legacy_note_by_id_and_a_doc_by_its_path(client: TestClient, tmp_path: Path) -> None:
    found = panel(client, "legacy-note:old-northwind-notes")
    assert "A legacy note has no anchors" in found["no_summary"]
    assert "Northwind's site lead prefers calls on Tuesdays." in text_of(found)
    path = next((tmp_path / "vault" / "processed").rglob("2026-10-13-fleet-review.md"))
    rel = path.relative_to(tmp_path / "vault").as_posix()
    by_path = panel(client, rel, "&lines=12")
    assert by_path["resolved"]
    assert cited(by_path["summary"]["lines"])
    assert by_path["spans"]


def test_a_locator_that_doesnt_resolve_is_the_panel_s_own_error(client: TestClient) -> None:
    for hit in ("r:calls:rec-9999@00:10", "[r:broken", "minutes:2026-01-01-no-such-doc"):
        found = panel(client, hit)
        assert (found["mode"], bool(found["error"])) == ("error", True), hit
    # The results beside it read as before.
    results = client.get("/api/web/search?query=Nortwin&hit=r:calls:rec-9999@00:10").json()["results"]
    assert results["groups"]
    assert "no record calls:rec-9999" in panel(client, "r:calls:rec-9999@00:10")["error"]


def test_a_record_with_two_versions_says_the_latest_is_shown(client: TestClient, tmp_path: Path) -> None:
    vault = Vault.open(tmp_path / "vault")
    vault.write_run("chat", [make_record(3, container="chat:C0X", text="first words")])
    vault.write_run("chat", [make_record(3, container="chat:C0X", text="second words")])
    found = panel(client, "r:chat:C0TEST/1791000003.000100")
    assert "2 versions; this is the latest" in text_of(found)
    assert "second words" in text_of(found)


def test_a_transcript_hit_names_what_covers_its_recording(client: TestClient, tmp_path: Path) -> None:
    vault = Vault.open(tmp_path / "vault")
    vault.write_run("plaud", plaud_records("transaction_polish"))
    old = "notes/plaud/2026-10-14-supplier-sync.md"
    head = DocMeta(
        id="legacy-note:supplier-sync", type="legacy-note", covers=[PLAUD], by="claude-test", old_paths=[old]
    )
    recall.doc_put(vault, ProcessedDoc(head, "# Supplier sync\n\nPrice agreed.\n", 0).render())
    recall.doc_put(vault, note("recording-note:2026-10-14-sync", covers=[PLAUD], lines=[f"- The call. [r:{PLAUD}]"]))
    found = panel(client, f"r:{POLISHED}@10:42")
    assert found["summary"]["id"] == "recording-note:2026-10-14-sync"
    assert cited(found["summary"]["lines"]) == [14]
    assert found["legacy"] == [f"legacy-note:supplier-sync: {old}"]


def test_a_recording_hit_marks_the_lines_on_its_transcript(client: TestClient, tmp_path: Path) -> None:
    vault = Vault.open(tmp_path / "vault")
    vault.write_run("plaud", plaud_records("transaction_polish"))
    recall.doc_put(vault, note("recording-note:2026-10-14-sync", covers=[POLISHED], lines=["- Ten million. @10:42"]))
    summary = panel(client, f"r:{PLAUD}")["summary"]
    assert summary["id"] == "recording-note:2026-10-14-sync"
    assert cited(summary["lines"]) == [14]


def test_a_folder_file_that_went_shows_its_last_text_with_a_note_in_place_of_the_latest_badge(
    client: TestClient, tmp_path: Path
) -> None:
    vault = Vault.open(tmp_path / "vault")
    when = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    first = RawRecord(
        rid="documents:0123456789ab", source="documents", kind="document", ts=when, title="q3.md",
        text="the gearbox overheated", native={"format": "md"}, fetched=when, via=records.VIA,
    )  # fmt: skip
    vault.write_run("documents", [first], when)
    gone = records.gone_version(first, at=when, day="2026-10-16", moved_to="documents:ba9876543210")
    vault.write_run("documents", [gone], datetime(2026, 10, 16, 9, 0, tzinfo=UTC))
    text = text_of(panel(client, f"r:{first.rid}"))
    assert "the gearbox overheated" in text
    assert "the last version with text; the latest says: Not read from the folder since 2026-10-16." in text
    assert "It moved to documents:ba9876543210." in text
    # The badge belongs to the newest version alone, the gone one; the old text carries the note instead.
    assert text.count("this is the latest") == 1
