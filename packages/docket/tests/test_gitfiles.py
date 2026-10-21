"""The git dir's files read without git (DR-0765), held to git's own answers on each layout: a branch with commits, one
with none yet, a detached HEAD, a ref only in packed-refs, a folder vault's host, and the index's unmerged paths in each
version. What the reader can't vouch for answers UNKNOWN, and the vault's git layer asks git instead: a linked
worktree's git dir, a malformed ref, an unreadable packed-refs, a repo whose refs live in a reftable, a split or sparse
index, one past the cap or torn. Invented data only."""

from __future__ import annotations

import hashlib
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
from docket.vault.vault import UnmergedError


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
    # The branch test at each check and the new commit's id are asked of git, as before #132, and so are the
    # unmerged paths: HEAD's file can't say how long the index's object ids are (DR-0765). HEAD's id is asked once
    # more by the put's raw lookup, for the forgotten list in force (DR-0579).
    assert calls == [
        "rev-parse --verify --quiet HEAD",
        "rev-parse --abbrev-ref HEAD",
        "log --no-show-signature --format=%H%x1f%B%x1e -- processed/recording-note/2026/10/2026-10-14-supplier-call.md",
        "rev-parse --abbrev-ref HEAD",
        COMMIT_CALLS[0],
        "ls-files -u",
        *COMMIT_CALLS[1:],
        "rev-parse --verify --quiet HEAD",
    ]
    assert vault.git.head_id() == head(vault.root)


# -- the index's unmerged paths (DR-0765) ------------------------------------------------------------------------------

# Names an index holds whole or prefix-compressed: a space, other than ASCII, a long one, a deep one.
NAMES = ["a.txt", "ünnep és szünet.jsonl", "raw/chat/2026/10/2026-10-14T0900-test-box.jsonl", "b" * 90 + ".md",
         "sub/deep/name with spaces.md", "z.md"]  # fmt: skip


def conflicted(path: Path) -> Path:
    """A repo with a merge stopped on conflicts: changed on both sides, added on both, changed on one and deleted on
    the other, in the top and in a folder."""
    top = repo(path)
    for name in [*NAMES, "both.md", "sub/both.md", "gone.md"]:
        (top / name).parent.mkdir(parents=True, exist_ok=True)
        (top / name).write_text(f"{name}\n", encoding="utf-8")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "base")
    git(top, "checkout", "-q", "-b", "side")
    for name, text in (("both.md", "side\n"), ("sub/both.md", "side\n"), ("new.md", "side\n"), ("gone.md", "side\n")):
        (top / name).write_text(text, encoding="utf-8")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "side")
    git(top, "checkout", "-q", "main")
    for name in ("both.md", "sub/both.md", "new.md"):
        (top / name).write_text("main\n", encoding="utf-8")
    (top / "gone.md").unlink()
    git(top, "add", "-A")
    git(top, "commit", "-qm", "main")
    subprocess.run(["git", "merge", "-q", "side"], cwd=top, capture_output=True, check=False)
    return top


def listed_unmerged(cwd: Path) -> list[str]:
    """`git ls-files -u` from a folder, its names unquoted."""
    out = sh(cwd, "git", "ls-files", "-u", "-z")
    return sorted({entry.split("\t", 1)[1] for entry in out.split("\0") if entry})


def staged(top: Path) -> list[tuple[bytes, int]]:
    """Every entry's name and stage as `git ls-files -s` lists them, in the index's order."""
    out = subprocess.run(["git", "ls-files", "-s", "-z"], cwd=top, capture_output=True, check=True).stdout
    return [(entry.split(b"\t", 1)[1], int(entry.split(b" ")[2][:1])) for entry in out.split(b"\0") if entry]


def read(top: Path) -> list[tuple[bytes, int]] | None:
    data = (top / ".git" / "index").read_bytes()
    return gitfiles._stages(data, 20)  # pyright: ignore[reportPrivateUsage] - the entries themselves, held to git's


def resigned(body: bytes) -> bytes:
    """An index with `body` in place of everything before its hash, and the hash git would write for it."""
    return body + hashlib.sha1(body).digest()  # noqa: S324 - the index's own checksum is SHA-1


@pytest.mark.parametrize("version", ["2", "3", "4"])
def test_unmerged_paths_read_from_each_index_version_as_git_lists_them(tmp_path: Path, version: str) -> None:
    top = conflicted(tmp_path / "r")
    git(top, "update-index", "--index-version", version)
    if version != "2":
        # Entries with the extended flags, which versions 3 and 4 carry.
        git(top, "update-index", "--skip-worktree", "z.md")
        (top / "ita.md").write_text("ita\n", encoding="utf-8")
        git(top, "add", "-N", "ita.md")
    assert (top / ".git" / "index").read_bytes()[4:8] == int(version).to_bytes(4, "big")
    assert read(top) == staged(top)
    assert (
        gitfiles.unmerged(top / ".git", "") == listed_unmerged(top) == ["both.md", "gone.md", "new.md", "sub/both.md"]
    )
    # From a folder, as a folder vault asks: only its own paths, made relative to it.
    assert gitfiles.unmerged(top / ".git", "sub/") == listed_unmerged(top / "sub") == ["both.md"]


def test_an_index_of_names_whole_and_compressed_reads_every_entry(tmp_path: Path) -> None:
    top = repo(tmp_path / "r")
    for n in range(300):
        name = top / "raw" / "chat" / "2026" / f"{n % 12 + 1:02d}" / f"2026-10-14T{n:04d}-test-box.jsonl"
        name.parent.mkdir(parents=True, exist_ok=True)
        name.write_text(f"{n}\n", encoding="utf-8")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "many")
    for version in ("2", "4"):
        git(top, "update-index", "--index-version", version)
        assert read(top) == staged(top)
        assert gitfiles.unmerged(top / ".git", "") == []


def test_the_optional_extensions_are_read_past(tmp_path: Path) -> None:
    top = conflicted(tmp_path / "r")
    git(top, "merge", "--abort")
    git(top, "config", "core.untrackedCache", "true")
    git(top, "config", "index.recordEndOfIndexEntries", "true")
    git(top, "config", "index.recordOffsetTable", "true")
    git(top, "config", "index.threads", "4")
    git(top, "update-index", "--untracked-cache")
    sh(top, "git", "status", "-uall")
    # A resolved conflict leaves REUC, the resolve-undo record.
    subprocess.run(["git", "merge", "-q", "side"], cwd=top, capture_output=True, check=False)
    (top / "both.md").write_text("resolved\n", encoding="utf-8")
    git(top, "add", "both.md")
    data = (top / ".git" / "index").read_bytes()
    for signature in (b"TREE", b"REUC", b"UNTR", b"EOIE", b"IEOT"):
        assert signature in data, signature
    assert gitfiles.unmerged(top / ".git", "") == listed_unmerged(top) == ["gone.md", "new.md", "sub/both.md"]
    # One git writes only where its file system monitor runs, and one no git knows yet: both optional, as git reads
    # them.
    end = len(data) - 20
    for signature in (b"FSMN", b"ZZZZ"):
        extra = signature + (12).to_bytes(4, "big") + bytes(12)
        (top / ".git" / "index").write_bytes(resigned(data[:end] + extra))
        assert gitfiles.unmerged(top / ".git", "") == ["gone.md", "new.md", "sub/both.md"]
    # A lowercase signature is one an index needs read, which this doesn't: git is asked.
    extra = b"zzzz" + (12).to_bytes(4, "big") + bytes(12)
    (top / ".git" / "index").write_bytes(resigned(data[:end] + extra))
    assert gitfiles.unmerged(top / ".git", "") is UNKNOWN


def test_a_split_index_or_a_sparse_one_asks_git(tmp_path: Path) -> None:
    top = conflicted(tmp_path / "r")
    git(top, "merge", "--abort")
    git(top, "update-index", "--split-index")
    assert b"link" in (top / ".git" / "index").read_bytes()
    assert gitfiles.unmerged(top / ".git", "") is UNKNOWN
    git(top, "update-index", "--no-split-index")
    assert gitfiles.unmerged(top / ".git", "") == []
    git(top, "sparse-checkout", "set", "--sparse-index", "--cone", "sub")
    assert b"sdir" in (top / ".git" / "index").read_bytes()
    assert gitfiles.unmerged(top / ".git", "") is UNKNOWN


def test_what_the_reader_can_t_vouch_for_asks_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = conflicted(tmp_path / "r")
    index = top / ".git" / "index"
    data = index.read_bytes()
    want = listed_unmerged(top)
    assert gitfiles.unmerged(top / ".git", "") == want
    # Past the cap; another version; torn; a byte changed under its hash; another index named; none at all.
    monkeypatch.setattr(gitfiles, "INDEX_CAP", len(data) - 1)
    assert gitfiles.unmerged(top / ".git", "") is UNKNOWN
    monkeypatch.undo()
    index.write_bytes(resigned(data[:4] + (5).to_bytes(4, "big") + data[8:-20]))
    assert gitfiles.unmerged(top / ".git", "") is UNKNOWN
    index.write_bytes(data[:-7])
    assert gitfiles.unmerged(top / ".git", "") is UNKNOWN
    index.write_bytes(data[:100] + bytes([data[100] ^ 1]) + data[101:])
    assert gitfiles.unmerged(top / ".git", "") is UNKNOWN
    index.write_bytes(data)
    monkeypatch.setenv("GIT_INDEX_FILE", str(index))
    assert gitfiles.unmerged(top / ".git", "") is UNKNOWN
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert gitfiles.unmerged(top / ".git", "") == want
    index.unlink()
    assert gitfiles.unmerged(top / ".git", "") is UNKNOWN
    # A repo with no commit: HEAD can't say how long its object ids are.
    fresh = repo(tmp_path / "fresh")
    (fresh / "a.txt").write_text("a\n", encoding="utf-8")
    git(fresh, "add", "a.txt")
    assert gitfiles.unmerged(fresh / ".git", "") is UNKNOWN


def test_an_index_git_writes_without_its_hash_or_with_sha256_reads(tmp_path: Path) -> None:
    top = conflicted(tmp_path / "r")
    git(top, "config", "index.skipHash", "true")
    (top / "late.md").write_text("late\n", encoding="utf-8")
    git(top, "add", "late.md")
    assert (top / ".git" / "index").read_bytes()[-20:] == bytes(20)
    assert gitfiles.unmerged(top / ".git", "") == listed_unmerged(top)
    other = repo(tmp_path / "s256", "--object-format=sha256")
    commit(other)
    (other / "b.txt").write_text("b\n", encoding="utf-8")
    git(other, "add", "b.txt")
    assert gitfiles.unmerged(other / ".git", "") == listed_unmerged(other) == []


def test_a_vault_refuses_a_conflict_the_index_shows_and_asks_git_where_it_can_t(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = fixture_vault(tmp_path)
    rel = "raw/chat/conflict.jsonl"
    blob = sh(vault.root, "git", "hash-object", "-w", "--", "vault.yaml").strip()
    info = "".join(f"100644 {blob} {n}\t{rel}\n" for n in (1, 2, 3))
    proc = subprocess.run(["git", "update-index", "--index-info"], cwd=vault.root, input=info.encode(), check=False)
    assert proc.returncode == 0
    with git_calls(monkeypatch) as calls, pytest.raises(UnmergedError, match=f"{rel} in .* has a conflict"):
        vault.refuse_unmerged()
    assert calls == []
    git(vault.root, "update-index", "--split-index")
    with git_calls(monkeypatch) as calls, pytest.raises(UnmergedError, match=f"{rel} in .* has a conflict"):
        vault.refuse_unmerged()
    assert calls == ["ls-files -u"]
