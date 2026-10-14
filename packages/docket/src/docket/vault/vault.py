"""A vault on disk: its layout, opening and creating one, committing, and checking that the raw log is intact."""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from docket.errors import DocketError
from docket.vault import machine as machines
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.git import Git, Identity, SyncResult, sync_marker
from docket.vault.lock import VaultLock
from docket.vault.rawlog import RUN_FILE, RawLog, parse_run_file
from docket.vault.records import RawRecord

log = logging.getLogger(__name__)

# Folders every vault has from the start; git keeps an empty folder only with a file in it.
FOLDERS = ["raw", "media", "processed", "entities", "ledger/ops", "state"]
GITATTRIBUTES = """\
# Text is stored with LF everywhere, so every machine sees the same bytes.
* text=auto eol=lf
# Files several machines append to merge by keeping both sides' lines.
*.jsonl merge=union
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
    records: Counter[str] = field(default_factory=Counter[str])
    runs: int = 0
    uncommitted_runs: list[str] = field(default_factory=list[str])
    seen_in_several_runs: int = 0
    last_commit: str | None = None


class Vault:
    def __init__(self, root: Path, config: VaultConfig) -> None:
        self.root = root
        self.config = config
        self.machine = machines.resolve(root)
        owner_email = config.owner.emails[0] if config.owner.emails else "docket@localhost"
        self.git = Git(root, Identity(config.owner.name, owner_email))
        self.lock = VaultLock(root / ".git")
        self.rawlog = RawLog(root, self.machine, self.lock)

    # -- finding and creating ------------------------------------------------------------------------------------

    @staticmethod
    def find(start: Path) -> Path | None:
        """The vault holding a path: the nearest folder upward with a vault.yaml and its own .git."""
        for folder in [start.resolve(), *start.resolve().parents]:
            if (folder / CONFIG_FILE).is_file() and (folder / ".git").exists():
                return folder
        return None

    @classmethod
    def open(cls, path: Path) -> Vault:
        root = cls.find(path)
        if root is None:
            raise DocketError(f"no vault at or above {path}; create one with `docket init`")
        return cls(root, VaultConfig.load(root / CONFIG_FILE))

    @classmethod
    def init(cls, path: Path, config: VaultConfig, machine: str) -> Vault:
        """Create a vault in a folder that doesn't exist yet, or is empty. Nothing existing is ever overwritten."""
        path = path.resolve()
        if cls.find(path) == path:
            raise DocketError(f"already a vault: {path}")
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise DocketError(f"{path} exists and isn't empty; a vault starts in an empty folder")
        created = not path.exists()
        written: list[str] = []
        try:
            path.mkdir(parents=True, exist_ok=True)
            vault_git = Git(path, Identity(config.owner.name, config.owner.emails[0] if config.owner.emails else ""))
            vault_git.init()
            for folder in FOLDERS:
                keep = path / folder / ".gitkeep"
                keep.parent.mkdir(parents=True, exist_ok=True)
                keep.write_text("", encoding="utf-8")
                written.append(f"{folder}/.gitkeep")
            for name, text in ((".gitattributes", GITATTRIBUTES), (".gitignore", GITIGNORE)):
                (path / name).write_text(text, encoding="utf-8", newline="\n")
                written.append(name)
            (path / CONFIG_FILE).write_text(config.dump(), encoding="utf-8", newline="\n")
            written.append(CONFIG_FILE)
            machines.remember(path, machine)
            vault_git.lfs_install()
            vault = cls(path, config)
            vault.git.commit(written, "Start the vault")
            return vault
        except BaseException:
            if created:
                remove_tree(path)
            else:
                for child in list(path.iterdir()):
                    remove_tree(child) if child.is_dir() else _unlink_writable(child)
            raise

    # -- writing ---------------------------------------------------------------------------------------------------

    def write_run(self, source: str, records: list[RawRecord], run_at: datetime | None = None) -> str | None:
        """Write a run of records and commit it with any run file an earlier, interrupted run left uncommitted."""
        result = self.rawlog.write_run(source, records, run_at or datetime.now(UTC))
        message = f"Raw: {source}, {result.written} record(s) from {self.machine}"
        return self.commit([], message) if result.path is not None else None

    def commit(self, paths: list[str], message: str) -> str | None:
        """Commit locally. Run files left uncommitted by an interrupted run go in too; never a changed raw file."""
        with self.lock:
            self._recover()
            orphans = [p for p in self.git.untracked("raw") if RUN_FILE.match(Path(p).name)]
            return self.git.commit([*paths, *orphans], message)

    def sync(self) -> SyncResult:
        with self.lock:
            self._recover()
            return self.git.sync()

    def _recover(self) -> None:
        """Before writing: undo a rebase a killed sync of ours left behind (its marker says so, and under the lock no
        sync of ours is running), and refuse to touch anything else git is in the middle of, which is the user's."""
        git_dir = self.root / ".git"
        marker = sync_marker(self.root)
        rebasing = (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()
        if rebasing and marker.exists():
            log.warning("undoing a rebase a stopped sync left in %s", self.root)
            self.git.run("rebase", "--abort")
            rebasing = False
        marker.unlink(missing_ok=True)
        if rebasing:
            raise DocketError(f"a rebase is in progress in {self.root}; finish it (git rebase --continue) or undo it")
        for head, what in (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert")):
            if (git_dir / head).exists():
                raise DocketError(f"a {what} is in progress in {self.root}; finish or undo it before Docket writes")
        if self.git.branch() == "HEAD":
            raise DocketError(f"{self.root} isn't on a branch (a detached HEAD); check out main before Docket writes")

    # -- checking ------------------------------------------------------------------------------------------------

    def check(self) -> list[Problem]:
        """What would make the raw log untrustworthy: a file that doesn't parse, a record twice in one run, a raw file
        changed or deleted after it was committed, or a file in raw/ that isn't a run file."""
        problems: list[Problem] = []
        raw = self.root / "raw"
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
                    if (record.rid, record.v) in seen:
                        problems.append(Problem(f"{rel}:{n}", f"{record.rid} is in this run twice"))
                    seen.add((record.rid, record.v))
        problems.extend(
            Problem(rel, "changed or deleted after it was committed; raw files are written once")
            for rel in self.git.changed_against_head("raw")
        )
        return problems

    def status(self) -> Status:
        status = Status(root=self.root, machine=self.machine, last_commit=self.git.last_commit())
        runs_of: dict[tuple[str, str], set[str]] = {}
        for run in self.rawlog.run_files():
            status.runs += 1
            for _, _, record in _records(run.path):
                status.records[record.source] += 1
                runs_of.setdefault((record.rid, record.v), set()).add(run.stem)
        status.seen_in_several_runs = sum(1 for runs in runs_of.values() if len(runs) > 1)
        status.uncommitted_runs = [p for p in self.git.untracked("raw") if RUN_FILE.match(Path(p).name)]
        return status


def _unlink_writable(path: Path) -> None:
    path.chmod(stat.S_IWRITE)
    path.unlink(missing_ok=True)


def remove_tree(path: Path) -> None:
    """Delete a folder git wrote into: git makes its objects read-only, which Windows won't delete as they are."""

    def retry(func: Callable[..., object], target: str, _exc: BaseException) -> None:
        os.chmod(target, stat.S_IWRITE)  # noqa: PTH101 - shutil hands over a string path
        func(target)

    shutil.rmtree(path, onexc=retry)


def _records(path: Path) -> list[tuple[Path, int, RawRecord]]:
    out: list[tuple[Path, int, RawRecord]] = []
    with path.open(encoding="utf-8", newline="\n") as f:
        for n, line in enumerate(f, start=1):
            if line.strip():
                out.append((path, n, RawRecord.model_validate(json.loads(line))))
    return out
