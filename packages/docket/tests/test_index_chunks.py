from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from docket_testkit import make_record

from docket.index.chunks import (
    Message,
    RecordInfo,
    doc_chunks,
    doc_title,
    locator,
    record_chunks,
    thread_chunks,
    windows,
)
from docket.vault.anchors import parse_locator
from docket.vault.processed import DOC_TYPES, parse_doc
from docket.vault.records import RawRecord
from docket.vault.segments import Segment

TZ = ZoneInfo("Europe/Budapest")


def seg(start: float, end: float, text: str, speaker: str | None = "Ben Sample") -> Segment:
    return Segment(start_ms=int(start * 1000), end_ms=int(end * 1000), speaker=speaker, text=text)


def call(**fields: Any) -> RawRecord:
    return make_record(rid="calls:rec-1", source="calls", kind="recording", container=None, **fields)


def test_windows_grow_to_60_s_stop_before_90_s_and_share_one_utterance() -> None:
    segs = [seg(i * 25, i * 25 + 25, f"part {i}") for i in range(8)]
    found = windows(segs)
    spans = [(w[0].start_ms // 1000, max(s.end_ms for s in w) // 1000) for w in found]
    assert spans == [(0, 75), (50, 125), (100, 175), (150, 200)]
    assert all(a[-1] == b[0] for a, b in itertools.pairwise(found))


def test_a_window_stops_before_350_words_and_a_gap_or_90_s() -> None:
    long = " ".join(["word"] * 200)
    found = windows([seg(0, 10, long), seg(10, 20, long), seg(20, 30, "short")])
    assert [len(w) for w in found] == [1, 2]
    gap = windows([seg(0, 10, "a"), seg(85, 95, "b"), seg(100, 110, "c")])
    assert [len(w) for w in gap] == [1, 2]
    assert windows([seg(0, 5, "only")]) == [[seg(0, 5, "only")]]


def test_transcript_chunks_keep_speakers_once_per_turn_times_and_locators() -> None:
    segs = [
        seg(0, 20, "Morning."),
        seg(18, 40, "Shall we start?", "Cleo Test"),
        seg(40, 50, "Yes."),
        seg(50, 61, "The price is ten million."),
    ]
    chunks = record_chunks(call(title="Supplier call"), segs)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.text == "Ben Sample: Morning.\nCleo Test: Shall we start?\nBen Sample: Yes. The price is ten million."
    assert chunk.locator == "r:calls:rec-1@00:00-01:01"
    assert (chunk.start_ms, chunk.end_ms, chunk.speakers) == (0, 61000, ["Ben Sample", "Cleo Test"])
    assert chunk.kind == "transcript" and chunk.grp == "calls:rec-1" and chunk.title == "Supplier call"
    assert chunk.ts == "2026-10-14T09:30:01.000Z"
    unlabelled = record_chunks(call(), [seg(0, 2, "hello", None)])
    assert unlabelled[0].text == "hello" and unlabelled[0].locator == "r:calls:rec-1@00:00-00:02"
    assert record_chunks(call(), [seg(5.2, 5.4, "hm")])[0].locator == "r:calls:rec-1@00:05-00:06"


def test_an_utterance_over_350_words_is_split_keeping_its_times() -> None:
    chunks = record_chunks(call(), [seg(0, 400, " ".join(f"w{i}" for i in range(800)))])
    assert len(chunks) == 3
    assert {c.locator for c in chunks} == {"r:calls:rec-1@00:00-06:40"}
    assert [c.ordinal for c in chunks] == [0, 1, 2]
    assert chunks[2].text.startswith("Ben Sample: w700")


def test_a_message_is_one_chunk_with_its_author() -> None:
    record = make_record(1, thread="chat:C0TEST/1791000001.000100", text="Hello\n\n\nthere")
    (chunk,) = record_chunks(record, [])
    assert chunk.kind == "message" and chunk.text == "Hello\n\nthere"
    assert chunk.authors == ["U0ADA"] and chunk.grp == "chat:C0TEST/1791000001.000100"
    assert chunk.locator == "r:chat:C0TEST/1791000001.000100"
    assert record_chunks(make_record(2, text="", title=None), []) == []
    assert record_chunks(make_record(3, text="", title="Only a title", author=None), [])[0].text == "Only a title"


def test_a_long_record_splits_between_paragraphs_counted_as_read_counts_them() -> None:
    para = " ".join(["word"] * 150)
    text = "\r\n\r\n".join([para, para, "\N{NO-BREAK SPACE}", para, para, " ".join(["big"] * 600)])
    record = make_record(rid="mail:<x@example.invalid>", source="mail", kind="mail", title="Offer", text=text)
    chunks = record_chunks(record, [])
    assert [c.locator for c in chunks] == [
        "r:mail:<x@example.invalid>#1-3",
        "r:mail:<x@example.invalid>#4",
        "r:mail:<x@example.invalid>#5",
    ]
    assert [(c.para_from, c.para_to) for c in chunks] == [(1, 3), (4, 4), (5, 5)]
    assert all(c.text.startswith("Offer\n\n") for c in chunks)
    short = record_chunks(make_record(rid="mail:<y@example.invalid>", source="mail", kind="mail", text="Hi."), [])
    assert short[0].locator == "r:mail:<y@example.invalid>" and short[0].kind == "record"


def test_locators_read_back_through_the_anchor_grammar() -> None:
    for rid in ("mail:<50%off]x@example.invalid>", "chat:C0/1@1:00", "calls:x#3"):
        for kind, start, end in (("record", 0, None), ("time", 61, 75), ("part", 2, 4), ("time", 5, 5)):
            parsed = parse_locator(locator(rid, kind, start, end), DOC_TYPES)
            assert parsed.value == rid and parsed.anchor is not None
            assert parsed.anchor.kind == kind
            if kind != "record":
                assert parsed.anchor.start == start and parsed.anchor.end == (None if end == start else end)


def messages(n: int, parent: bool = True) -> list[Message]:
    out = [Message("chat:C/0", "2026-10-08T07:00:00.000Z", "parent text", "U0A")] if parent else []
    return out + [
        Message(f"chat:C/{i}", f"2026-10-08T07:{i:02d}:00.000Z", f"reply {i}", "U0B") for i in range(1, n + 1)
    ]


def test_thread_windows_hold_the_parent_and_up_to_ten_replies_sharing_one() -> None:
    chunks = thread_chunks("chat:C/0", "chat", messages(13))
    assert [len(c.rids) for c in chunks] == [11, 5]
    assert chunks[0].rids[0] == "chat:C/0" and chunks[1].rids[0] == "chat:C/0"
    assert chunks[0].rids[-1] == chunks[1].rids[1] == "chat:C/10"
    assert [c.locator for c in chunks] == ["r:chat:C/0", "r:chat:C/10"]
    assert chunks[1].ts == "2026-10-08T07:10:00.000Z"
    assert chunks[0].text.startswith("parent text\nreply 1")
    assert chunks[0].authors == ["U0A", "U0B"] and chunks[0].kind == "thread" and chunks[0].owner == "t chat:C/0"


def test_a_thread_without_its_parent_or_of_one_message() -> None:
    orphan = thread_chunks("chat:C/0", "chat", messages(3, parent=False))
    assert [c.rids for c in orphan] == [["chat:C/1", "chat:C/2", "chat:C/3"]]
    assert orphan[0].locator == "r:chat:C/1"
    assert thread_chunks("chat:C/0", "chat", messages(0)) == []


def doc_text(body: str, covers: list[str] | None = None, doc_id: str = "recording-note:2026-10-07-call") -> str:
    lines = "\n".join(f"  - {c}" for c in (covers if covers is not None else ["calls:rec-1"]))
    head = f"id: {doc_id}\ntype: {doc_id.partition(':')[0]}\nrev: 1\ncovers:\n{lines}\nby: test\n"
    return f"---\n{head}---\n\n{body}"


def info_from(table: dict[str, RecordInfo]) -> Callable[[str], RecordInfo | None]:
    return table.get


RECORDS = {
    "calls:rec-1": RecordInfo("2026-10-07T08:00:00.000Z", "calls:rec-1", "calls"),
    "chat:C/1": RecordInfo("2026-10-08T07:00:00.000Z", "chat:C/0", "chat"),
    "chat:C/2": RecordInfo("2026-10-08T07:05:00.000Z", "chat:C/0", "chat"),
}


def test_a_doc_splits_at_headings_with_anchors_cut_and_kept() -> None:
    body = (
        "# Supplier call\n\nIntro without anchors. @00:10\n\n## Price\n\n- Ten million for the quarter. @00:30-01:10\n"
        "- Offer by Friday. [r:calls:rec-1@04:00]\n\n```\n# not a heading\n```\n\n## Empty\n\n## Notes\n\n"
        "- A note. @05:00\n"
    )
    doc = parse_doc(doc_text(body), lenient=True)
    chunks, rows = doc_chunks(doc, info_from(RECORDS), TZ)
    assert doc_title(doc) == "Supplier call"
    assert [c.title for c in chunks] == ["Supplier call", "Price", "Notes"]
    price = chunks[1]
    assert (
        price.text
        == "Supplier call\nPrice\n- Ten million for the quarter.\n- Offer by Friday.\n```\n# not a heading\n```"
    )
    assert price.anchors == ["r:calls:rec-1@00:30-01:10", "r:calls:rec-1@04:00"]
    assert (price.line_from, price.line_to) == (16, 21)
    assert price.locator == price.doc == "recording-note:2026-10-07-call"
    assert price.grp == "calls:rec-1" and price.source == "calls" and price.ts == "2026-10-07T08:00:00.000Z"
    assert [(r.line, r.ordinal, r.rid, r.kind, r.start, r.end) for r in rows] == [
        (12, 0, "calls:rec-1", "time", 10, None),
        (16, 0, "calls:rec-1", "time", 30, 70),
        (17, 0, "calls:rec-1", "time", 240, None),
        (27, 0, "calls:rec-1", "time", 300, None),
    ]


def test_a_long_section_splits_between_lines() -> None:
    line = "- " + " ".join(["word"] * 200) + " @00:10"
    doc = parse_doc(doc_text("# T\n\n" + "\n".join([line, line, line]) + "\n"), lenient=True)
    chunks, _ = doc_chunks(doc, info_from(RECORDS), TZ)
    assert [(c.line_from, c.line_to) for c in chunks] == [(12, 13), (14, 14)]
    assert all(c.anchors == ["r:calls:rec-1@00:10"] for c in chunks)


def test_a_doc_s_group_source_and_time_follow_what_it_covers() -> None:
    thread = parse_doc(doc_text("# T\n\n- x [r:chat:C/2]\n- y [r:chat:C/1]\n", ["chat:C/1", "chat:C/2"]), lenient=True)
    chunks, _ = doc_chunks(thread, info_from(RECORDS), TZ)
    assert (chunks[0].grp, chunks[0].source, chunks[0].ts) == ("chat:C/0", "chat", "2026-10-08T07:00:00.000Z")
    mixed = parse_doc(
        doc_text("# W\n\n- a [r:calls:rec-1]\n", ["calls:rec-1", "chat:C/1"], "digest:2026-10-09-week"), lenient=True
    )
    chunks, _ = doc_chunks(mixed, info_from(RECORDS), TZ)
    assert (chunks[0].grp, chunks[0].source) == ("digest:2026-10-09-week", None)
    unknown = parse_doc(doc_text("# W\n\n- a [r:calls:gone]\n", ["calls:gone"], "digest:2026-10-09-gone"), lenient=True)
    chunks, _ = doc_chunks(unknown, info_from(RECORDS), TZ)
    assert (chunks[0].grp, chunks[0].ts) == ("digest:2026-10-09-gone", "2026-10-08T22:00:00.000Z")


def test_a_legacy_note_keeps_its_text_and_its_slug_s_date_or_none() -> None:
    legacy = "---\nid: legacy-note:old-notes\ntype: legacy-note\nby: import\n---\n\nKeep [r:not:an-anchor]\n"
    chunks, rows = doc_chunks(parse_doc(legacy, lenient=True), info_from(RECORDS), TZ)
    assert chunks[0].text == "old-notes\nKeep [r:not:an-anchor]" and rows == [] and chunks[0].ts is None
    bad_date = "---\nid: brief:2026-13-40-x\ntype: brief\nby: me\ncovers: []\n---\n\n# B\n\nText.\n"
    assert doc_chunks(parse_doc(bad_date, lenient=True), info_from(RECORDS), TZ)[0][0].ts is None
    short = "---\nid: digest:2026-10-09-two\ntype: digest\nby: me\ncovers:\n  - a:1\n  - b:2\n---\n\nText. @00:01\n"
    _, rows = doc_chunks(parse_doc(short, lenient=True), info_from(RECORDS), TZ)
    assert rows == []
