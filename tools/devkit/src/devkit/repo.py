"""The repo the kit works on, and the git calls every tool shares."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class KitError(Exception):
    """A failure the user acts on: printed as one line, without a traceback."""


def repo_root() -> Path:
    """The repo root: `DOCKET_REPO_ROOT` when set (the tests use it), else git's top level from here."""
    env = os.environ.get("DOCKET_REPO_ROOT")
    if env:
        return Path(env).resolve()
    try:
        out = git(Path.cwd(), "rev-parse", "--show-toplevel")
    except KitError as e:
        raise KitError("not inside a git repository") from e
    return Path(out.strip()).resolve()


def git(root: Path, *args: str, check: bool = True) -> str:
    """Run git in `root` and return its stdout. A failure raises KitError with git's own message."""
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if check and proc.returncode != 0:
        raise KitError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def listed_files(root: Path) -> list[str]:
    """Every file git tracks or would add (untracked and not ignored), as posix paths relative to the root."""
    out = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    seen: dict[str, None] = {}
    for name in out.split("\0"):
        if name and (root / name).is_file():
            seen[name] = None
    return sorted(seen)


def default_base(root: Path) -> str | None:
    """Where the current branch left main: the merge base with origin/main, or None on main itself or without it."""
    if git(root, "rev-parse", "--verify", "--quiet", "origin/main", check=False).strip() == "":
        return None
    base = git(root, "merge-base", "origin/main", "HEAD", check=False).strip()
    head = git(root, "rev-parse", "HEAD", check=False).strip()
    if not base or base == head:
        return None
    return base


def user_name(root: Path) -> str:
    """git's user.name, the default author of a decision."""
    return git(root, "config", "user.name", check=False).strip() or "unknown"
