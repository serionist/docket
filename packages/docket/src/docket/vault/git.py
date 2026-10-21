"""The git calls the vault makes. Committing is always local; talking to a remote is a separate step that never
blocks a commit.

In a folder of the user's repo (the host), git runs from the vault's folder: pathspecs are relative to it, but some
names git prints are relative to the host's top, and the index and its lock are the host's. In both modes Docket's
commits, and sync's fetch and rebase, run no hooks; a push runs them (DR-0260, DR-0261).

What the git dir's files hold is read from them, with no git call: whether HEAD is on a branch, HEAD's commit id
(DR-0765). A question is asked once while its answer can't change (DR-0529). A commit is an add and a commit: the
staged check and git's identity are asked only when the commit fails (DR-0764). A Git's blob reads share one `git
cat-file --batch`, which an opened vault owns for one command or one call of the service (DR-0766).
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, cast

from docket.childproc import console_flags
from docket.errors import DocketError
from docket.vault import gitfiles
from docket.vault.home import Home, is_git_dir, make_private, repo_at, told_where

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
# A commit takes git's identity only from its config and environment, never one git guesses from the host's name, so
# a machine with none fails the commit and Docket commits again with the owner's (DR-0764).
STRICT_IDENTITY = ("-c", "user.useConfigOnly=true")
# How long a closing batch reader gets to exit on its own once its input is closed.
_BATCH_EXIT_SECONDS = 5.0
# Status codes whose path `git diff --name-only HEAD` lists, and those `not_in_head` lists: untracked, ignored, or
# staged but not in HEAD (DR-0764). A file added and then deleted (AD) is the second only; any code in neither is a
# state the status can't settle.
_CHANGED = frozenset({" M", " T", " A", "M ", "T ", "MD", "TD", "D ", "A ", "AM", "AT"})
_NEW = frozenset({"??", "!!", "A ", "AM", "AT", "AD"})
# Every Git whose batch reader is open, for `close_all`.
_OPEN: weakref.WeakSet[Git] = weakref.WeakSet()
_OPEN_LOCK = threading.Lock()
# The config files' sizes and times, and the GIT_CONFIG variables, an answer git gave from config stands on.
Stamp = tuple[object, ...]
# `toplevel`'s answers in this process, by the folder git was asked from (DR-0767).
_TOPS: dict[Path, tuple[Path | None, Stamp]] = {}
# The answers git gave from a repo's config in this process, by its top and the question (DR-0767).
_CONFIGURED: dict[tuple[Path, str], tuple[Stamp, object]] = {}


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
        # Asked at most once for this Git's life, one command or one call of the service: by sync's rebase before it
        # runs, by a commit only once it failed (DR-0764).
        self._identity: bool | None = None
        # HEAD's blobs by commit id and path: a commit's content never changes.
        self._blobs: dict[tuple[str, str], str] = {}
        # The parents of the last commit this made, from its object: the settings in force move their cache
        # forward on a commit of Docket's own with no call of their own (DR-0521).
        self.parents: tuple[str, ...] = ()
        # The batch reader, started by the first blob read and kept until `close` (DR-0766).
        self._batch: subprocess.Popen[bytes] | None = None
        self._batch_lock = threading.Lock()
        self._closer: weakref.finalize[[subprocess.Popen[bytes]], Git] | None = None

    def __enter__(self) -> Git:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _env(self, env: dict[str, str] | None = None) -> dict[str, str]:
        extra = dict(env or {})
        ceiling = self.home.ceiling if self.home is not None else None
        if ceiling is not None:
            extra["GIT_CEILING_DIRECTORIES"] = str(ceiling)
        return {**os.environ, **extra}

    def run(
        self,
        *args: str,
        check: bool = True,
        env: dict[str, str] | None = None,
        identity: bool = False,
        stdin: str | None = None,
        hooks: bool = True,
        strict: bool = False,
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
        if strict:
            config += STRICT_IDENTITY
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
                env=self._env(env),
                input=stdin,
                creationflags=console_flags(),
            )
            if not _index_held(proc) or time.monotonic() >= deadline:
                break
            time.sleep(_INDEX_LOCK_PAUSE)
        if check and proc.returncode != 0:
            raise self._failed(proc, args)
        return proc

    def _failed(self, proc: subprocess.CompletedProcess[str], args: tuple[str, ...]) -> DocketError:
        message = (proc.stderr or proc.stdout).strip()
        if _index_held(proc):
            message += self._index_lock_age()
        # The command's name, past any -c options before it.
        name = next(a for i, a in enumerate(args) if a != "-c" and (i == 0 or args[i - 1] != "-c"))
        return DocketError(f"git {name}: {message}")

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
        changes. `./` reads the vault's own path: in a folder of the user's repo, `HEAD:<rel>` is the repo's. From the
        batch reader; `git show` as before where it can't say, as for a folder, which show lists."""
        head = gitfiles.head_id(self.git_dir)
        key = (head, rel) if isinstance(head, str) else None
        if key is not None and key in self._blobs:
            return self._blobs[key]
        found = self.blob(f"{head if key is not None else 'HEAD'}:./{rel}")
        if isinstance(found, gitfiles.Unknown):
            text = self.run("show", f"HEAD:./{rel}", check=False).stdout
        else:
            text = "" if found is None else as_text(found)
        if key is not None:
            self._blobs[key] = text
        return text

    def blob_text(self, spec: str) -> str | None:
        """A blob's text as `git cat-file blob <spec>` with check=False gives it: None where that call fails. From
        the batch reader, else from that call."""
        found = self.blob(spec)
        if isinstance(found, gitfiles.Unknown):
            proc = self.run("cat-file", "blob", spec, check=False)
            return proc.stdout if proc.returncode == 0 else None
        return None if found is None else as_text(found)

    def blob_out(self, spec: str) -> str:
        """A blob's text as `out("cat-file", "blob", spec)` gives it, raising git's own error where it fails."""
        found = self.blob(spec)
        return as_text(found) if isinstance(found, bytes) else self.out("cat-file", "blob", spec)

    def blob(self, spec: str) -> bytes | gitfiles.Unknown | None:
        """A blob's bytes by `<commit>:./<path>` or by its id, from one `git cat-file --batch` this Git starts at its
        first read and keeps until `close` (DR-0766): None when git has no object by that name, UNKNOWN when the batch
        can't vouch (the name isn't a blob, the batch broke), and the caller asks git as before."""
        if "\n" in spec or "\r" in spec:
            return gitfiles.UNKNOWN
        with self._batch_lock:
            try:
                proc = self._batch if self._batch is not None else self._start_batch()
                pipe_in, pipe_out = _pipes(proc)
                pipe_in.write(spec.encode("utf-8") + b"\n")
                pipe_in.flush()
                header = pipe_out.readline()
                if header == spec.encode("utf-8") + b" missing\n":
                    return None
                fields = header.split()
                if len(fields) != 3 or not fields[2].isdigit():
                    raise OSError(f"git cat-file --batch answered {header[:80]!r}")
                size = int(fields[2])
                data = pipe_out.read(size)
                if len(data) != size or pipe_out.read(1) != b"\n":
                    raise OSError("git cat-file --batch ended part way")
            except OSError:
                self._close_batch()
                return gitfiles.UNKNOWN
            return data if fields[1] == b"blob" else gitfiles.UNKNOWN

    def _start_batch(self) -> subprocess.Popen[bytes]:
        proc = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=self.root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=self._env(),
            creationflags=console_flags(),
        )
        self._batch = proc
        self._closer = weakref.finalize(self, _shut, proc)
        with _OPEN_LOCK:
            _OPEN.add(self)
        return proc

    def close(self) -> None:
        """Stop the batch reader, if one runs: on Windows an open one holds pack files a repack or a move needs. The
        next read starts another."""
        with self._batch_lock:
            self._close_batch()

    def _close_batch(self) -> None:
        closer, self._closer, self._batch = self._closer, None, None
        with _OPEN_LOCK:
            _OPEN.discard(self)
        if closer is not None:
            closer()

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
            if not self._commit(paths, spec, message):
                return None
        except BaseException:
            if folder:
                self.run("reset", "-q", *_PATHSPEC, check=False, stdin=spec, hooks=False)
            raise
        # The branch's ref file holds the new id the moment the commit returns (DR-0765).
        found = self.head_id()
        commit = found if found is not None else self.out("rev-parse", "HEAD").strip()
        parents = gitfiles.commit_parents(self.git_dir, commit)
        self.parents = () if isinstance(parents, gitfiles.Unknown) else parents
        return commit

    def _commit(self, paths: list[str], spec: str, message: str) -> bool:
        """The commit after the add; False when the paths hold nothing new. It runs at once, taking git's identity
        only from git's config and environment. Two questions wait until it fails (DR-0764): whether the paths staged
        anything, since a commit with nothing new fails and is None as before; then whether git has an identity, and
        with none the commit runs again with the owner's, which this Git passes from then on."""
        args = ("commit", "-q", "--only", *_PATHSPEC, "-m", message)
        known = self.fallback is not None and self._identity is False
        strict = self.fallback is not None and not known
        proc = self.run(*args, check=False, identity=known, strict=strict, stdin=spec, hooks=False)
        if proc.returncode == 0:
            return True
        # `-- .` and the prefix, not --relative: that crashes git 2.47.0.windows.2 while a stash pop conflicts.
        staged = {p for p in self.out("diff", "--cached", "--name-only", "-z", "--", ".").split("\0") if p}
        if not staged & {self.prefix + Path(p).as_posix() for p in paths}:
            return False
        if not strict or self._has_identity_once():
            raise self._failed(proc, args)
        self.run(*args, identity=True, stdin=spec, hooks=False)
        return True

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

    def in_head(self, under: str) -> list[str] | None:
        """The files HEAD holds under a path, made relative to the vault; None when git can't say (no HEAD, a broken
        repo), so a caller keeping what HEAD may hold keeps everything."""
        proc = self.run("ls-tree", "-r", "-z", "--name-only", "--full-name", "HEAD", "--", under, check=False)
        return self._vault_names(proc.stdout) if proc.returncode == 0 else None

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

    def against_head(self, under: str) -> tuple[list[str], list[str]]:
        """What `changed_against_head(under)` and `not_in_head(under)` list, from one status call where its codes say
        as much (DR-0764). The status compares HEAD with the index and the index with the working tree, and the diff
        HEAD with the working tree, so a few states could go either way: a file staged and changed again (MM, which
        may be back to HEAD's bytes), one deleted that may only have been added with -N ( D), a rename or copy (git's
        rename detection weighs the working tree), an unmerged path. Any of them, and the two calls answer instead."""
        out = self.run(
            "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=traditional", "--", under,
            env={"GIT_OPTIONAL_LOCKS": "0"},
        ).stdout  # fmt: skip
        changed: list[str] = []
        new: list[str] = []
        for entry in out.split("\0"):
            if len(entry) < 4:
                continue
            code, path = entry[:2], entry[3:]
            if code not in _CHANGED and code not in _NEW:
                return self.changed_against_head(under), self.not_in_head(under)
            if code in _CHANGED:
                changed.append(path)
            if code in _NEW:
                new.append(path)
        if self.head_id() is None:
            changed = []
        return self._vault_names("\0".join(changed)), self._vault_names("\0".join(new))

    def changed_against_head(self, under: str) -> list[str]:
        """Tracked files under a path that differ from HEAD, staged or not, deletions included."""
        if self.head_id() is None:
            return []
        return self._vault_names(self.out("diff", "--name-only", "-z", "HEAD", "--", under))

    def branch(self) -> str:
        return self.out("rev-parse", "--abbrev-ref", "HEAD").strip()

    def has_remote(self, name: str = "origin") -> bool:
        """Whether the repo has a remote by that name: from its config, kept while the config files are (DR-0767)."""
        return name in configured(self.root, "remote", lambda: self.out("remote").split())

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
    """The git repo a folder is in, or None. Git is asked once per folder and process; its answer stands while the
    files say git would give it again (DR-0767): for a repo, a plain checkout's .git still the nearest above the
    folder and its config unchanged; for none, no .git anywhere above. With a variable telling git where the repo is,
    git is asked every time."""
    probe = path
    while not probe.exists():
        probe = probe.parent
    key = probe.resolve()
    held = _TOPS.get(key)
    if held is not None and not told_where() and _top_stands(key, *held):
        return held[0]
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=probe,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        creationflags=console_flags(),
    )
    top = Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None
    # Kept only where the files can vouch for it: no repo with no .git above, or a plain checkout's own top.
    nearest = repo_at(key)
    stamp = config_stamp(top) if top is not None else ()
    if stamp is not None and ((top is None and nearest is None) or (top is not None and nearest == top)):
        _TOPS[key] = (top, stamp)
    else:
        _TOPS.pop(key, None)
    return top


def _top_stands(probe: Path, top: Path | None, stamp: Stamp) -> bool:
    nearest = repo_at(probe)
    if top is None:
        return nearest is None
    return nearest == top and config_stamp(top) == stamp


def configured[T](top: Path, question: str, ask: Callable[[], T]) -> T:
    """An answer git gives from a repo's config, asked once per repo and process: it stands while every config file
    git reads keeps its size and time, as `config_stamp` names them, and no variable tells git where the repo is
    (DR-0767). The remotes, the hooks path and git-lfs's filter are such answers."""
    key = (top.resolve(), question)
    stamp = config_stamp(key[0])
    held = _CONFIGURED.get(key)
    if held is not None and stamp is not None and held[0] == stamp and not told_where():
        return cast("T", held[1])
    answer = ask()
    if stamp is not None:
        _CONFIGURED[key] = (stamp, answer)
    return answer


def config_stamp(top: Path) -> Stamp | None:
    """The size and time of each config file git reads for a plain checkout at `top`: its own .git/config, the user's
    global one (GIT_CONFIG_GLOBAL, else ~/.gitconfig and the XDG one), the system's where GIT_CONFIG_SYSTEM names it,
    and the GIT_CONFIG_ variables. None where `top` isn't a plain checkout, whose answers are never kept. A file a
    config includes, or the system's own when no variable names it, isn't watched: a change there is seen once the
    process restarts."""
    if not is_git_dir(top / ".git"):
        return None
    # git's own home: HOME where it is set, Git for Windows' too.
    home = Path(os.environ.get("HOME") or Path.home())
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(home / ".config")
    named = os.environ.get("GIT_CONFIG_GLOBAL")
    files = [
        top / ".git" / "config",
        *([Path(named)] if named else [home / ".gitconfig", Path(xdg) / "git" / "config"]),
        *([Path(os.environ["GIT_CONFIG_SYSTEM"])] if os.environ.get("GIT_CONFIG_SYSTEM") else []),
    ]
    stamp: list[object] = [*sorted((k, v) for k, v in os.environ.items() if k.startswith("GIT_CONFIG"))]
    for path in files:
        try:
            found = path.stat()
        except OSError:
            # Missing, or not there to read: as a file git doesn't read, until one is.
            stamp.append(None)
            continue
        stamp.append((found.st_size, found.st_mtime_ns))
    return tuple(stamp)


def close_all() -> None:
    """Stop every batch reader this process holds: at the end of each command and each call of the service, and
    before a sync, a convert or a forget's rewrite runs (DR-0766). A read running in another thread finishes first,
    and its Git's next read starts a new one."""
    with _OPEN_LOCK:
        held = list(_OPEN)
    for git in held:
        git.close()


def as_text(data: bytes) -> str:
    """A blob's bytes as a git call's output reads in text mode: UTF-8, a bad byte replaced, CRLF and CR read as LF."""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _index_held(proc: subprocess.CompletedProcess[str]) -> bool:
    return proc.returncode != 0 and "index.lock" in proc.stderr and "File exists" in proc.stderr


def _pipes(proc: subprocess.Popen[bytes]) -> tuple[IO[bytes], IO[bytes]]:
    if proc.stdin is None or proc.stdout is None:  # pragma: no cover - both are pipes, asked for at its start
        raise OSError("git cat-file gave no pipes")
    return proc.stdin, proc.stdout


def _shut(proc: subprocess.Popen[bytes]) -> None:
    """End a batch reader: its input closed, it exits; one that doesn't in time is killed."""
    for pipe in (proc.stdin, proc.stdout):
        if pipe is not None:
            with contextlib.suppress(OSError):
                pipe.close()
    try:
        proc.wait(timeout=_BATCH_EXIT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
