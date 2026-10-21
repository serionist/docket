from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import (
    MAIL,
    READ_ROUTES,
    REC,
    THREAD,
    at,
    copy_vault,
    indexed,
    make_record,
    note,
    read_only,
    read_route,
    shared,
    template_vault,
)

from docket import recall
from docket.errors import DocketError
from docket.vault.anchors import format_anchor
from docket.vault.docstore import DocStore
from docket.vault.processed import doc_path
from docket.vault.vault import Vault

NOTE_ID = "recording-note:2026-10-14-supplier-call"
NOTE_PATH = "processed/recording-note/2026/10/2026-10-14-supplier-call.md"
LINES = ["- Ben put the consultant at 10 million forints. @12:08", "- Cleo wants the offer in writing. @14:30-14:52"]


def with_the_note(fixture: Path, base: Path) -> Path:
    v = copy_vault(fixture, base)
    recall.doc_put(v, note(lines=LINES))
    return v.root


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture vault with the note put: built once, copied by each test."""
    fixture = template_vault(tmp_path_factory)
    return shared(lambda base: with_the_note(fixture, base), "recall-note", tmp_path_factory)


@pytest.fixture(params=READ_ROUTES)
def vault(template: Path, tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Vault:
    v = copy_vault(template, tmp_path)
    read_route(request.param, monkeypatch)
    return v


@pytest.fixture(scope="module")
def parsed(template: Path) -> Path:
    """One copy for the reads on the parse route, which finds no index: a write into it fails the run (DR-0531)."""
    return read_only(template)


@pytest.fixture(scope="module")
def through_index(tmp_path_factory: pytest.TempPathFactory, template: Path) -> Path:
    """One copy, its index built, for the reads on the index route: a write into it fails the run (DR-0531)."""
    return read_only(indexed(tmp_path_factory, template))


@pytest.fixture(params=READ_ROUTES)
def seen(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Vault:
    """The vault for a test that only reads, on each route."""
    root = request.getfixturevalue("parsed" if request.param == "parse" else "through_index")
    read_route(request.param, monkeypatch)
    return Vault.open(root)


def on_disk(vault: Vault, doc_id: str, text: str) -> None:
    """A doc written straight to disk, as a hand edit or an older Docket might leave it."""
    path = vault.root / doc_path(doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# -- read: docs -----------------------------------------------------------------------------------------------------


def test_a_doc_s_summary_is_the_doc_with_numbered_lines(seen: Vault) -> None:
    got = recall.read(seen, NOTE_ID)
    assert got.doc is not None
    assert (got.doc.id, got.doc.rev, got.doc.path, got.doc.covers, got.spans) == (NOTE_ID, 1, NOTE_PATH, [REC], [])
    assert got.doc.lines[0].text == "---"
    assert got.doc.lines[13].n == 14
    assert got.doc.lines[13].anchors == ["@12:08"]
    assert got.doc.text == (seen.root / NOTE_PATH).read_text(encoding="utf-8")
    assert got.doc.superseded_by == []


def test_a_doc_s_raw_is_its_records_and_both_is_both(seen: Vault) -> None:
    raw = recall.read(seen, NOTE_ID, view="raw")
    assert raw.doc is None
    assert [len(s.segments) for s in raw.spans] == [7]
    both = recall.read(seen, NOTE_ID, view="both")
    assert both.doc is not None
    assert len(both.spans) == 1


def test_a_covered_record_missing_from_the_raw_log_is_a_problem_not_an_error(vault: Vault) -> None:
    on_disk(vault, "brief:gone", note("brief:gone", covers=["calls:gone"], lines=["- gone @00:01"]))
    span = recall.read(vault, "brief:gone", view="raw").spans[0]
    assert span.problem == "calls:gone isn't in the raw log"


def test_a_doc_by_its_path_inside_the_vault_only(vault: Vault) -> None:
    assert recall.read(vault, NOTE_PATH).doc is not None
    absolute = recall.read(vault, str(vault.root / NOTE_PATH))
    assert absolute.doc is not None
    assert absolute.doc.id == NOTE_ID
    # A path outside processed/ is looked up as a legacy note's old path, a string match that never reads the file.
    private = "---\nid: brief:someone\ntype: brief\ncovers: [calls:a]\nby: me\n---\nprivate text @00:01\n"
    (vault.root / "raw" / "notes.md").write_text(private, encoding="utf-8")
    (vault.root.parent / "elsewhere.md").write_text(private, encoding="utf-8")
    for outside in ("raw/notes.md", str(vault.root.parent / "elsewhere.md"), "processed/brief/nobody.md"):
        with pytest.raises(DocketError, match="no legacy note has the old path"):
            recall.read(vault, outside)
    with pytest.raises(DocketError, match=r"has a \.\. part"):
        recall.read(vault, "processed/../raw/notes.md")


def test_an_earlier_rev_by_id_or_path(vault: Vault) -> None:
    recall.doc_put(vault, note(rev="1", lines=["- Corrected: for the quarter. @12:08"]))
    old = recall.read(vault, NOTE_ID, rev=1).doc
    assert old is not None
    assert "10 million forints" in old.text
    by_path = recall.read(vault, NOTE_PATH, rev=2).doc
    assert by_path is not None
    assert "Corrected" in by_path.text


def test_a_doc_that_isnt_there(vault: Vault) -> None:
    with pytest.raises(DocketError, match="no doc brief:nobody"):
        recall.read(vault, "brief:nobody")
    (vault.root / NOTE_PATH).unlink()
    with pytest.raises(DocketError, match="was deleted; its revs are in history"):
        recall.read(vault, NOTE_ID)
    assert recall.read(vault, NOTE_ID, rev=1).doc is not None


def test_a_superseded_doc_names_its_successor_and_drops_out_of_a_record_s_summary(vault: Vault) -> None:
    minutes = note("minutes:2026-10-14-supplier-call", supersedes=NOTE_ID, lines=["- Agreed: 10 million. @12:08"])
    recall.doc_put(vault, minutes)
    got = recall.read(vault, NOTE_ID).doc
    assert got is not None
    assert got.superseded_by == ["minutes:2026-10-14-supplier-call"]
    lines = recall.read(vault, "r:calls:rec-0042@12:08").lines
    assert [(c.doc, c.n) for c in lines] == [("minutes:2026-10-14-supplier-call", 15)]


# -- read: records --------------------------------------------------------------------------------------------------


def test_a_record_s_summary_is_the_doc_lines_near_the_position(seen: Vault) -> None:
    near = recall.read(seen, "r:calls:rec-0042@12:08").lines
    assert [(c.doc, c.rev, c.n, c.anchors) for c in near] == [(NOTE_ID, 1, 14, ["@12:08"])]
    wider = recall.read(seen, "[r:calls:rec-0042@12:08]", around=2).lines
    assert [c.n for c in wider] == [14, 15]
    stretch = recall.read(seen, "r:calls:rec-0042@14:40-15:00").lines
    assert [c.n for c in stretch] == [15]
    assert [c.n for c in recall.read(seen, REC).lines] == [14, 15]
    assert recall.read(seen, "r:calls:rec-0042@30:00").lines == []


def test_a_record_s_summary_across_records_and_parts(vault: Vault) -> None:
    whole = format_anchor(THREAD[0])
    part = format_anchor(MAIL, "part", 2)
    lines = [
        f"- Ben opened it. {whole}",
        f"- The offer stands. {part}",
        "- A time on the call. [r:calls:rec-0042@12:08]",
    ]
    recall.doc_put(vault, note("thread-summary:2026-10-14-offer", covers=[THREAD[0], MAIL, REC], lines=lines))
    legacy = "---\nid: legacy-note:old\ntype: legacy-note\ncovers: [calls:rec-0042]\nby: import\n---\nOld @12:08\n"
    recall.doc_put(vault, legacy)
    assert [c.n for c in recall.read(vault, f"r:{part[3:-1]}").lines] == [17]
    assert [c.n for c in recall.read(vault, format_anchor(MAIL, "part", 3)).lines] == []
    assert [c.n for c in recall.read(vault, format_anchor(MAIL, "part", 3), around=1).lines] == [17]
    assert [c.n for c in recall.read(vault, format_anchor(MAIL, "time", 1)).lines] == []
    assert [c.doc for c in recall.read(vault, THREAD[0]).lines] == ["thread-summary:2026-10-14-offer"]
    on_call = recall.read(vault, "r:calls:rec-0042@12:08").lines
    assert sorted(c.doc for c in on_call) == [NOTE_ID, "thread-summary:2026-10-14-offer"]


def test_a_record_s_raw_is_its_span(seen: Vault) -> None:
    point = recall.read(seen, "r:calls:rec-0042@12:08", view="raw")
    assert point.lines == []
    assert [s.text for s in point.spans[0].segments] == ["So ten million, for the whole quarter."]
    wider = recall.read(seen, "r:calls:rec-0042@12:08", view="both", around=2)
    assert len(wider.spans[0].segments) == 2
    assert len(wider.lines) == 2
    paragraph = recall.read(seen, format_anchor(MAIL, "part", 2), view="raw", around=1).spans[0]
    assert paragraph.paragraphs == (1, 3)
    assert len(recall.read(seen, REC, view="raw", around=3).spans) == 1


def test_around_counts_messages_in_a_thread(seen: Vault) -> None:
    spans = recall.read(seen, THREAD[1], view="raw", around=1).spans
    assert [s.rid for s in spans] == THREAD
    assert spans[2].versions == 2
    assert [s.rid for s in recall.read(seen, THREAD[1], view="raw").spans] == [THREAD[1]]


@pytest.mark.parametrize(
    ("locator", "kwargs", "says"),
    [
        ("r:calls:rec-0042@2:00:00", {"view": "raw"}, "2:00:00 is outside calls:rec-0042"),
        ("calls:nothing", {}, "no record calls:nothing in the raw log$"),
        ("calls:rec-0042@12:08", {}, "for a position, write r:<rid>@mm:ss"),
        (REC, {"rev": 1}, "rev is a doc's revision"),
        (REC, {"view": "everything"}, "view is one of summary, raw, both"),
        (REC, {"around": -1}, "can't be negative"),
        ("nonsense", {}, "neither a doc id, a doc's path, nor a rid"),
    ],
)
def test_a_read_that_can_t_be_answered_says_why(
    seen: Vault, locator: str, kwargs: dict[str, object], says: str
) -> None:
    with pytest.raises(DocketError, match=says):
        recall.read(seen, locator, **kwargs)  # type: ignore[arg-type]


# -- several in one read (DR-0725) ----------------------------------------------------------------------------------


def test_read_many_reads_each_as_read_does_in_order_a_locator_given_twice_once(seen: Vault) -> None:
    point = "r:calls:rec-0042@12:08"
    got = recall.read_many(seen, NOTE_ID, [point, NOTE_PATH, point, NOTE_ID], view="both")
    assert [one.locator for one in (got, *got.more)] == [NOTE_ID, point, NOTE_PATH]
    assert got.later == []
    alone = [recall.read(seen, locator, view="both") for locator in (NOTE_ID, point, NOTE_PATH)]
    assert got.model_dump(exclude={"more"}) == alone[0].model_dump(exclude={"more"})
    assert [one.model_dump() for one in got.more] == [one.model_dump(exclude={"more", "later"}) for one in alone[1:]]


def test_read_many_gives_a_locator_that_doesn_t_open_a_problem_of_its_own(seen: Vault) -> None:
    got = recall.read_many(seen, "calls:nothing", ["nonsense", "r:calls:rec-0042@2:00:00", REC], view="raw")
    assert (got.locator, got.doc, got.spans, got.problems) == (
        "calls:nothing", None, [], ["no record calls:nothing in the raw log"],
    )  # fmt: skip
    nonsense, outside, whole = got.more
    assert "neither a doc id, a doc's path, nor a rid" in nonsense.problems[0]
    assert outside.problems == ["2:00:00 is outside calls:rec-0042, which runs from 00:00 to 1:02:10"]
    assert (whole.problems, [s.rid for s in whole.spans]) == ([], [REC])


def test_read_many_keeps_each_further_read_only_while_the_result_fits(seen: Vault) -> None:
    """The first read always; then each while `fits` takes the result with it, the rest in later, unread and in
    order, the first refused among them."""
    asked = [REC, "r:calls:rec-0042@12:08", "calls:nothing", "nonsense"]
    offered: list[list[str]] = []

    def two_more(result: recall.ReadResult) -> bool:
        offered.append(result.later)
        return len(result.more) <= 2

    got = recall.read_many(seen, NOTE_ID, asked, fits=two_more)
    assert ([one.locator for one in got.more], got.later) == (asked[:2], asked[2:])
    assert offered == [asked[1:], asked[2:], asked[3:]]
    none = recall.read_many(seen, NOTE_ID, asked, fits=lambda _: False)
    assert (none.locator, none.doc is not None, none.more, none.later) == (NOTE_ID, True, [], asked)


@pytest.mark.parametrize(("kwargs", "says"), [({"view": "all"}, "view is one of"), ({"around": -2}, "negative")])
def test_read_many_refuses_what_read_refuses_of_the_call(seen: Vault, kwargs: dict[str, object], says: str) -> None:
    with pytest.raises(DocketError, match=says):
        recall.read_many(seen, NOTE_ID, [REC], **kwargs)  # type: ignore[arg-type]


# -- verify ---------------------------------------------------------------------------------------------------------


def test_verify_puts_each_line_next_to_its_raw(seen: Vault) -> None:
    got = recall.verify(seen, NOTE_ID)
    assert (got.doc, got.rev) == (NOTE_ID, 1)
    assert [(line.n, line.anchors, line.problems) for line in got.lines] == [
        (14, ["@12:08"], []),
        (15, ["@14:30-14:52"], []),
    ]
    assert [s.text for s in got.lines[0].spans[0].segments] == ["So ten million, for the whole quarter."]
    assert [s.speaker for s in got.lines[1].spans[0].segments] == ["Cleo Test"]
    short = recall.verify(seen, NOTE_PATH, window=1)
    assert short.lines[0].spans[0].end_ms == 729_000


def test_verify_chosen_lines(seen: Vault) -> None:
    got = recall.verify(seen, NOTE_ID, lines=[15, 11, 1, 99])
    assert [(line.n, line.anchors, line.problems) for line in got.lines] == [
        (11, [], []),
        (15, ["@14:30-14:52"], []),
        (1, [], ["line 1 isn't in the doc's body"]),
        (99, [], ["line 99 isn't in the doc's body"]),
    ]


def test_verify_reports_what_no_longer_resolves(vault: Vault) -> None:
    lines = [
        "- a record that is gone [r:calls:gone@1:00]",
        "- a short anchor in a doc of two records @00:01",
        "- no anchor at all",
        "- an anchor not in covers [r:calls:rec-0042@12:08]",
        "- a broken one @1:60",
    ]
    on_disk(vault, "digest:2026-10-14", note("digest:2026-10-14", covers=["calls:gone", MAIL], lines=lines))
    got = {line.n: line for line in recall.verify(vault, "digest:2026-10-14").lines}
    assert got[12].spans[0].problem == "calls:gone isn't in the raw log"
    assert got[13].spans[0].problem == "a short anchor needs a doc covering exactly one record"
    assert got[14].problems == ["no anchors at the end of the line"]
    assert got[15].problems == ["calls:rec-0042 isn't in covers"]
    assert got[15].spans[0].problem is None
    assert got[16].problems[0].startswith("@1:60:")


def test_verify_a_legacy_note_has_nothing_to_check(vault: Vault) -> None:
    recall.doc_put(vault, "---\nid: legacy-note:old\ntype: legacy-note\nby: import\n---\nOld text @12:08\n")
    assert recall.verify(vault, "legacy-note:old").lines == []
    assert [line.text for line in recall.verify(vault, "legacy-note:old", lines=[10]).lines] == ["Old text @12:08"]


@pytest.mark.parametrize(("doc", "says"), [(REC, "is a record; verify takes a doc"), ("nonsense", "neither")])
def test_verify_takes_a_doc(seen: Vault, doc: str, says: str) -> None:
    with pytest.raises(DocketError, match=says):
        recall.verify(seen, doc)


@pytest.mark.parametrize("doc_id", ["brief:../../../elsewhere", "brief:..\\..\\..\\elsewhere"])
def test_an_id_that_walks_out_of_processed_is_refused(vault: Vault, doc_id: str) -> None:
    outside = vault.root.parent / "elsewhere.md"
    outside.write_text("---\nid: brief:someone\ntype: brief\nby: me\n---\nprivate text\n", encoding="utf-8")
    with pytest.raises(DocketError, match="isn't a doc id"):
        recall.read(vault, doc_id)
    with pytest.raises(DocketError, match="isn't a doc id"):
        recall.verify(vault, doc_id)
    with pytest.raises(DocketError, match="isn't a doc id"):
        DocStore(vault).history(doc_id)


def test_around_on_a_recording_without_segments_in_its_record_counts_messages(vault: Vault) -> None:
    native_only = make_record(rid="calls:rec-raw", source="calls", kind="recording", native={"segments": [{"x": 1}]})
    vault.write_run("calls", [native_only], run_at=at(12))
    spans = recall.read(vault, "calls:rec-raw", view="raw", around=1).spans
    assert [(s.rid, s.segments) for s in spans] == [("calls:rec-raw", [])]
