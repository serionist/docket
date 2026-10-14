"""Committing in a folder of the user's repo (the host): only Docket's paths, never what the user staged or changed, the
host's hooks and locks left alone, and nothing ever done to the host's remote."""

from __future__ import annotations

import os
import shutil
import stat
import threading
import time
from pathlib import Path

import pytest
from docket_testkit import REC, folder_vault, git, make_config, make_record, note, sh, users_work, write_raw

from docket.cli import main
from docket.errors import DocketError
from docket.imports.notes import import_notes
from docket.vault import git as gitmod
from docket.vault import holds, host
from docket.vault.config import CONFIG_FILE, SourceConfig, VaultConfig
from docket.vault.docstore import DocStore
from docket.vault.vault import Vault

DOC = "recording-note:2026-10-14-supplier-call"
HELD = "of_00c0ffee00c0ffee00c0ffee00c0ffee"


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="needs git-lfs")
def test_docket_s_own_media_commit_keeps_git_lfs_out_of_an_in_tree_hooks_folder(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    git(top, "config", "core.hooksPath", ".husky")
    (top / ".husky").mkdir()
    (top / ".husky" / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    git(top, "lfs", "install", "--local", "--skip-repo")
    media = vault.root / "media" / "s" / "2026" / "10" / "a.age"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"encrypted audio")
    vault.commit(["media/s/2026/10/a.age"], "Media: a")
    assert "version https://git-lfs" in git(top, "cat-file", "blob", "HEAD:notes/vault/media/s/2026/10/a.age")
    assert sorted(p.name for p in (top / ".husky").iterdir()) == ["pre-commit"]
    assert host.new_hooks(top) == [".husky/pre-commit"]


def test_hold_lists_in_a_folder_vault(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    before = users_work(top)
    assert holds.add(vault, "calls", [HELD]) == 1
    assert files_in(top) == ["notes/vault/state/holds/calls.txt"]
    assert not (vault.root / ".git").exists()
    # A vault made before hold lists: the merge rule goes into the vault's own .gitattributes, committed with the list.
    attrs = vault.root / ".gitattributes"
    attrs.write_text(attrs.read_text(encoding="utf-8").replace(f"{holds.UNION}\n", ""), encoding="utf-8")
    git(top, "commit", "-qm", "older", "--", str(attrs))
    assert holds.add(vault, "chat", [HELD]) == 1
    assert sorted(files_in(top)) == ["notes/vault/.gitattributes", "notes/vault/state/holds/chat.txt"]
    merge = git(top, "check-attr", "merge", "--", "notes/vault/state/holds/chat.txt")
    assert merge.strip().endswith("merge: union")
    assert users_work(top)[1] == before[1]
    assert vault.check() == []


def test_the_notes_import_skips_a_folder_vault_inside_the_notes(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    (top / "notes" / "calls").mkdir()
    (top / "notes" / "calls" / "2026-09-10-supplier-call.md").write_text(
        "# Supplier call\n\nFirst.\n", encoding="utf-8"
    )
    git(top, "add", "notes/calls")
    report = import_notes(vault, top / "notes")
    assert report.new == 1
    docs = git(top, "show", "--name-only", "--format=", "HEAD").split()
    assert docs == ["notes/vault/processed/legacy-note/2026/09/2026-09-10-supplier-call.md"]
    assert "notes/calls/2026-09-10-supplier-call.md" in git(top, "diff", "--cached", "--name-only")


def test_storage_and_a_source_s_keys_sit_together_in_vault_yaml(tmp_path: Path) -> None:
    config = make_config("folder")
    config.sources["calls"] = SourceConfig(
        sensitivity="mixed",
        mapper=".claude/skills/sources/calls/mapper.py",
        tools=["get_transcript"],
        media_hosts=["media.example.invalid"],
    )
    (tmp_path / CONFIG_FILE).write_text(config.dump(), encoding="utf-8")
    loaded = VaultConfig.load(tmp_path / CONFIG_FILE)
    assert loaded.storage == "folder"
    assert loaded.sources["calls"].mapper == ".claude/skills/sources/calls/mapper.py"
    assert loaded == config


def files_in(top: Path, commit: str = "HEAD") -> list[str]:
    return git(top, "show", "--name-only", "--format=", commit).split()


def test_runs_and_docs_commit_only_docket_s_paths(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    before = users_work(top)
    write_raw(vault.root)
    vault.commit([], "Raw: the fixture")
    assert all(f.startswith("notes/vault/raw/") for f in files_in(top))
    store = DocStore(vault)
    first = store.put(note())
    assert files_in(top) == [f"notes/vault/{first.path}"]
    # A hand edit is taken in as the next rev before the put; still only the doc's own path.
    path = vault.root / first.path
    path.write_text(path.read_text(encoding="utf-8").replace("10 million", "ten million"), encoding="utf-8")
    second = store.put(note(lines=["- Ben put the consultant at 10 million forints, for now. @12:08"], rev="2"))
    assert second.taken_in == 2
    assert second.rev == 3
    assert users_work(top) == before
    assert "notes/vault/processed/mine.md" in git(top, "diff", "--cached", "--name-only").split()


def test_doc_revisions_read_from_the_vault_s_folder(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    write_raw(vault.root)
    vault.commit([], "Raw: the fixture")
    store = DocStore(vault)
    store.put(note())
    store.put(note(lines=["- Ben put the consultant at 10 million forints, for the quarter. @12:08"], rev="1"))
    assert [rev for rev, _ in store.history(DOC)] == [2, 1]
    assert "10 million forints. @12:08" in store.at_rev(DOC, 1).render(rev=1)


def write_hook(top: Path, name: str, body: str) -> None:
    hook = top / ".git" / "hooks" / name
    hook.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)


def test_the_host_s_hooks_never_run_for_docket_s_commits(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    write_hook(top, "pre-commit", 'for f in $(git diff --cached --name-only); do echo touched >> "$f"; done')
    write_hook(top, "commit-msg", "exit 1")
    vault.write_run("chat", [make_record(1)])
    run = next((vault.root / "raw").rglob("*.jsonl"))
    assert "touched" not in run.read_text(encoding="utf-8")
    assert files_in(top) == [f"notes/vault/{run.relative_to(vault.root).as_posix()}"]
    # The user's own commit still runs them.
    with pytest.raises(AssertionError):
        git(top, "commit", "-qm", "mine")


@pytest.mark.parametrize("state", ["MERGE_HEAD", "rebase-merge", "detached"])
def test_a_host_mid_operation_blocks_docket_s_writes(tmp_path: Path, state: str) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    if state == "detached":
        git(top, "checkout", "-q", "--detach")
    elif state == "MERGE_HEAD":
        (top / ".git" / state).write_text(git(top, "rev-parse", "HEAD"), encoding="utf-8")
    else:
        (top / ".git" / state).mkdir()
    head = git(top, "rev-parse", "HEAD")
    with pytest.raises(DocketError, match=str(top).replace("\\", "\\\\")):
        vault.write_run("chat", [make_record(1)])
    assert git(top, "rev-parse", "HEAD") == head
    if state == "rebase-merge":
        assert (top / ".git" / state).is_dir()


def test_a_stash_pop_conflict_in_the_user_s_files_doesnt_block_docket(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    git(top, "commit", "-qm", "the user's staged work")
    (top / "README.md").write_text("stashed\n", encoding="utf-8")
    git(top, "stash", "-q")
    (top / "README.md").write_text("committed meanwhile\n", encoding="utf-8")
    git(top, "commit", "-qam", "meanwhile")
    sh(top, "git", "stash", "pop", check=False)
    assert git(top, "ls-files", "-u", "--", ":/").strip()
    vault.write_run("chat", [make_record(1)])
    assert files_in(top)[0].startswith("notes/vault/raw/")
    assert git(top, "ls-files", "-u", "--", ":/").strip()


def test_a_run_file_the_host_ignores_fails_loudly(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    (top / ".gitignore").write_text("raw/\n", encoding="utf-8")
    with pytest.raises(DocketError, match="ignored"):
        vault.write_run("chat", [make_record(1)])
    assert len(vault.uncommitted_runs()) == 1
    assert "raw/" in str(vault.check()[0])


def test_a_failed_commit_leaves_nothing_of_docket_s_staged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    real = gitmod.Git.run

    def failing(self: gitmod.Git, *args: str, **kw: object) -> object:
        if "commit" in args:
            raise DocketError("git commit: the disk is full")
        return real(self, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(gitmod.Git, "run", failing)
    with pytest.raises(DocketError, match="disk is full"):
        vault.write_run("chat", [make_record(1)])
    monkeypatch.undo()
    staged = git(top, "diff", "--cached", "--name-only").split()
    assert not any(p.startswith("notes/vault/raw/") for p in staged)
    assert vault.uncommitted_runs()


def test_a_run_file_a_crash_left_staged_goes_in_with_the_next_commit(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    vault.rawlog.write_run("chat", [make_record(1)])
    git(top, "add", "notes/vault/raw")
    assert vault.git.untracked("raw") == []
    assert len(vault.uncommitted_runs()) == 1
    vault.write_run("chat", [make_record(2)])
    assert len([f for f in files_in(top) if f.startswith("notes/vault/raw/")]) == 2
    assert vault.uncommitted_runs() == []


def test_a_fresh_host_index_lock_is_waited_for(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    lock = vault.home.git_dir / "index.lock"
    lock.write_text("", encoding="utf-8")
    threading.Timer(0.5, lock.unlink).start()
    assert vault.write_run("chat", [make_record(1)]) is not None


def test_a_host_index_lock_that_stays_is_named_and_never_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gitmod, "INDEX_LOCK_WAIT_SECONDS", 0.3)
    vault = folder_vault(tmp_path)
    lock = vault.home.git_dir / "index.lock"
    lock.write_text("", encoding="utf-8")
    old = time.time() - 20 * 60
    os.utime(lock, (old, old))
    with pytest.raises(DocketError, match=r"index\.lock is 20 minute\(s\) old"):
        vault.write_run("chat", [make_record(1)])
    assert lock.exists()
    lock.unlink()
    assert vault.git._index_lock_age() == ""  # pyright: ignore[reportPrivateUsage]


def test_docket_commits_on_the_checked_out_branch_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    git(top, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    assert vault.status().branch_warning is None
    git(top, "checkout", "-q", "-b", "feature")
    vault.write_run("chat", [make_record(1)])
    assert git(top, "branch", "--contains", "HEAD").split() == ["*", "feature"]
    status = vault.status()
    assert status.storage == "folder"
    assert status.branch == "feature"
    assert status.branch_warning is not None
    assert "not main" in status.branch_warning
    assert main(["vault", "status", "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert f"a folder of {top}" in out
    assert "warning  Docket commits on feature, not main" in out


def test_sync_in_a_folder_touches_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", str(tmp_path / "origin.git"), str(other))
    (other / "upstream.txt").write_text("x\n", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "Upstream work")
    git(other, "push", "-q", "origin", "main")
    refs = git(top, "for-each-ref")
    index = (top / ".git" / "index").read_bytes()
    assert main(["sync", "--vault", str(vault.root)]) == 0
    assert "your own git (commit, pull, push) moves it" in capsys.readouterr().out
    assert git(top, "for-each-ref") == refs
    assert git(top, "stash", "list") == ""
    assert (top / ".git" / "index").read_bytes() == index


def test_sync_in_a_repo_of_its_own_exits_1_on_a_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = Vault.init(tmp_path / "own", make_config(), "test-box")
    monkeypatch.setattr(
        gitmod.Git, "sync", lambda _self: gitmod.SyncResult(True, False, "conflicted; resolve it by hand")
    )
    assert main(["sync", "--vault", str(vault.root)]) == 1
    assert "resolve it by hand" in capsys.readouterr().out
    monkeypatch.undo()
    assert main(["sync", "--vault", str(vault.root)]) == 0
    assert "no remote" in capsys.readouterr().out


def test_two_commits_for_one_doc_rev_are_reported(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    write_raw(vault.root)
    vault.commit([], "Raw: the fixture")
    first = DocStore(vault).put(note())
    # What a merge of two branches that each wrote rev 1 leaves: a second commit claiming it.
    doc = vault.root / first.path
    doc.write_text(doc.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    git(top, "commit", "-qm", f"Doc: {DOC} rev 1", "--", str(doc))
    problems = [str(p) for p in vault.check()]
    assert len(problems) == 1
    assert problems[0].startswith(f"{DOC}: rev 1 is claimed by 2 commits")
    assert DocStore(vault).put(note(lines=["- A third take. @12:08"], rev="1")).rev == 2


def test_check_names_an_ignore_rule_that_hides_the_vault(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    (top / ".gitignore").write_text("*.jsonl\n!/notes/vault/ledger/**\n", encoding="utf-8")
    problems = [str(p) for p in vault.check()]
    assert problems == [
        "raw/s/2026/01/2026-01-01T0000-m.jsonl: an ignore rule hides it from git (.gitignore:1: *.jsonl)"
    ]
    (top / ".gitignore").write_text("/notes/vault/\n", encoding="utf-8")
    problems = [str(p) for p in vault.check()]
    assert len(problems) == 1
    assert "and 8 more of the vault's paths" in problems[0]
    assert problems[0].endswith("took this line out of the repo's .gitignore: commit that edit")


def test_the_host_s_attributes_stop_at_the_vault(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    (top / ".gitattributes").write_text("*.jsonl filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8")
    assert vault.check() == []
    raw = git(vault.root, "check-attr", "filter", "merge", "--", "raw/a/2026/01/x.jsonl")
    assert "filter: unspecified" in raw
    assert "merge: union" in raw
    vault.write_run("chat", [make_record(1)])
    run = next(f for f in files_in(top) if f.startswith("notes/vault/raw/"))
    assert git(top, "cat-file", "blob", f"HEAD:{run}").startswith("{")
    # Only .git/info/attributes beats the vault's own file, and check says so.
    (top / ".git" / "info").mkdir(exist_ok=True)
    (top / ".git" / "info" / "attributes").write_text("*.age -filter\n", encoding="utf-8")
    problems = [str(p) for p in vault.check()]
    assert any(p.startswith("media/s/2026/01/x.age: git gives it filter: unset") for p in problems)


def test_a_record_read_in_a_folder_vault(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    write_raw(vault.root)
    vault.commit([], "Raw: the fixture")
    reopened = Vault.open(vault.root / "raw")
    assert reopened.rawlog.latest(REC) is not None
    assert reopened.status().last_commit is not None
    assert "Raw: the fixture" in (reopened.status().last_commit or "")
