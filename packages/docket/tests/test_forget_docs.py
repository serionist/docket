"""Docs under a forget (DR-0580): the apply drops each line the docs' rule drops in the doc's next rev, written as the
rule renders it so the index numbers its lines as the file does, takes a hand edit in first, keeps a confirmed decision
with its title and links when all its evidence goes, and removes a listed legacy note, which the import then refuses by
its old path or its body; `read` takes a doc the apply hasn't rewritten yet as it will be. Invented data only."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import PLAUD, POLISHED, copied, git, make_config, make_record, plaud_records
from forget_kit import forget

from docket import decisions, recall
from docket.errors import DocketError
from docket.forget import apply as forget_apply
from docket.imports import notes
from docket.index import build
from docket.media.keystore import KeyStore
from docket.vault import decision_form, forgotten
from docket.vault.docstore import DocStore
from docket.vault.processed import parse_doc
from docket.vault.vault import Vault

NOTE = "recording-note:2026-10-14-supplier-sync"
DIGEST = "digest:2026-10-14-day"
OLD = "decision:2026-10-13-eight-million"
DECISION = "decision:2026-10-14-ten-million"
CHAT = make_record(1).rid
TEN = f"- Ten million, signed by Friday. [r:{POLISHED}@10:42]"
KEPT = f"- Cleo opened the channel. [r:{CHAT}]"
AT = datetime(2026, 10, 14, 10, tzinfo=UTC)


def doc(doc_id: str, covers: Sequence[str], lines: Sequence[str], *head: str, title: str = "Supplier sync") -> str:
    covered = ["covers:", *(f"  - {rid}" for rid in covers)] if covers else ["covers: []"]
    front = [f"id: {doc_id}", f"type: {doc_id.partition(':')[0]}", *covered, "by: claude-test, sync 2026-10-14", *head]
    return "---\n" + "\n".join(front) + f"\n---\n\n# {title}\n\n" + "\n".join(lines) + "\n"


def cite(vault: Vault, doc_id: str, text: str) -> str:
    found = DocStore(vault).get(doc_id)
    assert found is not None
    return f"[d:{doc_id}@{found.meta.rev}#{next(n for n, said in found.lines() if said == text)}]"


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    """A note on the transcript and a chat message, a digest built on both of its lines, and a confirmed decision
    whose one line cites the note's transcript line and which replaced an earlier one: built once per run, each test a
    copy (DR-0531)."""
    return Vault.open(copied(tmp_path, _build_vault, f"{__name__}.vault"))


def _build_vault(folder: Path) -> Path:
    found = Vault.init(folder / "vault", make_config(), "test-box")
    recording, polished, summary = plaud_records("transaction_polish")
    found.write_run("plaud", [recording, polished, summary], AT)
    found.write_run("chat", [make_record(1)], AT)
    project = found.root / "entities" / "project" / "supplier-deal.yaml"
    project.parent.mkdir(parents=True)
    project.write_text("id: supplier-deal\ntype: project\nname: {en: Supplier deal}\n", encoding="utf-8")
    found.commit(["entities/project/supplier-deal.yaml"], "Entities")
    recall.doc_put(found, doc(NOTE, [CHAT, POLISHED], [TEN, KEPT]))
    day = [f"- The channel opened. {cite(found, NOTE, KEPT)}", f"- The deal is ten million. {cite(found, NOTE, TEN)}"]
    recall.doc_put(found, doc(DIGEST, [], day, title="The day"))
    store = DocStore(found)
    decided = ["entities: [supplier-deal]", "status: proposed", "subject: supplier-deal"]
    recall.doc_put(found, doc(OLD, [CHAT], [KEPT], *decided, title="Proposed: eight million"))
    store.decide(OLD, 1, "confirm")
    said = [f"- Said on the call. {cite(found, NOTE, TEN)}"]
    recall.doc_put(found, doc(DECISION, [], said, *decided, "replaces:", f"  - {OLD}", title="Proposed: ten million"))
    store.decide(DECISION, 1, "confirm")
    return found.root


def apply(vault: Vault) -> forget_apply.Applied:
    return forget_apply.apply(vault, KeyStore(vault.home), first=True)


def forget_transcript(vault: Vault) -> None:
    forget(vault, rids=[POLISHED], media=[POLISHED, PLAUD])


def text_of(vault: Vault, doc_id: str) -> str:
    return DocStore(vault).file(doc_id).read_text(encoding="utf-8")


# -- lines and their dependents ----------------------------------------------------------------------------------------


def test_a_line_on_the_family_and_the_lines_built_on_it_go_in_each_doc_s_next_rev(vault: Vault) -> None:
    forget_transcript(vault)
    done = apply(vault)
    assert (done.docs, done.removed) == (3, 0)
    note = DocStore(vault).get(NOTE)
    assert note is not None and note.meta.rev == 2
    assert TEN not in note.source and KEPT in note.source
    assert note.meta.covers == [CHAT]
    digest = DocStore(vault).get(DIGEST)
    assert digest is not None
    assert "ten million" not in digest.source and "The channel opened" in digest.source
    body = git(vault.root, "log", "-1", "--format=%B")
    assert body.startswith("Forget: 1 record(s), 0 media file(s), 3 doc(s) from test-box")
    assert f"Doc: {NOTE} rev 2" in body and f"Doc: {DIGEST} rev 2" in body
    # Nothing flagged: what was built on a dropped line went with it.
    stale = recall.stale(vault)
    assert stale.flagged == [] and stale.unapplied == []


def test_the_file_is_the_rule_s_text_so_the_index_numbers_its_lines_as_the_file_does(vault: Vault) -> None:
    forget_transcript(vault)
    apply(vault)
    build.rebuild(vault)
    note = DocStore(vault).get(NOTE)
    assert note is not None
    conn = sqlite3.connect(build.db_path(vault))
    try:
        rows = dict(conn.execute("SELECT line, hash FROM doc_lines WHERE doc = ?", (NOTE,)).fetchall())
    finally:
        conn.close()
    assert sorted(rows) == sorted(note.citable())


def test_a_confirmed_decision_whose_evidence_all_went_keeps_its_title_and_links(vault: Vault) -> None:
    forget_transcript(vault)
    apply(vault)
    text = text_of(vault, DECISION)
    found = parse_doc(text)
    assert found.meta.status == "confirmed" and found.meta.covers == [] and found.meta.supersedes == [OLD]
    assert found.meta.rev == 3
    assert "# ten million" in text and "Said on the call" not in text
    assert decision_form.evidence_gone(found)
    standing = decision_form.standings(DocStore(vault).decisions())
    assert standing[DECISION].current and standing[OLD].superseded_by == DECISION
    report = decisions.report(vault)
    (shown,) = [d for d in report.decisions if d.id == DECISION]
    assert shown.note == decision_form.EVIDENCE_FORGOTTEN
    view = recall.read(vault, DECISION).doc
    assert view is not None and view.note == decision_form.EVIDENCE_FORGOTTEN


def test_a_hand_edit_is_taken_in_before_the_forget_s_rev(vault: Vault) -> None:
    path = DocStore(vault).file(NOTE)
    path.write_text(path.read_text(encoding="utf-8").replace(KEPT, f"{KEPT}\n- Ada joined late. [r:{CHAT}]"),
                    encoding="utf-8", newline="\n")  # fmt: skip
    forget_transcript(vault)
    apply(vault)
    subjects = git(vault.root, "log", "--format=%s").splitlines()
    assert f"Doc: {NOTE} rev 2 (edited by hand)" in subjects
    note = DocStore(vault).get(NOTE)
    assert note is not None and note.meta.rev == 3
    assert "Ada joined late" in note.source and TEN not in note.source


def test_a_final_decision_edited_on_disk_is_left_for_its_owner(vault: Vault) -> None:
    path = DocStore(vault).file(DECISION)
    edited = path.read_text(encoding="utf-8") + "\nA note by hand.\n"
    path.write_text(edited, encoding="utf-8", newline="\n")
    forget_transcript(vault)
    done = apply(vault)
    assert done.left == [DECISION]
    assert path.read_text(encoding="utf-8") == edited
    # Every read takes it from its last commit, and drops the lines the forget drops.
    view = recall.read(vault, DECISION).doc
    assert view is not None and "Said on the call" not in view.text


def test_read_takes_a_doc_the_apply_hasn_t_rewritten_as_it_will_be(vault: Vault) -> None:
    forget_transcript(vault)
    view = recall.read(vault, NOTE).doc
    assert view is not None
    assert TEN not in view.text and KEPT in view.text
    assert text_of(vault, NOTE).count(TEN) == 1
    verified = recall.verify(vault, NOTE)
    assert all(TEN not in line.text for line in verified.lines)


def test_a_doc_left_with_no_line_is_removed_and_read_says_forgotten(vault: Vault) -> None:
    recall.doc_put(vault, doc("recording-note:2026-10-14-price", [POLISHED], [TEN], title="The price"))
    forget_transcript(vault)
    with pytest.raises(Exception, match="was forgotten"):
        recall.read(vault, "recording-note:2026-10-14-price")
    # Nor does `resolves` find it, by its id or its path, as `read` doesn't.
    path = DocStore(vault).file("recording-note:2026-10-14-price").relative_to(vault.root).as_posix()
    assert recall.resolves(vault, ["recording-note:2026-10-14-price", path, NOTE]) == {NOTE}
    done = apply(vault)
    assert done.removed == 1
    assert not DocStore(vault).file("recording-note:2026-10-14-price").exists()


# -- legacy notes --------------------------------------------------------------------------------------------------


def test_a_removed_legacy_note_is_refused_by_the_import_by_its_old_path_and_its_body(
    vault: Vault, tmp_path: Path
) -> None:
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "2026-09-13-supplier-call.md").write_text("# Supplier call\n\nTen million.\n", encoding="utf-8")
    notes.import_notes(vault, folder)
    (legacy,) = [d for d in DocStore(vault).all() if d.meta.type == "legacy-note"]
    assert legacy.meta.import_hash is not None
    forget(vault, rids=[POLISHED], old_paths=list(legacy.meta.old_paths), import_hashes=[legacy.meta.import_hash])
    apply(vault)
    assert not DocStore(vault).file(legacy.meta.id).exists()
    again = notes.import_notes(vault, folder)
    assert again.skipped == {"forgotten": 1} and again.new == 0
    # Renamed, moved or copied, its body gives it away.
    (folder / "sub").mkdir()
    (folder / "sub" / "copy.md").write_text("# Supplier call\n\nTen million.\n", encoding="utf-8")
    take = notes.Take(side="note", hash="abcdef", old_path=legacy.meta.old_paths[0])
    copied = notes.import_notes(vault, folder, takes=[take])
    assert copied.skipped == {"forgotten": 2} and copied.new == 0
    assert any("a forget removed it for good" in r for r in copied.refused)
    assert forgotten.load(vault.root, vault.home).legacy(["elsewhere.md"], legacy.meta.import_hash)


def test_a_time_on_the_recording_goes_when_it_lands_on_no_transcript_left(vault: Vault) -> None:
    # A second transcript under the recording, kept: a time it holds stays, one only the forgotten one held goes.
    second = make_record(rid=f"{PLAUD}/second", kind="transcript", source="plaud", thread=PLAUD, container=PLAUD,
                         segments=[{"start_ms": 0, "end_ms": 30_000, "speaker": "Ada Example", "text": "Hello."}],
                         text="Ada Example: Hello.")  # fmt: skip
    vault.write_run("plaud", [second], AT)
    lines = [f"- Early on. [r:{PLAUD}@0:10]", f"- Later on. [r:{PLAUD}@10:42]"]
    hand = parse_doc(doc("recording-note:2026-10-14-on-the-recording", [PLAUD], lines), lenient=True).render()
    path = vault.root / "processed" / "recording-note" / "2026" / "10" / "2026-10-14-on-the-recording.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(hand, encoding="utf-8", newline="\n")
    forget_transcript(vault)
    apply(vault)
    text = path.read_text(encoding="utf-8")
    assert "Early on." in text and "Later on." not in text


def test_refs_the_rule_can_t_follow_drop_nothing(vault: Vault) -> None:
    # A ref to a rev history doesn't hold, and one to a doc whose file isn't UTF-8.
    broken = vault.root / "processed" / "recording-note" / "2026" / "10" / "2026-10-14-broken.md"
    broken.write_bytes(b"---\nid: recording-note:2026-10-14-broken\n\xff\n")
    refs = [
        f"- Gone rev. [d:{NOTE}@7#9=deadbeef]",
        "- Bad file. [d:recording-note:2026-10-14-broken@1#9=deadbeef]",
    ]
    path = vault.root / "processed" / "digest" / "2026" / "10" / "2026-10-14-refs.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(parse_doc(doc("digest:2026-10-14-refs", [], refs), lenient=True).render(), encoding="utf-8",
                    newline="\n")  # fmt: skip
    forget_transcript(vault)
    apply(vault)
    text = path.read_text(encoding="utf-8")
    assert "Gone rev." in text and "Bad file." in text


# -- a hand edit judged again, a commit retried ------------------------------------------------------------------------


def test_only_the_docs_taken_in_and_those_citing_them_are_read_again(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = DocStore(vault).file(NOTE)
    path.write_text(path.read_text(encoding="utf-8").replace(KEPT, f"{KEPT}\n- Ada joined late. [r:{CHAT}]"),
                    encoding="utf-8", newline="\n")  # fmt: skip
    read: list[list[str]] = []
    real = DocStore.at_paths

    def watched(self: DocStore, paths: list[Path]) -> list[object]:
        read.append(sorted(p.name for p in paths))
        return real(self, paths)  # type: ignore[return-value]

    monkeypatch.setattr(DocStore, "at_paths", watched)
    forget_transcript(vault)
    apply(vault)
    # Every doc once, then the one taken in: its new rev is judged again with the digest and decision citing it.
    assert len(read) == 2 and len(read[0]) == len(list((vault.root / "processed").rglob("*.md")))
    assert read[1] == [path.name]
    digest = DocStore(vault).get(DIGEST)
    assert digest is not None and "ten million" not in digest.source and "The channel opened" in digest.source
    assert "Said on the call" not in text_of(vault, DECISION)


def test_a_commit_retried_by_the_next_call_carries_the_docs_revs(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    forget_transcript(vault)
    real = Vault.commit

    def failing(self: Vault, paths: list[str], message: str) -> str | None:
        if message.startswith("Forget: 1 record"):
            raise DocketError("a commit refused")
        return real(self, paths, message)

    monkeypatch.setattr(Vault, "commit", failing)
    with pytest.raises(DocketError, match="refused"):
        apply(vault)
    monkeypatch.setattr(Vault, "commit", real)
    forget_apply.apply(vault, KeyStore(vault.home))
    body = git(vault.root, "log", "-1", "--format=%B")
    assert body.startswith("Forget: applied on test-box")
    assert f"Doc: {NOTE} rev 2" in body and f"Doc: {DIGEST} rev 2" in body and f"Doc: {DECISION} rev 3" in body
    # The forget's rev is in the doc's history, so the next take-in finds no hand edit.
    store = DocStore(vault)
    assert store.history(NOTE)[0][0] == 2
    assert store.take_in(NOTE) is None
