"""Where a vault lives: a repo of its own or a folder of the user's repo, found, opened and refused by what vault.yaml
says and what the disk shows."""

from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import folder_vault, git, host_repo, make_config, make_record

from docket.errors import DocketError
from docket.vault.config import CONFIG_FILE
from docket.vault.home import Home, stopped_convert
from docket.vault.vault import Vault, is_docket_config


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
    assert git(top, "rev-parse", "HEAD") == before
    with pytest.raises(DocketError, match=r"no .git"):
        Vault.open(vault.root)


def test_a_stopped_convert_makes_open_point_at_it(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    assert stopped_convert(vault.root) is None
    state = vault.home.private / "convert.json"
    state.write_text("{}", encoding="utf-8")
    assert stopped_convert(vault.root) == state
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
