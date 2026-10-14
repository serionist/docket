"""The raw log: one JSONL file per source per sync run per machine, written once and never changed (DR-0014).

`raw/<source>/<yyyy>/<mm>/<yyyy-mm-dd>T<hhmm>-<machine>.jsonl`, in UTC; a second run of a source in the same minute on
the same machine gets `.2`, `.3` before `.jsonl`. A record whose `(rid, v)` is already in the log is not written again.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from docket.errors import DocketError
from docket.vault.lock import VaultLock
from docket.vault.records import RawRecord, version

RUN_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{4})-([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)(?:\.(\d+))?\.jsonl$")


@dataclass(frozen=True)
class RunFile:
    path: Path
    source: str
    date: str
    time: str
    machine: str
    counter: int

    @property
    def stem(self) -> str:
        return self.path.name[: -len(".jsonl")]

    @property
    def order(self) -> tuple[str, str, str, int, str]:
        return (self.date, self.time, self.machine, self.counter, self.source)


@dataclass(frozen=True)
class RunResult:
    path: Path | None
    written: int
    skipped: int


def parse_run_file(raw_dir: Path, path: Path) -> RunFile | None:
    m = RUN_FILE.match(path.name)
    try:
        rel = path.relative_to(raw_dir).parts
    except ValueError:
        return None
    if not m or len(rel) != 4:
        return None
    return RunFile(path, rel[0], m.group(1), m.group(2), m.group(3), int(m.group(4) or 1))


class RawLog:
    def __init__(self, root: Path, machine: str, lock: VaultLock, tmp_dir: Path | None = None) -> None:
        self.root = root
        self.raw_dir = root / "raw"
        self.tmp_dir = tmp_dir or root / ".git" / "docket" / "tmp"
        self.machine = machine
        self.lock = lock
        self._seen: set[tuple[str, str]] = set()
        self._read: set[Path] = set()

    def run_files(self) -> list[RunFile]:
        files = (
            [parse_run_file(self.raw_dir, p) for p in self.raw_dir.rglob("*.jsonl")] if self.raw_dir.is_dir() else []
        )
        return sorted((f for f in files if f is not None), key=lambda f: f.order)

    def _refresh(self) -> None:
        """Read the run files this process hasn't seen, so another process's writes count as already in the log."""
        runs = self.run_files()
        if self._read - {run.path for run in runs}:
            # A run file that was never committed was deleted by hand; its records are no longer in the log.
            self._seen.clear()
            self._read.clear()
        for run in runs:
            if run.path in self._read:
                continue
            for _, record in _lines(run.path):
                self._seen.add((record.rid, record.v))
            self._read.add(run.path)

    def contains(self, record: RawRecord) -> bool:
        self._refresh()
        return (record.rid, version(record)) in self._seen

    def rids(self) -> set[str]:
        """Every rid the raw log holds, from the same scan `contains` keeps."""
        self._refresh()
        return {rid for rid, _ in self._seen}

    def _target(self, source: str, run_at: datetime) -> Path:
        at = run_at.astimezone(UTC)
        folder = self.raw_dir / source / f"{at:%Y}" / f"{at:%m}"
        base = f"{at:%Y-%m-%dT%H%M}-{self.machine}"
        counter = 1
        while True:
            name = f"{base}.jsonl" if counter == 1 else f"{base}.{counter}.jsonl"
            if not (folder / name).exists():
                return folder / name
            counter += 1

    def write_run(self, source: str, records: list[RawRecord], run_at: datetime | None = None) -> RunResult:
        """Write one run of a source. Records already in the log, or earlier in this batch, are skipped."""
        foreign = next((r for r in records if r.source != source), None)
        if foreign is not None:
            raise DocketError(f"{foreign.rid} is from {foreign.source!r}, not this run's source {source!r}")
        with self.lock:
            self._refresh()
            target = self._target(source, run_at or datetime.now(UTC))
            stem = target.name[: -len(".jsonl")]
            lines: list[str] = []
            batch: set[tuple[str, str]] = set()
            skipped = 0
            for record in records:
                stored = record.model_copy(update={"v": version(record), "run": stem})
                key = (stored.rid, stored.v)
                if key in self._seen or key in batch:
                    skipped += 1
                    continue
                batch.add(key)
                lines.append(stored.to_json())
            if not lines:
                return RunResult(None, 0, skipped)
            self._write_new(target, "".join(f"{line}\n" for line in lines))
            # Only now are these records in the log: a write that failed leaves them free to be written again.
            self._seen |= batch
            self._read.add(target)
            return RunResult(target, len(lines), skipped)

    def _write_new(self, target: Path, text: str) -> None:
        """A new file through a temp file and a rename; the temp file lives outside raw/, so it is never committed."""
        if target.exists():
            raise DocketError(f"{target} exists; a raw file is written once")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.tmp_dir / f"{target.name}.{os.getpid()}.tmp"
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as out:
                out.write(text)
                out.flush()
                os.fsync(out.fileno())
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)

    def iter_records(self) -> Iterator[tuple[Path, int, RawRecord]]:
        for run in self.run_files():
            for line, record in _lines(run.path):
                yield run.path, line, record

    def read(self, rid: str) -> list[RawRecord]:
        """Every stored version of a record, in run order."""
        return [record for _, _, record in self.iter_records() if record.rid == rid]

    def latest(self, rid: str) -> RawRecord | None:
        versions = self.read(rid)
        return versions[-1] if versions else None


def _lines(path: Path) -> Iterator[tuple[int, RawRecord]]:
    with path.open(encoding="utf-8", newline="\n") as f:
        for n, line in enumerate(f, start=1):
            if line.strip():
                yield n, RawRecord.model_validate(json.loads(line))
