"""The apply (DR-0581, DR-0595, DR-0597): the forgotten list against this clone's tree, staging and key store, in one
commit; a crash at each step finished by the next gate call, with nothing of the family read meanwhile, no file or key
gone before its path is on record; the tree waiting while git is in the middle of something, keys and staging gone; a
line a union merge or a pull brought back taken out again; a record's line separators; a key no line named put on a
keys-only line; and no run file, doc or media file read when nothing moved. Lines are written by hand here, as
`docket forget` writes them; invented data only."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from docket_testkit import git, make_record, sh
from forget_kit import forget, line, stored
from media_kit import CALL, POLISHED, audio, call, in_raw, media_vault

from docket.errors import DocketError
from docket.forget import apply as forget_apply
from docket.media import layout, reader
from docket.media import put as media
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
from docket.vault import cursors, forgotten
from docket.vault.cursors import Cursor, Retry, Waiting
from docket.vault.gate import Gate
from docket.vault.staging import Staging, rid_hash
from docket.vault.vault import Vault

CHAT = [make_record(n, source="chat") for n in (1, 2)]


def put(vault: Vault) -> None:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(audio(4000))
    media.put(vault, CALL, path=str(drop / "take"))


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    """The call in raw with its audio stored and its key here, and two chat messages beside it."""
    found = media_vault(tmp_path)
    put(found)
    in_raw(found)
    found.write_run("chat", CHAT)
    return found


def key_of(vault: Vault) -> str:
    (path,) = layout.stored_files(vault.root, CALL)
    found = media_staged.key_of(path)
    assert found is not None
    return found


def lines_of(vault: Vault, source: str) -> list[str]:
    return [line for run in vault.rawlog.run_files() if run.source == source for line in _read(run.path)]


def _read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def subjects(vault: Vault) -> list[str]:
    return git(vault.root, "log", "--format=%s").splitlines()


def clean(vault: Vault) -> bool:
    return git(vault.root, "status", "--porcelain", "--", ".").strip() == ""


def forget_call(vault: Vault) -> str:
    key = key_of(vault)
    forget(vault, rids=[CALL], media=[CALL], keys=[key])
    return key


# -- each part -----------------------------------------------------------------------------------------------------


def test_the_apply_takes_the_family_out_in_one_commit(vault: Vault) -> None:
    key = forget_call(vault)
    (run,) = [r.path for r in vault.rawlog.run_files() if r.source == "plaud"]
    done = forget_apply.apply(vault, KeyStore(vault.home), first=True)
    assert (done.records, done.media, done.keys, done.docs) == (2, 1, 1, 0)
    assert subjects(vault)[:2] == ["Forget: 2 record(s), 1 media file(s), 0 doc(s) from test-box",
                                   "Forget: the list from test-box"]  # fmt: skip
    # The run file left with no line stays, empty, so another clone's rewrite of it merges.
    assert run.exists() and run.read_bytes() == b""
    assert not layout.stored_files(vault.root, CALL)
    assert not KeyStore(vault.home).has(key)
    assert len(lines_of(vault, "chat")) == 2
    assert clean(vault)
    head = git(vault.root, "ls-tree", "-r", "--name-only", "HEAD", "--", "media")
    assert "media/plaud/" not in head
    # Idempotent: a second run finds nothing.
    again = forget_apply.apply(vault, KeyStore(vault.home))
    assert (again.records, again.media, again.commit) == (0, 0, None)


def test_a_key_no_line_names_goes_on_a_keys_only_line_that_moves_no_index(vault: Vault) -> None:
    key = key_of(vault)
    forget(vault, rids=[CALL], media=[CALL])
    digest = forgotten.load(vault.root, vault.home).digest
    done = forget_apply.apply(vault, KeyStore(vault.home))
    assert done.listed_keys == 1
    after = forgotten.load(vault.root, vault.home)
    assert key in after.keys
    assert after.digest == digest
    assert subjects(vault)[0] == "Forget: applied on test-box"
    assert git(vault.root, "show", "--name-only", "--format=", "HEAD").count(forgotten.FORGOTTEN) == 1
    assert clean(vault)


def test_a_version_forget_takes_its_lines_and_its_file_by_its_key_and_withholds_nothing(vault: Vault) -> None:
    key = key_of(vault)
    forget(vault, versions=vault.rawlog.read(POLISHED), keys=[key])
    done = forget_apply.apply(vault, KeyStore(vault.home))
    assert (done.records, done.media, done.listed_keys) == (1, 1, 0)
    assert vault.rawlog.read(CALL) != []
    assert media_staged.read_withheld(vault.root) == frozenset()


def test_staged_media_and_versions_of_the_family_go_with_their_keys(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault)
    assert KeyStore(vault.home).ids()
    forget(vault, rids=[CALL], media=[CALL])
    done = forget_apply.apply(vault, KeyStore(vault.home))
    assert (done.staged, done.staged_media) == (2, 1)
    assert Staging(vault.root, vault.home).scan().entries == []
    assert KeyStore(vault.home).ids() == []
    assert rid_hash(CALL) in media_staged.read_withheld(vault.root)


def test_this_machine_s_cursor_entries_on_a_forgotten_rid_go(vault: Vault) -> None:
    since = CHAT[0].ts.date()
    cursor = Cursor(
        retry={"rec-a": Retry("rec-a", "gone", since), "rec-b": Retry("rec-b", "gone", since)},
        waiting={"rec-a/transaction_polish": Waiting("rec-a/transaction_polish", "transcript", since)},
    )
    cursors.save(vault, "plaud", cursor)
    forget_call(vault)
    forget_apply.apply(vault, KeyStore(vault.home))
    left = cursors.read(vault.root, vault.machine)["plaud"]
    assert set(left.retry) == {"rec-b"} and left.waiting == {}
    assert clean(vault)


# -- crashes and merges -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("step", ["keys", "staging", "media", "recorded", "written", "committed"])
def test_a_crash_at_each_step_is_finished_by_the_next_gate_call(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    key = forget_call(vault)

    def crash(at: str) -> None:
        if at == step:
            raise KeyboardInterrupt

    monkeypatch.setattr(forget_apply, "_reached", crash)
    with pytest.raises(KeyboardInterrupt):
        forget_apply.apply(vault, KeyStore(vault.home), first=True)
    # Meanwhile nothing of the family reads, whatever the files say.
    assert vault.rawlog.read(CALL) == [] and vault.rawlog.read(POLISHED) == []
    assert reader.state_of(vault, CALL) == "forgotten"
    monkeypatch.setattr(forget_apply, "_reached", lambda at: None)
    Gate(vault).list()
    assert clean(vault)
    assert not layout.stored_files(vault.root, CALL)
    assert not KeyStore(vault.home).has(key)
    assert not any(CALL in line for line in lines_of(vault, "plaud"))
    assert vault.check() == []


def test_a_crash_before_the_list_s_commit_is_committed_then_applied(vault: Vault) -> None:
    forgotten.write_line(vault, line(rids=[CALL], media=[CALL]))
    assert vault.rawlog.read(CALL) == []
    Gate(vault).list()
    assert subjects(vault)[:2] == ["Forget: applied on test-box", "Forget: the list committed on test-box"]
    assert clean(vault)


def test_a_commit_that_fails_leaves_the_files_for_the_next_call(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    forget_call(vault)
    real = Vault.commit
    calls: list[str] = []

    def failing(self: Vault, paths: list[str], message: str) -> str | None:
        calls.append(message)
        if message.startswith("Forget: 2 record(s)"):
            raise DocketError("a rebase is in progress")
        return real(self, paths, message)

    monkeypatch.setattr(Vault, "commit", failing)
    with pytest.raises(DocketError, match="rebase"):
        forget_apply.apply(vault, KeyStore(vault.home), first=True)
    assert not clean(vault)
    assert any("a forget took listed lines out" in str(p) for p in vault.check())
    monkeypatch.setattr(Vault, "commit", real)
    forget_apply.apply(vault, KeyStore(vault.home))
    assert clean(vault)
    assert vault.check() == []


def test_a_line_a_merge_brought_back_reads_as_gone_and_goes_at_the_next_call(vault: Vault) -> None:
    forget_call(vault)
    (run,) = [r.path for r in vault.rawlog.run_files() if r.source == "plaud"]
    before = run.read_text(encoding="utf-8")
    forget_apply.apply(vault, KeyStore(vault.home))
    # A union merge of two forgets of neighbouring lines brings this one back, committed by the merge.
    run.write_text(before.splitlines(keepends=True)[0], encoding="utf-8", newline="\n")
    rel = run.relative_to(vault.root).as_posix()
    git(vault.root, "commit", "-q", "-m", "Merge another clone's forget", "--", rel)
    assert vault.rawlog.read(CALL) == []
    Gate(vault).list()
    assert run.read_bytes() == b""
    assert subjects(vault)[0] == "Forget: applied on test-box"
    assert clean(vault)


def test_nothing_moved_reads_no_run_file(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    forget_call(vault)
    forget_apply.apply(vault, KeyStore(vault.home))
    read: list[str] = []
    real = Path.read_bytes

    def watched(path: Path) -> bytes:
        if "raw" in path.parts:
            read.append(path.name)
        return real(path)

    monkeypatch.setattr(Path, "read_bytes", watched)
    done = forget_apply.apply(vault, KeyStore(vault.home))
    assert read == [] and done.commit is None
    # A new run file moves it: only that file is read.
    vault.write_run("chat", [make_record(3, source="chat")])
    forget_apply.apply(vault, KeyStore(vault.home))
    assert len(read) == 1


def test_a_list_that_lacks_a_line_head_has_still_applies_and_is_never_committed(vault: Vault) -> None:
    forget_call(vault)
    forget_apply.apply(vault, KeyStore(vault.home))
    path = vault.root / forgotten.FORGOTTEN
    path.write_text("", encoding="utf-8")
    vault.write_run("plaud", [stored(call()[0])])
    assert vault.rawlog.read(CALL) == []
    forget_apply.apply(vault, KeyStore(vault.home))
    assert not any(CALL in line for line in lines_of(vault, "plaud"))
    assert path.read_text(encoding="utf-8") == ""
    assert any("lacks 1 line(s)" in str(p) for p in vault.check())


def test_no_list_costs_nothing(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    assert forget_apply.apply(vault, KeyStore(vault.home)) == forget_apply.Applied()
    assert not forget_apply.state_path(vault.home).exists()


def test_applied_state_that_doesn_t_read_is_read_again_whole(vault: Vault) -> None:
    forget_call(vault)
    forget_apply.apply(vault, KeyStore(vault.home))
    assert forget_apply.skipped(vault.home) == frozenset({rid_hash(CALL), rid_hash(POLISHED)})
    forget_apply.state_path(vault.home).write_text("{not json", encoding="utf-8")
    assert forget_apply.skipped(vault.home) == frozenset()
    done = forget_apply.apply(vault, KeyStore(vault.home))
    assert (done.records, done.commit) == (0, None)


def test_staged_media_on_a_forgotten_rid_goes_with_its_record_in_raw(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    put(vault)
    assert layout.staged_files(vault.root, CALL) and KeyStore(vault.home).ids()
    forget(vault, rids=[POLISHED], media=[POLISHED, CALL])
    done = forget_apply.apply(vault, KeyStore(vault.home))
    assert (done.staged, done.staged_media) == (0, 1)
    assert not layout.staged_files(vault.root, CALL) and KeyStore(vault.home).ids() == []


def test_a_stored_lfs_pointer_goes_with_every_key_the_cut_log_names(vault: Vault) -> None:
    key = key_of(vault)
    (path,) = layout.stored_files(vault.root, CALL)
    path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 9\n",
                    encoding="utf-8")  # fmt: skip
    forget(vault, rids=[CALL], media=[CALL])
    done = forget_apply.apply(vault, KeyStore(vault.home))
    assert done.media == 1 and not path.exists()
    assert not KeyStore(vault.home).has(key)
    assert key in forgotten.load(vault.root, vault.home).keys


def test_applied_state_a_later_docket_wrote_is_read_again_whole(vault: Vault) -> None:
    forget_call(vault)
    forget_apply.apply(vault, KeyStore(vault.home))
    path = forget_apply.state_path(vault.home)
    path.write_text(path.read_text(encoding="utf-8").replace('"schema_version":1', '"schema_version":2'),
                    encoding="utf-8")  # fmt: skip
    read: list[str] = []
    real = Path.read_bytes

    def watched(found: Path) -> bytes:
        if "raw" in found.parts:
            read.append(found.name)
        return real(found)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "read_bytes", watched)
        forget_apply.apply(vault, KeyStore(vault.home))
    assert len(read) == len(vault.rawlog.run_files())


def test_a_forget_of_a_message_leaves_the_audio_and_a_line_that_isn_t_a_record(vault: Vault) -> None:
    (run,) = [r.path for r in vault.rawlog.run_files() if r.source == "chat"]
    forget(vault, rids=[CHAT[0].rid])
    forget_apply.apply(vault, KeyStore(vault.home))
    assert layout.stored_files(vault.root, CALL) and len(lines_of(vault, "chat")) == 1
    # A line no reader takes for a record stays where it is: only a listed line ever leaves a run file.
    with run.open("a", encoding="utf-8", newline="\n") as f:
        f.write("not a record\n")
    forget_apply.apply(vault, KeyStore(vault.home))
    assert _read(run)[-1] == "not a record"
    assert layout.stored_files(vault.root, CALL)


# -- a record's line separators ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
def test_a_listed_record_whose_text_holds_a_line_separator_leaves_its_run_file(tmp_path: Path, separator: str) -> None:
    vault = media_vault(tmp_path, store=False)
    odd = make_record(1, source="chat", text=f"first part{separator}second part")
    plain = make_record(2, source="chat")
    kept = make_record(3, source="chat", text=f"kept{separator}as it is")
    vault.write_run("chat", [odd, plain, kept])
    (run,) = [r.path for r in vault.rawlog.run_files() if r.source == "chat"]
    # The record's JSON holds the separator as it is, which str.splitlines() would split the line at.
    assert separator in run.read_text(encoding="utf-8")
    forget(vault, rids=[odd.rid, plain.rid])
    done = forget_apply.apply(vault, KeyStore(vault.home), first=True)
    assert done.records == 2
    (left,) = _read_bytes_lines(run)
    assert kept.rid in left and separator in left
    assert clean(vault) and vault.check() == []


def _read_bytes_lines(path: Path) -> list[str]:
    return [line for line in path.read_bytes().decode("utf-8").split("\n") if line]


def test_vault_check_names_an_unfinished_forget_whose_kept_line_holds_a_separator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = media_vault(tmp_path, store=False)
    kept = make_record(1, source="chat", text="first part\u2028second part")
    gone = make_record(2, source="chat")
    vault.write_run("chat", [kept, gone])
    forget(vault, rids=[gone.rid])
    real = Vault.commit

    def failing(self: Vault, paths: list[str], message: str) -> str | None:
        if message.startswith("Forget: 1 record"):
            raise DocketError("a commit refused")
        return real(self, paths, message)

    monkeypatch.setattr(Vault, "commit", failing)
    with pytest.raises(DocketError, match="refused"):
        forget_apply.apply(vault, KeyStore(vault.home), first=True)
    problems = [str(p) for p in vault.check()]
    assert len(problems) == 1 and "a forget took listed lines out and isn't committed yet" in problems[0]


def test_vault_check_keeps_a_head_line_that_isn_t_a_record(tmp_path: Path) -> None:
    vault = media_vault(tmp_path, store=False)
    vault.write_run("chat", [make_record(1, source="chat"), make_record(2, source="chat")])
    (run,) = [r.path for r in vault.rawlog.run_files() if r.source == "chat"]
    rel = run.relative_to(vault.root).as_posix()
    good = run.read_text(encoding="utf-8")
    # A line a hand edit committed that isn't a record, then taken out on disk.
    run.write_text(good + "{oops\n", encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qm", "A hand edit", "--", rel)
    run.write_text(good, encoding="utf-8", newline="\n")
    forget(vault, rids=["chat:C0TEST/1791000099.000100"])
    problems = [str(p) for p in vault.check()]
    assert any(p.startswith(rel) and "changed or deleted after it was committed" in p for p in problems)


# -- git in the middle of something ------------------------------------------------------------------------------------


def _conflict(vault: Vault) -> None:
    """An unmerged path, as a stash pop that conflicts leaves one: stages 1 to 3 and no *_HEAD file."""
    blob = sh(vault.root, "git", "hash-object", "-w", "--", "vault.yaml").strip()
    info = "".join(f"100644 {blob} {n}\tconflict.txt\n" for n in (1, 2, 3))
    proc = subprocess.run(["git", "update-index", "--index-info"], cwd=vault.root, input=info.encode(), check=False)
    assert proc.returncode == 0


@pytest.mark.parametrize("state", ["rebase-merge", "MERGE_HEAD", "detached", "unmerged"])
def test_the_tree_waits_for_the_owner_s_git_while_keys_and_staging_go(vault: Vault, state: str) -> None:
    Gate(vault).stage([call(text="The crane failed its load test.")[1]])
    assert Staging(vault.root, vault.home).scan().entries
    key = forget_call(vault)
    (stored_file,) = layout.stored_files(vault.root, CALL)
    git_dir = vault.home.git_dir
    branch = git(vault.root, "branch", "--show-current").strip()
    if state == "rebase-merge":
        (git_dir / state).mkdir()
    elif state == "MERGE_HEAD":
        (git_dir / state).write_text(git(vault.root, "rev-parse", "HEAD"), encoding="utf-8")
    elif state == "detached":
        git(vault.root, "checkout", "-q", "--detach")
    else:
        _conflict(vault)
    head = git(vault.root, "rev-parse", "HEAD")
    # A gate call that only reads goes on, and so does every read: nothing of the family shows.
    listing = Gate(vault).list()
    assert all(e.staged.record.rid != POLISHED for e in listing.entries)
    done = forget_apply.apply(vault, KeyStore(vault.home))
    assert done.waits is not None and done.records == 0
    with pytest.raises(DocketError):
        forget_apply.apply(vault, KeyStore(vault.home), first=True)
    # The keys and staging are this clone's own: gone at once. The tree is the owner's: as it was.
    assert not KeyStore(vault.home).has(key)
    assert Staging(vault.root, vault.home).scan().entries == []
    assert stored_file.exists() and any(CALL in line for line in lines_of(vault, "plaud"))
    assert git(vault.root, "rev-parse", "HEAD") == head
    assert vault.rawlog.read(CALL) == []
    if state == "rebase-merge":
        (git_dir / state).rmdir()
    elif state == "MERGE_HEAD":
        (git_dir / state).unlink()
    elif state == "detached":
        git(vault.root, "checkout", "-q", branch)
    else:
        git(vault.root, "update-index", "--force-remove", "conflict.txt")
    Gate(vault).list()
    assert clean(vault) and not stored_file.exists()
    assert not any(CALL in line for line in lines_of(vault, "plaud"))


# -- the media step's order --------------------------------------------------------------------------------------------


def test_a_key_delete_that_fails_after_its_file_went_is_committed_by_the_next_call(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    (stored_file,) = layout.stored_files(vault.root, CALL)
    forget_call(vault)
    real = KeyStore.delete

    def failing(self: KeyStore, one: str) -> None:
        # The listed key goes first, its file still there; the media step's own delete fails, its file gone already.
        if not stored_file.exists():
            raise PermissionError("the key file is open in another process")
        real(self, one)

    monkeypatch.setattr(KeyStore, "delete", failing)
    with pytest.raises(PermissionError):
        forget_apply.apply(vault, KeyStore(vault.home), first=True)
    monkeypatch.setattr(KeyStore, "delete", real)
    assert not stored_file.exists()
    Gate(vault).list()
    assert clean(vault)
    assert "media/plaud/" not in git(vault.root, "ls-tree", "-r", "--name-only", "HEAD", "--", "media")


def test_a_kill_while_the_docs_are_judged_deletes_nothing_and_the_next_call_does_it_all(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    (stored_file,) = layout.stored_files(vault.root, CALL)
    forget_call(vault)

    def killed(*_: object, **__: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(forget_apply, "_docs", killed)
    with pytest.raises(KeyboardInterrupt):
        forget_apply.apply(vault, KeyStore(vault.home), first=True)
    monkeypatch.undo()
    # The stored file goes only once its path is on record for the commit.
    assert stored_file.exists()
    Gate(vault).list()
    assert clean(vault) and not stored_file.exists()
    assert "media/plaud/" not in git(vault.root, "ls-tree", "-r", "--name-only", "HEAD", "--", "media")


def test_a_version_forget_takes_a_stored_lfs_pointer_whose_every_logged_key_is_listed(vault: Vault) -> None:
    (path,) = layout.stored_files(vault.root, CALL)
    key = key_of(vault)
    # This clone never downloaded the object: the plan names its key from the cut log, as it does for a pointer.
    path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 9\n",
                    encoding="utf-8")  # fmt: skip
    git(vault.root, "commit", "-qam", "A pointer")
    assert forget_apply.logged_keys(vault.root, "plaud", path.name.split(".", 1)[0]) == {key}
    forget(vault, versions=vault.rawlog.read(POLISHED) + vault.rawlog.read(CALL), keys=[key])
    done = forget_apply.apply(vault, KeyStore(vault.home), first=True)
    assert done.media == 1 and not path.exists()
    assert clean(vault)


def test_a_pointer_with_a_logged_key_not_listed_stays(vault: Vault) -> None:
    (path,) = layout.stored_files(vault.root, CALL)
    path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 9\n",
                    encoding="utf-8")  # fmt: skip
    git(vault.root, "commit", "-qam", "A pointer")
    forget(vault, rids=[CHAT[0].rid], keys=["cd" * 32])
    forget_apply.apply(vault, KeyStore(vault.home))
    assert path.exists()


# -- stamps taken before the read --------------------------------------------------------------------------------------


def test_a_pull_landing_during_the_apply_is_read_again_by_the_next_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = media_vault(tmp_path, store=False)
    gone = make_record(1, source="chat")
    vault.write_run("chat", [gone, make_record(2, source="chat")])
    vault.write_run("chat", [make_record(3, source="chat")])
    later = [r.path for r in vault.rawlog.run_files() if r.source == "chat"][-1]
    forget(vault, rids=[gone.rid])
    real = forget_apply._docs

    def pull_lands(*args: object, **kwargs: object) -> object:
        found = real(*args, **kwargs)  # type: ignore[arg-type]
        # The owner's own pull lands meanwhile: a union merge brings the listed line into a run file already read.
        with later.open("a", encoding="utf-8", newline="\n") as f:
            f.write(stored(gone).model_copy(update={"run": later.stem}).to_json() + "\n")
        git(vault.root, "commit", "-qm", "Merge", "--", later.relative_to(vault.root).as_posix())
        return found

    monkeypatch.setattr(forget_apply, "_docs", pull_lands)
    forget_apply.apply(vault, KeyStore(vault.home), first=True)
    monkeypatch.setattr(forget_apply, "_docs", real)
    assert gone.rid in later.read_text(encoding="utf-8") and vault.rawlog.read(gone.rid) == []
    forget_apply.apply(vault, KeyStore(vault.home))
    assert gone.rid not in later.read_text(encoding="utf-8")
    assert clean(vault)


# -- what an unmoved list costs ----------------------------------------------------------------------------------------


def test_docs_and_media_are_walked_only_once_head_or_the_list_moves(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    forget_call(vault)
    forget_apply.apply(vault, KeyStore(vault.home))
    # The apply's own commit moved HEAD: the next call walks once more and keeps where HEAD is.
    forget_apply.apply(vault, KeyStore(vault.home))
    walks: list[str] = []
    real_docs, real_media = forget_apply._doc_stamps, forget_apply._media_stamps

    def docs(root: Path) -> object:
        walks.append("docs")
        return real_docs(root)

    def media_files(root: Path) -> object:
        walks.append("media")
        return real_media(root)

    monkeypatch.setattr(forget_apply, "_doc_stamps", docs)
    monkeypatch.setattr(forget_apply, "_media_stamps", media_files)
    done = forget_apply.apply(vault, KeyStore(vault.home))
    assert walks == [] and done.commit is None
    git(vault.root, "commit", "-q", "--allow-empty", "-m", "Something of the owner's")
    forget_apply.apply(vault, KeyStore(vault.home))
    assert walks == ["docs", "media"]
    forget_apply.apply(vault, KeyStore(vault.home))
    assert walks == ["docs", "media"]


def test_a_media_file_s_key_is_read_once_while_the_file_stays(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    read: list[str] = []
    real = media_staged.key_of

    def watched(path: Path) -> str | None:
        read.append(path.name)
        return real(path)

    monkeypatch.setattr(media_staged, "key_of", watched)
    # A list naming no key and no media reads no header.
    forget(vault, rids=[CHAT[0].rid])
    forget_apply.apply(vault, KeyStore(vault.home))
    assert read == []
    # A key listed: each stored file's header read once, then kept by its stamp.
    forget(vault, rids=[CHAT[1].rid], keys=["cd" * 32])
    forget_apply.apply(vault, KeyStore(vault.home))
    assert len(read) == 1
    forget(vault, keys=["ef" * 32])
    forget_apply.apply(vault, KeyStore(vault.home))
    assert len(read) == 1


def test_a_whole_rid_s_line_back_without_head_moving_has_the_docs_and_media_walked(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    (run,) = [r.path for r in vault.rawlog.run_files() if r.source == "plaud"]
    before = run.read_text(encoding="utf-8")
    forget_call(vault)
    forget_apply.apply(vault, KeyStore(vault.home))
    forget_apply.apply(vault, KeyStore(vault.home))
    walks: list[str] = []
    real = forget_apply._doc_stamps

    def docs(root: Path) -> object:
        walks.append("docs")
        return real(root)

    monkeypatch.setattr(forget_apply, "_doc_stamps", docs)
    # The line back on disk alone, as a checkout of an older run file leaves it: HEAD where it was.
    run.write_text(before.splitlines(keepends=True)[0], encoding="utf-8", newline="\n")
    done = forget_apply.apply(vault, KeyStore(vault.home))
    assert done.records == 1 and walks == ["docs"]
    assert run.read_bytes() == b"" and clean(vault)
