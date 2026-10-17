"""Where a vault's git and Docket's own files live: the top of a repo of its own, or a folder of the user's repo.

`vault.yaml` says which (`storage: repo` or `folder`), and the disk has to agree. In a repo of its own, Docket's
private files sit in its `.git` as they always have; in a folder of a host repo, they sit under the host's git dir, in
`docket/<vault path>/`, so nothing of Docket's ever shows in the user's tree.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from docket.errors import DocketError

Storage = Literal["repo", "folder"]
CONVERT_FILE = "convert.json"


@dataclass(frozen=True)
class Home:
    root: Path
    storage: Storage
    top: Path
    prefix: str  # the vault's path in its repo, with a trailing slash; empty for a repo of its own
    git_dir: Path
    private: Path

    @property
    def folder(self) -> bool:
        return self.storage == "folder"

    @property
    def lock_file(self) -> Path:
        return self.private / "docket.lock" if self.folder else self.git_dir / "docket.lock"

    @property
    def tmp_dir(self) -> Path:
        return self.private / "tmp"

    @property
    def sync_marker(self) -> Path:
        return self.private / "sync-rebase"

    @property
    def hooks_off(self) -> Path:
        """A file, not a folder, as git's hooks path for Docket's own calls: git finds no hook under a file, and
        git-lfs's filter, which writes its hooks into whatever folder the hooks path names, a missing one included,
        can't write there (DR-0260)."""
        return self.private / "hooks-off"

    @property
    def ceiling(self) -> Path | None:
        """In a repo of its own, git must never look above the vault: a vault whose .git went missing would commit
        into whatever repo holds it."""
        return None if self.folder else self.root.parent

    @staticmethod
    def resolve(root: Path, storage: Storage) -> Home:
        root = root.resolve()
        if storage == "repo":
            return _own_repo(root)
        return _folder(root)


def is_git_dir(path: Path) -> bool:
    """Git's own test for a git dir (`is_git_directory` in its setup.c): a HEAD file, objects/ and refs/. A .git that
    holds only Docket's folders fails it, and git skips such a folder and looks further up (DR-0270)."""
    return (path / "HEAD").is_file() and (path / "objects").is_dir() and (path / "refs").is_dir()


def make_private(git_dir: Path, folder: Path) -> Path:
    """Make a folder for Docket's own files under a git dir, never the git dir itself: the folders made on the way
    would pass for a .git, and the vault would stop saying its history is gone (DR-0270)."""
    if not is_git_dir(git_dir):
        raise DocketError(f"{git_dir} is missing or isn't a git repository; Docket writes nothing until it is restored")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def rev_parse(cwd: Path, *flags: str) -> list[str] | None:
    """`git rev-parse` with these flags, one answer per line; None outside a repo."""
    proc = subprocess.run(
        ["git", "rev-parse", *flags],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env={**os.environ},
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.splitlines()


def _own_repo(root: Path) -> Home:
    if (root / ".git").is_file():
        raise DocketError(
            f"{root}/.git is a link to a repository elsewhere (a submodule or a linked worktree); Docket works only in "
            "the vault's own checkout, with its .git folder"
        )
    if not is_git_dir(root / ".git"):
        found = " (the .git there holds no repository, only what was left in it)" if (root / ".git").exists() else ""
        raise DocketError(
            f"{root}/vault.yaml says storage: repo, but there is no .git in {root}{found}; restore it, or set storage: "
            "folder if the vault is a folder of the repo around it"
        )
    git_dir = root / ".git"
    return Home(root, "repo", root, "", git_dir, git_dir / "docket")


@dataclass(frozen=True)
class Found:
    """Where git finds the repo around a folder: its top, the folder's path in it, its git dir and common dir."""

    top: Path
    prefix: str
    git_dir: Path
    common: Path


# The variables that tell git where a repo is instead of letting it look: with one set, git is asked every time.
_GIT_PLACES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
)
# What git answered per folder, in this process, for a plain checkout (DR-0532).
_FOUND: dict[Path, Found] = {}


def discover(root: Path) -> Found | None:
    """Where git finds the repo around a folder, or None outside one. Git is asked once per folder and process; after
    that its answer stands while a few file checks say git would give it again: the git dir is still a repository and
    no folder between this one and the top holds a .git git would stop at (DR-0532). A folder vault opens on every
    call of the service, and each `git rev-parse` costs a process start."""
    if not any(os.environ.get(name) for name in _GIT_PLACES):
        found = _FOUND.get(root)
        if found is not None and _stands(root, found):
            return found
    answer = rev_parse(
        root, "--show-toplevel", "--show-prefix", "--absolute-git-dir", "--path-format=absolute", "--git-common-dir"
    )
    if answer is None:
        _FOUND.pop(root, None)
        return None
    top, prefix, git_dir, common = answer
    found = Found(Path(top).resolve(), prefix, Path(git_dir).resolve(), Path(common).resolve())
    # Only a plain checkout is kept, whose .git is the repository: a linked worktree's or a submodule's .git file
    # points elsewhere, and the checks above couldn't vouch for it.
    if found.git_dir == found.common == found.top / ".git" and is_git_dir(found.git_dir):
        _FOUND[root] = found
    else:
        _FOUND.pop(root, None)
    return found


def _stands(root: Path, found: Found) -> bool:
    """Whether git would find the same repo from the folder: its git dir still a repository, and no .git that git
    takes (a file, or a folder that passes `is_git_dir`) in the folder or any folder up to the top."""
    if not is_git_dir(found.git_dir):
        return False
    folder = root
    while folder != found.top:
        dot = folder / ".git"
        if dot.is_file() or is_git_dir(dot) or folder.parent == folder:
            return False
        folder = folder.parent
    return True


def _folder(root: Path) -> Home:
    if (root / ".git").exists():
        raise DocketError(
            f"{root}/vault.yaml says storage: folder, but {root} has a .git of its own; a folder vault is part of the "
            "repo around it"
        )
    found = discover(root)
    if found is None:
        raise DocketError(f"{root}/vault.yaml says storage: folder, but {root} isn't inside a git repo")
    # The prefix is never empty: the top of a repo has a .git, which a folder vault refuses above.
    if found.common != found.git_dir:
        main = found.common.parent / found.prefix.rstrip("/")
        raise DocketError(
            f"{root} is in a linked worktree; Docket writes the vault in the main checkout: {main} (pass --vault)"
        )
    if (found.top / "vault.yaml").is_file():
        raise DocketError(f"{root} is inside another vault, {found.top}; a vault can't hold another")
    private = found.git_dir.joinpath("docket", *found.prefix.rstrip("/").split("/"))
    return Home(root, "folder", found.top, found.prefix, found.git_dir, private)


def stopped_convert(root: Path) -> Path | None:
    """The state file of a convert that stopped part way, for a vault without a .git of its own: the host's git dir
    keeps it under the vault's path."""
    root = root.resolve()
    if is_git_dir(root / ".git"):
        return None
    found = discover(root)
    if found is None or not found.prefix:
        return None
    state = found.git_dir.joinpath("docket", *found.prefix.rstrip("/").split("/"), CONVERT_FILE)
    return state if state.is_file() else None
