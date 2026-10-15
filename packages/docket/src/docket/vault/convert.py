"""`docket vault convert`: a vault made as a repo of its own becomes a folder of the repo around it (the host), with
its history.

It is built for the shape `docket init` makes when it nests a vault: a repo with no remote, inside a working repo whose
.gitignore has /<vault>/ and whose .ignore has three lines for it. The dry run comes first, every time; nothing changes
before the user's yes. Each step is written to convert.json (in Docket's private folder under the host's git dir)
before it runs, so a second run finishes what a crash stopped, and `--abort` undoes a convert stopped before the host
commit.

1. Fetch the vault's branch into the host, and copy its LFS objects into the host's store.
2. Rewrite that history under the vault's path with one `git fast-import`, each commit's tree the original tree at the
   vault's path, and end it with one ordinary commit that makes the vault a folder (storage: folder, .ignore, the
   .gitattributes lines it lacks). The host merge then adds nothing of its own, so a pull --rebase keeps everything.
3. Move the vault's .git into the private folder, as the backup.
4. Write the folder files in the working tree, byte for byte as the folder commit holds them.
5. Merge the rewritten head into the host's branch under the host's index.lock, as git commit does.
6. Take the vault's lines out of the host's .gitignore and .ignore (left for the user to commit), and clean up.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from docket.errors import DocketError
from docket.vault import host
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.git import Git, Identity
from docket.vault.home import CONVERT_FILE, make_private, rev_parse
from docket.vault.host import HostReport, Visibility, remote_visibility, same_rule
from docket.vault.lock import VaultLock
from docket.vault.vault import IGNORE, with_attributes
from docket.vault.wizard import Prompter, confirm_private

SOURCE_REF = "refs/docket/convert/source"
REWRITTEN_REF = "refs/docket/convert/rewritten"
FOLDER_COMMIT = "Make the vault a folder of this repo"
MERGE_COMMIT = "Move the vault into this repo, with its history"
BACKUP = "before-folder.git"
# An LFS object being copied has this after its name until it is whole.
PARTIAL = ".docket-partial"
TEMP_INDEX = "convert.index"
FOLDER_FILES = (CONFIG_FILE, ".gitattributes", ".ignore")
# On Windows a program holding a file open makes a rename fail for a moment.
RENAME_SECONDS = 3.0
_RENAME_PAUSE = 0.1
LAST_STEP = 6


@dataclass(frozen=True)
class Where:
    root: Path
    top: Path
    prefix: str
    git_dir: Path

    @property
    def private(self) -> Path:
        return self.git_dir.joinpath("docket", *self.prefix.rstrip("/").split("/"))

    @property
    def state_file(self) -> Path:
        return self.private / CONVERT_FILE

    @property
    def backup(self) -> Path:
        return self.private / BACKUP

    @property
    def name(self) -> str:
        return self.prefix.rstrip("/")


@dataclass
class State:
    step: int
    branch: str
    source: str = ""
    rewritten: str = ""
    lfs_copied: list[str] = field(default_factory=list[str])

    def save(self, where: Where) -> None:
        make_private(where.git_dir, where.private)
        where.state_file.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def load(where: Where) -> State:
        data: Any = json.loads(where.state_file.read_text(encoding="utf-8"))
        return State(**data)


@dataclass
class Options:
    dry_run: bool = False
    yes: bool = False
    abort: bool = False
    remote_is_private: bool = False
    allow_shared: bool = False


@dataclass
class Survey:
    """What the dry run shows: what changes, what only the backup keeps, and why the convert is refused, if it is."""

    where: Where
    branch: str
    commits: int
    lfs_objects: int
    report: HostReport
    refusals: list[str] = field(default_factory=list[str])
    backup_only: list[str] = field(default_factory=list[str])
    host_edits: list[str] = field(default_factory=list[str])

    def lines(self) -> list[str]:
        w = self.where
        refused = [f"refused:  {line}" for line in self.refusals]
        return [
            f"vault:    {w.root}, a repo of its own on {self.branch}, {self.commits} commit(s)",
            f"into:     {w.top}, on {self.report.branch}, as {w.prefix}",
            *(f"          {line}" for line in self.report.lines()),
            f"history:  the {self.commits} commit(s) join {self.report.branch}'s history under {w.prefix}, then "
            f'"{FOLDER_COMMIT}" and one merge',
            f"lfs:      {self.lfs_objects} object(s) copied into the repo's store",
            f"edits:    {'; '.join(self.host_edits) or 'none'} (left for you to commit)",
            f"backup:   {w.root / '.git'} moves to {w.backup}; only it keeps "
            f"{', '.join(self.backup_only) or 'nothing else'}",
            "push:     your next push sends the vault and its history to the repo's remotes",
            *refused,
        ]


def locate(root: Path) -> Where:
    """The host of a vault folder, asked from the folder above it: the vault's own .git, if it is still there, is
    never what answers."""
    root = root.resolve()
    answer = rev_parse(
        root.parent, "--show-toplevel", "--show-prefix", "--absolute-git-dir", "--path-format=absolute",
        "--git-common-dir",
    )  # fmt: skip
    if answer is None:
        raise DocketError(f"{root.parent} isn't inside a git repo; there is no repo to make the vault a folder of")
    top, above, git_dir, common = answer
    if Path(common).resolve() != Path(git_dir).resolve():
        raise DocketError(f"{root} is in a linked worktree; convert the vault from the main checkout")
    return Where(root, Path(top).resolve(), f"{above}{root.name}/", Path(git_dir).resolve())


def survey(where: Where, opts: Options, visibility: Callable[[str], Visibility]) -> Survey:
    root = where.root
    vault_git = Git(root, home=None)
    refusals: list[str] = []
    if not (root / ".git").is_dir():
        raise DocketError(f"{root} has no .git of its own; only a vault that is a repo of its own can be converted")
    config = VaultConfig.load(root / CONFIG_FILE)
    if config.storage == "folder":
        refusals.append(f"{root}/vault.yaml already says storage: folder")
    remotes = vault_git.out("remote").split()
    if remotes:
        refusals.append(
            f"the vault has a remote ({', '.join(remotes)}): another machine's clone of it would be overwritten by "
            "that machine's next pull of the repo. Retire the other clones first (bring their commits here, delete "
            "them), then remove the remote"
        )
    branch = vault_git.run("symbolic-ref", "-q", "--short", "HEAD", check=False).stdout.strip()
    refusals += host.state_problems(root)
    if vault_git.out("status", "--porcelain").strip():
        refusals.append(f"{root} has uncommitted changes; commit them first")
    if _lock_held(root / ".git" / "docket.lock"):
        refusals.append(f"another docket process holds {root / '.git' / 'docket.lock'}")
    # The vault's own /<vault>/ line hides every other ignore rule while it is there: the rules are read without it.
    report = host.check(where.top, where.prefix, config.owner.emails, visibility, without=f"/{where.prefix}")
    refusals += report.blocking()
    if Git(where.top).out("ls-files", "--", where.name).strip():
        refusals.append(f"{where.top} already tracks files under {where.name}/ (or holds it as a submodule)")
    if report.shared and opts.yes and not opts.allow_shared:
        refusals.append(f"{where.top} looks shared ({', '.join(report.shared)}); pass --allow-shared to go on anyway")
    found = Survey(
        where,
        branch or "(no branch)",
        int(vault_git.run("rev-list", "--count", "HEAD", check=False).stdout.strip() or 0),
        len(_lfs_missing(_lfs_store(root / ".git", vault_git), _lfs_store(where.git_dir, Git(where.top)))),
        report,
        refusals,
    )
    refs = vault_git.out("for-each-ref", "--format=%(refname)").split()
    found.backup_only = [*(r for r in refs if r != f"refs/heads/{branch}"), "commit signatures"]
    found.host_edits = _host_edits(where, apply=False)
    return found


def convert(
    root: Path, p: Prompter, opts: Options, visibility: Callable[[str], Visibility] = remote_visibility
) -> None:
    where = locate(root)
    if opts.abort:
        abort(where, p)
        return
    if where.state_file.is_file():
        state = State.load(where)
        if opts.dry_run:
            p.say(f"A convert of {where.root} stopped at step {state.step}; run without --dry-run to finish it.")
            return
        p.say(f"Finishing the convert of {where.root} that stopped at step {state.step}.")
        _run(where, state, p)
        return
    found = survey(where, opts, visibility)
    p.say("\n".join(found.lines()))
    if found.refusals:
        raise DocketError(f"{where.root} isn't converted: {found.refusals[0]}")
    if opts.dry_run:
        return
    if not opts.yes and not p.confirm("Convert it?", False):
        raise DocketError("nothing changed")
    confirm_private(p, found.report.unknown, opts.remote_is_private, opts.yes)
    note = host.setup_lfs(where.top, found.report.lfs)
    if note:
        p.say(f"git-lfs: {note}")
    _run(where, State(1, found.branch), p)


def _run(where: Where, state: State, p: Prompter) -> None:
    steps: dict[int, Callable[[Where, State], None]] = {
        1: _fetch,
        2: _rewrite,
        3: _move,
        4: _write_folder_files,
        5: _merge,
        6: _finish,
    }
    # Until the vault's .git has moved, the vault may have changed since: start again from the fetch.
    start = 1 if (where.root / ".git").exists() else state.step
    for n in range(start, LAST_STEP + 1):
        state.step = n
        state.save(where)
        steps[n](where, state)
    where.state_file.unlink(missing_ok=True)
    edits = _host_edits(where, apply=False, done=True)
    p.say(
        f"The vault is a folder of {where.top} now; your own git moves it. Left for you to commit: "
        f"{', '.join(edits) or 'nothing'}. The old repo is in {where.backup}; delete it when you're happy."
    )
    hooks = host.new_hooks(where.top)
    if hooks:
        p.say(
            f"git-lfs added its hooks to your repo's own hooks folder: {', '.join(hooks)}. Commit them, or delete them "
            'and add `git lfs pre-push "$@"` to your own pre-push hook, so your push carries media.'
        )


# -- the steps ---------------------------------------------------------------------------------------------------


def _fetch(where: Where, state: State) -> None:
    top = Git(where.top)
    with VaultLock(where.root / ".git", timeout=0):
        top.run("fetch", "-q", "--no-tags", str(where.root), f"+refs/heads/{state.branch}:{SOURCE_REF}")
    state.source = top.out("rev-parse", SOURCE_REF).strip()
    source_store = _lfs_store(where.root / ".git", Git(where.root))
    target_store = _lfs_store(where.git_dir, top)
    for rel in _lfs_missing(source_store, target_store):
        # Recorded before the copy, so an abort after a crash mid-copy still removes it; copied under a temp name and
        # renamed, so a crash never leaves a short object with the real name.
        if rel not in state.lfs_copied:
            state.lfs_copied.append(rel)
            state.save(where)
        target = target_store / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = _partial(target)
        shutil.copy2(source_store / rel, partial)
        partial.replace(target)


def _rewrite(where: Where, state: State) -> None:
    top = Git(where.top)
    top.run("update-ref", "-d", REWRITTEN_REF, check=False)
    shas = top.out("rev-list", "--reverse", "--topo-order", state.source).split()
    commits = _cat_commits(where.top, shas)
    stream = bytearray()
    marks: dict[str, int] = {}
    for n, sha in enumerate(shas, start=1):
        headers, message = commits[sha]
        parents = [line[7:].decode() for line in headers if line.startswith(b"parent ")]
        marks[sha] = n
        if not parents:
            stream += f"reset {REWRITTEN_REF}\n".encode()
        stream += f"commit {REWRITTEN_REF}\nmark :{n}\n".encode()
        for line in headers:
            if line.startswith((b"author ", b"committer ", b"encoding ")):
                stream += line + b"\n"
        stream += f"data {len(message)}\n".encode() + message + b"\n"
        for i, parent in enumerate(parents):
            stream += f"{'from' if i == 0 else 'merge'} :{marks[parent]}\n".encode()
        tree = next(line[5:].decode() for line in headers if line.startswith(b"tree "))
        stream += f"deleteall\nM 040000 {tree} {where.name}\n\n".encode()
    stream += _folder_commit(where, state, len(shas))
    proc = subprocess.run(
        ["git", "fast-import", "--quiet"], cwd=where.top, input=bytes(stream), capture_output=True, check=False
    )
    if proc.returncode != 0:
        raise DocketError(f"git fast-import: {proc.stderr.decode('utf-8', 'replace').strip()}")
    state.rewritten = top.out("rev-parse", REWRITTEN_REF).strip()


def _move(where: Where, state: State) -> None:
    own = where.root / ".git"
    if own.exists():
        if where.backup.exists():
            raise DocketError(f"{where.backup} is already there; move it away, then run `docket vault convert` again")
        _rename(own, where.backup, f"couldn't move {own}; close what uses it and run `docket vault convert` again")
    head = Git(where.root).run("--git-dir", str(where.backup), "rev-parse", "HEAD").stdout.strip()
    if head != state.source:
        _rename(where.backup, own, f"couldn't move {where.backup} back to {own}; move it yourself")
        state.step = 1
        state.save(where)
        raise DocketError(f"{where.root} changed while it was being converted; run `docket vault convert` again")


def _write_folder_files(where: Where, state: State) -> None:
    for name in FOLDER_FILES:
        data = _blob(where.top, f"{state.rewritten}:{where.prefix}{name}")
        (where.root / name).write_bytes(data or b"")


def _merge(where: Where, state: State) -> None:
    top = Git(where.top)
    lock = where.git_dir / "index.lock"
    if top.run("merge-base", "--is-ancestor", state.rewritten, "HEAD", check=False).returncode == 0:
        # A second run: the merge is in; the vault's entries go in the index, the same however often this runs. An
        # index.lock a crash left between update-ref and the rename is reported with its age, never deleted.
        top.run("reset", "-q", "--", where.name)
        return
    head = top.out("rev-parse", "HEAD").strip()
    temp_index = where.private / TEMP_INDEX
    temp_index.unlink(missing_ok=True)
    env = {"GIT_INDEX_FILE": str(temp_index)}
    top.run("read-tree", head, env=env)
    top.run("read-tree", f"--prefix={where.prefix}", f"{state.rewritten}:{where.name}", env=env)
    tree = top.run("write-tree", env=env).stdout.strip()
    temp_index.unlink(missing_ok=True)
    owner = _owner_git(where)
    merge = owner.run("commit-tree", tree, "-p", head, "-p", state.rewritten, "-m", MERGE_COMMIT, identity=True)
    merge_sha = merge.stdout.strip()
    _take(lock)
    try:
        # The index git commit would write: the user's, with the vault's entries set to the merge's.
        lock.write_bytes((where.git_dir / "index").read_bytes())
        top.run("reset", "-q", merge_sha, "--", where.name, env={"GIT_INDEX_FILE": str(lock)})
        if top.run("update-ref", "-m", MERGE_COMMIT, "HEAD", merge_sha, head, check=False).returncode != 0:
            raise DocketError(f"{where.top} moved while the vault was being merged; run `docket vault convert` again")
    except BaseException:
        lock.unlink(missing_ok=True)
        raise
    if not _try_rename(lock, where.git_dir / "index"):
        lock.unlink(missing_ok=True)
        raise DocketError(
            f"couldn't replace {where.git_dir / 'index'}: a program holds it. Until `docket vault convert` runs again, "
            "git shows the vault's files as deleted; don't commit before it has"
        )


def _finish(where: Where, state: State) -> None:
    _host_edits(where, apply=True)
    machine = where.backup / "docket" / "machine"
    target = where.private / "machine"
    if machine.is_file() and not target.exists():
        shutil.copy2(machine, target)
    top = Git(where.top)
    for ref in (SOURCE_REF, REWRITTEN_REF):
        top.run("update-ref", "-d", ref, check=False)
    (where.private / TEMP_INDEX).unlink(missing_ok=True)


# -- undoing -----------------------------------------------------------------------------------------------------


def abort(where: Where, p: Prompter) -> None:
    """Undo a convert stopped before its host commit: the old .git back, the folder files as they were, the refs and
    the copied LFS objects gone."""
    if not where.state_file.is_file():
        raise DocketError(f"no convert of {where.root} to undo")
    state = State.load(where)
    top = Git(where.top)
    if state.rewritten and top.run("merge-base", "--is-ancestor", state.rewritten, "HEAD", check=False).returncode == 0:
        raise DocketError(
            f"the convert already made its commit in {where.top}; run `docket vault convert` to finish it. The old "
            f"repo is in {where.backup}"
        )
    own = where.root / ".git"
    removed: list[str] = []
    if not own.exists() and where.backup.exists():
        _rename(where.backup, own, f"couldn't move {where.backup} back to {own}; move it yourself")
        removed.append(f"moved {where.backup} back to {own}")
    for name in FOLDER_FILES:
        old = _blob(where.root, f"HEAD:{name}")
        path = where.root / name
        if old is not None:
            path.write_bytes(old)
        else:
            path.unlink(missing_ok=True)
    for ref in (SOURCE_REF, REWRITTEN_REF):
        if top.run("update-ref", "-d", ref, check=False).returncode == 0:
            removed.append(f"deleted {ref}")
    store = _lfs_store(where.git_dir, top)
    for rel in state.lfs_copied:
        (store / rel).unlink(missing_ok=True)
        _partial(store / rel).unlink(missing_ok=True)
    if state.lfs_copied:
        removed.append(f"removed the {len(state.lfs_copied)} LFS object(s) it copied")
    (where.private / TEMP_INDEX).unlink(missing_ok=True)
    where.state_file.unlink()
    p.say(f"Undid the convert of {where.root}: {'; '.join(removed) or 'nothing had changed yet'}.")


# -- helpers -------------------------------------------------------------------------------------------------------


def _folder_commit(where: Where, state: State, after: int) -> bytes:
    """The last commit of the stream: the folder files under the vault's path."""
    files = {name: _blob(where.top, f"{state.source}:{name}") or b"" for name in FOLDER_FILES}
    files[CONFIG_FILE] = _with_storage(files[CONFIG_FILE].decode("utf-8")).encode("utf-8")
    files[".gitattributes"] = with_attributes(files[".gitattributes"].decode("utf-8")).encode("utf-8")
    ignore = files[".ignore"].decode("utf-8")
    have = ignore.splitlines()
    files[".ignore"] = (ignore if have else IGNORE).encode("utf-8")
    if have:
        missing = [line for line in IGNORE.splitlines() if not line.startswith("#") and line not in have]
        files[".ignore"] = (ignore + "".join(f"{line}\n" for line in missing)).encode("utf-8")
    ident = _identity(where)
    message = FOLDER_COMMIT.encode()
    out = bytearray(f"commit {REWRITTEN_REF}\n".encode())
    out += b"author " + ident + b"\ncommitter " + ident + b"\n"
    out += f"data {len(message)}\n".encode() + message + b"\n"
    out += f"from :{after}\n".encode()
    for name, data in files.items():
        out += f"M 100644 inline {where.prefix}{name}\ndata {len(data)}\n".encode() + data + b"\n"
    return bytes(out) + b"\n"


def _with_storage(text: str) -> str:
    """vault.yaml with `storage: folder`, edited as text so the user's comments and order stay."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("storage:"):
            lines[i] = "storage: folder"
            break
    else:
        at = next((i + 1 for i, line in enumerate(lines) if line.startswith("schema_version:")), 0)
        lines.insert(at, "storage: folder")
    return "".join(f"{line}\n" for line in lines)


def _identity(where: Where) -> bytes:
    """Who the folder commit is by: git's own identity in the host, else the vault's owner, now."""
    proc = subprocess.run(["git", "var", "GIT_COMMITTER_IDENT"], cwd=where.top, capture_output=True, check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    owner = VaultConfig.load(where.root / CONFIG_FILE).owner
    email = owner.emails[0] if owner.emails else "docket@localhost"
    return f"{owner.name} <{email}> {int(datetime.now(UTC).timestamp())} +0000".encode()


def _blob(cwd: Path, spec: str) -> bytes | None:
    """A blob's bytes as git stores them, or None when there is none."""
    proc = subprocess.run(["git", "cat-file", "blob", spec], cwd=cwd, capture_output=True, check=False)
    return proc.stdout if proc.returncode == 0 else None


def _cat_commits(top: Path, shas: list[str]) -> dict[str, tuple[list[bytes], bytes]]:
    """Every commit's header lines and message, read in one `git cat-file --batch`."""
    proc = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=top, input="".join(f"{s}\n" for s in shas).encode(), capture_output=True,
        check=True,
    )  # fmt: skip
    out = proc.stdout
    commits: dict[str, tuple[list[bytes], bytes]] = {}
    at = 0
    for sha in shas:
        end = out.index(b"\n", at)
        size = int(out[at:end].split()[2])
        body = out[end + 1 : end + 1 + size]
        at = end + 1 + size + 1
        head, _, message = body.partition(b"\n\n")
        commits[sha] = (head.split(b"\n"), message)
    return commits


def _lfs_store(git_dir: Path, git: Git) -> Path:
    configured = git.run("config", "--get", "lfs.storage", check=False).stdout.strip()
    base = git_dir / configured if configured else git_dir / "lfs"
    return base / "objects"


def _lfs_missing(source: Path, target: Path) -> list[str]:
    """The LFS objects in `source` that `target` lacks, or holds at another size, as paths relative to the store."""
    if not source.is_dir():
        return []
    found = [p for p in source.rglob("*") if p.is_file() and not p.name.endswith(PARTIAL)]
    return sorted(
        p.relative_to(source).as_posix()
        for p in found
        if not (t := target / p.relative_to(source)).is_file() or t.stat().st_size != p.stat().st_size
    )


def _partial(target: Path) -> Path:
    return target.with_name(target.name + PARTIAL)


def _host_edits(where: Where, *, apply: bool, done: bool = False) -> list[str]:
    """The vault's lines in the host's .gitignore and .ignore, which a folder vault no longer needs: listed, or taken
    out of the working tree. Once applied, `done` names the files left for the user to commit."""
    name = where.name
    edits: list[str] = []
    for file, lines in (
        (".gitignore", [f"/{name}/"]),
        (".ignore", [f"!/{name}/", f"/{name}/*", f"!/{name}/processed/"]),
    ):
        path = where.top / file
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        # The same matcher the dry run's ignore check uses: a line git reads as the rule is the rule, spaces or not.
        kept = [line for line in text.splitlines() if not any(same_rule(line, rule) for rule in lines)]
        if done:
            if Git(where.top).out("status", "--porcelain", "--", file).strip():
                edits.append(file)
            continue
        if len(kept) == len(text.splitlines()):
            continue
        found = [rule for rule in lines if any(same_rule(line, rule) for line in text.splitlines())]
        edits.append(f"{file} drops {', '.join(found)}")
        if apply:
            path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8", newline="\n")
    return edits


def _try_rename(source: Path, target: Path) -> bool:
    """Rename, trying again for a few seconds while another program holds the file (Windows refuses then)."""
    deadline = time.monotonic() + RENAME_SECONDS
    while True:
        try:
            source.replace(target)
            return True
        except PermissionError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(_RENAME_PAUSE)


def _rename(source: Path, target: Path, failure: str) -> None:
    if not _try_rename(source, target):
        raise DocketError(failure)


def _owner_git(where: Where) -> Git:
    """git in the host, committing as the vault's owner when git has no identity of its own."""
    owner = VaultConfig.load(where.root / CONFIG_FILE).owner
    return Git(where.top, Identity(owner.name, owner.emails[0] if owner.emails else "docket@localhost"))


def _take(lock: Path) -> None:
    """Take the host's index.lock the way git does: create it, failing if it is there. One that stays is reported with
    its age and never deleted: only the user can tell a running git's from one a crash left."""
    deadline = time.monotonic() + RENAME_SECONDS
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return
        except FileExistsError as e:
            if time.monotonic() >= deadline:
                age = int((time.time() - lock.stat().st_mtime) // 60)
                raise DocketError(
                    f"{lock} is there, {age} minute(s) old; if no git is running, delete it and run `docket vault "
                    "convert` again"
                ) from e
            time.sleep(_RENAME_PAUSE)


def _lock_held(path: Path) -> bool:
    if not path.parent.is_dir():
        return False
    lock = FileLock(str(path), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        return True
    lock.release()
    return False


__all__ = ["Options", "Survey", "abort", "convert", "locate", "survey"]
