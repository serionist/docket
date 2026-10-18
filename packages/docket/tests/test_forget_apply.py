"""The apply (DR-0581, DR-0595): the forgotten list against this clone's tree, staging and key store, in one commit; a
crash at each step finished by the next gate call, with nothing of the family read meanwhile; a line a union merge
brought back taken out again; a key no line named put on a keys-only line; and no run file read when nothing moved.
Lines are written by hand here, as `docket forget` writes them; invented data only."""

from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import git, make_record
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


@pytest.mark.parametrize("step", ["keys", "staging", "recorded", "written", "committed"])
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
