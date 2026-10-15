"""Where a vault lives: a repo of its own or a folder of the user's repo, found, opened and refused by what vault.yaml
says and what the disk shows."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, folder_vault, git, host_repo, make_config, make_record

from docket.errors import DocketError
from docket.sources import local
from docket.vault import cursors, machine
from docket.vault.config import CONFIG_FILE, VaultConfig, write_config
from docket.vault.convert import State, Where
from docket.vault.docstore import DocStore
from docket.vault.git import Git
from docket.vault.home import Home, stopped_convert
from docket.vault.records import version
from docket.vault.staging import Staged, Staging, write_replacing
from docket.vault.vault import Vault, is_docket_config, remove_tree


def test_a_folder_vault_keeps_docket_s_files_under_the_host_s_git_dir(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = tmp_path / "host"
    private = top / ".git" / "docket" / "notes" / "vault"
    assert vault.home.private == private.resolve()
    assert vault.home.prefix == "notes/vault/"
    assert vault.home.lock_file == private.resolve() / "docket.lock"
    assert (private / "machine").read_text(encoding="utf-8").strip() == "test-box"
    vault.write_run("chat", [make_record(1)])
    assert vault.home.tmp_dir.is_dir()
    # Nothing of Docket's own shows in the user's tree, ignored or not.
    ignored = [line for line in git(top, "status", "--porcelain", "--ignored").splitlines() if line.startswith("!!")]
    assert ignored == []


def test_both_kinds_are_found_from_inside(tmp_path: Path) -> None:
    folder = folder_vault(tmp_path)
    own = Vault.init(tmp_path / "own", make_config(), "test-box")
    assert Vault.find(folder.root / "raw") == folder.root
    assert Vault.find(own.root / "raw") == own.root
    assert Vault.open(folder.root / "raw").home.storage == "folder"
    assert Vault.open(own.root).home.private == own.root / ".git" / "docket"


def test_another_tool_s_vault_yaml_is_not_a_vault(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    (top / "k8s").mkdir()
    (top / "k8s" / CONFIG_FILE).write_text("server:\n  dev: true\n", encoding="utf-8")
    assert Vault.find(top / "k8s") is None
    (top / "k8s" / CONFIG_FILE).write_bytes(b"\xff\xfe not text")
    assert not is_docket_config(top / "k8s" / CONFIG_FILE)
    # Docket's own, with a typo: found, and its load says what is wrong.
    (top / "k8s" / CONFIG_FILE).write_text("storage: folder\nprimary_machine: [test-box\n", encoding="utf-8")
    assert Vault.find(top / "k8s") == top / "k8s"
    with pytest.raises(DocketError, match="not valid YAML"):
        Vault.open(top / "k8s")


def test_storage_and_the_disk_must_agree(tmp_path: Path) -> None:
    folder = folder_vault(tmp_path)
    own = Vault.init(tmp_path / "own", make_config(), "test-box")
    with pytest.raises(DocketError, match=r"storage: repo, but there is no .git"):
        Home.resolve(folder.root, "repo")
    with pytest.raises(DocketError, match=r"has a .git of its own"):
        Home.resolve(own.root, "folder")
    loose = tmp_path / "loose"
    loose.mkdir()
    with pytest.raises(DocketError, match="isn't inside a git repo"):
        Home.resolve(loose, "folder")


def test_a_vault_inside_another_vault_is_refused(tmp_path: Path) -> None:
    own = Vault.init(tmp_path / "own", make_config(), "test-box")
    (own.root / "inner").mkdir()
    with pytest.raises(DocketError, match="inside another vault"):
        Home.resolve(own.root / "inner", "folder")


def test_a_linked_worktree_points_at_the_main_checkout(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = tmp_path / "host"
    git(top, "commit", "-qm", "the user's staged work")
    git(top, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "side")
    with pytest.raises(DocketError, match="linked worktree") as refused:
        Vault.open(tmp_path / "wt" / "notes" / "vault")
    assert str(vault.root.relative_to(tmp_path)).replace("\\", "/") in str(refused.value).replace("\\", "/")


def test_a_vault_whose_git_went_missing_never_commits_into_the_repo_above(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    vault = Vault.init(top / "nested", make_config(), "test-box")
    before = git(top, "rev-parse", "HEAD")
    (vault.root / ".git").rename(tmp_path / "moved.git")
    with pytest.raises(DocketError):
        vault.git.commit(["vault.yaml"], "Should never land")
    # A write through the vault opened before: the lock refuses before the raw log writes, and nothing makes a .git
    # that the next open would take for the vault's own.
    with pytest.raises(DocketError, match="isn't a git repository"):
        vault.write_run("chat", [make_record(1)])
    assert not (vault.root / ".git").exists()
    assert vault.rawlog.run_files() == []
    assert git(top, "rev-parse", "HEAD") == before
    with pytest.raises(DocketError, match=r"no .git"):
        Vault.open(vault.root)


def test_a_folder_vault_whose_host_lost_its_git_never_commits_into_the_repo_above(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    host = tmp_path / "host"
    # The host sits in an outer repo of the user's, with a commit of its own.
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "outer.txt").write_text("outer\n", encoding="utf-8")
    git(tmp_path, "add", "outer.txt")
    git(tmp_path, "commit", "-q", "-m", "Outer")
    before = git(tmp_path, "rev-parse", "HEAD")
    (host / ".git").rename(tmp_path / "moved.git")
    with pytest.raises(DocketError, match="isn't a git repository"):
        vault.write_run("chat", [make_record(1)])
    assert not (host / ".git").exists()
    # Opened again, git finds the outer repo, which doesn't track the vault.
    with pytest.raises(DocketError, match=r"doesn't track its vault\.yaml") as refused:
        Vault.open(vault.root)
    named = str(refused.value)
    assert str(vault.root) in named
    assert str(tmp_path.resolve()) in named
    assert git(tmp_path, "rev-parse", "HEAD") == before
    assert git(tmp_path, "ls-files").split() == ["outer.txt"]
    # A refusal is never kept: once the outer repo tracks the vault, as for a vault moved there on purpose, it opens.
    git(tmp_path, "add", "host/notes/vault/vault.yaml")
    assert Vault.open(vault.root).home.top == tmp_path.resolve()


def test_a_folder_vault_s_tracked_check_runs_once_per_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = folder_vault(tmp_path)
    calls: list[tuple[str, ...]] = []
    real = Git.run

    def spy(self: Git, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Git, "run", spy)
    # As a new process finds it: nothing kept yet. Every open after the first answers from what was kept.
    monkeypatch.setattr("docket.vault.vault._TRACKED", set[tuple[Path, str]]())
    for _ in range(3):
        Vault.open(vault.root)
    assert calls == [("ls-files", "--error-unmatch", "--", CONFIG_FILE)]


def test_a_git_dir_holding_only_docket_s_folders_is_no_git_dir(tmp_path: Path) -> None:
    vault = Vault.init(tmp_path / "own", make_config(), "test-box")
    remove_tree(vault.root / ".git")
    # What a write left before Docket stopped making folders under a missing git dir.
    (vault.root / ".git" / "docket" / "tmp").mkdir(parents=True)
    (vault.root / ".git" / "docket.lock").write_text("", encoding="utf-8")
    assert Vault.find(vault.root) == vault.root
    with pytest.raises(DocketError, match=r"no \.git in .*holds no repository.*; restore it") as refused:
        Vault.open(vault.root)
    assert "link to a repository elsewhere" not in str(refused.value)
    other = tmp_path / "k8s"
    (other / ".git" / "docket").mkdir(parents=True)
    (other / CONFIG_FILE).write_text("server:\n  dev: true\n", encoding="utf-8")
    assert Vault.find(other) is None


def test_a_repo_vault_reached_through_a_git_file_says_to_use_its_own_checkout(tmp_path: Path) -> None:
    vault = Vault.init(tmp_path / "own", make_config(), "test-box")
    git(vault.root, "worktree", "add", "-q", str(tmp_path / "wt"))
    assert (tmp_path / "wt" / ".git").is_file()
    with pytest.raises(DocketError, match=r"link to a repository elsewhere.*vault's own checkout") as refused:
        Vault.open(tmp_path / "wt")
    assert "restore it" not in str(refused.value)


def _write_config(vault: Vault) -> None:
    write_config(vault.home, vault.root / CONFIG_FILE, vault.config.dump())


def _write_replacing(vault: Vault) -> None:
    write_replacing(vault.home, vault.root / "state" / "x.txt", "x\n", "x")


def _stage(vault: Vault) -> None:
    record = make_record(1)
    staged = Staged(
        state="pending", staged_at=datetime.now(UTC), record=record.model_copy(update={"v": version(record)})
    )
    Staging(vault.root, vault.home).write(staged)


def _raw_file(vault: Vault) -> None:
    vault.rawlog._write_new(vault.root / "raw" / "chat" / "x.jsonl", "{}\n")  # pyright: ignore[reportPrivateUsage]


def _doc_file(vault: Vault) -> None:
    DocStore(vault)._replace(vault.root / "processed" / "x.md", b"x\n")  # pyright: ignore[reportPrivateUsage]


def _convert_state(vault: Vault) -> None:
    State(step=1, branch="main").save(Where(vault.root, vault.root, "vault/", vault.root / ".git"))


def _lock(vault: Vault) -> None:
    with vault.lock:
        pass


@pytest.mark.parametrize(
    "write",
    [
        _lock,
        _raw_file,
        _stage,
        _write_replacing,
        _write_config,
        _doc_file,
        lambda vault: machine.remember(vault.home, "other-box"),
        lambda vault: vault.git.run("status", hooks=False),
        _convert_state,
        lambda vault: cursors.save(vault, "chat", cursors.Cursor()),
        lambda vault: local.record(vault, "chat", vault.root.parent),
    ],
    ids=[
        "lock",
        "raw log",
        "staging",
        "replace",
        "vault.yaml",
        "doc",
        "machine",
        "hooks-off",
        "convert",
        "cursor",
        "imported folders",
    ],
)
def test_no_writer_of_docket_s_own_files_remakes_a_missing_git_dir(
    tmp_path: Path, config: VaultConfig, write: Callable[[Vault], object]
) -> None:
    vault = empty_vault(tmp_path, config)
    remove_tree(vault.root / ".git")
    with pytest.raises(DocketError, match="isn't a git repository"):
        write(vault)
    assert not (vault.root / ".git").exists()


def test_a_stopped_convert_makes_open_point_at_it(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    assert stopped_convert(vault.root) is None
    state = vault.home.private / "convert.json"
    state.write_text("{}", encoding="utf-8")
    assert stopped_convert(vault.root) == state
    # A .git a write left with only Docket's folders, while the convert had the real one moved away, hides nothing.
    (vault.root / ".git" / "docket").mkdir(parents=True)
    assert stopped_convert(vault.root) == state
    remove_tree(vault.root / ".git")
    with pytest.raises(DocketError, match=r"docket vault convert` to finish it.*--abort"):
        Vault.open(vault.root)
    # Between the rename and the folder files a vault.yaml says nothing of storage and there is no .git: still found.
    text = (vault.root / CONFIG_FILE).read_text(encoding="utf-8").replace("storage: folder\n", "")
    (vault.root / CONFIG_FILE).write_text(text, encoding="utf-8")
    assert Vault.find(vault.root) == vault.root
    state.unlink()
    with pytest.raises(DocketError, match=r"storage: repo, but there is no .git"):
        Vault.open(vault.root)
    assert stopped_convert(tmp_path / "host") is None
    loose = tmp_path / "loose"
    loose.mkdir()
    assert stopped_convert(loose) is None
