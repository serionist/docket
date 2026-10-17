"""The git dir's files read without git (DR-0528), held to git's own answers on each layout: a branch with commits, one
with none yet, a detached HEAD, a ref only in packed-refs, a folder vault's host. What the reader can't vouch for
answers UNKNOWN, and the vault's git layer asks git instead: a linked worktree's git dir, a malformed ref, an unreadable
packed-refs, a repo whose refs live in a reftable. Invented data only."""

from __future__ import annotations

import os
import subprocess
import zlib
from pathlib import Path

import pytest
from docket_testkit import COMMIT_CALLS, fixture_vault, folder_vault, git, git_calls, note, sh

from docket import recall
from docket.vault import gitfiles
from docket.vault.git import Git
from docket.vault.gitfiles import UNKNOWN


def repo(path: Path, *init: str) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main", *init)
    return path


def commit(top: Path, name: str = "a.txt") -> str:
    (top / name).write_text(f"{name}\n", encoding="utf-8")
    git(top, "add", name)
    git(top, "commit", "-qm", f"Add {name}")
    return sh(top, "git", "rev-parse", "HEAD").strip()


def symref(top: Path) -> str | None:
    proc = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=top, capture_output=True, text=True, check=False)
    return proc.stdout.strip() or None


def head(top: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=top, capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() or None


def answers(top: Path, git_dir: Path | None = None) -> tuple[object, object]:
    found = git_dir or top / ".git"
    return gitfiles.head_ref(found), gitfiles.head_id(found)


def test_a_branch_with_commits_reads_as_git_answers(tmp_path: Path) -> None:
    top = repo(tmp_path / "r")
    commit(top)
    second = commit(top, "b.txt")
    assert answers(top) == (symref(top), head(top)) == ("refs/heads/main", second)


def test_a_new_commit_s_parents_are_read_from_its_object(tmp_path: Path) -> None:
    """The settings in force move their cache on a commit of Docket's own by its parent (DR-0521): read from the
    loose object git just wrote, and unknown for one packed, missing or not a commit."""
    top = repo(tmp_path / "r")
    first = commit(top)
    assert gitfiles.commit_parents(top / ".git", first) == ()
    second = commit(top, "b.txt")
    assert gitfiles.commit_parents(top / ".git", second) == (first,)
    tree = sh(top, "git", "rev-parse", "HEAD^{tree}").strip()
    assert gitfiles.commit_parents(top / ".git", tree) is UNKNOWN
    assert gitfiles.commit_parents(top / ".git", "not an id") is UNKNOWN
    # A commit object whose parent line isn't an id, written as git stores a loose object.
    odd = "f" * 40
    (top / ".git" / "objects" / odd[:2]).mkdir(exist_ok=True)
    body = f"tree {tree}\nparent not-an-id\nauthor A <a@example.invalid> 0 +0000\n\nodd\n".encode()
    (top / ".git" / "objects" / odd[:2] / odd[2:]).write_bytes(zlib.compress(b"commit %d\0" % len(body) + body))
    assert gitfiles.commit_parents(top / ".git", odd) is UNKNOWN
    (top / ".git" / "objects" / odd[:2] / odd[2:]).unlink()
    git(top, "gc", "-q")
    assert gitfiles.commit_parents(top / ".git", second) is UNKNOWN
    assert gitfiles.commit_parents(tmp_path / "nowhere", second) is UNKNOWN


def test_a_branch_with_no_commit_yet_has_no_id(tmp_path: Path) -> None:
    top = repo(tmp_path / "r")
    assert answers(top) == (symref(top), head(top)) == ("refs/heads/main", None)


def test_a_detached_head_names_no_branch(tmp_path: Path) -> None:
    top = repo(tmp_path / "r")
    first = commit(top)
    commit(top, "b.txt")
    git(top, "checkout", "-q", "--detach", first)
    assert answers(top) == (symref(top), head(top)) == (None, first)


def test_a_ref_only_in_packed_refs_is_found_there(tmp_path: Path) -> None:
    top = repo(tmp_path / "r")
    first = commit(top)
    git(top, "tag", "-a", "-m", "a tag, so packed-refs holds a peeled line", "v1")
    git(top, "pack-refs", "--all")
    assert not (top / ".git" / "refs" / "heads" / "main").exists()
    assert answers(top) == (symref(top), head(top)) == ("refs/heads/main", first)
    # A loose ref written after the pack wins over the packed line, as in git.
    second = commit(top, "b.txt")
    assert (top / ".git" / "refs" / "heads" / "main").is_file()
    assert answers(top) == ("refs/heads/main", second)


def test_a_folder_vault_reads_its_host_s_git_dir(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.git.git_dir.parent
    assert vault.git.git_dir == top / ".git"
    assert answers(top) == (symref(top), head(top)) == ("refs/heads/main", head(top))
    assert vault.git.head_id() == head(top)
    assert vault.git.on_branch()


def test_a_linked_worktree_is_unknown_and_git_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = repo(tmp_path / "r")
    first = commit(top)
    linked = tmp_path / "linked"
    git(top, "worktree", "add", "-q", "--detach", str(linked), first)
    # Its .git is a file pointing at a git dir with a commondir file: neither is read.
    assert answers(linked) == (UNKNOWN, UNKNOWN)
    own = top / ".git" / "worktrees" / "linked"
    assert (own / "commondir").is_file()
    assert answers(linked, own) == (UNKNOWN, UNKNOWN)
    with_git = Git(linked)
    with git_calls(monkeypatch) as calls:
        assert (with_git.head_id(), with_git.on_branch()) == (first, False)
    assert calls == ["rev-parse --verify --quiet HEAD", "rev-parse --abbrev-ref HEAD"]


@pytest.mark.parametrize(
    "ref_bytes",
    [b"not a commit id\n", b"ref: HEAD\n", b"0123abcd\n", b"ref: refs/heads/main\n", b"\xff\xfe not text\n"],
    ids=["words", "a symref out of refs", "a short id", "a symref to itself", "not utf-8"],
)
def test_a_ref_file_git_wouldn_t_write_is_unknown(tmp_path: Path, ref_bytes: bytes) -> None:
    top = repo(tmp_path / "r")
    commit(top)
    (top / ".git" / "refs" / "heads" / "main").write_bytes(ref_bytes)
    assert gitfiles.head_id(top / ".git") is UNKNOWN
    assert repr(UNKNOWN) == "UNKNOWN"


def test_a_ref_that_is_a_folder_is_unknown(tmp_path: Path) -> None:
    top = repo(tmp_path / "r")
    commit(top)
    git(top, "branch", "topic/one")
    (top / ".git" / "HEAD").write_text("ref: refs/heads/topic\n", encoding="utf-8")
    assert answers(top) == ("refs/heads/topic", UNKNOWN)


def test_an_unreadable_head_is_unknown(tmp_path: Path) -> None:
    top = repo(tmp_path / "r")
    commit(top)
    (top / ".git" / "HEAD").write_bytes(b"ref: refs/heads/\xff\xfe\n")
    assert answers(top) == (UNKNOWN, UNKNOWN)


@pytest.mark.parametrize(
    "packed",
    [
        b"# pack-refs with: peeled fully-peeled sorted \n0123 refs/heads/main\n",
        b"# pack-refs with: peeled fully-peeled sorted \n" + b"a" * 40 + b" refs/heads/main\n^not-an-id\n",
        b"\xff\xfe not text\n",
        None,
    ],
    ids=["a short id", "a peeled line without an id", "not utf-8", "a folder"],
)
def test_an_unreadable_packed_refs_is_unknown(tmp_path: Path, packed: bytes | None) -> None:
    top = repo(tmp_path / "r")
    commit(top)
    (top / ".git" / "refs" / "heads" / "main").unlink()
    path = top / ".git" / "packed-refs"
    if packed is None:
        path.mkdir()
    else:
        path.write_bytes(packed)
    assert gitfiles.head_ref(top / ".git") == "refs/heads/main"
    assert gitfiles.head_id(top / ".git") is UNKNOWN


def test_a_packed_refs_read_whole_without_the_ref_means_no_commit_yet(tmp_path: Path) -> None:
    top = repo(tmp_path / "r")
    commit(top)
    git(top, "branch", "other")
    git(top, "pack-refs", "--all")
    git(top, "checkout", "-q", "--orphan", "fresh")
    assert answers(top) == (symref(top), head(top)) == ("refs/heads/fresh", None)


def test_a_reftable_repo_is_unknown_and_a_write_asks_git_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = subprocess.run(
        ["git", "init", "-q", "--ref-format=reftable", str(tmp_path / "probe")], capture_output=True, check=False
    )
    if probe.returncode != 0:
        pytest.skip("this git has no reftable")
    gitconfig = Path(os.environ["GIT_CONFIG_GLOBAL"])
    gitconfig.write_text(gitconfig.read_text(encoding="utf-8").replace("= files", "= reftable"), encoding="utf-8")
    vault = fixture_vault(tmp_path)
    assert sh(vault.root, "git", "rev-parse", "--show-ref-format").strip() == "reftable"
    assert answers(vault.root) == (UNKNOWN, UNKNOWN)
    with git_calls(monkeypatch) as calls:
        recall.doc_put(vault, note())
    # The branch test at each check and the new commit's id are asked of git, as before #132; git's identity was
    # asked when the fixture committed through this same vault.
    assert calls == [
        "rev-parse --abbrev-ref HEAD",
        "log --no-show-signature --format=%H%x1f%B%x1e -- processed/recording-note/2026/10/2026-10-14-supplier-call.md",
        "rev-parse --abbrev-ref HEAD",
        *[c for c in COMMIT_CALLS if not c.startswith("config")],
        "rev-parse --verify --quiet HEAD",
    ]
    assert vault.git.head_id() == head(vault.root)
