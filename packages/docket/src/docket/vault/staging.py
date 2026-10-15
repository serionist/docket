"""Staging: records waiting for the gate, on the local disk and never committed.

`staging/<source>/<key>.json` is one staged version of a record, the key the first 32 hex characters of
sha256(rid + "\\n" + v). Its file holds the state (`pending`, waiting for a classification, or `ready`, going to raw at
the next promotion), who classified it, its cuts, why a promotion held it back, and the record as redacted at intake.
`staging/_decided.jsonl` keeps hashes of the versions decided by hand (classified private, or promoted with cuts), so
a lookback that pulls one again doesn't ask again; it never holds content.

Staging holds the only copy of a waiting record, so its format is a one-way door: a later Docket reads every earlier
`schema_version`. Every file is written through a temp file and a rename, so a crash leaves the old file or the new.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from docket.vault.cuts import Cut
from docket.vault.home import Home, make_private
from docket.vault.records import RawRecord

STAGING = "staging"
DECIDED = "_decided.jsonl"
SCHEMA_VERSION = 1
# How long a rename or delete Windows refuses (another process has the file open) is tried again.
RETRY_SECONDS = 1.0
_RETRY_PAUSE = 0.05

State = Literal["pending", "ready"]
Classifier = Literal["user", "claude"]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def key_of(rid: str, v: str) -> str:
    return digest(f"{rid}\n{v}")


def rid_hash(rid: str) -> str:
    return digest(rid)


class Staged(BaseModel):
    """One staged version of a record."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    state: State
    staged_at: datetime
    classified_by: Classifier | None = None
    cuts: list[Cut] = Field(default_factory=list[Cut])
    problem: str | None = None
    record: RawRecord

    @field_validator("staged_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("staged_at needs its time zone")
        return value

    @property
    def key(self) -> str:
        return key_of(self.record.rid, self.record.v)


@dataclass(frozen=True)
class Entry:
    path: Path
    staged: Staged

    @property
    def key(self) -> str:
        return self.staged.key


@dataclass
class Scan:
    entries: list[Entry] = field(default_factory=list[Entry])
    unreadable: list[tuple[Path, str]] = field(default_factory=list[tuple[Path, str]])


@dataclass(frozen=True)
class Decided:
    keys: frozenset[str]
    rids: frozenset[str]


def retry(op: Callable[[], object]) -> None:
    """Run a rename or delete, trying again for a moment when another process (an indexer, an antivirus, a reader on
    Windows) holds the file open."""
    deadline = time.monotonic() + RETRY_SECONDS
    while True:
        try:
            op()
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(_RETRY_PAUSE)


def write_replacing(home: Home, path: Path, text: str, tmp_name: str) -> None:
    """Write a vault file through a temp file in Docket's private folder and a rename, so a crash leaves the old file
    or the new, and a link planted at the path is replaced, never written through."""
    tmp = make_private(home.git_dir, home.tmp_dir) / f"{tmp_name}.{os.getpid()}.tmp"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        retry(lambda: tmp.replace(path))
    finally:
        tmp.unlink(missing_ok=True)


class Staging:
    def __init__(self, root: Path, home: Home) -> None:
        self.dir = root / STAGING
        self.home = home
        self.tmp_dir = home.tmp_dir
        self.decided_file = self.dir / DECIDED

    def path(self, staged: Staged) -> Path:
        return self.dir / staged.record.source / f"{staged.key}.json"

    def write(self, staged: Staged) -> Path:
        """Write a staged version through a temp file and a rename; the temp file lives in .git, outside staging."""
        target = self.path(staged)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = make_private(self.home.git_dir, self.tmp_dir) / f"staging-{staged.key}.{os.getpid()}.tmp"
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as out:
                out.write(staged.model_dump_json(indent=2) + "\n")
                out.flush()
                os.fsync(out.fileno())
            retry(lambda: tmp.replace(target))
        finally:
            tmp.unlink(missing_ok=True)
        return target

    def read(self, path: Path) -> Staged:
        return Staged.model_validate_json(path.read_text(encoding="utf-8"))

    def delete(self, path: Path) -> None:
        retry(lambda: path.unlink(missing_ok=True))

    def scan(self) -> Scan:
        """Every staged file; one that doesn't parse is reported and never promoted."""
        scan = Scan()
        if not self.dir.is_dir():
            return scan
        for path in sorted(self.dir.glob("*/*.json")):
            try:
                scan.entries.append(Entry(path, self.read(path)))
            except (OSError, ValueError, ValidationError) as e:
                scan.unreadable.append((path, str(e).splitlines()[0] if str(e) else type(e).__name__))
        return scan

    def clear_temp(self) -> int:
        """Remove temp files a crash left. Called under the vault lock, when no write of ours is running."""
        stale = list(self.tmp_dir.glob("staging-*.tmp")) if self.tmp_dir.is_dir() else []
        for tmp in stale:
            tmp.unlink(missing_ok=True)
        return len(stale)

    # -- the hand-decision memory --------------------------------------------------------------------------------

    def decided(self) -> Decided:
        """The keys and rid hashes decided by hand; a torn last line (a crash mid-append) is skipped."""
        keys: set[str] = set()
        rids: set[str] = set()
        try:
            lines = self.decided_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        for line in lines:
            try:
                data: Any = json.loads(line)
                keys.add(str(data["key"]))
                rids.add(str(data["rid"]))
            except (ValueError, KeyError, TypeError):
                continue
        return Decided(frozenset(keys), frozenset(rids))

    def remember(self, decided: list[tuple[str, str]]) -> None:
        """Append (rid, key) pairs as hashes, and fsync, before the staged files they name are deleted."""
        if not decided:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        lines = "".join(json.dumps({"rid": rid_hash(rid), "key": key}) + "\n" for rid, key in decided)
        with self.decided_file.open("a+b") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() > 0:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    # A crash mid-append left a torn line; end it so the next line stays whole.
                    lines = "\n" + lines
            f.seek(0, os.SEEK_END)
            f.write(lines.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
