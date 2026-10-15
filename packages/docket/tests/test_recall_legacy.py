from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from docket_testkit import (
    READ_ROUTES,
    REC,
    at,
    imported_template,
    make_record,
    note,
    notes_template,
    read_route,
    template_vault,
)

from docket import index, recall
from docket.errors import DocketError
from docket.vault.docstore import DocStore
from docket.vault.processed import DocMeta, ProcessedDoc, doc_path
from docket.vault.records import RawRecord
from docket.vault.spans import RawLookup
from docket.vault.vault import Vault

CALL = "notes/calls/2026-09-10-supplier-call.md"
CALL_ID = "legacy-note:2026-09-10-supplier-call"
CHAT = "notes/chat/2026-09-10-supplier-call.md"
TWO = "notes/calls/2026-09-12-two-parts.md"
OFFER_ID = "legacy-note:2026-09-11-offer"


@pytest.fixture(scope="module")
def imported(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return imported_template(tmp_path_factory, template_vault(tmp_path_factory), notes_template(tmp_path_factory))


@pytest.fixture
def plain(imported: Path, tmp_path: Path) -> Vault:
    shutil.copytree(imported / "vault", tmp_path / "vault")
    return Vault.open(tmp_path / "vault")


@pytest.fixture(params=READ_ROUTES)
def vault(plain: Vault, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Vault:
    read_route(request.param, monkeypatch)
    return plain


def test_an_old_path_reads_its_legacy_note(vault: Vault) -> None:
    result = recall.read(vault, CALL)
    assert result.doc is not None
    assert (result.doc.id, result.doc.old_paths, result.resolved, result.problems) == (CALL_ID, [CALL], CALL_ID, [])
    raw = recall.read(vault, CALL, view="raw")
    assert raw.doc is None
    assert [s.rid for s in raw.spans] == [REC]


@pytest.mark.parametrize(
    ("locator", "doc"),
    [
        ("notes\\calls\\2026-09-10-supplier-call.md", CALL_ID),
        ("NOTES/CALLS/2026-09-10-Supplier-Call.md", CALL_ID),
        ("2026-09-11-offer.md", OFFER_ID),
        (f"calls:{CALL}", CALL_ID),
        ("D:\\work\\repo\\notes\\calls\\2026-09-10-supplier-call.md", CALL_ID),
        (f"/home/ada/work/{CALL}", CALL_ID),
        ("processed/legacy-note/2026/09/2026-09-10-supplier-call.md", CALL_ID),
    ],
)
def test_the_ways_old_references_were_written(vault: Vault, locator: str, doc: str) -> None:
    result = recall.read(vault, locator)
    assert result.doc is not None
    assert (result.doc.id, result.resolved) == (doc, doc)


def test_an_old_path_with_a_time_reads_the_recording_there(vault: Vault) -> None:
    both = recall.read(vault, f"{CALL}@12:08", view="both")
    assert both.resolved == "r:calls:rec-0042@12:08"
    assert both.doc is not None
    assert both.doc.id == CALL_ID
    assert [s.text for s in both.spans[0].segments] == ["So ten million, for the whole quarter."]
    assert recall.read(vault, f"{CALL}@12:08").spans == []
    raw = recall.read(vault, f"calls:{CALL}@12:08", view="raw")
    assert (raw.doc, raw.resolved) == (None, "r:calls:rec-0042@12:08")
    stretch = recall.read(vault, f"{CALL}@12:08-14:52", view="raw", around=1)
    assert "Please send the offer in writing by Friday." in [s.text for s in stretch.spans[0].segments]


def test_a_time_the_note_cant_place_is_a_problem(vault: Vault) -> None:
    none = recall.read(vault, f"{CHAT}@12:08", view="both")
    assert none.doc is not None
    assert none.spans == []
    assert none.problems == [
        f"{none.doc.id} covers no recording with a transcript in the raw log, so @12:08 can't be placed; "
        "import the recording, then run docket import notes again"
    ]
    two = recall.read(vault, f"{TWO}@00:02")
    assert two.problems == [
        "legacy-note:2026-09-12-two-parts covers 2 transcripts; "
        "read one of r:calls:rec-0042@00:02, r:vendor:rec-0007@00:02"
    ]
    past = recall.read(vault, f"{CALL}@99:00", view="raw")
    assert past.spans == []
    assert past.problems == ["1:39:00 is outside calls:rec-0042, which runs from 00:00 to 1:02:10"]


# A recording kept as Plaud's mapper keeps one: the recording, its transcript and its notes, three records whose thread
# and container are the recording's rid, and only the transcript has segments. The note covers the recording.
PLAUD = "plaud:of_00feed0000feed0000feed0000feed00"
POLISHED = f"{PLAUD}/transaction_polish"
SYNC = "notes/plaud/2026-10-14-supplier-sync.md"
SYNC_ID = "legacy-note:2026-10-14-supplier-sync"
MINUTES = "recording-note:2026-10-14-supplier-sync"
SAID = [
    {"start_ms": 0, "end_ms": 4000, "speaker": "Ben Sample", "text": "Morning, Cleo."},
    {"start_ms": 642_000, "end_ms": 650_000, "speaker": "Cleo Test", "text": "Ten million, signed by Friday."},
    {"start_ms": 900_000, "end_ms": 905_000, "speaker": "Ben Sample", "text": "Thanks, bye."},
]


def plaud_records(*blocks: str) -> list[RawRecord]:
    under = {"source": "plaud", "container": PLAUD, "thread": PLAUD, "author": None}
    said = "\n".join(f"{s['speaker']}: {s['text']}" for s in SAID)
    return [
        make_record(rid=PLAUD, kind="recording", title="Supplier sync", text="", **under),
        *(make_record(rid=f"{PLAUD}/{b}", kind="transcript", segments=SAID, text=said, **under) for b in blocks),
        make_record(rid=f"{PLAUD}/summary", kind="vendor-summary", text="## Summary\n\nPrice agreed.", **under),
    ]


def with_plaud(vault: Vault, *blocks: str) -> None:
    """The recording with a transcript per block, the legacy note covering it, and minutes citing its polished
    transcript at 10:42 when there is one."""
    vault.write_run("plaud", plaud_records(*blocks), run_at=at(12))
    head = DocMeta(id=SYNC_ID, type="legacy-note", covers=[PLAUD], by="claude-test", old_paths=[SYNC])
    recall.doc_put(vault, ProcessedDoc(head, "# Supplier sync\n\nPrice agreed, signed by Friday.\n", 0).render())
    if "transaction_polish" in blocks:
        recall.doc_put(vault, note(MINUTES, covers=[POLISHED], lines=["- Cleo agreed to ten million. @10:42"]))


@pytest.mark.parametrize(
    ("locator", "doc"), [(f"{SYNC}@10:42", SYNC_ID), (f"plaud:{SYNC}@10:42", SYNC_ID), (f"r:{PLAUD}@10:42", None)]
)
def test_a_time_on_a_recording_is_read_on_its_transcript(vault: Vault, locator: str, doc: str | None) -> None:
    with_plaud(vault, "transaction_polish")
    both = recall.read(vault, locator, view="both")
    assert (both.resolved, both.problems) == (f"r:{POLISHED}@10:42", [])
    assert [s.rid for s in both.spans] == [POLISHED]
    assert [s.text for s in both.spans[0].segments] == ["Ten million, signed by Friday."]
    assert [(c.doc, c.text) for c in both.lines] == [(MINUTES, "- Cleo agreed to ten million. @10:42")]
    assert (both.doc.id if both.doc else None, [ref.id for ref in both.legacy]) == (doc, [] if doc else [SYNC_ID])


def test_a_recording_s_time_still_lists_the_lines_on_the_whole_recording(vault: Vault) -> None:
    with_plaud(vault, "transaction_polish")
    whole = "recording-note:2026-10-14-whole"
    about, both = f"- About the call. [r:{PLAUD}]", f"- Ten million. [r:{PLAUD}] [r:{POLISHED}@10:42]"
    recall.doc_put(vault, note(whole, covers=[PLAUD, POLISHED], lines=[about, both]))
    cited = recall.read(vault, f"r:{PLAUD}@10:42").lines
    assert [(c.doc, c.text) for c in cited] == [
        (MINUTES, "- Cleo agreed to ten million. @10:42"),
        (whole, both),
        (whole, about),
    ]


def test_two_transcripts_of_a_recording_are_named_to_choose_from(vault: Vault) -> None:
    with_plaud(vault, "transaction", "transaction_polish")
    options = f"r:{PLAUD}/transaction@10:42, r:{POLISHED}@10:42"
    two = recall.read(vault, f"{SYNC}@10:42", view="both")
    assert two.problems == [f"{SYNC_ID} covers 2 transcripts; read one of {options}"]
    assert [(s.rid, s.anchor) for s in two.spans] == [(PLAUD, f"[r:{PLAUD}]")]
    with pytest.raises(DocketError, match=re.escape(f"{PLAUD} has 2 transcripts; read one of {options}")):
        recall.read(vault, f"r:{PLAUD}@10:42")
    assert recall.read(vault, f"r:{POLISHED}@10:42", view="raw").spans[0].rid == POLISHED


def test_a_recording_with_no_transcript_under_it_places_no_time(vault: Vault) -> None:
    with_plaud(vault)
    none = recall.read(vault, f"{SYNC}@10:42")
    assert none.problems == [
        f"{SYNC_ID} covers no recording with a transcript in the raw log, so @10:42 can't be placed; "
        "import the recording, then run docket import notes again"
    ]
    with pytest.raises(DocketError, match="has no transcript segments"):
        recall.read(vault, f"r:{PLAUD}@10:42", view="raw")


def test_a_stretch_that_runs_backwards_is_refused(vault: Vault) -> None:
    with pytest.raises(DocketError, match="ends before it starts"):
        recall.read(vault, f"{CALL}@14:52-14:30")


def test_nothing_or_several_is_an_error_naming_them(vault: Vault) -> None:
    with pytest.raises(DocketError, match=r"no legacy note has the old path notes/nowhere\.md"):
        recall.read(vault, "notes/nowhere.md")
    with pytest.raises(DocketError, match="could be any of"):
        recall.read(vault, "2026-09-10-supplier-call.md")


def test_a_rid_in_the_raw_log_that_ends_in_md_stays_raw(vault: Vault) -> None:
    plan = make_record(rid="docs:notes/plan.md", source="docs", kind="document", container=None, text="the plan")
    vault.write_run("docs", [plan], run_at=at(12))
    result = recall.read(vault, "docs:notes/plan.md", view="raw")
    assert (result.doc, [s.rid for s in result.spans]) == (None, ["docs:notes/plan.md"])


def test_a_record_names_the_legacy_notes_covering_it(vault: Vault) -> None:
    result = recall.read(vault, REC)
    assert {ref.id for ref in result.legacy} == {
        CALL_ID,
        "legacy-note:2026-09-12-two-parts",
        "legacy-note:2026-10-14-supplier-call",
    }
    assert next(ref for ref in result.legacy if ref.id == CALL_ID).old_paths == [CALL]


def test_an_old_path_at_an_earlier_rev(vault: Vault) -> None:
    doc = DocStore(vault).get(CALL_ID)
    assert doc is not None
    edited = doc.meta.model_copy(update={"by": "claude-test"})
    recall.doc_put(vault, ProcessedDoc(edited, "# Supplier call\n\nCorrected.\n", 0).render())
    assert recall.read(vault, CALL).doc.text.endswith("Corrected.\n")  # type: ignore[union-attr]
    first = recall.read(vault, CALL, rev=1).doc
    assert first is not None
    assert "Source: call recorder `rec-0042`" in first.text


def test_verify_takes_a_doc_inside_processed_only(vault: Vault) -> None:
    with pytest.raises(DocketError, match="isn't a doc in this vault's processed/ folder"):
        recall.verify(vault, "raw/notes.md")
    with pytest.raises(DocketError, match="no doc at"):
        recall.verify(vault, "processed/brief/nobody.md")


def test_two_notes_claiming_one_old_path_are_both_named(vault: Vault) -> None:
    doc = DocStore(vault).get(CALL_ID)
    assert doc is not None
    twin = doc.meta.model_copy(update={"id": "legacy-note:twin", "rev": 0, "covers": []})
    recall.doc_put(vault, ProcessedDoc(twin, "# Twin\n", 0).render())
    with pytest.raises(DocketError, match=f"could be any of {CALL_ID}, legacy-note:twin; give more of its path"):
        recall.read(vault, CALL)


def test_a_note_edited_after_the_update_takes_the_parse_route(plain: Vault) -> None:
    index.update(plain)
    path = plain.root / doc_path(CALL_ID)
    path.write_text(path.read_text(encoding="utf-8").replace(CALL, "notes/calls/renamed.md"), encoding="utf-8")
    with pytest.raises(DocketError, match="no legacy note has the old path"):
        recall.read(plain, CALL)
    found = recall.read(plain, "notes/calls/renamed.md")
    assert found.resolved == CALL_ID


@pytest.mark.parametrize(("current", "parses"), [(True, 0), (False, 1)])
def test_a_read_parses_every_doc_once_at_most_and_loads_a_source_once(
    plain: Vault, monkeypatch: pytest.MonkeyPatch, current: bool, parses: int
) -> None:
    index.update(plain)
    if not current:
        (plain.root / doc_path("legacy-note:hand-written")).write_text("# By hand\n", encoding="utf-8")
    calls: list[str] = []
    every, load = DocStore.all, RawLookup._source  # pyright: ignore[reportPrivateUsage]

    def counted_all(self: DocStore) -> list[ProcessedDoc]:
        calls.append("all")
        return every(self)

    def counted_load(self: RawLookup, source: str) -> dict[str, list[object]]:
        if source not in self._sources:  # pyright: ignore[reportPrivateUsage]
            calls.append(source)
        return load(self, source)  # type: ignore[return-value]

    monkeypatch.setattr(DocStore, "all", counted_all)
    monkeypatch.setattr(RawLookup, "_source", counted_load)
    both = recall.read(plain, f"calls:{CALL}@12:08", view="both")
    assert both.resolved == "r:calls:rec-0042@12:08"
    assert sorted(calls) == sorted(["calls", *["all"] * parses])
