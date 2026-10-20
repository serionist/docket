from __future__ import annotations

from datetime import UTC, datetime

from docket import render
from docket.recall import DocLine, DocView, ReadResult
from docket.vault.records import Segment
from docket.vault.spans import Span


def test_a_records_terminal_codes_come_out_escaped() -> None:
    raw = Span(rid="chat:C0TEST/1", anchor="[r:chat:C0TEST/1]", kind="message", text="copy \x1b]52;c;aGk=\x07 this")
    shown = render.text(render.span(raw))
    assert "\x1b" not in shown
    assert "\x07" not in shown
    assert "copy \\x1b]52;c;aGk=\\x07 this" in shown


def test_a_span_shows_its_header_segments_and_versions() -> None:
    s = Span(
        rid="calls:rec-0042",
        anchor="@12:08",
        kind="transcript",
        ts=datetime(2026, 10, 14, 9, 0, tzinfo=UTC),
        author="Ben Sample",
        versions=2,
        segments=[Segment(start_ms=728000, end_ms=740500, speaker=None, text="So ten million.")],
    )
    assert render.span(s, "  ") == [
        "  @12:08  transcript 2026-10-14 09:00Z Ben Sample (2 versions; this is the latest)",
        "    12:08  ?: So ten million.",
    ]
    assert render.span(Span(rid="x:1", anchor="[r:x:1]", problem="x:1 isn't in the raw log")) == [
        "[r:x:1]",
        "  ! x:1 isn't in the raw log",
    ]


def test_a_doc_nothing_supersedes_names_no_successor() -> None:
    doc = DocView(
        id="recording-note:2026-10-14-call", type="recording-note", rev=1, path="processed/x.md", by="claude-test",
        covers=["calls:rec-0042"], supersedes=[], entities=[], superseded_by=[], text="# A note\n",
        lines=[DocLine(n=1, text="# A note")],
    )  # fmt: skip
    assert render.read(ReadResult(locator="x", view="summary", doc=doc)) == [
        "recording-note:2026-10-14-call rev 1  processed/x.md",
        "   1  # A note",
    ]


def test_a_read_of_several_asks_again_from_the_first_left_until_later_is_empty() -> None:
    """The read already made isn't asked for again: the first locator left is the next call's locator (DR-0725)."""
    one = ReadResult(locator="calls:rec-0042", view="raw", later=["calls:rec-0043"])
    assert render.reads(one) == [
        "== calls:rec-0042",
        "past the room, not read: ask again with locator calls:rec-0043, until later is empty",
    ]
    two = one.model_copy(update={"later": ["calls:rec-0043", "calls:rec-0044", "calls:rec-0045"]})
    assert render.reads(two)[-1] == (
        "past the room, not read: ask again with locator calls:rec-0043, more calls:rec-0044, calls:rec-0045, until "
        "later is empty"
    )
