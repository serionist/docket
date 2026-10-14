from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import MAIL, REC, THREAD, at, copy_vault, make_record, note, template_vault

from docket import recall
from docket.errors import DocketError
from docket.vault.anchors import format_anchor
from docket.vault.docstore import DocStore
from docket.vault.processed import doc_path
from docket.vault.vault import Vault

NOTE_ID = "recording-note:2026-10-14-supplier-call"
NOTE_PATH = "processed/recording-note/2026/10/2026-10-14-supplier-call.md"
LINES = ["- Ben put the consultant at 10 million forints. @12:08", "- Cleo wants the offer in writing. @14:30-14:52"]


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_vault(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    v = copy_vault(template, tmp_path)
    recall.doc_put(v, note(lines=LINES))
    return v


def on_disk(vault: Vault, doc_id: str, text: str) -> None:
    """A doc written straight to disk, as a hand edit or an older Docket might leave it."""
    path = vault.root / doc_path(doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# -- read: docs -----------------------------------------------------------------------------------------------------


def test_a_doc_s_summary_is_the_doc_with_numbered_lines(vault: Vault) -> None:
    got = recall.read(vault, NOTE_ID)
    assert got.doc is not None
    assert (got.doc.id, got.doc.rev, got.doc.path, got.doc.covers, got.spans) == (NOTE_ID, 1, NOTE_PATH, [REC], [])
    assert got.doc.lines[0].text == "---"
    assert got.doc.lines[13].n == 14
    assert got.doc.lines[13].anchors == ["@12:08"]
    assert got.doc.text == (vault.root / NOTE_PATH).read_text(encoding="utf-8")
    assert got.doc.superseded_by == []


def test_a_doc_s_raw_is_its_records_and_both_is_both(vault: Vault) -> None:
    raw = recall.read(vault, NOTE_ID, view="raw")
    assert raw.doc is None
    assert [len(s.segments) for s in raw.spans] == [7]
    both = recall.read(vault, NOTE_ID, view="both")
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
    (vault.root / "raw" / "notes.md").write_text("---\n", encoding="utf-8")
    for outside in ("raw/notes.md", "processed/../raw/notes.md", str(vault.root.parent / "elsewhere.md")):
        with pytest.raises(DocketError, match="isn't a doc in this vault's processed/ folder"):
            recall.read(vault, outside)
    with pytest.raises(DocketError, match="no doc at"):
        recall.read(vault, "processed/brief/nobody.md")


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


def test_a_record_s_summary_is_the_doc_lines_near_the_position(vault: Vault) -> None:
    near = recall.read(vault, "r:calls:rec-0042@12:08").lines
    assert [(c.doc, c.rev, c.n, c.anchors) for c in near] == [(NOTE_ID, 1, 14, ["@12:08"])]
    wider = recall.read(vault, "[r:calls:rec-0042@12:08]", around=2).lines
    assert [c.n for c in wider] == [14, 15]
    stretch = recall.read(vault, "r:calls:rec-0042@14:40-15:00").lines
    assert [c.n for c in stretch] == [15]
    assert [c.n for c in recall.read(vault, REC).lines] == [14, 15]
    assert recall.read(vault, "r:calls:rec-0042@30:00").lines == []


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


def test_a_record_s_raw_is_its_span(vault: Vault) -> None:
    point = recall.read(vault, "r:calls:rec-0042@12:08", view="raw")
    assert point.lines == []
    assert [s.text for s in point.spans[0].segments] == ["So ten million, for the whole quarter."]
    wider = recall.read(vault, "r:calls:rec-0042@12:08", view="both", around=2)
    assert len(wider.spans[0].segments) == 2
    assert len(wider.lines) == 2
    paragraph = recall.read(vault, format_anchor(MAIL, "part", 2), view="raw", around=1).spans[0]
    assert paragraph.paragraphs == (1, 3)
    assert len(recall.read(vault, REC, view="raw", around=3).spans) == 1


def test_around_counts_messages_in_a_thread(vault: Vault) -> None:
    spans = recall.read(vault, THREAD[1], view="raw", around=1).spans
    assert [s.rid for s in spans] == THREAD
    assert spans[2].versions == 2
    assert [s.rid for s in recall.read(vault, THREAD[1], view="raw").spans] == [THREAD[1]]


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
    vault: Vault, locator: str, kwargs: dict[str, object], says: str
) -> None:
    with pytest.raises(DocketError, match=says):
        recall.read(vault, locator, **kwargs)  # type: ignore[arg-type]


# -- verify ---------------------------------------------------------------------------------------------------------


def test_verify_puts_each_line_next_to_its_raw(vault: Vault) -> None:
    got = recall.verify(vault, NOTE_ID)
    assert (got.doc, got.rev) == (NOTE_ID, 1)
    assert [(line.n, line.anchors, line.problems) for line in got.lines] == [
        (14, ["@12:08"], []),
        (15, ["@14:30-14:52"], []),
    ]
    assert [s.text for s in got.lines[0].spans[0].segments] == ["So ten million, for the whole quarter."]
    assert [s.speaker for s in got.lines[1].spans[0].segments] == ["Cleo Test"]
    short = recall.verify(vault, NOTE_PATH, window=1)
    assert short.lines[0].spans[0].end_ms == 729_000


def test_verify_chosen_lines(vault: Vault) -> None:
    got = recall.verify(vault, NOTE_ID, lines=[15, 11, 1, 99])
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
def test_verify_takes_a_doc(vault: Vault, doc: str, says: str) -> None:
    with pytest.raises(DocketError, match=says):
        recall.verify(vault, doc)


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


def test_around_on_a_record_with_a_broken_transcript_counts_messages(vault: Vault) -> None:
    broken = make_record(rid="calls:rec-bad", source="calls", kind="recording", native={"segments": [{"start_ms": 1}]})
    vault.write_run("calls", [broken], run_at=at(12))
    spans = recall.read(vault, "calls:rec-bad", view="raw", around=1).spans
    assert [(s.rid, s.segments) for s in spans] == [("calls:rec-bad", [])]
