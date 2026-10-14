"""The git calls the vault makes. Committing is always local; talking to a remote is a separate step that never
blocks a commit.

In a folder of the user's repo (the host), git runs from the vault's folder: pathspecs are relative to it, but some
names git prints are relative to the host's top, and the index, its lock and the hooks are the host's.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from docket.errors import DocketError
from docket.vault.home import Home

# A processed doc's rev is the commit whose message has this line, in its subject or its body (DR-0112).
DOC_LINE = re.compile(r"^Doc: (\S+) rev ([1-9]\d*)(?: \(edited by hand\))?$")
# Another git holding the index (an editor refreshing its status) lets go within moments; past this, it is reported.
INDEX_LOCK_WAIT_SECONDS = 3.0
_INDEX_LOCK_PAUSE = 0.1
_PATHSPEC = ("--pathspec-from-file=-", "--pathspec-file-nul")


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
    def __init__(self, root: Path, fallback: Identity | None = None, home: Home | None = None) -> None:
        self.root = root
        self.fallback = fallback
        self.home = home
        self.prefix = home.prefix if home is not None else ""

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
        extra = dict(env or {})
        ceiling = self.home.ceiling if self.home is not None else None
        if ceiling is not None:
            extra["GIT_CEILING_DIRECTORIES"] = str(ceiling)
        deadline = time.monotonic() + INDEX_LOCK_WAIT_SECONDS
        while True:
            proc = subprocess.run(
                ["git", *config, *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                env={**os.environ, **extra},
                input=stdin,
            )
            held = proc.returncode != 0 and "index.lock" in proc.stderr and "File exists" in proc.stderr
            if not held or time.monotonic() >= deadline:
                break
            time.sleep(_INDEX_LOCK_PAUSE)
        if check and proc.returncode != 0:
            message = (proc.stderr or proc.stdout).strip()
            if held:
                message += self._index_lock_age()
            raise DocketError(f"git {args[0]}: {message}")
        return proc

    def _index_lock_age(self) -> str:
        git_dir = self.home.git_dir if self.home is not None else self.root / ".git"
        try:
            age = time.time() - (git_dir / "index.lock").stat().st_mtime
        except FileNotFoundError:
            return ""
        return f" (index.lock is {int(age // 60)} minute(s) old; if no git is running, delete it)"

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
        """Commit exactly these file paths (files, not folders); None when they hold nothing new. Whatever else the
        user staged stays staged and out of Docket's commit."""
        if not paths:
            return None
        # On stdin, not the command line: a backlog of run files would pass Windows' 32,767-character limit.
        spec = "\0".join(paths)
        folder = self.home is not None and self.home.folder
        try:
            self.run("add", *_PATHSPEC, stdin=spec)
            # `-- .` and the prefix, not --relative: that crashes git 2.47.0.windows.2 while a stash pop conflicts.
            staged = {p for p in self.out("diff", "--cached", "--name-only", "-z", "--", ".").split("\0") if p}
            if not staged & {self.prefix + Path(p).as_posix() for p in paths}:
                return None
            hooks: list[str] = []
            if folder and self.home is not None:
                # The host's hooks are written for the user's commits: a formatter would rewrite a raw file written
                # once, a commit-msg rule would refuse or rewrite a Doc: line.
                hooks = ["-c", f"core.hooksPath={self.home.no_hooks.as_posix()}"]
            self.run(*hooks, "commit", "-q", "--only", *_PATHSPEC, "-m", message, identity=True, stdin=spec)
        except BaseException:
            if folder:
                self.run("reset", "-q", *_PATHSPEC, check=False, stdin=spec)
            raise
        return self.out("rev-parse", "HEAD").strip()

    def _vault_names(self, out: str) -> list[str]:
        """Names git printed from the repo's top, made relative to the vault."""
        return [p.removeprefix(self.prefix) for p in out.split("\0") if p]

    def untracked(self, under: str) -> list[str]:
        out = self.out("ls-files", "-z", "--others", "--exclude-standard", "--", under)
        return [p for p in out.split("\0") if p]

    def staged_new(self, under: str) -> list[str]:
        """Files under a path that are staged but not in HEAD: what a crash between add and commit leaves."""
        return self._vault_names(self.out("diff", "--cached", "--name-only", "--diff-filter=A", "-z", "--", under))

    def changed_against_head(self, under: str) -> list[str]:
        """Tracked files under a path that differ from HEAD, staged or not, deletions included."""
        if self.run("rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode != 0:
            return []
        return self._vault_names(self.out("diff", "--name-only", "-z", "HEAD", "--", under))

    def branch(self) -> str:
        return self.out("rev-parse", "--abbrev-ref", "HEAD").strip()

    def has_remote(self, name: str = "origin") -> bool:
        return name in self.out("remote").split()

    def last_commit(self) -> str | None:
        proc = self.run("log", "-1", "--no-show-signature", "--format=%h %cs %s", "--", ".", check=False)
        return proc.stdout.strip() or None

    def doc_revs(self, commits: str) -> set[tuple[str, int]]:
        """The (doc, rev) pairs the `Doc:` lines in a range of commits' messages name. A user's log.showSignature
        would put signature lines into the output, so it is turned off."""
        out = self.out("log", "--no-show-signature", "--format=%B", commits)
        return {(m.group(1), int(m.group(2))) for line in out.splitlines() if (m := DOC_LINE.match(line.strip()))}

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
        # Two machines that both wrote the same rev of a doc, on lines far apart, would rebase cleanly into two
        # commits claiming one rev and a merged text nobody checked; that is a conflict, whatever git thinks.
        both = self.doc_revs(f"{upstream}..HEAD") & self.doc_revs(f"HEAD..{upstream}")
        if both:
            named = ", ".join(f"{doc} rev {rev}" for doc, rev in sorted(both))
            return SyncResult(True, False, f"this machine and {upstream} both wrote {named}; resolve it by hand")
        # The marker says this rebase is Docket's own: one a killed sync leaves behind may be undone, a user's never.
        marker = sync_marker(self.root)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(upstream, encoding="utf-8")
        # The marker is removed only once the rebase is over. Interrupted (Ctrl+C, a kill), it stays, so the next
        # write knows the rebase left behind is Docket's and undoes it.
        proc = self.run("rebase", "--autostash", "-q", upstream, check=False, identity=True)
        if proc.returncode != 0:
            self.run("rebase", "--abort", check=False)
            marker.unlink(missing_ok=True)
            return SyncResult(True, False, f"the rebase onto {upstream} conflicted and was undone; resolve it by hand")
        marker.unlink(missing_ok=True)
        return SyncResult(True, True, f"rebased onto {upstream}")


def sync_marker(root: Path) -> Path:
    return root / ".git" / "docket" / "sync-rebase"


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
