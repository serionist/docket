"""The git calls the vault makes. Committing is always local; talking to a remote is a separate step that never
blocks a commit."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from docket.errors import DocketError


@dataclass(frozen=True)
class Identity:
    name: str
    email: str


@dataclass(frozen=True)
class SyncResult:
    fetched: bool
    rebased: bool
    message: str


class Git:
    def __init__(self, root: Path, fallback: Identity | None = None) -> None:
        self.root = root
        self.fallback = fallback

    def run(
        self,
        *args: str,
        check: bool = True,
        env: dict[str, str] | None = None,
        identity: bool = False,
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        config: list[str] = []
        if identity and self.fallback is not None and not self.has_identity():
            config = ["-c", f"user.name={self.fallback.name}", "-c", f"user.email={self.fallback.email}"]
        proc = subprocess.run(
            ["git", *config, *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env={**os.environ, **(env or {})},
            input=stdin,
        )
        if check and proc.returncode != 0:
            raise DocketError(f"git {args[0]}: {(proc.stderr or proc.stdout).strip()}")
        return proc

    def out(self, *args: str) -> str:
        return self.run(*args).stdout

    def has_identity(self) -> bool:
        name = self.run("config", "user.name", check=False).stdout.strip()
        email = self.run("config", "user.email", check=False).stdout.strip()
        return bool(name and email)

    def init(self) -> None:
        self.run("init", "-q", "-b", "main")

    def lfs_install(self) -> bool:
        """Set up git-lfs for this repo; False when git-lfs isn't installed (media waits until it is)."""
        if shutil.which("git-lfs") is None:
            return False
        return self.run("lfs", "install", "--local", check=False).returncode == 0

    def commit(self, paths: list[str], message: str) -> str | None:
        """Add the paths and commit them; None when there was nothing to commit."""
        if paths:
            # On stdin, not the command line: a backlog of run files would pass Windows' 32,767-character limit.
            self.run("add", "--pathspec-from-file=-", "--pathspec-file-nul", stdin="\0".join(paths))
        if self.run("diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        self.run("commit", "-q", "-m", message, identity=True)
        return self.out("rev-parse", "HEAD").strip()

    def untracked(self, under: str) -> list[str]:
        out = self.out("ls-files", "-z", "--others", "--exclude-standard", "--", under)
        return [p for p in out.split("\0") if p]

    def changed_against_head(self, under: str) -> list[str]:
        """Tracked files under a path that differ from HEAD, staged or not, deletions included."""
        if self.run("rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode != 0:
            return []
        out = self.out("diff", "--name-only", "-z", "HEAD", "--", under)
        return [p for p in out.split("\0") if p]

    def branch(self) -> str:
        return self.out("rev-parse", "--abbrev-ref", "HEAD").strip()

    def has_remote(self, name: str = "origin") -> bool:
        return name in self.out("remote").split()

    def last_commit(self) -> str | None:
        proc = self.run("log", "-1", "--format=%h %cs %s", check=False)
        return proc.stdout.strip() or None

    def sync(self) -> SyncResult:
        """Fetch, and rebase local commits onto the remote branch when there is one. Never loses a local commit."""
        if not self.has_remote():
            return SyncResult(False, False, "no remote")
        env = {"GIT_TERMINAL_PROMPT": "0"}
        if self.run("fetch", "-q", "origin", check=False, env=env).returncode != 0:
            return SyncResult(False, False, "couldn't reach the remote; the commits stay local")
        upstream = f"origin/{self.branch()}"
        if self.run("rev-parse", "--verify", "--quiet", upstream, check=False).returncode != 0:
            return SyncResult(True, False, f"{upstream} doesn't exist yet")
        proc = self.run("rebase", "--autostash", "-q", upstream, check=False, identity=True)
        if proc.returncode != 0:
            self.run("rebase", "--abort", check=False)
            return SyncResult(True, False, f"the rebase onto {upstream} conflicted and was undone; resolve it by hand")
        return SyncResult(True, True, f"rebased onto {upstream}")


def toplevel(path: Path) -> Path | None:
    """The git repo a folder is in, or None."""
    probe = path
    while not probe.exists():
        probe = probe.parent
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=probe,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None
