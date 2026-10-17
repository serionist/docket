"""The git calls the vault makes. Committing is always local; talking to a remote is a separate step that never
blocks a commit.

In a folder of the user's repo (the host), git runs from the vault's folder: pathspecs are relative to it, but some
names git prints are relative to the host's top, and the index and its lock are the host's. In both modes Docket's
commits, and sync's fetch and rebase, run no hooks; a push runs them (DR-0260, DR-0261).

What the git dir's files hold is read from them, with no git call: whether HEAD is on a branch, HEAD's commit id
(DR-0528). A question is asked once while its answer can't change (DR-0529), and git's identity once per Git, which an
opened vault owns for one command or one call of the service (DR-0530).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from docket.childproc import console_flags
from docket.errors import DocketError
from docket.vault import gitfiles
from docket.vault.home import Home, make_private

# A processed doc's rev is the commit whose message has this line, in its subject or its body (DR-0112).
DOC_LINE = re.compile(r"^Doc: (\S+) rev ([1-9]\d*)(?: \(edited by hand\))?$")
# Another git holding the index (an editor refreshing its status) lets go within moments; past this, it is reported.
INDEX_LOCK_WAIT_SECONDS = 3.0
_INDEX_LOCK_PAUSE = 0.1
_PATHSPEC = ("--pathspec-from-file=-", "--pathspec-file-nul")
NO_SIGNING = ("-c", "commit.gpgsign=false")
HOOKS_OFF_TEXT = (
    "Docket points core.hooksPath at this file for its own commits and sync's rebase, so no hook runs for them. It is\n"
    "a file, not a folder, because git-lfs writes its hooks into any folder there.\n"
)


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
        self.git_dir = home.git_dir if home is not None else root / ".git"
        # Asked once for this Git's life: one command, or one call of the service (DR-0530).
        self._identity: bool | None = None
        # HEAD's blobs by commit id and path: a commit's content never changes.
        self._blobs: dict[tuple[str, str], str] = {}

    def run(
        self,
        *args: str,
        check: bool = True,
        env: dict[str, str] | None = None,
        identity: bool = False,
        stdin: str | None = None,
        hooks: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        # Docket's commits are machine commits, so they never sign, and neither does sync's rebase, which writes
        # commits again: a service with no terminal would hang on a pinentry prompt or fail on a key it can't unlock.
        config: list[str] = list(NO_SIGNING)
        # Hooks are off only where a call says so: a push that skipped git-lfs's pre-push would leave media off the
        # remote, and a later push of the same branch wouldn't send it.
        if not hooks and self.home is not None:
            config += ["-c", f"core.hooksPath={_hooks_off(self.home).as_posix()}"]
        if identity and self.fallback is not None and not self._has_identity_once():
            config += ["-c", f"user.name={self.fallback.name}", "-c", f"user.email={self.fallback.email}"]
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
                creationflags=console_flags(),
            )
            held = proc.returncode != 0 and "index.lock" in proc.stderr and "File exists" in proc.stderr
            if not held or time.monotonic() >= deadline:
                break
            time.sleep(_INDEX_LOCK_PAUSE)
        if check and proc.returncode != 0:
            message = (proc.stderr or proc.stdout).strip()
            if held:
                message += self._index_lock_age()
            # The command's name, past any -c options before it.
            name = next(a for i, a in enumerate(args) if a != "-c" and (i == 0 or args[i - 1] != "-c"))
            raise DocketError(f"git {name}: {message}")
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
        # Both keys in one call. git lists a key's values from every level, the one it uses last, as `git config
        # user.name` would answer.
        out = self.run("config", "--get-regexp", "^user[.](name|email)$", check=False).stdout
        found: dict[str, str] = {}
        for line in out.splitlines():
            key, _, value = line.partition(" ")
            found[key] = value.strip()
        return bool(found.get("user.name") and found.get("user.email"))

    def _has_identity_once(self) -> bool:
        if self._identity is None:
            self._identity = self.has_identity()
        return self._identity

    def on_branch(self) -> bool:
        """Whether HEAD names a branch rather than a commit (a detached HEAD): from HEAD's file, else from git."""
        ref = gitfiles.head_ref(self.git_dir)
        if isinstance(ref, gitfiles.Unknown):
            return self.branch() != "HEAD"
        return ref is not None

    def head_id(self) -> str | None:
        """HEAD's commit id, None before the first commit: from the git dir's files, else from git."""
        found = gitfiles.head_id(self.git_dir)
        if not isinstance(found, gitfiles.Unknown):
            return found
        proc = self.run("rev-parse", "--verify", "--quiet", "HEAD", check=False)
        return proc.stdout.strip() or None if proc.returncode == 0 else None

    def show_at_head(self, rel: str) -> str:
        """A vault file's text at HEAD, empty when HEAD lacks it; kept by HEAD's commit id, whose content never
        changes. `./` reads the vault's own path: in a folder of the user's repo, `HEAD:<rel>` is the repo's."""
        head = gitfiles.head_id(self.git_dir)
        key = (head, rel) if isinstance(head, str) else None
        if key is not None and key in self._blobs:
            return self._blobs[key]
        text = self.run("show", f"HEAD:./{rel}", check=False).stdout
        if key is not None:
            self._blobs[key] = text
        return text

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
        # The repo's hooks are written for the user's commits: a formatter would rewrite a raw file written once, a
        # commit-msg rule would refuse or rewrite a Doc: line, and git-lfs's post-commit is most of a commit's time on
        # Windows. On the add too: git-lfs's filter writes its own hooks into the hooks path when it runs, which for a
        # host like husky's is the user's tree.
        try:
            self.run("add", *_PATHSPEC, stdin=spec, hooks=False)
            # `-- .` and the prefix, not --relative: that crashes git 2.47.0.windows.2 while a stash pop conflicts.
            staged = {p for p in self.out("diff", "--cached", "--name-only", "-z", "--", ".").split("\0") if p}
            if not staged & {self.prefix + Path(p).as_posix() for p in paths}:
                return None
            self.run("commit", "-q", "--only", *_PATHSPEC, "-m", message, identity=True, stdin=spec, hooks=False)
        except BaseException:
            if folder:
                self.run("reset", "-q", *_PATHSPEC, check=False, stdin=spec, hooks=False)
            raise
        # The branch's ref file holds the new id the moment the commit returns (DR-0528).
        found = self.head_id()
        return found if found is not None else self.out("rev-parse", "HEAD").strip()

    def _vault_names(self, out: str) -> list[str]:
        """Names git printed from the repo's top, made relative to the vault."""
        return [p.removeprefix(self.prefix) for p in out.split("\0") if p]

    def untracked(self, under: str, *, ignored_too: bool = False) -> list[str]:
        """Files under a path git doesn't track that its ignore rules let through, or with `ignored_too` every one,
        hidden or not, in one call."""
        rules = [] if ignored_too else ["--exclude-standard"]
        out = self.out("ls-files", "-z", "--others", *rules, "--", under)
        return [p for p in out.split("\0") if p]

    def staged_new(self, under: str) -> list[str]:
        """Files under a path that are staged but not in HEAD: what a crash between add and commit leaves."""
        return self._vault_names(self.out("diff", "--cached", "--name-only", "--diff-filter=A", "-z", "--", under))

    def not_in_head(self, under: str) -> list[str]:
        """What `untracked(under, ignored_too=True)` and `staged_new(under)` list together, in one status call
        (DR-0529): `??` and `!!` are the files git doesn't track, hidden or not, and `A` in the first column a file
        staged but not in HEAD. A staged rename or copy names its source in the next field, which belongs to it. No
        optional locks, so the status never rewrites the index."""
        out = self.run(
            "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=traditional", "--", under,
            env={"GIT_OPTIONAL_LOCKS": "0"},
        ).stdout  # fmt: skip
        fields = out.split("\0")
        found: list[str] = []
        i = 0
        while i < len(fields):
            entry = fields[i]
            i += 1
            if len(entry) < 4:
                continue
            code, path = entry[:2], entry[3:]
            if code[0] in "RC":
                i += 1
            if code in ("??", "!!") or code[0] == "A":
                found.append(path)
        return self._vault_names("\0".join(found))

    def changed_against_head(self, under: str) -> list[str]:
        """Tracked files under a path that differ from HEAD, staged or not, deletions included."""
        if self.head_id() is None:
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
        # Sync's fetch and rebase run no hooks either: the rebase runs pre-rebase, post-checkout, post-commit for every
        # commit it picks and post-rewrite, so a user's refusing pre-rebase would stop every sync (DR-0261).
        if self.run("fetch", "-q", "origin", check=False, env=env, hooks=False).returncode != 0:
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
        make_private(self.root / ".git", marker.parent)
        marker.write_text(upstream, encoding="utf-8")
        # The marker is removed only once the rebase is over. Interrupted (Ctrl+C, a kill), it stays, so the next
        # write knows the rebase left behind is Docket's and undoes it.
        proc = self.run("rebase", "--autostash", "-q", upstream, check=False, identity=True, hooks=False)
        if proc.returncode != 0:
            self.run("rebase", "--abort", check=False, hooks=False)
            marker.unlink(missing_ok=True)
            return SyncResult(True, False, f"the rebase onto {upstream} conflicted and was undone; resolve it by hand")
        marker.unlink(missing_ok=True)
        return SyncResult(True, True, f"rebased onto {upstream}")


def sync_marker(root: Path) -> Path:
    return root / ".git" / "docket" / "sync-rebase"


def _hooks_off(home: Home) -> Path:
    """The file Docket points the hooks path at, written when it is missing: a missing path is no better than a folder,
    since git-lfs's filter creates it and writes its hooks there."""
    path = home.hooks_off
    # Refused, not skipped, when the git dir went missing: in a folder vault git would go on to the repo above the
    # host and run Docket's add there (DR-0270).
    make_private(home.git_dir, path.parent)
    if not path.is_file():
        path.write_text(HOOKS_OFF_TEXT, encoding="utf-8")
    return path


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
        creationflags=console_flags(),
    )
    return Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None
