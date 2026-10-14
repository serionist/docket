from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import make_record, sh

from docket.errors import DocketError
from docket.vault.config import VaultConfig
from docket.vault.git import sync_marker
from docket.vault.lock import VaultLock, clear_stale_index_lock
from docket.vault.rawlog import RawLog
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

RUN_AT = datetime(2026, 10, 14, 15, 50, tzinfo=UTC)


def new_vault(tmp_path: Path, config: VaultConfig, name: str = "vault") -> Vault:
    return Vault.init(tmp_path / name, config, "test-box")


def tracked(root: Path) -> list[str]:
    return sorted(sh(root, "git", "ls-files").split())


# -- init ------------------------------------------------------------------------------------------------------------


def test_init_writes_the_layout_and_commits_only_its_own_files(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    assert tracked(vault.root) == sorted(
        [
            ".gitattributes",
            ".gitignore",
            "entities/.gitkeep",
            "ledger/ops/.gitkeep",
            "media/.gitkeep",
            "processed/.gitkeep",
            "raw/.gitkeep",
            "state/.gitkeep",
            "vault.yaml",
        ]
    )
    log = sh(vault.root, "git", "log", "--format=%an <%ae> %s")
    assert log.strip() == "Ada Example <ada@example.invalid> Start the vault"
    assert (vault.root / ".git" / "docket" / "machine").read_text(encoding="utf-8").strip() == "test-box"
    assert Vault.open(vault.root / "raw").root == vault.root


def test_init_refuses_an_existing_vault_and_leaves_it_alone(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    before = (vault.root / "vault.yaml").read_bytes()
    with pytest.raises(DocketError, match="already a vault"):
        Vault.init(vault.root, config.model_copy(update={"primary_machine": "other"}), "other")
    assert (vault.root / "vault.yaml").read_bytes() == before


def test_init_refuses_a_folder_with_files(tmp_path: Path, config: VaultConfig) -> None:
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "notes.md").write_text("mine\n", encoding="utf-8")
    with pytest.raises(DocketError, match="isn't empty"):
        Vault.init(tmp_path / "busy", config, "test-box")
    assert [p.name for p in (tmp_path / "busy").iterdir()] == ["notes.md"]


def test_a_failed_init_leaves_nothing(tmp_path: Path, config: VaultConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object, **__: object) -> None:
        raise DocketError("disk full")

    monkeypatch.setattr("docket.vault.git.Git.commit", boom)
    with pytest.raises(DocketError, match="disk full"):
        new_vault(tmp_path, config)
    assert not (tmp_path / "vault").exists()
    (tmp_path / "empty").mkdir()
    with pytest.raises(DocketError):
        Vault.init(tmp_path / "empty", config, "test-box")
    assert list((tmp_path / "empty").iterdir()) == []


# -- the raw log -----------------------------------------------------------------------------------------------------


def test_a_run_is_written_once_and_committed(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    sha = vault.write_run("chat", [make_record(1), make_record(2)], RUN_AT)
    assert sha is not None
    path = "raw/chat/2026/10/2026-10-14T1550-test-box.jsonl"
    assert path in tracked(vault.root)
    stored = vault.rawlog.read(make_record(1).rid)
    assert len(stored) == 1
    assert stored[0].run == "2026-10-14T1550-test-box"
    assert stored[0].v.startswith("sha256:")
    assert vault.check() == []


def test_records_already_in_the_log_or_the_batch_are_dropped(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    vault.write_run("chat", [make_record(1)], RUN_AT)
    result = vault.rawlog.write_run("chat", [make_record(1), make_record(2), make_record(2)], RUN_AT)
    assert (result.written, result.skipped) == (1, 2)
    assert result.path is not None
    assert result.path.name == "2026-10-14T1550-test-box.2.jsonl"
    nothing = vault.rawlog.write_run("chat", [make_record(1)], RUN_AT)
    assert nothing.path is None


def test_an_edit_is_a_new_version_read_in_run_order(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    vault.write_run("chat", [make_record(1)], RUN_AT)
    vault.write_run("chat", [make_record(1, text="edited")], RUN_AT)
    versions = vault.rawlog.read(make_record(1).rid)
    assert [r.text for r in versions] == ["message 1", "edited"]
    latest = vault.rawlog.latest(make_record(1).rid)
    assert latest is not None
    assert latest.text == "edited"
    assert vault.rawlog.latest("chat:nothing") is None


def test_another_process_s_writes_count_as_already_in_the_log(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    other = RawLog(vault.root, "test-box", vault.lock)
    assert not other.contains(make_record(1))
    vault.write_run("chat", [make_record(1)], RUN_AT)
    assert other.contains(make_record(1))
    assert other.write_run("chat", [make_record(1)], RUN_AT).written == 0


def test_a_record_from_another_source_is_refused(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    with pytest.raises(DocketError, match="not this run's source"):
        vault.rawlog.write_run("calls", [make_record(1)], RUN_AT)


def test_a_run_whose_commit_was_missed_goes_in_with_the_next(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    first = vault.rawlog.write_run("chat", [make_record(1)], RUN_AT)
    assert first.path is not None
    (vault.root / "raw" / "chat" / ".DS_Store").write_text("x", encoding="utf-8")
    (vault.root / "raw" / "chat" / "notes.tmp").write_text("x", encoding="utf-8")
    assert vault.status().uncommitted_runs == ["raw/chat/2026/10/2026-10-14T1550-test-box.jsonl"]
    vault.write_run("chat", [make_record(2)], RUN_AT)
    files = tracked(vault.root)
    assert "raw/chat/2026/10/2026-10-14T1550-test-box.jsonl" in files
    assert "raw/chat/2026/10/2026-10-14T1550-test-box.2.jsonl" in files
    assert not any(f.endswith((".DS_Store", ".tmp")) for f in files)
    assert vault.status().uncommitted_runs == []


def test_the_temp_file_never_lands_in_raw(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    vault.write_run("chat", [make_record(1)], RUN_AT)
    assert [p.name for p in (vault.root / "raw").rglob("*.tmp")] == []
    assert vault.rawlog.tmp_dir.is_dir()


def test_a_raw_file_that_exists_is_never_overwritten(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    target = vault.root / "raw" / "chat" / "x.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text("keep\n", encoding="utf-8")
    with pytest.raises(DocketError, match="written once"):
        vault.rawlog._write_new(target, "new\n")  # pyright: ignore[reportPrivateUsage]
    assert target.read_text(encoding="utf-8") == "keep\n"


# -- check and status ------------------------------------------------------------------------------------------------


def test_check_finds_a_changed_raw_file(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    vault.write_run("chat", [make_record(1)], RUN_AT)
    path = vault.root / "raw/chat/2026/10/2026-10-14T1550-test-box.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("message 1", "rewritten"), encoding="utf-8")
    assert [p.message for p in vault.check()] == [
        "changed or deleted after it was committed; raw files are written once"
    ]
    path.unlink()
    assert len(vault.check()) == 1


def test_check_finds_bad_lines_duplicates_and_stray_files(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    folder = vault.root / "raw" / "chat" / "2026" / "10"
    folder.mkdir(parents=True)
    line = make_record(1).model_copy(update={"v": "sha256:x"}).to_json()
    (folder / "2026-10-14T1550-test-box.jsonl").write_text(f"{line}\n{line}\nnot json\n", encoding="utf-8")
    (folder / "stray.txt").write_text("x", encoding="utf-8")
    messages = [str(p) for p in vault.check()]
    assert any(":2: chat:C0TEST/1791000001.000100 is in this run twice" in m for m in messages)
    assert any(":3: not a raw record" in m for m in messages)
    assert any("stray.txt: isn't a run file" in m for m in messages)


def test_status_counts_records_runs_and_overlaps(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    vault.write_run("chat", [make_record(1), make_record(2)], RUN_AT)
    other = RawLog(vault.root, "other-box", vault.lock)
    other._seen = set()  # pyright: ignore[reportPrivateUsage] - a second machine that hadn't synced yet
    other._read = {f.path for f in other.run_files()}  # pyright: ignore[reportPrivateUsage]
    other.write_run("chat", [make_record(1)], RUN_AT)
    status = vault.status()
    assert status.records["chat"] == 3
    assert status.runs == 2
    assert status.seen_in_several_runs == 1
    assert status.last_commit is not None


# -- git -------------------------------------------------------------------------------------------------------------


def test_a_stale_index_lock_is_cleared(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    lock = vault.root / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    assert not clear_stale_index_lock(vault.root / ".git")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    vault.write_run("chat", [make_record(1)], RUN_AT)
    assert not lock.exists()


def test_sync_without_a_remote_does_nothing(tmp_path: Path, config: VaultConfig) -> None:
    assert new_vault(tmp_path, config).sync().message == "no remote"


def test_sync_with_an_empty_remote_keeps_the_local_commits(tmp_path: Path, config: VaultConfig) -> None:
    sh(tmp_path, "git", "init", "-q", "--bare", "remote.git")
    vault = new_vault(tmp_path, config)
    vault.git.run("remote", "add", "origin", str(tmp_path / "remote.git"))
    assert vault.write_run("chat", [make_record(1)], RUN_AT) is not None
    result = vault.sync()
    assert (result.fetched, result.rebased) == (True, False)
    assert "doesn't exist yet" in result.message


def test_sync_with_an_unreachable_remote_keeps_the_local_commits(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    vault.git.run("remote", "add", "origin", str(tmp_path / "nowhere.git"))
    assert vault.write_run("chat", [make_record(1)], RUN_AT) is not None
    assert vault.sync().fetched is False
    assert "raw/chat/2026/10/2026-10-14T1550-test-box.jsonl" in tracked(vault.root)


def _clone_second_machine(tmp_path: Path, remote: Path) -> Path:
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", "-b", "main", str(remote), str(other))
    sh(
        other,
        "git",
        "-c",
        "user.name=Ada",
        "-c",
        "user.email=ada@example.invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    return other


def test_sync_rebases_onto_the_remote_with_a_dirty_tracked_file(tmp_path: Path, config: VaultConfig) -> None:
    sh(tmp_path, "git", "init", "-q", "--bare", "remote.git")
    remote = tmp_path / "remote.git"
    vault = new_vault(tmp_path, config)
    vault.git.run("remote", "add", "origin", str(remote))
    vault.git.run("push", "-q", "origin", "main")
    other = _clone_second_machine(tmp_path, remote)
    sh(other, "git", "push", "-q", "origin", "main")
    vault.write_run("chat", [make_record(1)], RUN_AT)
    (vault.root / "vault.yaml").write_text((vault.root / "vault.yaml").read_text(encoding="utf-8") + "\n", "utf-8")
    result = vault.sync()
    assert result.rebased, result.message
    assert sh(vault.root, "git", "log", "--format=%s").split("\n")[:3] == [
        "Raw: chat, 1 record(s) from test-box",
        "x",
        "Start the vault",
    ]
    assert sh(vault.root, "git", "status", "--porcelain").strip() == "M vault.yaml"


def test_a_conflicting_remote_is_reported_and_the_local_commit_kept(tmp_path: Path, config: VaultConfig) -> None:
    sh(tmp_path, "git", "init", "-q", "--bare", "remote.git")
    remote = tmp_path / "remote.git"
    vault = new_vault(tmp_path, config)
    vault.git.run("remote", "add", "origin", str(remote))
    vault.git.run("push", "-q", "origin", "main")
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", "-b", "main", str(remote), str(other))
    (other / "vault.yaml").write_text("# theirs\n", encoding="utf-8")
    sh(other, "git", "-c", "user.name=B", "-c", "user.email=b@example.invalid", "commit", "-qam", "theirs")
    sh(other, "git", "push", "-q", "origin", "main")
    (vault.root / "vault.yaml").write_text("# ours\n", encoding="utf-8")
    vault.commit(["vault.yaml"], "ours")
    result = vault.sync()
    assert not result.rebased
    assert "conflicted" in result.message
    assert sh(vault.root, "git", "log", "-1", "--format=%s").strip() == "ours"
    assert not (vault.root / ".git" / "rebase-merge").exists()


def test_records_type(records: Callable[..., RawRecord]) -> None:
    assert records(3).rid.endswith("3.000100")


# -- the review's findings -------------------------------------------------------------------------------------------


def test_a_refused_batch_leaves_its_records_free_to_write(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    foreign = make_record(2, rid="calls:x", source="calls")
    with pytest.raises(DocketError, match="not this run's source"):
        vault.rawlog.write_run("chat", [make_record(1), foreign], RUN_AT)
    assert vault.rawlog.write_run("chat", [make_record(1)], RUN_AT).written == 1


def test_a_failed_write_leaves_its_records_free_to_write(
    tmp_path: Path, config: VaultConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = new_vault(tmp_path, config)
    real = RawLog._write_new  # pyright: ignore[reportPrivateUsage]
    calls: list[int] = []

    def flaky(self: RawLog, target: Path, text: str) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("the rename was blocked")
        real(self, target, text)

    monkeypatch.setattr(RawLog, "_write_new", flaky)
    with pytest.raises(OSError, match="blocked"):
        vault.rawlog.write_run("chat", [make_record(1)], RUN_AT)
    assert vault.rawlog.write_run("chat", [make_record(1)], RUN_AT).written == 1
    assert len(vault.rawlog.read(make_record(1).rid)) == 1


def test_an_init_whose_real_commit_fails_leaves_nothing(tmp_path: Path, config: VaultConfig) -> None:
    gitconfig = Path(os.environ["GIT_CONFIG_GLOBAL"])
    gitconfig.write_text("[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = no-such-signer\n", encoding="utf-8")
    with pytest.raises(DocketError, match="git commit"):
        new_vault(tmp_path, config)
    assert not (tmp_path / "vault").exists()
    (tmp_path / "empty").mkdir()
    with pytest.raises(DocketError):
        Vault.init(tmp_path / "empty", config, "test-box")
    assert list((tmp_path / "empty").iterdir()) == []
    gitconfig.write_text("", encoding="utf-8")
    assert new_vault(tmp_path, config).root == (tmp_path / "vault").resolve()


def test_a_rebase_left_in_progress_is_undone_before_the_next_commit(tmp_path: Path, config: VaultConfig) -> None:
    sh(tmp_path, "git", "init", "-q", "--bare", "remote.git")
    remote = tmp_path / "remote.git"
    vault = new_vault(tmp_path, config)
    vault.git.run("remote", "add", "origin", str(remote))
    vault.git.run("push", "-q", "origin", "main")
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", "-b", "main", str(remote), str(other))
    (other / "vault.yaml").write_text("# theirs\n", encoding="utf-8")
    sh(other, "git", "-c", "user.name=B", "-c", "user.email=b@example.invalid", "commit", "-qam", "theirs")
    sh(other, "git", "push", "-q", "origin", "main")
    (vault.root / "vault.yaml").write_text("# ours\n", encoding="utf-8")
    vault.commit(["vault.yaml"], "ours")
    vault.git.run("fetch", "-q", "origin")
    # A sync killed mid-way leaves its marker and the rebase behind.
    sync_marker(vault.root).write_text("origin/main", encoding="utf-8")
    vault.git.run("rebase", "origin/main", check=False, identity=True)
    assert (vault.root / ".git" / "rebase-merge").exists() or (vault.root / ".git" / "rebase-apply").exists()
    assert vault.write_run("chat", [make_record(1)], RUN_AT) is not None
    assert not sync_marker(vault.root).exists()
    assert vault.git.branch() == "main"
    assert sh(vault.root, "git", "log", "--format=%s", "-2").split("\n")[:2] == [
        "Raw: chat, 1 record(s) from test-box",
        "ours",
    ]


def test_nothing_is_committed_on_a_detached_head(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    vault.git.run("checkout", "-q", "--detach")
    with pytest.raises(DocketError, match="detached HEAD"):
        vault.write_run("chat", [make_record(1)], RUN_AT)
    with pytest.raises(DocketError, match="detached HEAD"):
        vault.sync()


def test_a_large_backlog_of_run_files_still_commits(tmp_path: Path, config: VaultConfig) -> None:
    vault = new_vault(tmp_path, config)
    folder = vault.root / "raw" / "chat" / "2026" / "10"
    folder.mkdir(parents=True)
    line = make_record(1).model_copy(update={"v": "sha256:x"}).to_json()
    for n in range(2, 802):
        (folder / f"2026-10-14T1550-test-box.{n}.jsonl").write_text(f"{line}\n", encoding="utf-8")
    assert vault.commit([], "backlog") is not None
    assert len([f for f in tracked(vault.root) if f.endswith(".jsonl")]) == 800


def test_the_lock_is_released_when_clearing_a_stale_index_lock_fails(
    tmp_path: Path, config: VaultConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = new_vault(tmp_path, config)

    def held(_git_dir: Path, now: float | None = None) -> bool:
        raise PermissionError("index.lock is open")

    monkeypatch.setattr("docket.vault.lock.clear_stale_index_lock", held)
    with pytest.raises(PermissionError), vault.lock:
        pass
    monkeypatch.undo()
    # Another instance, as another process would be: filelock lets the same instance take its own lock again.
    with VaultLock(vault.root / ".git", timeout=0.5):
        pass


def _conflicting_vault(tmp_path: Path, config: VaultConfig) -> Vault:
    sh(tmp_path, "git", "init", "-q", "--bare", "remote.git")
    remote = tmp_path / "remote.git"
    vault = new_vault(tmp_path, config)
    vault.git.run("remote", "add", "origin", str(remote))
    vault.git.run("push", "-q", "origin", "main")
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", "-b", "main", str(remote), str(other))
    (other / "vault.yaml").write_text("# theirs\n", encoding="utf-8")
    sh(other, "git", "-c", "user.name=B", "-c", "user.email=b@example.invalid", "commit", "-qam", "theirs")
    sh(other, "git", "push", "-q", "origin", "main")
    (vault.root / "vault.yaml").write_text("# ours\n", encoding="utf-8")
    vault.commit(["vault.yaml"], "ours")
    vault.git.run("fetch", "-q", "origin")
    return vault


def test_a_rebase_the_user_is_resolving_is_never_touched(tmp_path: Path, config: VaultConfig) -> None:
    vault = _conflicting_vault(tmp_path, config)
    vault.git.run("rebase", "origin/main", check=False, identity=True)
    (vault.root / "vault.yaml").write_text("# resolved by hand\n", encoding="utf-8")
    vault.git.run("add", "vault.yaml")
    with pytest.raises(DocketError, match="a rebase is in progress"):
        vault.write_run("chat", [make_record(1)], RUN_AT)
    assert (vault.root / "vault.yaml").read_text(encoding="utf-8") == "# resolved by hand\n"
    assert (vault.root / ".git" / "rebase-merge").exists() or (vault.root / ".git" / "rebase-apply").exists()
    assert vault.rawlog.read(make_record(1).rid) != []


def test_a_merge_in_progress_is_refused(tmp_path: Path, config: VaultConfig) -> None:
    vault = _conflicting_vault(tmp_path, config)
    vault.git.run("merge", "origin/main", check=False, identity=True)
    assert (vault.root / ".git" / "MERGE_HEAD").exists()
    with pytest.raises(DocketError, match="a merge is in progress"):
        vault.commit([], "x")
