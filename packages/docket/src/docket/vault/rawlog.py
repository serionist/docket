"""The raw log: one JSONL file per source per sync run per machine, written once and never changed (DR-0014).

`raw/<source>/<yyyy>/<mm>/<yyyy-mm-dd>T<hhmm>-<machine>.jsonl`, in UTC; a second run of a source in the same minute on
the same machine gets `.2`, `.3` before `.jsonl`, always after the highest counter that minute has. A record whose
`(rid, v)` is already in the log is not written again.

A run file holds at most 16 MiB of lines (DR-0488): a write past that goes on in the next files, each a run of its own
by the same rule, in the records' order, so a host's file limit (GitHub's 100 MB) is never met and an older Docket
reads them as it reads any runs. A record is never split; one larger than the cap is a file of its own.

A line the forgotten list takes reads as gone here, wherever a merge left it (DR-0579), and what this log keeps of the
files it read is keyed by their size and modification time too, since a forget changes a run file in place.
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
from docket.vault import forgotten
from docket.vault.forgotten import Forgotten
from docket.vault.home import Home, make_private
from docket.vault.lock import VaultLock
from docket.vault.records import RawRecord, version

# The most bytes of lines a run file takes before the next record goes to a file of its own (DR-0488).
RUN_FILE_BYTES = 16 * 1024 * 1024
RUN_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{4})-([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)(?:\.(\d+))?\.jsonl$")

# A run's place in run order: date, time, machine, counter, source.
RunOrder = tuple[str, str, str, int, str]


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
    def order(self) -> RunOrder:
        return (self.date, self.time, self.machine, self.counter, self.source)


@dataclass(frozen=True)
class RunResult:
    # Every file the write made, in run order; none when everything was already in the log.
    paths: tuple[Path, ...]
    written: int
    skipped: int

    @property
    def path(self) -> Path | None:
        """The first file the write made, or None."""
        return self.paths[0] if self.paths else None


def parse_run_file(raw_dir: Path, path: Path) -> RunFile | None:
    m = RUN_FILE.match(path.name)
    try:
        rel = path.relative_to(raw_dir).parts
    except ValueError:
        return None
    if not m or len(rel) != 4:
        return None
    return RunFile(path, rel[0], m.group(1), m.group(2), m.group(3), int(m.group(4) or 1))


def run_stamps(root: Path, source: str) -> dict[str, tuple[int, int]]:
    """Each run file of a source, by its path from the vault's root, with its size and modification time: the files
    `parse_run_file` takes under raw/<source>/, listed with os.scandir, which on Windows gives both without opening
    each file (DR-0446)."""
    stamps: dict[str, tuple[int, int]] = {}
    try:
        years = list(os.scandir(root / "raw" / source))
    except (FileNotFoundError, NotADirectoryError):
        return stamps
    for year in years:
        if not year.is_dir():
            continue
        for month in os.scandir(year.path):
            if not month.is_dir():
                continue
            for entry in os.scandir(month.path):
                if RUN_FILE.match(entry.name):
                    st = entry.stat()
                    stamps[f"raw/{source}/{year.name}/{month.name}/{entry.name}"] = (st.st_size, st.st_mtime_ns)
    return stamps


class RawLog:
    def __init__(
        self, root: Path, machine: str, lock: VaultLock, tmp_dir: Path | None = None, home: Home | None = None
    ) -> None:
        self.root = root
        self.raw_dir = root / "raw"
        self.tmp_dir = tmp_dir or root / ".git" / "docket" / "tmp"
        self.machine = machine
        self.lock = lock
        self.home = home
        self._seen: set[tuple[str, str]] = set()
        # Each run file read, with its size and modification time then, and the forgotten list it was read under.
        self._read: dict[Path, tuple[int, int]] = {}
        self._digest = ""
        self._reading = forgotten.EMPTY.reading()

    def listed(self) -> Forgotten:
        """The forgotten list in force, which every read here obeys (DR-0579)."""
        return forgotten.load(self.root, self.home) if self.home is not None else forgotten.at(self.root)

    def stamped(self) -> list[tuple[RunFile, tuple[int, int]]]:
        """Every run file in run order, with its size and modification time, listed with os.scandir as `run_stamps`
        lists one source's (DR-0446)."""
        found: list[tuple[RunFile, tuple[int, int]]] = []
        try:
            sources = [entry.name for entry in os.scandir(self.raw_dir) if entry.is_dir()]
        except (FileNotFoundError, NotADirectoryError):
            return found
        for source in sources:
            for rel, stamp in run_stamps(self.root, source).items():
                run = parse_run_file(self.raw_dir, self.root / rel)
                if run is not None:
                    found.append((run, stamp))
        return sorted(found, key=lambda f: f[0].order)

    def run_files(self) -> list[RunFile]:
        return [run for run, _ in self.stamped()]

    def _refresh(self) -> None:
        """Read the run files this process hasn't seen, so another process's writes count as already in the log."""
        listed = self.listed()
        runs = self.stamped()
        stamps = {run.path: stamp for run, stamp in runs}
        if listed.digest != self._digest or any(stamps.get(path) != stamp for path, stamp in self._read.items()):
            # A run file deleted by hand, one a forget changed in place, or a list that moved: read everything again.
            self._seen.clear()
            self._read.clear()
            self._digest = listed.digest
            self._reading = listed.reading()
        for run, stamp in runs:
            if run.path in self._read:
                continue
            for _, record in _lines(run.path):
                if not self._reading.skips(record):
                    self._seen.add((record.rid, record.v))
            self._read[run.path] = stamp

    def contains(self, record: RawRecord) -> bool:
        self._refresh()
        return (record.rid, version(record)) in self._seen

    def rids(self) -> set[str]:
        """Every rid the raw log holds, from the same scan `contains` keeps."""
        self._refresh()
        return {rid for rid, _ in self._seen}

    def _target(self, source: str, run_at: datetime) -> Path:
        """The next run file of this minute and machine: after the highest counter the minute has, never in a gap a
        deleted file left, so a newer run always sorts after an older one."""
        at = run_at.astimezone(UTC)
        folder = self.raw_dir / source / f"{at:%Y}" / f"{at:%m}"
        base = f"{at:%Y-%m-%dT%H%M}-{self.machine}"
        names: list[str] = [p.name for p in folder.glob(f"{base}*.jsonl")] if folder.is_dir() else []
        taken = [
            int(m.group(4) or 1)
            for name in names
            if (m := RUN_FILE.match(name)) and f"{m.group(1)}T{m.group(2)}-{m.group(3)}" == base
        ]
        counter = max(taken, default=0) + 1
        return folder / (f"{base}.jsonl" if counter == 1 else f"{base}.{counter}.jsonl")

    def write_run(self, source: str, records: list[RawRecord], run_at: datetime | None = None) -> RunResult:
        """Write one run of a source, in the records' order, in as many files as 16 MiB of lines each takes. Records
        already in the log, or earlier in this batch, are skipped."""
        foreign = next((r for r in records if r.source != source), None)
        if foreign is not None:
            raise DocketError(f"{foreign.rid} is from {foreign.source!r}, not this run's source {source!r}")
        with self.lock:
            self._refresh()
            at = run_at or datetime.now(UTC)
            fresh: list[RawRecord] = []
            batch: set[tuple[str, str]] = set()
            skipped = 0
            for record in records:
                v = version(record)
                key = (record.rid, v)
                if key in self._seen or key in batch:
                    skipped += 1
                    continue
                batch.add(key)
                fresh.append(record.model_copy(update={"v": v}))
            paths: list[Path] = []
            lines: list[str] = []
            keys: set[tuple[str, str]] = set()
            size = 0
            target = self._target(source, at)
            for record in fresh:
                line = _line(record, target)
                if lines and size + len(line.encode("utf-8")) > RUN_FILE_BYTES:
                    self._finish(target, lines, keys)
                    paths.append(target)
                    lines, keys, size = [], set[tuple[str, str]](), 0
                    target = self._target(source, at)
                    line = _line(record, target)
                lines.append(line)
                keys.add((record.rid, record.v))
                size += len(line.encode("utf-8"))
            if lines:
                self._finish(target, lines, keys)
                paths.append(target)
            return RunResult(tuple(paths), len(fresh), skipped)

    def _finish(self, target: Path, lines: list[str], keys: set[tuple[str, str]]) -> None:
        """One run file written. Only now are its records in the log: a write that failed leaves them free to be
        written again, and a file already written keeps its own."""
        self._write_new(target, "".join(lines))
        self._seen |= keys
        st = target.stat()
        self._read[target] = (st.st_size, st.st_mtime_ns)

    def _write_new(self, target: Path, text: str) -> None:
        """A new file through a temp file and a rename; the temp file lives outside raw/, so it is never committed."""
        if target.exists():
            raise DocketError(f"{target} exists; a raw file is written once")
        make_private(self.lock.git_dir, self.tmp_dir)
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
        """Every record in run order, but those the forgotten list takes."""
        reading = self.listed().reading()
        for run in self.run_files():
            for line, record in _lines(run.path):
                if not reading.skips(record):
                    yield run.path, line, record

    def read(self, rid: str) -> list[RawRecord]:
        """Every stored version of a record, in run order."""
        return [record for _, _, record in self.iter_records() if record.rid == rid]

    def latest(self, rid: str) -> RawRecord | None:
        versions = self.read(rid)
        return versions[-1] if versions else None


def _line(record: RawRecord, target: Path) -> str:
    """A record as its run file's line, its `run` the file's stem."""
    return record.model_copy(update={"run": target.name[: -len(".jsonl")]}).to_json() + "\n"


def _lines(path: Path) -> Iterator[tuple[int, RawRecord]]:
    with path.open(encoding="utf-8", newline="\n") as f:
        for n, line in enumerate(f, start=1):
            if line.strip():
                yield n, RawRecord.model_validate(json.loads(line))
