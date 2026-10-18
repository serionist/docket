"""The plan (DR-0578, DR-0577): the family each kind of rid takes, the list's line it makes, hashes only, and the dry
run and the screen: counts by kind and line numbers in the dry run, never a doc's id, a line's text or a listed
value; ids on the screen at the console; what a forget can't reach, every time. Invented data only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import THREAD, make_record
from media_kit import CALL, POLISHED, audio, call, in_raw, media_vault

from docket import recall
from docket.documents import records as documents
from docket.errors import DocketError
from docket.forget import plan as planning
from docket.media import cutlog, layout
from docket.media import put as media
from docket.media import staged as media_staged
from docket.vault import curatelog, holds
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.docstore import DocStore
from docket.vault.records import TRANSCRIBED_VIA, RawRecord
from docket.vault.staging import rid_hash
from docket.vault.vault import Vault

WHISPER = f"{CALL}/whisper"
NOTE = "recording-note:2026-10-12-crane-check"
AT = datetime(2026, 10, 12, 8, 30, tzinfo=UTC)
MARKER = "zanzibarquill"


def put(vault: Vault) -> None:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(audio(4000))
    media.put(vault, CALL, path=str(drop / "take"))


def whisper() -> RawRecord:
    recording, transcript = call()
    return make_record(
        rid=WHISPER, kind="transcript", segments=transcript.segments, text=transcript.text, source="plaud",
        via=f"{TRANSCRIBED_VIA} {'ab' * 32}", fidelity="derived", engine="whisper-fake@0123456", ts=recording.ts,
        thread=CALL, container=CALL,
    )  # fmt: skip


def doc(doc_id: str, covers: list[str], lines: list[str]) -> str:
    covered = "".join(f"  - {rid}\n" for rid in covers)
    return (
        f"---\nid: {doc_id}\ntype: {doc_id.partition(':')[0]}\ncovers:\n{covered}by: claude-test, sync 2026-10-12\n---"
        f"\n\n# Crane check\n\n" + "\n".join(lines) + "\n"
    )


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    """The call in raw with a Whisper transcript, its audio stored, a note on its transcript, and a chat thread."""
    found = media_vault(tmp_path)
    put(found)
    in_raw(found)
    found.write_run("plaud", [whisper()])
    found.write_run("chat", [make_record(n + 1, rid=rid, source="chat", thread=THREAD[0]) for n, rid in
                             enumerate(THREAD)])  # fmt: skip
    recall.doc_put(found, doc(NOTE, [POLISHED], [f"- The crane passed. [r:{POLISHED}@0:01]", "- A summary line."
                                                 f" [r:{POLISHED}@0:12]"]))  # fmt: skip
    return found


# -- the family --------------------------------------------------------------------------------------------------------


def test_a_recording_takes_everything_under_it_its_audio_and_its_keys(vault: Vault) -> None:
    plan = planning.make(vault, "rids", [CALL])
    assert plan.taken == [planning.Taken(CALL, "whole")]
    assert plan.family == {CALL, POLISHED, WHISPER}
    assert plan.rids == {CALL} and plan.media == {CALL}
    (path,) = layout.stored_files(vault.root, CALL)
    assert plan.keys == {media_staged.key_of(path)} and plan.keys_here == 1
    assert (plan.records, plan.stored_files) == (3, 1)
    line = plan.line("test-box", AT)
    assert line.rids == [rid_hash(CALL)] and line.media == [rid_hash(CALL)]
    # Hashes only: no rid, path or key... but the key id, which names no content.
    assert CALL not in line.line() and "plaud" not in line.line()


def test_a_transcript_takes_its_recording_s_audio_and_whisper_transcript(vault: Vault) -> None:
    plan = planning.make(vault, "rids", [POLISHED])
    assert plan.rids == {POLISHED, WHISPER}
    assert plan.family == {POLISHED, WHISPER}
    assert plan.media == {POLISHED, CALL, WHISPER}
    assert plan.stored_files == 1 and plan.keys
    assert any(f"{CALL} stays" in note for note in plan.notes)
    dry = planning.lines(plan, screen=False)
    assert any(line.startswith("note      plaud:rec-a stays") for line in dry)


def test_a_reply_never_takes_its_thread(vault: Vault) -> None:
    plan = planning.make(vault, "rids", [THREAD[1]])
    assert plan.family == {THREAD[1]}
    assert plan.records == 1 and not plan.keys


def test_a_record_only_staged_is_taken_and_one_with_nothing_says_so(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    plan = planning.make(vault, "rids", [CALL, "plaud:rec-zz"])
    assert plan.staged == 2 and plan.records == 0
    assert plan.nothing == ["plaud:rec-zz"]
    assert "nothing   nothing stored as plaud:rec-zz" in planning.lines(plan, screen=False)


def test_a_rid_already_forgotten_says_so(vault: Vault) -> None:
    from forget_kit import forget

    forget(vault, rids=[CALL])
    plan = planning.make(vault, "rids", [CALL])
    assert plan.already == [CALL] and not plan.taken
    assert planning.lines(plan, screen=False) == ["already   plaud:rec-a was forgotten"]


def test_a_held_rid_is_taken_by_its_versions_and_puts_nothing_on_media(vault: Vault) -> None:
    holds.add(vault, "plaud", ["rec-a"])
    plan = planning.make(vault, "rids", [CALL])
    assert plan.taken == [planning.Taken(CALL, "held")]
    assert plan.rids == set() and plan.media == set()
    assert len(plan.versions) == 3 and plan.keys
    assert planning.make(vault, "held").family == {CALL, POLISHED, WHISPER}
    assert planning.make(vault, "held", source="chat").taken == []


def test_never_store_takes_what_a_list_matches_and_counts_it_by_source(vault: Vault) -> None:
    path = vault.root / "vault.yaml"
    config = VaultConfig.load(path)
    config.never_store.keywords.append("load test")
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    found = planning.make(Vault.open(vault.root), "never-store")
    assert {t.rid for t in found.taken} == {POLISHED, WHISPER}
    dry = planning.lines(found, screen=False)
    assert dry[0] == "forget    records a never-store list matches (2 in plaud): 2 versions in 2 run files"
    assert not any(POLISHED in line for line in dry)
    assert any("docs that say a listed value" in line for line in dry)


def test_a_folder_file_takes_its_versions_parts_and_identity(tmp_path: Path) -> None:
    config = VaultConfig.load(media_vault(tmp_path / "a", store=False).root / "vault.yaml")
    config.sources["docs"] = SourceConfig(folder="docs")
    vault = Vault.init(tmp_path / "vault", config, "test-box")
    native = {"sha256": "cd" * 32}
    file = make_record(rid="docs:aaaaaaaaaaaa", source="docs", via=documents.VIA, kind="document", native=native)
    part = make_record(rid="docs:aaaaaaaaaaaa/page1", source="docs", via=documents.VIA, kind="page", text="Page one.")
    vault.write_run("docs", [file, part])
    plan = planning.make(vault, "rids", ["docs:aaaaaaaaaaaa"])
    assert plan.taken == [planning.Taken("docs:aaaaaaaaaaaa", "folder")]
    assert plan.files == {f"sha256:{'cd' * 32}"} and len(plan.versions) == 2
    assert plan.rids == set() and plan.media == set()
    assert any("move the file out" in note for note in plan.notes)


def test_an_lfs_pointer_whose_key_nothing_names_stops_the_forget(vault: Vault) -> None:
    (path,) = layout.stored_files(vault.root, CALL)
    log = cutlog.log_path(vault.root, "plaud")
    log.write_text("", encoding="utf-8")
    path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 9\n",
                    encoding="utf-8")  # fmt: skip
    plan = planning.make(vault, "rids", [CALL])
    assert plan.stops
    dry = planning.lines(plan, screen=False)
    assert any(line.startswith("stops     a stored media file is an LFS pointer") for line in dry)
    assert any(path.name in line for line in planning.lines(plan, screen=True))


def test_one_kind_a_call() -> None:
    with pytest.raises(DocketError, match="one kind a call"):
        planning.make(None, "held", ["plaud:x"])  # type: ignore[arg-type]
    with pytest.raises(DocketError, match="--source goes with"):
        planning.make(None, "rids", [], "plaud")  # type: ignore[arg-type]


def test_a_rid_that_isn_t_one_is_refused(vault: Vault) -> None:
    with pytest.raises(DocketError, match="isn't a rid"):
        planning.make(vault, "rids", ["no rid here"])


# -- the dry run and the screen ----------------------------------------------------------------------------------------


def test_the_dry_run_counts_by_kind_and_never_names_a_doc_or_its_text(vault: Vault) -> None:
    decision_id = f"decision:2026-10-12-{MARKER}"
    legacy = vault.root / "processed" / "legacy-note" / "2026" / "10" / f"2026-10-12-{MARKER}.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        f"---\nid: legacy-note:2026-10-12-{MARKER}\ntype: legacy-note\ncovers:\n  - {CALL}\nby: import\nold_paths:\n"
        f"  - notes/{MARKER}.md\nimport_hash: sha256:{'ab' * 32}\n---\n\n# {MARKER}\n\nThe {MARKER} call.\n",
        encoding="utf-8",
    )
    project = vault.root / "entities" / "project" / "crane.yaml"
    project.parent.mkdir(parents=True)
    project.write_text("id: crane\ntype: project\nname: {en: Crane}\n", encoding="utf-8")
    vault.commit(["entities/project/crane.yaml"], "Entities")
    said = f"- The {MARKER} crane passed. [r:{POLISHED}@0:01]"
    head = "entities: [crane]\nstatus: proposed\nsubject: crane\n"
    text = doc(decision_id, [POLISHED], [said]).replace("---\n\n# Crane check", f"{head}---\n\n# Proposed: it passed")
    recall.doc_put(vault, text)
    plan = planning.make(vault, "rids", [POLISHED])
    dry = planning.lines(plan, screen=False)
    text = "\n".join(dry)
    assert MARKER not in text and NOTE not in text and "crane passed" not in text
    assert "docs      1 line in 1 decision (16); 2 lines in 1 recording note (14, 15); 1 legacy note removed" in text
    assert "removed   2 docs left with no line" in text
    assert text.count("can't     key exports made before") == 1
    screen = "\n".join(planning.lines(plan, screen=True))
    assert NOTE in screen and decision_id in screen
    assert plan.old_paths == {f"notes/{MARKER}.md"} and plan.import_hashes == {f"sha256:{'ab' * 32}"}


def test_the_screen_counts_an_id_a_never_store_list_names(vault: Vault) -> None:
    plan = planning.make(vault, "rids", [POLISHED])
    plan.lists = plan.lists.model_copy(update={"keywords": ["crane check"]})
    screen = planning.lines(plan, screen=True)
    assert NOTE not in "\n".join(screen)
    assert "unnamed   1 id a never-store list names" in screen


def test_the_next_line_and_what_the_api_provider_holds(vault: Vault) -> None:
    plan = planning.make(vault, "rids", [CALL])
    assert planning.command(plan) == f'docket forget {CALL} --vault "{vault.root}" --now'
    plan.api = True
    assert any("chunks sent to the API provider" in line for line in planning.lines(plan, screen=False))
    held = planning.make(vault, "held", source="plaud")
    assert planning.command(held) == f'docket forget --held --source plaud --vault "{vault.root}" --now'


# -- the docs, what stays, and every line of the screens ---------------------------------------------------------------


def write_doc(vault: Vault, rel: str, text: str) -> None:
    """A doc file as a hand edit or an older Docket left it, not put."""
    path = vault.root / "processed" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def curate_op(vault: Vault, locator: str) -> str:
    """An alias added to the crane project, its evidence one locator."""
    line = curatelog.Line.model_validate(
        {
            "schema_version": 1, "id": "20261012T083000-test-box-1", "op": "add", "at": "2026-10-12T08:30:00Z",
            "machine": "test-box", "by": "user", "via": "cli", "key": "alias:crane:crain", "entity": "crane",
            "change": {"alias": {"text": "Crain", "kind": "asr"}},
            "evidence": {"chunks": 1, "containers": 1, "locators": [locator]},
        }
    )  # fmt: skip
    path = vault.root / line.rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line.text() + "\n", encoding="utf-8", newline="\n")
    return line.id


def test_every_kind_of_doc_and_what_stays_is_said_on_both_screens(vault: Vault) -> None:
    project = vault.root / "entities" / "project" / "crane.yaml"
    project.parent.mkdir(parents=True)
    project.write_text("id: crane\ntype: project\nname: {en: Crane}\n", encoding="utf-8")
    # Only the transcript and Whisper's say it; the recording's title says the crane, which stays.
    topic = vault.root / "entities" / "topic" / "load-test.yaml"
    topic.parent.mkdir(parents=True)
    topic.write_text("id: load-test\ntype: topic\nname: {en: Load test}\n", encoding="utf-8")
    vault.commit(["entities/project/crane.yaml", "entities/topic/load-test.yaml"], "Entities")
    op = curate_op(vault, f"r:{POLISHED}@0:01")
    note = DocStore(vault).get(NOTE)
    assert note is not None
    first = next(n for n, said in note.lines() if "crane passed" in said)
    cite = f"[d:{NOTE}@{note.meta.rev}#{first}]"
    recall.doc_put(
        vault, doc("digest:2026-10-12-day", [THREAD[1]], [f"- All is fine. {cite}", f"- A reply. [r:{THREAD[1]}]"])
    )
    head = "entities: [crane]\nstatus: proposed\nsubject: crane\n"
    decided = doc("decision:2026-10-12-passed", [], [f"- Said so. {cite}"])
    recall.doc_put(vault, decided.replace("---\n\n# Crane check", f"{head}---\n\n# Proposed: it passed"))
    DocStore(vault).decide("decision:2026-10-12-passed", 1, "confirm")
    # A time on the recording lands on the transcript the forget takes; a broken anchor names it; a legacy note
    # covers it and a reply.
    write_doc(vault, "recording-note/2026/10/2026-10-12-by-hand.md",
              doc("recording-note:2026-10-12-by-hand", [CALL], [f"- On the recording. [r:{CALL}@0:01]"]))  # fmt: skip
    write_doc(vault, "recording-note/2026/10/2026-10-12-broken.md",
              doc("recording-note:2026-10-12-broken", [THREAD[1]], [f"- About {POLISHED}, newer. [d:x]"]))  # fmt: skip
    write_doc(vault, "legacy-note/2026/09/2026-09-12-both.md",
              "---\nid: legacy-note:2026-09-12-both\ntype: legacy-note\ncovers:\n"
              f"  - {POLISHED}\n  - {THREAD[1]}\nby: import\n---\n\n# Both\n\nBoth of them.\n")  # fmt: skip
    plan = planning.make(vault, "rids", [POLISHED])
    assert plan.entities == ["load-test"] and plan.ops == [op]
    assert plan.legacy_kept == ["legacy-note:2026-09-12-both"]
    dry = "\n".join(planning.lines(plan, screen=False))
    assert "built on  1 line in 1 decision (15); 1 line in 1 digest (14): dropped too" in dry
    assert "decision  1 final decision keeps its title, no evidence left" in dry
    assert "by hand   1 legacy note covering more keep their text, named at the console" in dry
    assert "stays     1 entity nothing else mentions and 1 curate op whose locators all lie here: named at the" in dry
    assert "stops     a recording note line 11: an anchor this Docket can't read" in dry
    assert "recording-note:2026-10-12-by-hand" not in dry
    assert "2 lines in 1 recording note" in dry or "lines in 2 recording notes" in dry
    screen = "\n".join(planning.lines(plan, screen=True))
    assert "          entity load-test" in screen and f"          docket curate undo {op}" in screen
    assert "by hand   1 legacy note covering more keep their text (legacy-note:2026-09-12-both)" in screen
    assert f"records   {WHISPER}" in screen
    assert "recording-note:2026-10-12-broken line 11" in screen


def test_held_records_are_counted_by_source_and_staged_ones_said(vault: Vault, tmp_path: Path) -> None:
    holds.add(vault, "plaud", ["rec-a"])
    dry = planning.lines(planning.make(vault, "held"), screen=False)
    assert dry[0] == "forget    records in raw under a hold not released (3 in plaud): 3 versions in 2 run files"
    staged = media_vault(tmp_path / "staged")
    assert planning.lines(planning.make(staged, "rids", [CALL]), screen=False)[0] == (
        f"forget    {CALL} and 1 record with it: 0 versions in 0 run files, 2 staged"
    )


def test_a_pointer_the_cut_log_names_and_staged_media_give_their_keys(vault: Vault, tmp_path: Path) -> None:
    (path,) = layout.stored_files(vault.root, CALL)
    key = media_staged.key_of(path)
    path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 9\n",
                    encoding="utf-8")  # fmt: skip
    plan = planning.make(vault, "rids", [CALL])
    assert plan.stops == [] and key in plan.keys
    staged = media_vault(tmp_path / "staged")
    put(staged)
    found = planning.make(staged, "rids", [CALL])
    sidecar = media_staged.read_sidecar(layout.sidecar_path(staged.root, CALL))
    assert sidecar is not None and found.keys == {sidecar.key} and found.staged_files == 1


def test_a_final_decision_edited_on_disk_stops_the_forget(vault: Vault) -> None:
    project = vault.root / "entities" / "project" / "crane.yaml"
    project.parent.mkdir(parents=True)
    project.write_text("id: crane\ntype: project\nname: {en: Crane}\n", encoding="utf-8")
    vault.commit(["entities/project/crane.yaml"], "Entities")
    head = "entities: [crane]\nstatus: proposed\nsubject: crane\n"
    decided = doc("decision:2026-10-12-passed", [POLISHED], [f"- It passed. [r:{POLISHED}@0:01]"])
    recall.doc_put(vault, decided.replace("---\n\n# Crane check", f"{head}---\n\n# Proposed: it passed"))
    DocStore(vault).decide("decision:2026-10-12-passed", 1, "confirm")
    path = DocStore(vault).file("decision:2026-10-12-passed")
    path.write_text(path.read_text(encoding="utf-8") + "A note by hand.\n", encoding="utf-8", newline="\n")
    plan = planning.make(vault, "rids", [POLISHED])
    assert plan.stops == [
        (
            "decision:2026-10-12-passed was edited on disk: check it out again, then forget",
            "a confirmed or rejected decision was edited on disk: check it out again, then forget",
        )
    ]


def test_a_legacy_note_covering_nothing_and_an_op_whose_locator_doesn_t_read_stay_unnamed(vault: Vault) -> None:
    legacy = "---\nid: legacy-note:2026-09-12-none\ntype: legacy-note\ncovers: []\nby: import\n---\n\n# None\n\nText.\n"
    write_doc(vault, "legacy-note/2026/09/2026-09-12-none.md", legacy)
    curate_op(vault, "r:")
    plan = planning.make(vault, "rids", [POLISHED])
    assert plan.old_paths == set() and plan.legacy_kept == [] and plan.ops == []
