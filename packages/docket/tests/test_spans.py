from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docket_testkit import (
    MAIL,
    PLAUD,
    REC,
    SIDE,
    THREAD,
    VENDOR,
    copy_vault,
    digest,
    index_template,
    make_record,
    plaud_records,
    shared,
    write_raw,
)

from docket import index
from docket.index.lookup import ReadIndex, open_read
from docket.vault.anchors import Anchor, parse_anchor
from docket.vault.records import RawRecord, version
from docket.vault.spans import Place, RawLookup, problem, span
from docket.vault.vault import Vault


@pytest.fixture(scope="module")
def raw_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture's raw log without git, written once for the module: these tests only read it."""
    root = tmp_path_factory.mktemp("raw-only")
    write_raw(root)
    return root


@pytest.fixture
def lookup(raw_root: Path) -> RawLookup:
    return RawLookup(raw_root)


def record(lookup: RawLookup, rid: str) -> RawRecord:
    found = lookup.latest(rid)
    assert found is not None
    return found


def test_versions_come_in_run_order_and_unknown_rids_have_none(lookup: RawLookup) -> None:
    versions = lookup.versions(THREAD[2])
    assert [v.text for v in versions] == ["thread message 3", "thread message 3, edited"]
    assert lookup.versions("chat:nothing-here") == []
    assert lookup.versions("not a rid") == []
    assert lookup.versions("nosuch:source") == []
    assert lookup.latest("chat:nothing-here") is None


def test_only_the_rid_s_source_folder_is_read(tmp_path: Path) -> None:
    write_raw(tmp_path)
    broken = tmp_path / "raw" / "mail" / "2026" / "10" / "2026-10-14T1200-test-box.jsonl"
    broken.write_text("not json\n", encoding="utf-8")
    lookup = RawLookup(tmp_path)
    assert lookup.latest(THREAD[0]) is not None
    with pytest.raises(ValueError, match="Expecting value"):
        lookup.latest(MAIL)


def test_neighbours_are_the_thread_then_the_container(lookup: RawLookup) -> None:
    def rids(rid: str, n: int) -> list[str]:
        return [r.rid for r in lookup.neighbours(record(lookup, rid), n)]

    assert rids(THREAD[1], 1) == THREAD
    assert rids(THREAD[0], 1) == THREAD[:2]
    assert rids(THREAD[1], 0) == [THREAD[1]]
    assert rids(SIDE[0], 5) == SIDE
    assert rids(REC, 3) == [REC]
    assert [r.text for r in lookup.neighbours(record(lookup, THREAD[1]), 1)][2] == "thread message 3, edited"


@pytest.mark.parametrize(
    ("rid", "token", "says"),
    [
        (REC, "@12:08", None),
        (REC, "@0:15", None),  # in the gap between utterances
        (REC, "@1:02:10", None),  # the greatest end, rounded up
        (REC, "@1:02:11", "1:02:11 is outside calls:rec-0042, which runs from 00:00 to 1:02:10"),
        (REC, "@14:30-14:52", None),
        (REC, "@1:02:00-1:03:00", "1:03:00 is outside calls:rec-0042, which runs from 00:00 to 1:02:10"),
        (VENDOR, "@0:00", "00:00 is outside vendor:rec-0007, which runs from 00:01 to 00:05"),
        (VENDOR, "@0:04", None),
        (MAIL, "#2", None),
        (MAIL, "#2-3", None),
        (MAIL, "#4", f"{MAIL} has 3 paragraph(s), so #4 isn't one"),
        (
            MAIL,
            "@0:01",
            f"{MAIL} has no transcript segments; anchor the whole record: [r:mail:<50%25off%5Dx@example.invalid>]",
        ),
        (THREAD[0], "", None),
    ],
)
def test_whether_an_anchor_resolves(lookup: RawLookup, rid: str, token: str, says: str | None) -> None:
    if token.startswith("#"):
        first, _, last = token[1:].partition("-")
        anchor = Anchor(rid, "part", int(first), int(last) if last else None)
    else:
        anchor = parse_anchor(token) if token else Anchor(rid, "record")
    assert problem(anchor, record(lookup, rid)) == says


def test_segments_in_native_are_never_read() -> None:
    native_only = make_record(
        rid="calls:rec-raw", source="calls", native={"segments": [{"start_ms": 0, "end_ms": 5000, "text": "hi"}]}
    )
    assert "has no transcript segments" in (problem(Anchor(None, "time", 1), native_only) or "")
    whole = span(Anchor("calls:rec-raw", "record"), native_only, 1)
    assert (whole.problem, whole.segments, whole.text) == (None, [], "message 1")


def test_a_point_shows_the_next_window_and_around_widens_it(lookup: RawLookup) -> None:
    call = record(lookup, REC)
    point = span(parse_anchor("@12:08"), call, 1)
    assert [s.text for s in point.segments] == ["So ten million, for the whole quarter."]
    assert (point.start_ms, point.end_ms, point.anchor, point.kind, point.title) == (
        728_000,
        788_000,
        "@12:08",
        "recording",
        "Supplier call",
    )
    wider = span(parse_anchor("@12:08"), call, 1, around=2)
    assert [s.speaker for s in wider.segments] == ["Ben Sample", "Cleo Test"]
    gap = span(parse_anchor("@0:15"), call, 1, window=10)
    assert [s.text for s in gap.segments] == ["The consultant is 10 million forints."]
    start = span(parse_anchor("@0:04"), call, 1, window=1)
    assert [s.speaker for s in start.segments] == ["Cleo Test"]


def test_a_stretch_shows_what_overlaps_it(lookup: RawLookup) -> None:
    got = span(parse_anchor("[r:calls:rec-0042@14:30-14:52]"), record(lookup, REC), 1, written="as written")
    assert [s.text for s in got.segments] == ["Please send the offer in writing by Friday."]
    assert (got.start_ms, got.end_ms, got.anchor) == (870_000, 893_000, "as written")


def test_paragraphs_and_whole_records_show_their_text(lookup: RawLookup) -> None:
    mail = record(lookup, MAIL)
    two = span(Anchor(MAIL, "part", 2), mail, 1)
    assert (two.paragraphs, two.text) == ((2, 2), "The offer stands at 10 million.\nUntil Friday.")
    assert span(Anchor(MAIL, "part", 2), mail, 1, around=5).paragraphs == (1, 3)
    whole = span(Anchor(MAIL, "record"), mail, 1)
    assert "\r" not in whole.text
    assert whole.anchor == "[r:mail:<50%25off%5Dx@example.invalid>]"
    edited = span(Anchor(THREAD[2], "record"), record(lookup, THREAD[2]), 2)
    assert (edited.versions, edited.text) == (2, "thread message 3, edited")
    call = span(Anchor(REC, "record"), record(lookup, REC), 1)
    assert len(call.segments) == 7


def test_an_anchor_that_doesnt_resolve_comes_back_with_its_problem(lookup: RawLookup) -> None:
    got = span(parse_anchor("@2:00:00"), record(lookup, REC), 1)
    assert got.problem is not None
    assert got.segments == []


# -- by place (DR-0446, DR-0447) --------------------------------------------------------------------------------------

ORDERED = "chat:C0ORD/1791600000.000100"
TWICE = "chat:C0ORD/1791600001.000100"
# Four versions of one message in one minute: by RunFile.order desk, desk.2, desk.10, desk-2; by the path's text the
# other way round, since - sorts before . and 1 before 2 before j.
ORDER_RUNS = [
    "2026-10-14T1000-desk.jsonl",
    "2026-10-14T1000-desk.2.jsonl",
    "2026-10-14T1000-desk.10.jsonl",
    "2026-10-14T1000-desk-2.jsonl",
]
WINDOW_THREAD = "chat:C0WIN/1791500000.000100"
WINDOW = [f"chat:C0WIN/{1791500000 + n}.000100" for n in range(1, 7)]


def window_records() -> list[RawRecord]:
    """A thread of six where messages 3 and 4 share a millisecond and, by microsecond, 4 comes first: the index orders
    them 3, 4 and the exact sort 4, 3."""
    base = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    offsets = [
        timedelta(seconds=1),
        timedelta(seconds=2),
        timedelta(seconds=3, microseconds=500_900),
        timedelta(seconds=3, microseconds=500_100),
        timedelta(seconds=4),
        timedelta(seconds=5),
    ]
    return [
        make_record(rid=rid, container="chat:C0WIN", thread=WINDOW_THREAD, ts=base + at, text=f"window {n}")
        for n, (rid, at) in enumerate(zip(WINDOW, offsets, strict=True), start=1)
    ]


def put_run(root: Path, source: str, name: str, records: list[RawRecord], newline: bytes = b"\n") -> Path:
    path = root / "raw" / source / "2026" / "10" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    stem = name.removesuffix(".jsonl")
    lines = [r.model_copy(update={"v": version(r), "run": stem}).to_json().encode("utf-8") for r in records]
    path.write_bytes(b"".join(line + newline for line in lines))
    return path


def add_places_runs(vault: Vault) -> None:
    """A recording kept as Plaud keeps one, a message in four runs whose order the path's text reverses, one version in
    two machines' runs, and the thread with two messages in one millisecond, committed."""
    put_run(vault.root, "plaud", "2026-10-14T0900-test-box.jsonl", plaud_records("transaction", "polish"))
    for n, name in enumerate(ORDER_RUNS, start=1):
        same = [make_record(rid=TWICE, container="chat:C0ORD", text="the same everywhere")] if n in (1, 4) else []
        put_run(vault.root, "chat", name, [make_record(rid=ORDERED, container="chat:C0ORD", text=f"v{n}"), *same])
    put_run(vault.root, "chat", "2026-10-14T1100-test-box.jsonl", window_records())
    vault.commit([], "Raw: by place")


@pytest.fixture(scope="module")
def places_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The index fixture's template, copied, with the runs above: built once per session."""
    base = index_template(tmp_path_factory)

    def build(folder: Path) -> Path:
        vault = copy_vault(base, folder)
        add_places_runs(vault)
        return vault.root

    return shared(build, "places-" + digest(str(base)), tmp_path_factory)


@pytest.fixture
def indexed(places_template: Path, tmp_path: Path) -> Vault:
    vault = copy_vault(places_template, tmp_path)
    index.update(vault)
    return vault


def two_routes(vault: Vault) -> tuple[RawLookup, RawLookup, ReadIndex]:
    """A lookup by place and one that loads, on the same vault."""
    found = open_read(vault)
    assert found is not None
    return RawLookup(vault.root, lambda: found), RawLookup(vault.root), found


def loaded(lookup: RawLookup) -> set[str]:
    return set(lookup._sources)  # pyright: ignore[reportPrivateUsage]


def answers(lookup: RawLookup, rid: str) -> list[object]:
    """Everything a read asks the raw about a rid, as data."""
    versions = lookup.versions(rid)
    latest = versions[-1] if versions else None
    out: list[object] = [
        [v.model_dump() for v in versions],
        lookup.has(rid),
        [r.rid for r in lookup.under(rid)],
        [r.rid for r in lookup.transcripts(rid)],
        lookup.timed([rid]),
        lookup.nested(rid),
    ]
    if latest is not None:
        out.append(lookup.recordings(latest))
        out += [[r.rid for r in lookup.neighbours(latest, n)] for n in range(4)]
    return out


def every_rid(vault: Vault) -> list[str]:
    return sorted({r.rid for _, _, r in vault.rawlog.iter_records()})


def test_by_place_every_question_has_the_loaded_answer_and_nothing_is_loaded(indexed: Vault) -> None:
    placed, whole, found = two_routes(indexed)
    try:
        rids = [*every_rid(indexed), "chat:nothing-here", "nosuch:source", "not a rid", f"{PLAUD}/missing"]
        for rid in rids:
            assert answers(placed, rid) == answers(whole, rid), rid
        assert loaded(placed) == set()
        assert loaded(whole) >= {"cal", "calls", "chat", "mail", "plaud"}
    finally:
        found.close()


def test_nested_rids_are_the_rid_and_those_under_it_exactly(indexed: Vault) -> None:
    near = ["plaud:near", "plaud:near/one", "plaud:near/one/two", "plaud:near0", "plaud:near-x", "plaud:NEAR/x"]
    records = [make_record(n, rid=rid, source="plaud", container=None, text=rid) for n, rid in enumerate(near)]
    put_run(indexed.root, "plaud", "2026-10-15T0900-test-box.jsonl", records)
    indexed.commit([], "Raw: near")
    index.update(indexed)
    placed, whole, found = two_routes(indexed)
    try:
        for lookup in (placed, whole):
            assert lookup.nested("plaud:near") == ["plaud:near", "plaud:near/one", "plaud:near/one/two"]
            assert lookup.nested("plaud:near/one") == ["plaud:near/one", "plaud:near/one/two"]
            assert lookup.nested("plaud:nearly") == []
        assert loaded(placed) == set()
    finally:
        found.close()


def test_versions_follow_run_order_never_the_path_s_text(indexed: Vault) -> None:
    placed, whole, found = two_routes(indexed)
    try:
        for lookup in (placed, whole):
            assert [v.text for v in lookup.versions(ORDERED)] == ["v1", "v2", "v3", "v4"]
            assert [v.run for v in lookup.versions(TWICE)] == ["2026-10-14T1000-desk", "2026-10-14T1000-desk-2"]
        assert loaded(placed) == set()
    finally:
        found.close()


@pytest.mark.parametrize("n", range(1, len(WINDOW) + 1))
def test_a_window_takes_whole_milliseconds_and_the_exact_order(indexed: Vault, n: int) -> None:
    placed, whole, found = two_routes(indexed)
    try:
        for rid in WINDOW:
            message = placed.latest(rid)
            assert message is not None
            got = [r.rid for r in placed.neighbours(message, n)]
            assert got == [r.rid for r in whole.neighbours(message, n)], (rid, n)
        assert loaded(placed) == set()
    finally:
        found.close()
    exact = [WINDOW[0], WINDOW[1], WINDOW[3], WINDOW[2], WINDOW[4], WINDOW[5]]
    lookup = RawLookup(indexed.root)
    for i, rid in enumerate(exact):
        message = lookup.latest(rid)
        assert message is not None
        assert [r.rid for r in lookup.neighbours(message, n)] == exact[max(0, i - n) : i + n + 1]


def test_a_line_that_moved_sends_its_source_to_the_load_with_the_same_answer(indexed: Vault) -> None:
    path = indexed.root / "raw" / "chat" / "2026" / "10" / "2026-10-14T1100-test-box.jsonl"
    stat = path.stat()
    # Another rid of the same length at the same place, and the file's size and time as the index read them.
    path.write_bytes(
        path.read_bytes().replace(WINDOW[0].encode(), WINDOW[0].replace("1791500001", "1791500009").encode())
    )
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    placed, whole, found = two_routes(indexed)
    try:
        assert placed.versions(WINDOW[0]) == whole.versions(WINDOW[0]) == []
        assert loaded(placed) == {"chat"}
        assert placed.versions(PLAUD) == whole.versions(PLAUD)
        assert "plaud" not in loaded(placed)
    finally:
        found.close()


def test_a_run_gone_after_the_check_sends_its_source_to_the_load(indexed: Vault) -> None:
    placed, whole, found = two_routes(indexed)
    try:
        assert placed.has(ORDERED)
        (indexed.root / "raw" / "chat" / "2026" / "10" / ORDER_RUNS[3]).unlink()
        assert [v.text for v in placed.versions(ORDERED)] == [v.text for v in whole.versions(ORDERED)]
        assert [v.text for v in placed.versions(ORDERED)] == ["v1", "v2", "v3"]
        assert loaded(placed) == {"chat"}
    finally:
        found.close()


def test_a_place_on_a_line_the_load_refuses_never_answers(indexed: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    """Decoded as strict UTF-8 and parsed as text: json.loads on the bytes would drop a BOM and pass a surrogate."""
    record = make_record(rid="chat:C0BAD/1791700000.000100", container="chat:C0BAD", text="X")
    line = record.model_copy(update={"v": version(record)}).to_json().encode("utf-8")
    cases = {
        "2026-10-14T1200-test-box.jsonl": b"\xef\xbb\xbf" + line + b"\n",
        "2026-10-14T1300-test-box.jsonl": line.replace(b'"X"', b'"\xed\xa0\x80"') + b"\n",
    }
    for name, data in cases.items():
        path = indexed.root / "raw" / "chat" / "2026" / "10" / name
        path.write_bytes(data)
        place = Place(f"raw/chat/2026/10/{name}", 1, 0, version(record))
        found = open_read(indexed)
        assert found is not None
        try:
            # As if the index held that line, so the place is read: it must fail as the load does.
            monkeypatch.setattr(found, "raw_current", lambda source: True)
            monkeypatch.setattr(found, "places", lambda rid, source, place=place: [place])
            placed, whole = RawLookup(indexed.root, lambda found=found: found), RawLookup(indexed.root)
            with pytest.raises(ValueError) as by_place:
                placed.versions(record.rid)
            with pytest.raises(ValueError) as by_load:
                whole.versions(record.rid)
            assert type(by_place.value) is type(by_load.value)
        finally:
            found.close()
        path.unlink()


def test_a_crlf_run_reads_the_same_by_place(indexed: Vault) -> None:
    crlf = [
        make_record(rid=f"chat:C0CRLF/{1791800000 + n}.000100", container="chat:C0CRLF", text=f"crlf {n}")
        for n in (1, 2)
    ]
    put_run(indexed.root, "chat", "2026-10-14T1400-test-box.jsonl", crlf, newline=b"\r\n")
    index.update(indexed)
    placed, whole, found = two_routes(indexed)
    try:
        for record in crlf:
            assert answers(placed, record.rid) == answers(whole, record.rid)
        assert loaded(placed) == set()
    finally:
        found.close()


def test_rows_that_disagree_with_the_runs_send_the_source_to_the_load(
    indexed: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    placed, whole, found = two_routes(indexed)
    try:
        monkeypatch.setattr(found, "places", lambda rid, source: [])
        assert [r.rid for r in placed.transcripts(PLAUD)] == [r.rid for r in whole.transcripts(PLAUD)]
        assert loaded(placed) == {"plaud"}
        monkeypatch.setattr(found, "group", lambda field, value, source: [])
        message = whole.latest(WINDOW[2])
        assert message is not None
        assert placed.neighbours(message, 1) == whole.neighbours(message, 1)
        assert loaded(placed) == {"plaud", "chat"}
    finally:
        found.close()


def test_without_an_index_a_lookup_loads(indexed: Vault) -> None:
    lookup = RawLookup(indexed.root, lambda: None)
    assert lookup.has(PLAUD)
    assert loaded(lookup) == {"plaud"}
