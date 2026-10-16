"""Listing a folder Docket only reads: the notes import's and a folder source's (DR-0480).

In a git repo the folder is listed through git's own ignore rules, with git told not to refresh that repo's index, so a
file the user kept out of git never reaches the vault and nothing in the repo changes; outside a repo it is walked,
dot folders and nested repos left out. Either way a path with a link or a junction in it under the folder is left out
and counted, and so is the vault. The folder itself may be a link or a junction: the caller resolves it first.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from docket.errors import DocketError
from docket.vault.git import Git

# A git command run in the folder's repo must not refresh that repo's index file, which plain status-like calls may do.
GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0"}
# A file whose bytes aren't on disk, which reading would make a sync client download: Windows' recall-on-data-access,
# recall-on-open and offline attributes, and macOS's SF_DATALESS flag.
RECALL_ATTRIBUTES = 0x400000 | 0x40000 | 0x1000
SF_DATALESS = 0x40000000


@dataclass(frozen=True)
class Found:
    """A file in the folder: its path inside it (NFC, /-separated), its path on disk, and its lstat."""

    rel: str
    path: Path
    info: os.stat_result


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return Git(cwd).run(*args, check=False, env=GIT_ENV)


def repo_root(folder: Path) -> Path | None:
    """The git repo the folder is in, or None. A repo git refuses to read stops the listing: reading it without its
    ignore rules could take in what the user kept out of git."""
    if not any((p / ".git").exists() for p in (folder, *folder.parents)):
        return None
    proc = _git(folder, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        why = (proc.stderr or proc.stdout).strip()
        raise DocketError(f"git can't read the repo {folder} is in, so its ignore rules can't be kept: {why}")
    return Path(proc.stdout.strip()).resolve()


def is_link(path: Path) -> bool:
    """A symbolic link, or on Windows a junction, which `is_symlink` doesn't see."""
    return path.is_symlink() or path.is_junction()


def placeholder(info: os.stat_result) -> bool:
    """Whether a file's bytes are elsewhere, by its attributes on Windows and its flags on macOS."""
    flags = getattr(info, "st_flags", 0) if sys.platform == "darwin" else 0
    return bool(getattr(info, "st_file_attributes", 0) & RECALL_ATTRIBUTES or flags & SF_DATALESS)


def list_folder(folder: Path, repo: Path | None, vault_root: Path, skipped: Counter[str]) -> list[Found]:
    """Every file in the folder, in path order; links, junctions and nested repos are counted only, and anything that
    isn't a regular file is counted as such."""
    found = _from_git(folder, repo, vault_root, skipped) if repo is not None else _walk(folder, vault_root, skipped)
    return sorted(found, key=lambda f: f.rel)


def _from_git(folder: Path, repo: Path, vault_root: Path, skipped: Counter[str]) -> list[Found]:
    rel = folder.relative_to(repo).as_posix()
    proc = _git(repo, "ls-files", "-z", "-co", "--exclude-standard", "--", rel)
    if proc.returncode != 0:
        raise DocketError(f"git couldn't list {folder}: {(proc.stderr or proc.stdout).strip()}")
    base = "" if rel == "." else f"{rel}/"
    linked: dict[Path, bool] = {}

    def under_link(path: Path) -> bool:
        """Whether a folder between the listed folder and this path is a link or a junction, each asked once."""
        parent = path.parent
        if parent == folder:
            return False
        if parent not in linked:
            linked[parent] = is_link(parent) or under_link(parent)
        return linked[parent]

    out: list[Found] = []
    for entry in sorted({e.rstrip("/") for e in proc.stdout.split("\0") if e}):
        path = repo / entry
        if path == vault_root or vault_root in path.parents:
            continue
        if under_link(path) or is_link(path):
            skipped["links"] += 1
            continue
        item = _file(path, entry.removeprefix(base), skipped)
        if item is not None:
            out.append(item)
    return out


def _walk(folder: Path, vault_root: Path, skipped: Counter[str]) -> list[Found]:
    out: list[Found] = []
    stack = [folder]
    while stack:
        here = stack.pop()
        with os.scandir(here) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.name.startswith(".") or path == vault_root:
                    continue
                if entry.is_symlink() or entry.is_junction():
                    skipped["links"] += 1
                elif entry.is_dir(follow_symlinks=False):
                    if (path / ".git").exists():
                        skipped["nested repos"] += 1
                    else:
                        stack.append(path)
                else:
                    rel = path.relative_to(folder).as_posix()
                    item = _file(path, rel, skipped, entry.stat(follow_symlinks=False))
                    if item is not None:
                        out.append(item)
    return out


def _file(path: Path, rel: str, skipped: Counter[str], info: os.stat_result | None = None) -> Found | None:
    try:
        info = info or path.lstat()
    except OSError:
        # Gone since it was listed.
        return None
    if stat.S_ISDIR(info.st_mode):
        # A folder git lists is a nested repo or a submodule.
        skipped["nested repos"] += 1
        return None
    if not stat.S_ISREG(info.st_mode):
        # A FIFO, a socket or a device: reading one can block.
        skipped["not files"] += 1
        return None
    return Found(unicodedata.normalize("NFC", rel), path, info)
