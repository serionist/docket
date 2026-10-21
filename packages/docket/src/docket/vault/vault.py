"""A vault on disk: its layout, opening and creating one, committing, and checking that the raw log is intact.

A vault is the top of a repo of its own, or a folder of the user's repo (the host); `vault.yaml` says which and
`home.py` works out where git and Docket's own files are.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
from collections import Counter
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from docket.errors import DocketError
from docket.vault import forgotten, gitfiles, inforce
from docket.vault import machine as machines
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.git import DOC_LINE, Git, Identity, SyncResult, close_all
from docket.vault.home import Home, is_git_dir, stopped_convert
from docket.vault.lock import VaultLock
from docket.vault.rawlog import RUN_FILE, RawLog, lines_of, parse_run_file, record_of
from docket.vault.records import RawRecord

log = logging.getLogger(__name__)


class UnmergedError(DocketError):
    """A commit refused for unmerged paths, before anything of Docket's was staged: a writer putting its files back
    leaves git's index alone, so the user's conflict stays as it was."""


# How long after a sync writes its marker git starts the rebase: seconds in practice.
SYNC_REBASE_WINDOW_SECONDS = 60
# How long a write waits for another Docket process to let go of the vault.
LOCK_TIMEOUT_SECONDS = 60.0
# Folder vaults whose vault.yaml was tracked in the repo Open found, by (git dir, path in it): the service opens the
# vault on every call, and the answer doesn't change while the repo is there (DR-0271).
_TRACKED: set[tuple[Path, str]] = set()

# Folders every vault has from the start; git keeps an empty folder only with a file in it.
FOLDERS = ["raw", "media", "processed", "entities", "ledger/ops", "state"]
# The first attribute line must stay first: a host's rules stop at the vault, and the lines after it set the vault's
# own. Appended last, it would cancel them.
RESET_LINE = "* !filter !diff !merge !working-tree-encoding"
GITATTRIBUTES = f"""\
# Attributes from a repo around the vault stop here; the lines below set the vault's own.
{RESET_LINE}
# Text is stored with LF everywhere, so every machine sees the same bytes.
* text=auto eol=lf
# Files several machines append to merge by keeping both sides' lines.
*.jsonl merge=union
# Hold lists from several machines merge by keeping both sides' ids.
state/holds/*.txt merge=union
# Media goes to LFS, encrypted per file.
media/** filter=lfs diff=lfs merge=lfs -text
"""
GITIGNORE = """\
# Records waiting for the gate, and the index rebuilt from what's committed: never committed.
staging/
index/
*.tmp
.DS_Store
"""
# A folder vault's search rules: ripgrep, Claude Code and editors see the processed docs and nothing else in it.
IGNORE = """\
# Search tools see the processed docs; the rest is read through Docket.
/*
!/processed/
"""
# A sample path, an attribute and the value it must have; what `git check-attr` says for each is checked.
ATTRIBUTE_CHECKS = [
    ("raw/s/2026/01/2026-01-01T0000-m.jsonl", "filter", "unspecified"),
    ("raw/s/2026/01/2026-01-01T0000-m.jsonl", "merge", "union"),
    ("raw/s/2026/01/2026-01-01T0000-m.jsonl", "eol", "lf"),
    ("media/s/2026/01/x.age", "filter", "lfs"),
    ("processed/t/x.md", "filter", "unspecified"),
    ("state/holds/s.txt", "merge", "union"),
    ("state/releases/s.jsonl", "merge", "union"),
    ("state/forgotten.jsonl", "merge", "union"),
    ("entities/_log/2026-01-m.jsonl", "merge", "union"),
    ("entities/_log/2026-01-m.jsonl", "eol", "lf"),
    ("ledger/ops/2026-01-m.jsonl", "merge", "union"),
    ("ledger/ops/2026-01-m.jsonl", "eol", "lf"),
]
# A path in each place the vault commits; no ignore rule may match one.
IGNORE_SAMPLES = [
    CONFIG_FILE,
    ".gitattributes",
    "raw/s/2026/01/2026-01-01T0000-m.jsonl",
    "media/s/2026/01/x.age",
    "processed/t/x.md",
    "entities/t/x.yaml",
    "entities/_log/2026-01-m.jsonl",
    "ledger/ops/2026-01-m.jsonl",
    "state/cursors-m.yaml",
    "state/holds/s.txt",
    "state/releases/s.jsonl",
    "state/forgotten.jsonl",
]


# `git check-ignore -v`'s rule: <source>:<line>:<pattern>, where a source on Windows can hold a drive's colon.
_IGNORE_RULE = re.compile(r"^(.*?):(\d+):(.*)$")


@dataclass
class Problem:
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass
class Status:
    root: Path
    machine: str
    storage: str = "repo"
    host: Path | None = None
    branch: str | None = None
    branch_warning: str | None = None
    records: Counter[str] = field(default_factory=Counter[str])
    runs: int = 0
    uncommitted_runs: list[str] = field(default_factory=list[str])
    seen_in_several_runs: int = 0
    last_commit: str | None = None


class Vault:
    def __init__(
        self, root: Path, config: VaultConfig, home: Home | None = None, *, lock_timeout: float = LOCK_TIMEOUT_SECONDS
    ) -> None:
        self.root = root
        # The settings in force, and vault.yaml as written; the same unless an edit not committed holds more.
        self.config = config
        self.written = config
        self.home = home or Home.resolve(root, config.storage)
        self.machine = machines.resolve(self.home.private)
        owner_email = config.owner.emails[0] if config.owner.emails else "docket@localhost"
        self.git = Git(root, Identity(config.owner.name, owner_email), self.home)
        self.lock = VaultLock(
            self.home.git_dir,
            timeout=lock_timeout,
            lock_file=self.home.lock_file,
            clear_stale_index=not self.home.folder,
        )
        self.rawlog = RawLog(root, self.machine, self.lock, self.home.tmp_dir, self.home)

    def close(self) -> None:
        """Stop the vault's batch reader (DR-0766); the vault stays usable, and its next blob read starts another."""
        self.git.close()

    # -- finding and creating ------------------------------------------------------------------------------------

    @staticmethod
    def find(start: Path) -> Path | None:
        """The vault holding a path: the nearest folder upward with a vault.yaml and its own .git, or a vault.yaml
        Docket wrote (a folder vault, or one whose .git is missing, which open then explains). Another tool's
        vault.yaml in some repo is never taken for a vault."""
        for folder in [start.resolve(), *start.resolve().parents]:
            config = folder / CONFIG_FILE
            if config.is_file() and (is_git_dir(folder / ".git") or is_docket_config(config)):
                return folder
        return None

    @classmethod
    def open(cls, path: Path, lock_timeout: float = LOCK_TIMEOUT_SECONDS) -> Vault:
        """The vault holding a path. `lock_timeout` is how long a write waits for another process's: the service
        waits less than the command line, so a client gets Docket's message before its own timeout."""
        root = cls.find(path)
        if root is None:
            raise DocketError(f"no vault at or above {path}; create one with `docket init`")
        if stopped_convert(root) is not None:
            raise DocketError(
                f"a convert of {root} stopped part way; run `docket vault convert` to finish it, or `docket vault "
                "convert --abort` to undo it"
            )
        written = VaultConfig.load(root / CONFIG_FILE)
        home = Home.resolve(root, written.storage)
        if home.folder:
            _check_tracked(home)
        # What the jobs obey: HEAD's vault.yaml with what an uncommitted edit only tightens (DR-0521).
        settings = inforce.load(root, home)
        vault = cls(root, settings.in_force, home, lock_timeout=lock_timeout)
        vault.written = settings.file
        return vault

    @classmethod
    def init(cls, path: Path, config: VaultConfig, machine: str) -> Vault:
        """Create a vault in a folder that doesn't exist yet, or is empty. Nothing existing is ever overwritten. A
        folder vault writes only inside its folder, and its one commit holds only its own paths."""
        path = path.resolve()
        if cls.find(path) == path:
            raise DocketError(f"already a vault: {path}")
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise DocketError(f"{path} exists and isn't empty; a vault starts in an empty folder")
        created = not path.exists()
        folder = config.storage == "folder"
        private: Path | None = None
        private_existed = False
        written: list[str] = []
        try:
            path.mkdir(parents=True, exist_ok=True)
            if not folder:
                Git(path).init()
            home = Home.resolve(path, config.storage)
            private, private_existed = home.private, home.private.exists()
            for name in FOLDERS:
                keep = path / name / ".gitkeep"
                keep.parent.mkdir(parents=True, exist_ok=True)
                keep.write_text("", encoding="utf-8")
                written.append(f"{name}/.gitkeep")
            files = [(".gitattributes", GITATTRIBUTES), (".gitignore", GITIGNORE)]
            for name, text in [*files, *([(".ignore", IGNORE)] if folder else [])]:
                (path / name).write_text(text, encoding="utf-8", newline="\n")
                written.append(name)
            (path / CONFIG_FILE).write_text(config.dump(), encoding="utf-8", newline="\n")
            written.append(CONFIG_FILE)
            machines.remember(home, machine)
            vault = cls(path, config, home)
            if not folder:
                vault.git.lfs_install()
            started = vault.git.commit(written, "Start the vault")
            if started is not None:
                # The settings in force read HEAD's vault.yaml: this commit's is the text just written (DR-0521). It
                # holds no forgotten list, which every reader of raw asks about (DR-0579).
                inforce.remember(home, started, config.dump())
                forgotten.remember(home, started, "")
            return vault
        except BaseException:
            if created:
                remove_tree(path)
            else:
                for child in list(path.iterdir()):
                    remove_tree(child) if child.is_dir() else _unlink_writable(child)
            if folder and private is not None and not private_existed and private.exists():
                remove_tree(private)
            raise

    # -- writing ---------------------------------------------------------------------------------------------------

    def write_run(self, source: str, records: list[RawRecord], run_at: datetime | None = None) -> str | None:
        """Write a run of records and commit it with any run file an earlier, interrupted run left uncommitted."""
        result = self.rawlog.write_run(source, records, run_at or datetime.now(UTC))
        message = f"Raw: {source}, {result.written} record(s) from {self.machine}"
        return self.commit([], message) if result.path is not None else None

    def commit(self, paths: list[str], message: str) -> str | None:
        """Commit locally. Run files left uncommitted by an interrupted run go in too; never a changed raw file. The
        unmerged paths are asked for right before the add, as close to the commit as the check can sit (DR-0765)."""
        with self.lock:
            self._recover()
            everything = [*paths, *self.uncommitted_runs()]
            self.refuse_unmerged()
            commit = self.git.commit(everything, message)
            if commit is not None:
                inforce.carry(self.home, commit, self.git.parents, paths)
                forgotten.carry(self.home, commit, self.git.parents, paths)
            return commit

    @property
    def waiting(self) -> bool:
        """Whether vault.yaml holds an edit that isn't in force until it is committed."""
        return self.written != self.config

    def uncommitted_runs(self) -> list[str]:
        """Run files not in HEAD: untracked, or staged by an add whose commit never came. One an ignore rule hides
        counts too, so its add fails loudly instead of the file staying out of git unseen."""
        return sorted({p for p in self.git.not_in_head("raw") if RUN_FILE.match(Path(p).name)})

    def sync(self) -> SyncResult:
        if self.home.folder:
            # The host is the user's: Docket never fetches, rebases, stashes or pushes it.
            message = f"{self.root} is a folder of {self.home.top}: your own git (commit, pull, push) moves it"
            return SyncResult(False, False, message)
        with self.lock:
            self._recover()
            self.refuse_unmerged()
            # A rebase may repack, and on Windows an open batch reader holds the pack files (DR-0766).
            close_all()
            return self.git.sync()

    @contextmanager
    def writing(self) -> Generator[None]:
        """The lock, then the recovery check, for a writer that reads what it is about to change: nothing is read
        while a git operation is under way, and a rebase a killed sync left behind is undone first. Unmerged paths are
        asked for at each commit, right before its add, or first by a writer that calls `refuse_unmerged`."""
        with self.lock:
            self._recover()
            yield

    def _recover(self) -> None:
        """Before writing: undo a rebase a killed sync of ours left behind (its marker says so, and under the lock no
        sync of ours is running), and refuse to touch anything else git is in the middle of, which is the user's. In
        a folder of the user's repo Docket never syncs, so nothing there is ever undone. Each check here reads files,
        the branch's too, so it runs at every write and every commit (DR-0765)."""
        git_dir = self.home.git_dir
        repo = self.home.top
        state = next((git_dir / d for d in ("rebase-merge", "rebase-apply") if (git_dir / d).exists()), None)
        rebasing = state is not None
        if not self.home.folder:
            marker = self.home.sync_marker
            if state is not None and marker.exists() and _started_with(state, marker):
                log.warning("undoing a rebase a stopped sync left in %s", self.root)
                self.git.run("rebase", "--abort", hooks=False)
                rebasing = False
            marker.unlink(missing_ok=True)
        if rebasing:
            raise DocketError(f"a rebase is in progress in {repo}; finish it (git rebase --continue) or undo it")
        for head, what in (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert")):
            if (git_dir / head).exists():
                raise DocketError(f"a {what} is in progress in {repo}; finish or undo it before Docket writes")
        if not self.git.on_branch():
            raise DocketError(f"{repo} isn't on a branch (a detached HEAD); check out a branch before Docket writes")

    def refuse_unmerged(self) -> None:
        """A conflict a stash pop or a merge --squash left leaves no *_HEAD file, only unmerged paths; committing then
        would take in the conflict markers and mark the conflict resolved, since add and commit --only both resolve a
        path they name. Only the vault's own paths count: git's partial commit leaves a conflict in the user's files as
        it is. One plain ls-files -u, vault-wide: it takes no pathspec file, and a backlog of run files named on the
        command line could pass Windows' limit. Each commit asks right before its add; a writer that asks the owner
        something before its first commit, as a release does, asks first too, so a conflict refuses before the
        question. The paths are read from the index file where it can say, else from git (DR-0765)."""
        unmerged = gitfiles.unmerged(self.home.git_dir, self.home.prefix)
        if isinstance(unmerged, gitfiles.Unknown):
            unmerged = sorted({line.split("\t", 1)[-1] for line in self.git.out("ls-files", "-u").splitlines()})
        if unmerged:
            raise UnmergedError(f"{', '.join(unmerged)} in {self.root} has a conflict; resolve it before Docket writes")

    def blocked(self) -> str | None:
        """Why Docket can't change the tree now, asked before a writer changes a tracked file it commits later: a
        rebase, merge, cherry-pick or revert under way, a detached HEAD, or unmerged paths, each the owner's to finish
        (DR-0765, DR-0597). A rebase a killed sync of ours left is undone first, as every write does. None when it
        can."""
        try:
            self._recover()
            self.refuse_unmerged()
        except DocketError as e:
            return str(e)
        return None

    # -- checking ------------------------------------------------------------------------------------------------

    def check(self) -> list[Problem]:
        """What would make the vault untrustworthy: a raw file that doesn't parse, a record twice in one run, a raw
        file changed or deleted after it was committed, a file in raw/ that isn't a run file; an ignore rule that
        hides a path the vault commits, an attribute that isn't the vault's own; a doc rev two commits claim; what
        the ledger's check names (DR-0617)."""
        # The ledger's modules import the vault, so its check is imported when it runs.
        from docket.ledger import check as ledger  # noqa: PLC0415

        problems: list[Problem] = []
        raw = self.root / "raw"
        # A line a forget took is no record of its run's to count twice, and is never named (DR-0579).
        listed = self.rawlog.listed()
        reading = listed.reading()
        if listed.problem is not None:
            problems.append(Problem(forgotten.FORGOTTEN, listed.problem))
        for path in sorted(raw.rglob("*")) if raw.is_dir() else []:
            if not path.is_file() or path.name == ".gitkeep":
                continue
            rel = path.relative_to(self.root).as_posix()
            if parse_run_file(raw, path) is None:
                problems.append(Problem(rel, "isn't a run file (raw/<source>/<yyyy>/<mm>/<run>.jsonl)"))
                continue
            seen: set[tuple[str, str]] = set()
            with path.open(encoding="utf-8", newline="\n") as f:
                for n, line in enumerate(f, start=1):
                    if not line.strip():
                        continue
                    try:
                        record = RawRecord.model_validate(json.loads(line))
                    except (json.JSONDecodeError, ValidationError) as e:
                        problems.append(Problem(f"{rel}:{n}", f"not a raw record: {str(e).splitlines()[0]}"))
                        continue
                    if reading.skips(record):
                        continue
                    if (record.rid, record.v) in seen:
                        problems.append(Problem(f"{rel}:{n}", f"{record.rid} is in this run twice"))
                    seen.add((record.rid, record.v))
        problems.extend(
            Problem(
                rel,
                "a forget took listed lines out and isn't committed yet; the next gate call commits it"
                if self._forget_unfinished(listed, rel)
                else "changed or deleted after it was committed; raw files are written once",
            )
            for rel in self.git.changed_against_head("raw")
        )
        hidden: dict[tuple[str, str], list[str]] = {}
        for rel, rule, pattern in ignored(self.root):
            hidden.setdefault((rule, pattern), []).append(rel)
        for (rule, pattern), rels in hidden.items():
            more = f" and {len(rels) - 1} more of the vault's paths" if len(rels) > 1 else ""
            hint = (
                "; `docket vault convert` took this line out of the repo's .gitignore: commit that edit"
                if pattern == f"/{self.home.prefix}"
                else ""
            )
            problems.append(Problem(rels[0], f"an ignore rule hides it{more} from git ({rule}){hint}"))
        problems.extend(Problem(rel, message) for rel, message in attribute_mismatches(self.root))
        problems.extend(self._doc_rev_problems())
        problems.extend(Problem(where, message) for where, message in ledger.problems(self))
        return problems

    def _forget_unfinished(self, listed: forgotten.Forgotten, rel: str) -> bool:
        """Whether a run file differs from HEAD's only by lines the forgotten list takes: a forget's apply whose commit
        hasn't landed (DR-0581), never a raw file changed by hand."""
        path = self.root / rel
        if not listed.acts or not path.is_file():
            return False
        reading = listed.reading()
        # A line that doesn't read as a record is kept, as the apply keeps it: only a listed line leaves a run file.
        left = [
            line
            for line in lines_of(self.git.blob_out(f"HEAD:./{rel}"))
            if not (line.strip() and (record := record_of(line)) is not None and reading.skips(record))
        ]
        return "".join(left) == path.read_bytes().decode("utf-8", errors="replace")

    def _doc_rev_problems(self) -> list[Problem]:
        """A doc rev that two commits claim: the user's own merge can bring one from each side, which Docket's sync
        would have stopped. The next put goes on from the highest rev."""
        out = self.git.run("log", "--no-show-signature", "--format=%H%x1f%B%x1e", "--", "processed", check=False)
        claims: dict[tuple[str, int], list[str]] = {}
        for entry in out.stdout.split("\x1e"):
            sha, _, message = entry.strip("\n").partition("\x1f")
            for line in message.splitlines():
                m = DOC_LINE.match(line.strip())
                if m:
                    claims.setdefault((m.group(1), int(m.group(2))), []).append(sha[:12])
        return [
            Problem(doc, f"rev {rev} is claimed by {len(shas)} commits ({', '.join(shas)}); the next put goes on from "
                    "the highest rev")
            for (doc, rev), shas in sorted(claims.items())
            if len(shas) > 1
        ]  # fmt: skip

    def status(self) -> Status:
        status = Status(root=self.root, machine=self.machine, last_commit=self.git.last_commit())
        status.storage = self.home.storage
        status.host = self.home.top if self.home.folder else None
        status.branch = self.git.branch()
        default = self.git.run("symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD", check=False).stdout.strip()
        if default and default.removeprefix("origin/") != status.branch:
            status.branch_warning = (
                f"Docket commits on {status.branch}, not {default.removeprefix('origin/')}, the remote's default"
            )
        runs_of: dict[tuple[str, str], set[str]] = {}
        reading = self.rawlog.listed().reading()
        for run in self.rawlog.run_files():
            status.runs += 1
            for _, _, record in _records(run.path):
                if reading.skips(record):
                    continue
                status.records[record.source] += 1
                runs_of.setdefault((record.rid, record.v), set()).add(run.stem)
        status.seen_in_several_runs = sum(1 for runs in runs_of.values() if len(runs) > 1)
        status.uncommitted_runs = self.uncommitted_runs()
        return status


def is_docket_config(config: Path) -> bool:
    """Whether a vault.yaml is Docket's: it names the primary machine. Read as text, so one with a typo still counts
    and its load says what is wrong, while another tool's vault.yaml is just a no."""
    try:
        text = config.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return any(line.startswith("primary_machine:") for line in text.splitlines())


def _check_tracked(home: Home) -> None:
    """Refuse a folder vault whose vault.yaml the repo git found doesn't track. A folder vault has no ceiling: when its
    host's .git goes missing, git finds whatever repo holds the host, and Docket would commit the vault there. Init and
    convert never open a vault before its first commit, so a vault Docket made always passes (DR-0271)."""
    key = (home.git_dir, home.prefix)
    if key in _TRACKED:
        return
    if Git(home.root).run("ls-files", "--error-unmatch", "--", CONFIG_FILE, check=False).returncode != 0:
        raise DocketError(
            f"{home.root} is a folder vault, but {home.top}, the repo git finds around it, doesn't track its "
            f"{CONFIG_FILE}. If the repo the vault was made in, between the two, lost its .git, restore it; if you "
            f"moved the vault into {home.top}, commit its {CONFIG_FILE} there first"
        )
    _TRACKED.add(key)


def ignored(cwd: Path, prefix: str = "", config: tuple[str, ...] = ()) -> list[tuple[str, str, str]]:
    """(the sample path, the rule, its pattern) for each sample path of the vault an ignore rule hides; run from the
    vault, or from the host's top with the vault's prefix before it exists. `config` is git's own -c options."""
    paths = [prefix + p for p in IGNORE_SAMPLES]
    proc = Git(cwd).run(*config, "check-ignore", "-v", "--no-index", "--", *paths, check=False)
    found: list[tuple[str, str, str]] = []
    for line in proc.stdout.splitlines():
        rule, _, path = line.partition("\t")
        m = _IGNORE_RULE.match(rule)
        source, number, pattern = m.groups() if m else (rule, "", "")
        if pattern.startswith("!"):
            continue  # a rule that brings the path back
        found.append((path[len(prefix) :], f"{source}:{number}: {pattern}", pattern))
    return found


def attribute_mismatches(cwd: Path) -> list[tuple[str, str]]:
    """(the sample path, what is wrong) for each attribute git resolves to something other than the vault's own."""
    paths = sorted({path for path, _, _ in ATTRIBUTE_CHECKS})
    out = Git(cwd).run("check-attr", "filter", "merge", "eol", "--", *paths, check=False).stdout
    seen: dict[tuple[str, str], str] = {}
    for line in out.splitlines():
        path, attr, value = line.rsplit(": ", 2)
        seen[(path, attr)] = value
    return [
        (path, f"git gives it {attr}: {seen.get((path, attr), 'nothing')}, the vault's own is {want}; a rule in "
               ".git/info/attributes or the repo around it overrides the vault's .gitattributes")
        for path, attr, want in ATTRIBUTE_CHECKS
        if seen.get((path, attr)) != want
    ]  # fmt: skip


def with_attributes(text: str) -> str:
    """A vault's .gitattributes with every line of the current one it lacks: the reset line first, the rest after."""
    have = {line.strip() for line in text.splitlines()}
    lines = text.splitlines()
    wanted = [line for line in GITATTRIBUTES.splitlines() if line and not line.startswith("#")]
    if RESET_LINE not in have:
        lines = [GITATTRIBUTES.splitlines()[0], RESET_LINE, *lines]
    lines += [line for line in wanted if line not in have and line != RESET_LINE]
    return "".join(f"{line}\n" for line in lines)


def _started_with(state: Path, marker: Path) -> bool:
    """Whether the rebase in `state` is the one the sync that wrote `marker` began: git writes its head-name within
    seconds of the marker. A stale marker must never make Docket undo a rebase the user started later."""
    head = state / "head-name"
    try:
        return abs(head.stat().st_mtime - marker.stat().st_mtime) < SYNC_REBASE_WINDOW_SECONDS
    except FileNotFoundError:
        return False


def _unlink_writable(path: Path) -> None:
    path.chmod(stat.S_IWRITE)
    path.unlink(missing_ok=True)


def retry_writable(func: Callable[..., object], target: str, _exc: BaseException) -> None:
    """rmtree's error handler: make the file writable and try again. Only Windows refuses to delete a read-only file."""
    os.chmod(target, stat.S_IWRITE)  # noqa: PTH101 - shutil hands over a string path
    func(target)


def remove_tree(path: Path) -> None:
    """Delete a folder git wrote into: git makes its objects read-only, which Windows won't delete as they are."""
    shutil.rmtree(path, onexc=retry_writable)


def _records(path: Path) -> list[tuple[Path, int, RawRecord]]:
    out: list[tuple[Path, int, RawRecord]] = []
    with path.open(encoding="utf-8", newline="\n") as f:
        for n, line in enumerate(f, start=1):
            if line.strip():
                out.append((path, n, RawRecord.model_validate(json.loads(line))))
    return out
