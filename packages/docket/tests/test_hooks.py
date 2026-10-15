"""Docket's commits and sync's fetch and rebase run no hooks, in a repo of its own and in a folder of the user's repo,
while the user's own commits and every push still run them (DR-0260, DR-0261)."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import empty_vault, folder_vault, git, host_repo, make_config, make_record, sh

from docket.vault.vault import Vault

RUN_AT = datetime(2026, 10, 14, 15, 50, tzinfo=UTC)
needs_lfs = pytest.mark.skipif(shutil.which("git-lfs") is None, reason="needs git-lfs")


def vault_of(tmp_path: Path, storage: str) -> Vault:
    return empty_vault(tmp_path, make_config()) if storage == "repo" else folder_vault(tmp_path)


def write_hook(folder: Path, name: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    hook = folder / name
    hook.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)


def log_hooks(folder: Path, log: Path, *names: str) -> None:
    """Hooks that only write their name to a log; they replace git-lfs's of the same name."""
    for name in names:
        write_hook(folder, name, f'echo {name} >> "{log.as_posix()}"')


def ran(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").split() if log.exists() else []


def subjects(root: Path) -> list[str]:
    return sh(root, "git", "log", "--format=%s").splitlines()


def hooks_of(vault: Vault) -> Path:
    return vault.home.git_dir / "hooks"


@pytest.mark.parametrize("storage", ["repo", "folder"])
def test_docket_s_commits_run_no_hook_and_the_user_s_own_still_do(tmp_path: Path, storage: str) -> None:
    vault = vault_of(tmp_path, storage)
    top, hooks, log = vault.home.top, hooks_of(vault), tmp_path / "ran.log"
    write_hook(hooks, "pre-commit", 'for f in $(git diff --cached --name-only); do echo touched >> "$f"; done')
    write_hook(hooks, "commit-msg", "exit 1")
    log_hooks(hooks, log, "post-commit")
    assert vault.write_run("chat", [make_record(1)], RUN_AT) is not None
    run = next((vault.root / "raw").rglob("*.jsonl"))
    assert "touched" not in run.read_text(encoding="utf-8")
    assert subjects(top)[0] == "Raw: chat, 1 record(s) from test-box"
    assert ran(log) == []
    assert vault.home.hooks_off.is_file()
    # The user's own commit runs them: commit-msg refuses it, and without that rule post-commit runs.
    with pytest.raises(AssertionError):
        git(top, "commit", "--allow-empty", "-qm", "mine")
    (hooks / "commit-msg").unlink()
    git(top, "commit", "--allow-empty", "-qm", "mine")
    assert ran(log) == ["post-commit"]


def test_a_deleted_hooks_off_file_is_written_again(tmp_path: Path) -> None:
    vault = vault_of(tmp_path, "repo")
    log = tmp_path / "ran.log"
    log_hooks(hooks_of(vault), log, "post-commit")
    vault.home.hooks_off.unlink()
    assert vault.write_run("chat", [make_record(1)], RUN_AT) is not None
    assert vault.home.hooks_off.is_file()
    assert ran(log) == []


@needs_lfs
@pytest.mark.parametrize("storage", ["repo", "folder"])
def test_media_still_goes_to_lfs_and_git_lfs_writes_no_hooks(tmp_path: Path, storage: str) -> None:
    vault = vault_of(tmp_path, storage)
    top, hooks, log = vault.home.top, hooks_of(vault), tmp_path / "ran.log"
    if storage == "folder":
        git(top, "lfs", "install", "--local")
    log_hooks(hooks, log, "post-commit")
    before = sorted(p.name for p in hooks.iterdir())
    media = vault.root / "media" / "s" / "2026" / "10" / "a.age"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"encrypted audio")
    assert vault.commit(["media/s/2026/10/a.age"], "Media: a") is not None
    blob = git(top, "cat-file", "blob", f"HEAD:{vault.home.prefix}media/s/2026/10/a.age")
    assert blob.startswith("version https://git-lfs")
    assert ran(log) == []
    assert sorted(p.name for p in hooks.iterdir()) == before
    assert vault.home.hooks_off.is_file()


@needs_lfs
def test_a_folder_vault_in_a_host_with_git_lfs_runs_no_git_lfs_hook(tmp_path: Path) -> None:
    # Init's add of media/.gitkeep runs git-lfs's filter, which wrote its hooks into the empty folder that used to be
    # the hooks path, so every later commit ran git-lfs's post-commit.
    top = host_repo(tmp_path)
    git(top, "lfs", "install", "--local")
    log = tmp_path / "ran.log"
    log_hooks(top / ".git" / "hooks", log, "post-commit")
    vault = Vault.init(top / "notes" / "vault", make_config("folder"), "test-box")
    assert vault.write_run("chat", [make_record(1)], RUN_AT) is not None
    assert vault.home.hooks_off.is_file()
    assert not (vault.home.private / "no-hooks").exists()
    assert ran(log) == []


def test_a_refusing_hook_doesn_t_stop_init(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks"
    write_hook(hooks, "pre-commit", "exit 1")
    Path(os.environ["GIT_CONFIG_GLOBAL"]).write_text(f"[core]\n\thooksPath = {hooks.as_posix()}\n", encoding="utf-8")
    vault = Vault.init(tmp_path / "vault", make_config(), "test-box")
    assert subjects(vault.root) == ["Start the vault"]


def test_sync_rebases_past_a_refusing_pre_rebase_and_runs_no_hook(tmp_path: Path) -> None:
    vault = vault_of(tmp_path, "repo")
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", "remote.git")
    remote = tmp_path / "remote.git"
    vault.git.run("remote", "add", "origin", str(remote))
    vault.git.run("push", "-q", "origin", "main")
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", "-b", "main", str(remote), str(other))
    git(other, "commit", "--allow-empty", "-qm", "theirs")
    sh(other, "git", "push", "-q", "origin", "main")
    hooks, log = hooks_of(vault), tmp_path / "ran.log"
    write_hook(hooks, "pre-rebase", "exit 1")
    log_hooks(hooks, log, "post-checkout", "post-commit", "post-rewrite")
    assert vault.write_run("chat", [make_record(1)], RUN_AT) is not None
    # The user's own rebase is refused.
    vault.git.run("fetch", "-q", "origin")
    with pytest.raises(AssertionError, match="pre-rebase"):
        git(vault.root, "rebase", "-q", "origin/main")
    result = vault.sync()
    assert result.rebased, result.message
    assert subjects(vault.root) == ["Raw: chat, 1 record(s) from test-box", "theirs", "Start the vault"]
    assert ran(log) == []


def test_a_push_runs_the_hooks(tmp_path: Path) -> None:
    vault = vault_of(tmp_path, "repo")
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", "remote.git")
    vault.git.run("remote", "add", "origin", str(tmp_path / "remote.git"))
    log = tmp_path / "ran.log"
    log_hooks(hooks_of(vault), log, "pre-push")
    vault.git.run("push", "-q", "origin", "main")
    assert ran(log) == ["pre-push"]


@needs_lfs
def test_a_push_uploads_the_media_through_git_lfs_s_pre_push(tmp_path: Path) -> None:
    vault = vault_of(tmp_path, "repo")
    remote = tmp_path / "remote.git"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", "remote.git")
    vault.git.run("remote", "add", "origin", str(remote))
    content = b"encrypted audio"
    media = vault.root / "media" / "s" / "2026" / "10" / "a.age"
    media.parent.mkdir(parents=True)
    media.write_bytes(content)
    assert vault.commit(["media/s/2026/10/a.age"], "Media: a") is not None
    vault.git.run("push", "-q", "origin", "main")
    oid = hashlib.sha256(content).hexdigest()
    assert (remote / "lfs" / "objects" / oid[:2] / oid[2:4] / oid).read_bytes() == content
