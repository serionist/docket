from __future__ import annotations

import logging
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import MAIL, REC, THREAD, at, copy_vault, make_record, note, sh, template_vault

from docket import recall
from docket.errors import DocketError
from docket.vault import processed
from docket.vault.anchors import format_anchor
from docket.vault.docstore import DocStore
from docket.vault.processed import DocRefusedError, double_quoted
from docket.vault.vault import Vault

NOTE_ID = "recording-note:2026-10-14-supplier-call"
NOTE_PATH = "processed/recording-note/2026/10/2026-10-14-supplier-call.md"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_vault(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return copy_vault(template, tmp_path)


def refused(vault: Vault, text: str) -> list[str]:
    with pytest.raises(DocRefusedError) as e:
        recall.doc_put(vault, text)
    return [str(p) for p in e.value.problems]


def head(vault: Vault) -> str:
    return sh(vault.root, "git", "rev-parse", "HEAD").strip()


def staged(vault: Vault) -> str:
    return sh(vault.root, "git", "diff", "--cached", "--name-only")


def fail_commits(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    real = vault.git.run

    def run(*args: str, **kwargs: Any) -> Any:
        if args and args[0] == "commit":
            raise DocketError("git commit: a hook said no")
        return real(*args, **kwargs)

    monkeypatch.setattr(vault.git, "run", run)


def test_a_doc_is_written_at_rev_1_and_committed(vault: Vault) -> None:
    result = recall.doc_put(
        vault, note(lines=["- Ben put it at 10 million. @12:08", "- Cleo wants it in writing. @14:30-14:52"])
    )
    assert (result.id, result.rev, result.path, result.changed, result.taken_in) == (NOTE_ID, 1, NOTE_PATH, True, None)
    assert result.commit == head(vault)
    assert sh(vault.root, "git", "log", "-1", "--format=%s") == f"Doc: {NOTE_ID} rev 1\n"
    assert sh(vault.root, "git", "status", "--porcelain") == ""
    stored = (vault.root / NOTE_PATH).read_text(encoding="utf-8")
    assert "\nrev: 1\n" in stored
    assert stored.endswith("- Cleo wants it in writing. @14:30-14:52\n")
    assert not any((vault.root / ".git" / "docket" / "tmp").iterdir())


@pytest.mark.parametrize(
    ("text", "says"),
    [
        (note(type="minutes"), "type is 'minutes' but the id starts with 'recording-note'"),
        (note(covers=["calls:nope"]), "covers: calls:nope isn't in the raw log"),
        (note(lines=[f"- the offer {format_anchor(MAIL, 'part', 2)}"]), "isn't in covers; add it there"),
        (note(lines=["- past the end @1:05:00"]), "@1:05:00: 1:05:00 is outside calls:rec-0042"),
        (note(lines=["- no anchor here"]), "no anchors at the end of the line"),
        (note(lines=["- @12:08 the price came first"]), "anchors go at the very end"),
        (note(covers=[REC, MAIL], lines=["- which one? @12:08"]), "@12:08 needs a doc covering one record"),
        (note(rev="3"), "there is no recording-note:2026-10-14-supplier-call yet; leave rev out"),
        (note(supersedes="brief:nobody"), "supersedes: brief:nobody doesn't exist"),
        (note(lines=["- a reserved form [d:digest-1]"]), "only [r:...] is an anchor"),
        (note(lines=["- not a time [r:calls:rec-0042@1:60]"]), "if @1:60 is meant as a position, it isn't one"),
    ],
)
def test_a_doc_is_refused_with_the_reason(vault: Vault, text: str, says: str) -> None:
    problems = refused(vault, text)
    assert any(says in p for p in problems), problems
    assert not (vault.root / NOTE_PATH).exists()


def test_every_problem_is_reported_at_once_with_its_line(vault: Vault) -> None:
    text = note(covers=[REC, "calls:nope"], lines=["- fine [r:calls:rec-0042@12:08]", "- no anchor", "- bad @1:60"])
    problems = refused(vault, text)
    assert len(problems) == 3
    assert problems[0].startswith("line 6: covers: calls:nope")
    assert problems[1].startswith('line 13 ("- no anchor")')
    assert problems[2].startswith('line 14 ("- bad @1:60"): @1:60:')


def test_a_put_identical_to_the_stored_doc_makes_no_commit(vault: Vault) -> None:
    recall.doc_put(vault, note())
    before = head(vault)
    again = recall.doc_put(vault, note(rev="1"))
    assert (again.changed, again.rev, again.commit) == (False, 1, None)
    # The same content at a stale rev is still nothing to do.
    assert recall.doc_put(vault, note()).changed is False
    assert head(vault) == before


def test_a_changed_doc_is_the_next_rev_and_history_keeps_the_last(vault: Vault) -> None:
    first = recall.doc_put(vault, note())
    second = recall.doc_put(vault, note(rev="1", lines=["- Corrected: 10 million for the quarter. @12:08"]))
    assert second.rev == 2
    store = DocStore(vault)
    assert store.history(NOTE_ID) == [(2, second.commit), (1, first.commit)]
    assert "Corrected" not in store.at_rev(NOTE_ID, 1).body
    assert "Corrected" in store.at_rev(NOTE_ID, 2).body
    with pytest.raises(DocketError, match="has no rev 3; its revs: 1, 2"):
        store.at_rev(NOTE_ID, 3)
    with pytest.raises(DocketError, match="no rev 1; its revs: none"):
        store.at_rev("brief:nobody", 1)


def test_a_stale_rev_is_refused(vault: Vault) -> None:
    recall.doc_put(vault, note())
    recall.doc_put(vault, note(rev="1", lines=["- second @12:08"]))
    assert any("is at rev 2 now; read it again" in p for p in refused(vault, note(rev="1", lines=["- third @12:08"])))
    assert any("is at rev 2 now" in p for p in refused(vault, note(lines=["- no rev at all @12:08"])))


def test_two_sessions_at_the_same_rev_cannot_both_write_the_next(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    recall.doc_put(vault, note())
    other = Vault.open(vault.root)
    real_writing = other.writing

    @contextmanager
    def first_commits_meanwhile() -> Iterator[None]:
        # The other session's unlocked checks passed; this one commits before it takes the lock.
        recall.doc_put(vault, note(rev="1", lines=["- this session's edit @12:08"]))
        with real_writing():
            yield

    monkeypatch.setattr(other, "writing", first_commits_meanwhile)
    problems = refused(other, note(rev="1", lines=["- the other session's edit @14:30"]))
    assert any("is at rev 2 now" in p for p in problems)
    assert "this session's edit" in (vault.root / NOTE_PATH).read_text(encoding="utf-8")
    assert sh(vault.root, "git", "log", "--format=%s").count(f"Doc: {NOTE_ID} rev 2") == 1


def test_two_new_docs_cannot_share_an_id(vault: Vault) -> None:
    recall.doc_put(vault, note())
    assert any("is at rev 1 now" in p for p in refused(vault, note(lines=["- someone else's @14:30"])))


def test_supersedes_must_not_make_a_cycle(vault: Vault) -> None:
    recall.doc_put(vault, note())
    minutes = note("minutes:2026-10-14-supplier-call", supersedes=NOTE_ID)
    assert recall.doc_put(vault, minutes).rev == 1
    back = note(rev="1", supersedes="minutes:2026-10-14-supplier-call", lines=["- loop @12:08"])
    assert any("already leads back to recording-note" in p for p in refused(vault, back))


def test_a_diamond_of_supersedes_is_fine(vault: Vault) -> None:
    recall.doc_put(vault, note("brief:c"))
    recall.doc_put(vault, note("brief:b", supersedes="brief:c"))
    recall.doc_put(vault, note("brief:x", supersedes="[brief:b, brief:c]"))
    assert recall.doc_put(vault, note("brief:y", supersedes="brief:x")).rev == 1


def test_a_superseded_doc_that_doesnt_parse_ends_the_chain(vault: Vault) -> None:
    broken = vault.root / "processed" / "brief" / "broken.md"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"---\nid: brief:broken\nby: \xe9\n---\n")
    assert recall.doc_put(vault, note("brief:new", supersedes="brief:broken")).rev == 1


def test_a_put_during_a_user_s_merge_is_refused_and_touches_nothing(vault: Vault) -> None:
    (vault.root / "resolution.txt").write_text("the user's resolution\n", encoding="utf-8")
    sh(vault.root, "git", "add", "resolution.txt")
    (vault.root / ".git" / "MERGE_HEAD").write_text(head(vault) + "\n", encoding="utf-8")
    with pytest.raises(DocketError, match="a merge is in progress"):
        recall.doc_put(vault, note())
    assert not (vault.root / NOTE_PATH).exists()
    assert staged(vault) == "resolution.txt\n"


def hand_edit(vault: Vault, old: str, new: str) -> None:
    path = vault.root / NOTE_PATH
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8", newline="\n")


def test_an_uncommitted_hand_edit_goes_in_first_and_a_stale_put_is_refused(vault: Vault) -> None:
    recall.doc_put(vault, note())
    hand_edit(vault, "10 million forints", "10 million forints, net")
    problems = refused(vault, note(rev="1", lines=["- Claude's edit @12:08"]))
    assert any("is at rev 2 now, after a hand edit taken in as rev 2" in p for p in problems)
    assert sh(vault.root, "git", "log", "-1", "--format=%s") == f"Doc: {NOTE_ID} rev 2 (edited by hand)\n"
    stored = (vault.root / NOTE_PATH).read_text(encoding="utf-8")
    assert "net" in stored
    assert "\nrev: 2\n" in stored
    assert sh(vault.root, "git", "status", "--porcelain") == ""


def test_a_hand_edit_committed_by_hand_goes_in_too(vault: Vault) -> None:
    recall.doc_put(vault, note())
    hand_edit(vault, "10 million forints", "ten million forints")
    hand_commit(vault, "my edit")
    assert any("is at rev 2 now" in p for p in refused(vault, note(rev="1", lines=["- stale @12:08"])))
    store = DocStore(vault)
    assert [rev for rev, _ in store.history(NOTE_ID)] == [2, 1]
    # rev 1 is what doc_put wrote, not the hand commit that still said rev: 1.
    assert "10 million forints" in store.at_rev(NOTE_ID, 1).body
    assert "ten million" in store.at_rev(NOTE_ID, 2).body


def test_a_hand_commit_that_raised_the_rev_line_itself_still_gets_its_own_rev(vault: Vault) -> None:
    recall.doc_put(vault, note())
    hand_edit(vault, "rev: 1\n", "rev: 2\n")
    hand_edit(vault, "10 million forints", "ten million forints")
    hand_commit(vault, "my edit, rev raised by hand")
    assert any("is at rev 3 now" in p for p in refused(vault, note(rev="2", lines=["- stale @12:08"])))
    store = DocStore(vault)
    assert [rev for rev, _ in store.history(NOTE_ID)] == [3, 1]
    assert "ten million" in store.at_rev(NOTE_ID, 3).body


def test_putting_the_hand_edited_file_itself_takes_it_in(vault: Vault) -> None:
    recall.doc_put(vault, note())
    hand_edit(vault, "10 million forints", "10 million forints, net")
    result = recall.doc_put(vault, (vault.root / NOTE_PATH).read_text(encoding="utf-8"))
    assert (result.rev, result.changed, result.taken_in) == (2, False, 2)


def test_a_hand_edit_without_a_rev_line_gets_one(vault: Vault) -> None:
    recall.doc_put(vault, note())
    hand_edit(vault, "rev: 1\n", "")
    refused(vault, note(rev="1", lines=["- stale @12:08"]))
    assert "type: recording-note\nrev: 2\n" in (vault.root / NOTE_PATH).read_text(encoding="utf-8")


def test_a_hand_made_file_counts_as_rev_1(vault: Vault) -> None:
    path = vault.root / "processed" / "brief" / "ben-sample.md"
    path.parent.mkdir(parents=True)
    # A quoted key is still YAML; with no `type:` line to follow, the rev line goes first.
    path.write_text('---\nid: brief:ben-sample\n"type": brief\nby: Ada\n---\nNotes about Ben.\n', encoding="utf-8")
    brief = note("brief:ben-sample", lines=["- Ben works with Cleo. @12:08"])
    assert any("is at rev 1 now" in p for p in refused(vault, brief))
    assert path.read_text(encoding="utf-8").startswith("---\nrev: 1\nid: brief:ben-sample\n")


def hand_commit(vault: Vault, message: str) -> None:
    sh(
        vault.root,
        "git",
        "-c",
        "user.name=Ada Example",
        "-c",
        "user.email=ada@example.invalid",
        "commit",
        "-q",
        "-am",
        message,
    )


def test_a_hand_edit_that_breaks_the_frontmatter_refuses_the_put(vault: Vault) -> None:
    recall.doc_put(vault, note())
    hand_edit(vault, "---\n\n# A note", "\n# A note")
    problems = refused(vault, note(rev="1", lines=["- edit @12:08"]))
    assert problems == [
        f"doc: {NOTE_PATH} was edited by hand and its frontmatter no longer parses; fix it or check it out again"
    ]


def test_a_failed_hand_edit_commit_leaves_the_user_s_bytes(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    recall.doc_put(vault, note())
    hand_edit(vault, "10 million forints", "10 million forints, net")
    users = (vault.root / NOTE_PATH).read_bytes()
    fail_commits(vault, monkeypatch)
    with pytest.raises(DocketError, match="a hook said no"):
        recall.doc_put(vault, note(rev="1", lines=["- edit @12:08"]))
    assert (vault.root / NOTE_PATH).read_bytes() == users
    assert staged(vault) == ""


def test_a_failed_commit_leaves_the_old_bytes_and_nothing_staged(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    recall.doc_put(vault, note())
    before = (vault.root / NOTE_PATH).read_bytes()
    fail_commits(vault, monkeypatch)
    with pytest.raises(DocketError, match="a hook said no"):
        recall.doc_put(vault, note(rev="1", lines=["- edit @12:08"]))
    assert (vault.root / NOTE_PATH).read_bytes() == before
    assert staged(vault) == ""
    with pytest.raises(DocketError, match="a hook said no"):
        recall.doc_put(vault, note("brief:ben-sample"))
    assert not (vault.root / "processed" / "brief" / "ben-sample.md").exists()
    assert staged(vault) == ""


def test_a_doc_deleted_by_hand_goes_on_from_its_last_rev(vault: Vault) -> None:
    recall.doc_put(vault, note())
    (vault.root / NOTE_PATH).unlink()
    hand_commit(vault, "tidy up")
    assert any("is at rev 1 now" in p for p in refused(vault, note()))
    assert recall.doc_put(vault, note(rev="1", lines=["- rebuilt @12:08"])).rev == 2
    assert DocStore(vault).exists(NOTE_ID)


def test_a_legacy_note_needs_no_anchors(vault: Vault) -> None:
    legacy = "---\nid: legacy-note:old-plans\ntype: legacy-note\nby: import\n---\n\nPlans from before Docket.\n"
    assert recall.doc_put(vault, legacy).path == "processed/legacy-note/old-plans.md"


def test_one_commit_can_carry_many_docs(vault: Vault) -> None:
    ids = ["legacy-note:one", "legacy-note:two", "legacy-note:three"]
    for doc_id in ids:
        path = vault.root / "processed" / "legacy-note" / f"{doc_id.partition(':')[2]}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = f"---\nid: {doc_id}\ntype: legacy-note\nrev: 1\nby: import\n---\nOld note.\n"
        path.write_text(text, encoding="utf-8", newline="\n")
    body = "\n".join(f"Doc: {doc_id} rev 1" for doc_id in ids)
    sha = vault.commit([f"processed/legacy-note/{i.partition(':')[2]}.md" for i in ids], f"Import notes\n\n{body}")
    store = DocStore(vault)
    assert [store.history(doc_id) for doc_id in ids] == [[(1, sha)]] * 3
    assert store.at_rev("legacy-note:two", 1).body == "Old note.\n"
    changed = "---\nid: legacy-note:two\ntype: legacy-note\nrev: 1\nby: import\n---\nOld note, tidied.\n"
    result = recall.doc_put(vault, changed)
    assert (result.rev, result.taken_in) == (2, None)


def test_another_program_holding_the_doc_open(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    recall.doc_put(vault, note())
    before = (vault.root / NOTE_PATH).read_bytes()
    monkeypatch.setattr(time, "sleep", lambda _: None)
    real_replace = Path.replace
    tries: list[int] = []

    def held_open(times: int) -> Callable[[Path, Path], Path]:
        def replace(self: Path, target: Path) -> Path:
            tries.append(1)
            if len(tries) <= times:
                raise PermissionError("in use")
            return real_replace(self, target)

        return replace

    monkeypatch.setattr(Path, "replace", held_open(5))
    with pytest.raises(DocketError, match="another program has it open"):
        recall.doc_put(vault, note(rev="1", lines=["- edit @12:08"]))
    assert (vault.root / NOTE_PATH).read_bytes() == before
    assert not any((vault.root / ".git" / "docket" / "tmp").iterdir())
    tries.clear()
    monkeypatch.setattr(Path, "replace", held_open(2))
    assert recall.doc_put(vault, note(rev="1", lines=["- edit @12:08"])).rev == 2


def test_reading_docs_skips_what_doesn_t_parse_or_sits_in_the_wrong_place(
    vault: Vault, caplog: pytest.LogCaptureFixture
) -> None:
    recall.doc_put(vault, note())
    store = DocStore(vault)
    broken = vault.root / "processed" / "brief" / "broken.md"
    broken.parent.mkdir(parents=True)
    broken.write_text("no frontmatter\n", encoding="utf-8")
    (vault.root / "processed" / "brief" / "latin.md").write_bytes(b"---\nid: brief:latin\nby: \xe9\n---\n")
    misplaced = vault.root / "processed" / "brief" / "elsewhere.md"
    misplaced.write_text("---\nid: brief:ben\ntype: brief\ncovers: [calls:a]\nby: x\n---\n", encoding="utf-8")
    caplog.set_level(logging.WARNING)
    assert [d.meta.id for d in store.all()] == [NOTE_ID]
    assert "broken.md" in caplog.text
    assert "latin.md isn't UTF-8" in caplog.text
    assert "belongs at processed/brief/ben.md" in caplog.text
    with pytest.raises(DocketError, match="isn't UTF-8"):
        store.get("brief:latin")
    assert store.get("brief:nobody") is None


def test_a_vault_with_no_processed_folder_has_no_docs(vault: Vault) -> None:
    sh(vault.root, "git", "rm", "-q", "-r", "processed")
    assert DocStore(vault).all() == []


def test_a_thread_summary_across_records(vault: Vault) -> None:
    summary = note(
        "thread-summary:2026-10-14-offer",
        covers=[*THREAD, MAIL],
        lines=[
            f"- Ben opened the thread. [r:{THREAD[0]}]",
            f"- Cleo's offer stands at 10 million [r:{MAIL.replace('%', '%25').replace(']', '%5D')}#2]",
        ],
    )
    assert recall.doc_put(vault, summary).rev == 1


# -- two machines -----------------------------------------------------------------------------------------------------


def test_two_machines_that_wrote_the_same_rev_dont_rebase(vault: Vault, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(remote))
    lines = [f"- Point {i} about the consultant. @12:08" for i in range(1, 11)]
    recall.doc_put(vault, note(lines=lines))
    sh(vault.root, "git", "remote", "add", "origin", str(remote))
    sh(vault.root, "git", "push", "-q", "-u", "origin", "main")
    sh(tmp_path, "git", "clone", "-q", str(remote), str(tmp_path / "other"))
    other = Vault.open(tmp_path / "other")

    def edited(n: int, where: str) -> list[str]:
        return [f"- Point {n}, corrected {where}. @12:08" if i == n else line for i, line in enumerate(lines, start=1)]

    # Different revs of different docs rebase as before.
    recall.doc_put(other, note("brief:ben-sample"))
    recall.doc_put(vault, note(rev="1", lines=edited(2, "here")))
    sh(vault.root, "git", "push", "-q", "origin", "main")
    assert other.sync().rebased
    assert [rev for rev, _ in DocStore(other).history(NOTE_ID)] == [2, 1]
    # Both machines write rev 3 on lines far apart: git would rebase it cleanly, Docket doesn't.
    recall.doc_put(vault, note(rev="2", lines=edited(3, "here")))
    sh(vault.root, "git", "push", "-q", "origin", "main")
    mine = recall.doc_put(other, note(rev="2", lines=edited(9, "there")))
    result = other.sync()
    assert (result.fetched, result.rebased) == (True, False)
    assert f"both wrote {NOTE_ID} rev 3; resolve it by hand" in result.message
    assert head(other) == mine.commit
    assert not (other.root / ".git" / "rebase-merge").exists()
    assert [rev for rev, _ in DocStore(other).history(NOTE_ID)] == [3, 2, 1]


# -- what a vault can hold ------------------------------------------------------------------------------------------


def test_a_rid_yaml_cant_hold_raw_is_written_escaped_and_reads_back(vault: Vault) -> None:
    rid = "mail:<a\x7fb\x9b" + chr(0xFFFE) + "@example.invalid>"
    vault.write_run("mail", [make_record(rid=rid, source="mail", kind="mail", text="junk offer")], run_at=at(12))
    text = note(
        "digest:2026-10-14-junk", covers=[double_quoted(rid)], lines=[f"- A junk offer arrived. {format_anchor(rid)}"]
    )
    recall.doc_put(vault, text)
    stored = (vault.root / "processed/digest/2026/10/2026-10-14-junk.md").read_text(encoding="utf-8")
    assert '"mail:<a\\u007fb\\u009b\\ufffe@example.invalid>"' in stored
    got = recall.read(vault, "digest:2026-10-14-junk").doc
    assert got is not None
    assert got.covers == [rid]
    again = note(
        "digest:2026-10-14-junk", covers=[double_quoted(rid)], rev="1", lines=[f"- It came in. {format_anchor(rid)}"]
    )
    assert recall.doc_put(vault, again).rev == 2


@pytest.mark.parametrize(
    "broken",
    [lambda value, context: value, lambda value, context: double_quoted(value + "x")],
    ids=["unquoted", "changed"],
)
def test_a_frontmatter_that_wouldnt_read_back_is_refused(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, broken: Callable[[str, str], str]
) -> None:
    rid = "mail:<a\x7fb@example.invalid>"
    vault.write_run("mail", [make_record(rid=rid, source="mail", kind="mail", text="junk")], run_at=at(12))
    monkeypatch.setattr(processed, "_scalar", broken)
    problems = refused(
        vault, note("digest:2026-10-14-junk", covers=[double_quoted(rid)], lines=[f"- junk {format_anchor(rid)}"])
    )
    assert problems == [
        "doc: the frontmatter can't be written so that it reads back the same; a value holds what YAML can't"
    ]


def test_doc_blobs_dont_go_through_git_show(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    real = vault.git.run

    def run(*args: str, **kwargs: Any) -> Any:
        if args and args[0] == "show":
            raise DocketError("git show: the path is too long")  # what Windows says past 260 characters
        return real(*args, **kwargs)

    monkeypatch.setattr(vault.git, "run", run)
    recall.doc_put(vault, note())
    hand_edit(vault, "10 million forints", "ten million forints")
    assert any("is at rev 2 now" in p for p in refused(vault, note(rev="1", lines=["- stale @12:08"])))
    assert "10 million forints" in DocStore(vault).at_rev(NOTE_ID, 1).body


def test_signed_commits_shown_with_their_signatures_dont_confuse_the_history(vault: Vault, tmp_path: Path) -> None:
    keygen = shutil.which("ssh-keygen")
    if keygen is None:
        pytest.skip("no ssh-keygen to sign with")
    key = tmp_path / "key"
    subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True, capture_output=True)
    allowed = tmp_path / "allowed"
    public = (tmp_path / "key.pub").read_text(encoding="utf-8").strip()
    allowed.write_text(f"ada@example.invalid {public}\n", encoding="utf-8")
    settings = {
        "gpg.format": "ssh",
        "gpg.ssh.allowedSignersFile": allowed.as_posix(),
        "user.signingkey": key.as_posix(),
        "commit.gpgsign": "true",
        "log.showSignature": "true",
    }
    for name, value in settings.items():
        sh(vault.root, "git", "config", name, value)
    probe = ["git", "-c", "user.name=Ada", "-c", "user.email=ada@example.invalid", "commit", "-q", "--allow-empty"]
    if subprocess.run([*probe, "-m", "probe"], cwd=vault.root, capture_output=True, check=False).returncode != 0:
        pytest.skip("this git can't sign with ssh")
    first = recall.doc_put(vault, note())
    assert len(sh(vault.root, "git", "log", "-1", "--format=%H").splitlines()) > 1  # the signature shows
    assert recall.doc_put(vault, note(rev="1", lines=["- signed again @12:08"])).rev == 2
    assert DocStore(vault).history(NOTE_ID)[1] == (1, first.commit)
    assert (vault.git.last_commit() or "").endswith(f"Doc: {NOTE_ID} rev 2")


def test_a_put_during_a_stash_pop_conflict_is_refused_and_commits_nothing(vault: Vault) -> None:
    recall.doc_put(vault, note())
    hand_edit(vault, "10 million forints", "ten million forints, said Ada")
    sh(vault.root, "git", "-c", "user.name=Ada", "-c", "user.email=ada@example.invalid", "stash", "-q")
    recall.doc_put(vault, note(rev="1", lines=["- Ben put the consultant at 12 million forints. @12:08"]))
    sh(vault.root, "git", "stash", "pop", check=False)
    assert sh(vault.root, "git", "ls-files", "-u")
    before = head(vault)
    with pytest.raises(DocketError, match=f"{NOTE_PATH} in .* has a conflict; resolve it before Docket writes"):
        recall.doc_put(vault, note(rev="2", lines=["- Ben put the consultant at 12 million. @12:08"]))
    assert head(vault) == before
    assert sh(vault.root, "git", "ls-files", "-u")
    with pytest.raises(DocketError, match="has a conflict"):
        vault.commit([], "anything")
