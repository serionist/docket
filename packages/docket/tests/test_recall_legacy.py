from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import (
    PLAUD,
    POLISHED,
    READ_ROUTES,
    REC,
    SAID,
    at,
    imported_template,
    make_record,
    note,
    notes_template,
    plaud_records,
    read_route,
    shared,
    template_vault,
)

from docket import index, recall
from docket.errors import DocketError
from docket.vault.docstore import DocStore
from docket.vault.processed import DocMeta, DocRefusedError, ProcessedDoc, doc_path
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
def route(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    read_route(request.param, monkeypatch)
    return request.param


@pytest.fixture
def vault(plain: Vault, route: str) -> Vault:
    return plain


@pytest.fixture
def plaud(imported: Path, tmp_path: Path, route: str) -> Callable[..., Vault]:
    """The imported vault with the recording in Plaud's shape (with_plaud), built once per set of blocks: each test
    gets a copy, as it would get the imported vault."""

    def build(base: Path, blocks: tuple[str, ...]) -> Path:
        shutil.copytree(imported / "vault", base / "vault")
        with_plaud(Vault.open(base / "vault"), *blocks)
        return base / "vault"

    def copy(*blocks: str) -> Vault:
        built = shared(lambda base: build(base, blocks), f"recall-legacy-plaud-{'+'.join(blocks) or 'none'}")
        shutil.copytree(built, tmp_path / "vault")
        return Vault.open(tmp_path / "vault")

    return copy


def test_resolves_says_which_locators_read_finds_something_at_without_reading_them(vault: Vault) -> None:
    found = [CALL, f"plaud:{CALL}@12:08", doc_path(CALL_ID).as_posix(), CALL_ID, REC]
    missing = ["notes/calls/missing.md", "mail:Venue dates | 2026-09-01 10:00", "legacy-note:nothing-here", "r:[x"]
    assert recall.resolves(vault, [*found, *missing, CALL]) == set(found)


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


# A recording in Plaud's shape (PLAUD), with the legacy note covering the recording.
SYNC = "notes/plaud/2026-10-14-supplier-sync.md"
SYNC_ID = "legacy-note:2026-10-14-supplier-sync"
MINUTES = "recording-note:2026-10-14-supplier-sync"


def with_plaud(vault: Vault, *blocks: str) -> None:
    """The recording with a transcript per block, the legacy note covering it, and minutes citing its polished
    transcript at 10:42 when there is one. Put by the store itself, so the template is the same whatever route the test
    asking for it takes."""
    vault.write_run("plaud", plaud_records(*blocks), run_at=at(12))
    head = DocMeta(id=SYNC_ID, type="legacy-note", covers=[PLAUD], by="claude-test", old_paths=[SYNC])
    store = DocStore(vault)
    store.put(ProcessedDoc(head, "# Supplier sync\n\nPrice agreed, signed by Friday.\n", 0).render())
    if "transaction_polish" in blocks:
        store.put(note(MINUTES, covers=[POLISHED], lines=["- Cleo agreed to ten million. @10:42"]))


@pytest.mark.parametrize(
    ("locator", "doc"), [(f"{SYNC}@10:42", SYNC_ID), (f"plaud:{SYNC}@10:42", SYNC_ID), (f"r:{PLAUD}@10:42", None)]
)
def test_a_time_on_a_recording_is_read_on_its_transcript(
    plaud: Callable[..., Vault], locator: str, doc: str | None
) -> None:
    vault = plaud("transaction_polish")
    both = recall.read(vault, locator, view="both")
    assert (both.resolved, both.problems) == (f"r:{POLISHED}@10:42", [])
    assert [s.rid for s in both.spans] == [POLISHED]
    assert [s.text for s in both.spans[0].segments] == ["Ten million, signed by Friday."]
    assert [(c.doc, c.text) for c in both.lines] == [(MINUTES, "- Cleo agreed to ten million. @10:42")]
    assert (both.doc.id if both.doc else None, [ref.id for ref in both.legacy]) == (doc, [] if doc else [SYNC_ID])


@pytest.mark.parametrize("locator", [f"r:{PLAUD}@10:42", f"r:{POLISHED}@10:42", f"{SYNC}@10:42", f"plaud:{SYNC}@10:42"])
def test_a_recording_s_time_lists_the_lines_on_the_whole_recording_on_every_read(
    plaud: Callable[..., Vault], locator: str
) -> None:
    vault = plaud("transaction_polish")
    whole = "recording-note:2026-10-14-whole"
    about, both = f"- About the call. [r:{PLAUD}]", f"- Ten million. [r:{PLAUD}] [r:{POLISHED}@10:42]"
    recall.doc_put(vault, note(whole, covers=[PLAUD, POLISHED], lines=[about, both]))
    found = recall.read(vault, locator)
    assert [(c.doc, c.text) for c in found.lines] == [
        (MINUTES, "- Cleo agreed to ten million. @10:42"),
        (whole, both),
        (whole, about),
    ]
    assert found.resolved == (None if locator.startswith(f"r:{POLISHED}") else f"r:{POLISHED}@10:42")


@pytest.mark.parametrize("locator", [f"r:{PLAUD}", PLAUD, f"[r:{PLAUD}]"])
def test_a_recording_read_whole_lists_the_lines_on_its_transcripts(plaud: Callable[..., Vault], locator: str) -> None:
    # A search hit on the recording, as the web panel opens it, shows the minutes anchored on its transcripts.
    vault = plaud("transaction", "transaction_polish")
    whole, said = "recording-note:2026-10-14-whole", "recording-note:2026-10-14-said"
    about, both = f"- About the call. [r:{PLAUD}]", f"- Ten million. [r:{PLAUD}] [r:{POLISHED}@10:42]"
    recall.doc_put(vault, note(whole, covers=[PLAUD, POLISHED], lines=[about, both]))
    morning = f"- Ben opened. [r:{PLAUD}/transaction@00:00]"
    recall.doc_put(vault, note(said, covers=[f"{PLAUD}/transaction"], lines=[morning]))
    found = recall.read(vault, locator, view="both")
    assert ([s.rid for s in found.spans], found.resolved, found.problems) == ([PLAUD], None, [])
    assert [(c.doc, c.text) for c in found.lines] == [
        (whole, about),
        (whole, both),
        (said, morning),
        (MINUTES, "- Cleo agreed to ten million. @10:42"),
    ]
    assert [ref.id for ref in found.legacy] == [SYNC_ID]


def test_a_recording_s_other_records_and_paragraphs_stay_its_own(plaud: Callable[..., Vault]) -> None:
    vault = plaud("transaction_polish")
    summary = f"{PLAUD}/summary"
    recall.doc_put(vault, note("digest:2026-10-14-summary", covers=[summary], lines=[f"- Agreed. [r:{summary}]"]))
    # Only a transcript under the recording is read with it, never its vendor summary.
    assert [c.doc for c in recall.read(vault, f"r:{PLAUD}").lines] == [MINUTES]
    # A transcript's paragraphs are its own, so a paragraph of the recording lists none of its lines.
    first = f"- Who spoke. [r:{POLISHED}#1]"
    recall.doc_put(vault, note("digest:2026-10-14-speakers", covers=[POLISHED], lines=[first]))
    assert [c.text for c in recall.read(vault, f"r:{POLISHED}#1").lines] == [first]
    assert recall.read(vault, f"r:{PLAUD}#1").lines == []


def test_two_transcripts_of_a_recording_are_named_to_choose_from(plaud: Callable[..., Vault]) -> None:
    vault = plaud("transaction", "transaction_polish")
    options = f"r:{PLAUD}/transaction@10:42, r:{POLISHED}@10:42"
    two = recall.read(vault, f"{SYNC}@10:42", view="both")
    assert two.problems == [f"{SYNC_ID} covers 2 transcripts; read one of {options}"]
    assert [(s.rid, s.anchor) for s in two.spans] == [(PLAUD, f"[r:{PLAUD}]")]
    with pytest.raises(DocketError, match=re.escape(f"{PLAUD} has 2 transcripts; read one of {options}")):
        recall.read(vault, f"r:{PLAUD}@10:42")
    assert recall.read(vault, f"r:{POLISHED}@10:42", view="raw").spans[0].rid == POLISHED


def test_a_recording_with_no_transcript_under_it_places_no_time(plaud: Callable[..., Vault]) -> None:
    vault = plaud()
    none = recall.read(vault, f"{SYNC}@10:42")
    assert none.problems == [
        f"{SYNC_ID} covers no recording with a transcript in the raw log, so @10:42 can't be placed; "
        "import the recording, then run docket import notes again"
    ]
    with pytest.raises(DocketError, match="has no transcript segments"):
        recall.read(vault, f"r:{PLAUD}@10:42", view="raw")


@pytest.mark.parametrize("locator", [f"r:{POLISHED}", POLISHED, f"r:{POLISHED}@10:42"])
def test_a_transcript_s_read_names_what_covers_its_recording(plaud: Callable[..., Vault], locator: str) -> None:
    # A search hit on the transcript, as the web panel opens it, names the legacy note and the lines on the recording.
    vault = plaud("transaction_polish")
    whole = "recording-note:2026-10-14-whole"
    about = f"- About the call. [r:{PLAUD}]"
    recall.doc_put(vault, note(whole, covers=[PLAUD], lines=[about]))
    found = recall.read(vault, locator, view="both")
    assert ([s.rid for s in found.spans], found.resolved, found.problems) == ([POLISHED], None, [])
    assert [(c.doc, c.text) for c in found.lines] == [(MINUTES, "- Cleo agreed to ten million. @10:42"), (whole, about)]
    assert [(ref.id, ref.old_paths) for ref in found.legacy] == [(SYNC_ID, [SYNC])]
    assert [ref.id for ref in recall.read(vault, f"r:{PLAUD}/summary").legacy] == []


@pytest.mark.parametrize(
    ("blocks", "time", "hint"),
    [
        (
            ["transaction_polish"],
            "@10:42-10:50",
            f"the time is on its transcript {POLISHED}: write [r:{POLISHED}@10:42-10:50], with {POLISHED} in covers",
        ),
        (
            ["transaction", "transaction_polish"],
            "@10:42-10:50",
            f"the time is on its transcripts {PLAUD}/transaction, {POLISHED}: "
            f"write [r:{PLAUD}/transaction@10:42-10:50] or [r:{POLISHED}@10:42-10:50], with that one in covers",
        ),
        ([], "@10:42-10:50", f"anchor the whole record: [r:{PLAUD}]"),
        # A time past the transcript's end gets no anchor that would be refused in turn, only why.
        (["transaction_polish"], "@1:39:00", f"1:39:00 is outside {POLISHED}, which runs from 00:00 to 15:05"),
        (
            ["transaction", "transaction_polish"],
            "@1:39:00",
            f"1:39:00 is outside {PLAUD}/transaction, which runs from 00:00 to 15:05; "
            f"1:39:00 is outside {POLISHED}, which runs from 00:00 to 15:05",
        ),
    ],
)
def test_a_time_on_a_recording_is_refused_naming_its_transcripts(
    plaud: Callable[..., Vault], blocks: list[str], time: str, hint: str
) -> None:
    vault = plaud(*blocks)
    doc_id = "recording-note:2026-10-14-on-the-recording"
    doc = note(doc_id, covers=[PLAUD], lines=[f"- Ten million. {time}"])
    with pytest.raises(DocRefusedError) as refused:
        recall.doc_put(vault, doc)
    assert [p.message for p in refused.value.problems] == [f"{time}: {PLAUD} has no transcript segments; {hint}"]
    # The same doc written by hand: verify says the same.
    path = vault.root / doc_path(doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8", newline="\n")
    checked = recall.verify(vault, doc_id).lines
    assert [s.problem for line in checked for s in line.spans] == [f"{PLAUD} has no transcript segments; {hint}"]


def test_a_hint_names_only_the_transcripts_that_hold_the_time(vault: Vault) -> None:
    under = {"source": "plaud", "container": PLAUD, "thread": PLAUD, "author": None}
    raw = make_record(rid=f"{PLAUD}/transaction", kind="transcript", segments=SAID[:1], text="Morning, Cleo.", **under)
    vault.write_run("plaud", [*plaud_records("transaction_polish"), raw], run_at=at(12))
    with pytest.raises(DocRefusedError) as refused:
        recall.doc_put(vault, note("recording-note:2026-10-14-x", covers=[PLAUD], lines=["- Ten million. @10:42"]))
    assert [p.message for p in refused.value.problems] == [
        f"@10:42: {PLAUD} has no transcript segments; "
        f"the time is on its transcript {POLISHED}: write [r:{POLISHED}@10:42], with {POLISHED} in covers"
    ]


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
        recall.verify(vault, "processed/digest/nobody.md")


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


@pytest.mark.parametrize(("docs", "raw"), [(True, True), (False, True), (True, False), (False, False)])
def test_a_read_parses_every_doc_once_at_most_and_loads_a_source_once(
    plain: Vault, monkeypatch: pytest.MonkeyPatch, docs: bool, raw: bool
) -> None:
    index.update(plain)
    if not docs:
        (plain.root / doc_path("legacy-note:hand-written")).write_text("# By hand\n", encoding="utf-8")
    if not raw:
        plain.write_run("calls", [make_record(rid="calls:rec-new", source="calls", container=None)])
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
    assert sorted(calls) == sorted([*([] if raw else ["calls"]), *([] if docs else ["all"])])
