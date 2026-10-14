from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from docket_testkit import REC, at, imported_template, make_record, notes_template, template_vault

from docket import recall
from docket.errors import DocketError
from docket.vault.docstore import DocStore
from docket.vault.processed import ProcessedDoc
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
def vault(imported: Path, tmp_path: Path) -> Vault:
    shutil.copytree(imported / "vault", tmp_path / "vault")
    return Vault.open(tmp_path / "vault")


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
        f"{none.doc.id} covers no recording in the raw log, so @12:08 can't be placed; "
        "import the recording, then run docket import notes again"
    ]
    two = recall.read(vault, f"{TWO}@00:02")
    assert two.problems == [
        "legacy-note:2026-09-12-two-parts covers 2 recordings; "
        "read one of r:calls:rec-0042@00:02, r:vendor:rec-0007@00:02"
    ]
    past = recall.read(vault, f"{CALL}@99:00", view="raw")
    assert past.spans == []
    assert past.problems == ["1:39:00 is outside calls:rec-0042, which runs from 00:00 to 1:02:10"]


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
