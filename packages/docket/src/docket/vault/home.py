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


def _folder(root: Path) -> Home:
    if (root / ".git").exists():
        raise DocketError(
            f"{root}/vault.yaml says storage: folder, but {root} has a .git of its own; a folder vault is part of the "
            "repo around it"
        )
    answer = rev_parse(
        root, "--show-toplevel", "--show-prefix", "--absolute-git-dir", "--path-format=absolute", "--git-common-dir"
    )
    if answer is None:
        raise DocketError(f"{root}/vault.yaml says storage: folder, but {root} isn't inside a git repo")
    # The prefix is never empty: the top of a repo has a .git, which a folder vault refuses above.
    top, prefix, git_dir, common = answer
    top_path = Path(top).resolve()
    common_dir = Path(common).resolve()
    git_path = Path(git_dir).resolve()
    if common_dir != git_path:
        main = common_dir.parent / prefix.rstrip("/")
        raise DocketError(
            f"{root} is in a linked worktree; Docket writes the vault in the main checkout: {main} (pass --vault)"
        )
    if (top_path / "vault.yaml").is_file():
        raise DocketError(f"{root} is inside another vault, {top_path}; a vault can't hold another")
    return Home(root, "folder", top_path, prefix, git_path, git_path.joinpath("docket", *prefix.rstrip("/").split("/")))


def stopped_convert(root: Path) -> Path | None:
    """The state file of a convert that stopped part way, for a vault without a .git of its own: the host's git dir
    keeps it under the vault's path."""
    root = root.resolve()
    if is_git_dir(root / ".git"):
        return None
    answer = rev_parse(root, "--absolute-git-dir", "--show-prefix")
    if answer is None or not answer[1]:
        return None
    state = Path(answer[0]).joinpath("docket", *answer[1].rstrip("/").split("/"), CONVERT_FILE)
    return state if state.is_file() else None
