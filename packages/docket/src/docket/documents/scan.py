"""`docket scan`: a folder source read into staging, read-only (DR-0480, DR-0486, DR-0487).

Cheap on thousands of files: a stat each, a hash when the stat moved, the extraction child only when the bytes did.

| Step | A file is |
|---|---|
| its stat matches the cache, and the vault's newest version still carries the cached identity | done |
| its bytes match the cache, or its identity matches the vault's newest version | done; the cache moves |
| else | extracted: the file record gets a new version, a part one only when its redacted title or text differs |

A file deleted, moved, newly excluded, or kept out whole by the never-store lists gets a gone version of its record
and of every part it had, once. A passing reason (locked, a placeholder, on hold, a stopped or crashed read) never
makes a file gone. What the vault holds comes from the source's run files and its records in staging; the cache, in
the vault's private folder and keyed by id, names no file. Records go to the gate in batches, and the cache is saved
after each, so an interrupted scan goes on where it stopped. Only the primary machine scans, one scan at a time.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout
from pydantic import ValidationError

from docket import folders, userconfig
from docket.documents import records
from docket.documents import text as texts
from docket.documents.extract import Done, Extractor, Failed, NotRead, Outcome
from docket.documents.limits import LIMITS, MIB, Limits, NotReadError, mib
from docket.documents.parts import PLAIN, Extracted, Format, format_of
from docket.errors import DocketError
from docket.vault import holds, inforce, neverstore
from docket.vault.config import NeverStore, SourceConfig
from docket.vault.gate import Gate, StageResult
from docket.vault.git import toplevel
from docket.vault.home import make_private
from docket.vault.rawlog import parse_run_file
from docket.vault.records import RawRecord, canonical_json, owning_id
from docket.vault.redact import Redactor
from docket.vault.staging import Staged, Staging
from docket.vault.vault import Vault

# The text a call to the gate takes at most: each call reads all of staging. A batch also goes in after this many
# files or seconds, and the cache is saved with it, so an interrupted scan goes on where it stopped.
BATCH_TEXT = 32 * MIB
BATCH_FILES = 200
BATCH_SECONDS = 60.0
# A scan that would make more than half the files it knew gone, and at least this many, stages nothing unasked.
GONE_GUARD = 20
CACHE_DIR = "documents"
CACHE_SCHEMA = 1
LOCK_NAME = "scan.lock"
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"
LFS_MAX = 1024
OFFICE_LOCK = "~$"
_CHUNK = MIB

Settled = Literal["same", "touched"]


@dataclass
class ScanReport:
    """What a scan did, in counts and rids: never the path of a file it left out."""

    source: str
    folder: str = ""
    missing: bool = False
    files: int = 0
    # Per file: same, touched, new, changed, back, moved, not read, kept out, retry, unreadable, on hold,
    # placeholders, lfs pointers.
    outcomes: Counter[str] = field(default_factory=Counter[str])
    # Paths the listing left out: excluded, links, nested repos, not files, lock files, never-store, same id, other
    # extensions by extension.
    skipped: Counter[str] = field(default_factory=Counter[str])
    gone: int = 0
    not_read: list[str] = field(default_factory=list[str])
    # Folders that couldn't be listed now, by their paths inside the folder: their files keep their last version.
    unlisted: list[str] = field(default_factory=list[str])
    # Set when the guard stopped the scan: the files that would have gone, of the ones the vault held.
    guarded: int | None = None
    known: int = 0
    staged: StageResult = field(default_factory=StageResult)


@dataclass
class _Cached:
    """A file's cache entry, by id: the stat taken before its last read, its bytes' sha256, and what came of it."""

    size: int
    mtime_ns: int
    sha256: str | None = None
    # The identity raw holds for it (DR-0483); none for an md or txt file past a cap, whose record holds none.
    identity: str | None = None
    no_identity: bool = False
    # The never-store lists' hash when they kept the file out.
    kept_out: str | None = None
    # The bytes' sha256 of a read that stopped or crashed, tried once more.
    failed: str | None = None

    def stat(self) -> tuple[int, int]:
        return (self.size, self.mtime_ns)


@dataclass(frozen=True)
class _Candidate:
    rel: str
    path: Path
    info: os.stat_result
    fmt: Format
    rid: str

    @property
    def id(self) -> str:
        return self.rid.split(":", 1)[1]

    @property
    def stat(self) -> tuple[int, int]:
        return (self.info.st_size, self.info.st_mtime_ns)


def folder_of(vault: Vault, source: str, config: SourceConfig) -> Path:
    """The folder a folder source names on this machine, its links followed: the user's config's when it names one
    (DR-0490), else vault.yaml's, under the home folder or relative to the repo that holds the vault (DR-0481)."""
    value = config.folder
    if value is None:
        raise DocketError(f"source {source} has no folder in vault.yaml, so docket scan doesn't read it")
    mine = userconfig.folder_for(vault.root, source)
    if mine is not None:
        folder = mine
    else:
        if value == "~" or value.startswith("~/"):
            base = Path.home()
            folder = base / value[2:]
        else:
            repo = toplevel(vault.root.parent)
            if repo is None:
                raise DocketError(
                    f"{source}'s folder {value} is relative to the repo that holds the vault, and {vault.root} isn't "
                    "in one; write it under ~/, or give this machine's folder in Docket's config.yaml, under folders"
                )
            base = repo
            folder = repo / value
        # Before links are followed: vault.yaml's folder is under the home folder or in the repo, never past them.
        if not Path(os.path.normpath(folder)).is_relative_to(base):
            raise DocketError(
                f"{source}'s folder {value} leads out of {base}; vault.yaml names one under ~/ or in the repo"
            )
    folder = folder.resolve()
    root = vault.root.resolve()
    if folder == root or root in folder.parents:
        raise DocketError(f"{source}'s folder is inside the vault; a folder source reads a folder outside it")
    return folder


def folder_sources(vault: Vault) -> list[str]:
    return sorted(name for name, c in vault.config.sources.items() if c.folder is not None and c.enabled)


def scan(
    vault: Vault,
    source: str,
    *,
    accept_gone: bool = False,
    extractor: Callable[[], Extractor] = Extractor,
    limits: Limits = LIMITS,
    now: datetime | None = None,
) -> ScanReport:
    """Read a folder source's folder into staging. Refused on any machine but the vault's primary one."""
    config = vault.config.sources.get(source)
    if config is None:
        names = ", ".join(folder_sources(vault)) or "none"
        raise DocketError(f"no source {source!r} in vault.yaml; folder sources: {names}{inforce.note(vault.waiting)}")
    if config.folder is None:
        raise DocketError(f"source {source} isn't a folder source: it has no folder in vault.yaml")
    if not config.enabled:
        raise DocketError(f"source {source} is turned off in vault.yaml{inforce.note(vault.waiting)}")
    primary = vault.config.primary_machine
    if vault.machine != primary:
        raise DocketError(
            f"folder sources are scanned on the vault's primary machine, {primary}, and this is {vault.machine}: two "
            "checkouts at different commits would turn each other's edits back"
        )
    folder = folder_of(vault, source, config)
    report = ScanReport(source, folder=str(folder))
    if not folder.is_dir():
        report.missing = True
        return report
    private = make_private(vault.home.git_dir, vault.home.private / CACHE_DIR)
    try:
        with FileLock(private / LOCK_NAME, timeout=0):
            run = _Scan(
                vault, source, config=config, folder=folder, private=private, report=report, extractor=extractor,
                limits=limits, now=now,
            )  # fmt: skip
            return run.run(accept_gone)
    except Timeout:
        raise DocketError("another docket scan of this vault is running; try again once it ends") from None


def lists_hash(lists: NeverStore) -> str:
    return hashlib.sha256(canonical_json(lists.model_dump(mode="json")).encode("utf-8")).hexdigest()


class _Holdings:
    """What the vault holds of a source: each record's newest version, from its run files in run order, then its
    records in staging in the order they were staged."""

    def __init__(self, vault: Vault, source: str) -> None:
        self.newest: dict[str, RawRecord] = {}
        raw = vault.root / "raw"
        folder = raw / source
        runs = [parse_run_file(raw, p) for p in folder.rglob("*.jsonl")] if folder.is_dir() else []
        for run in sorted((r for r in runs if r is not None), key=lambda r: r.order):
            with run.path.open(encoding="utf-8", newline="\n") as f:
                for line in f:
                    if line.strip():
                        self._add(RawRecord.model_validate(json.loads(line)))
        staging = Staging(vault.root, vault.home)
        staged: list[Staged] = []
        for path in sorted((vault.root / "staging" / source).glob("*.json")):
            try:
                staged.append(staging.read(path))
            except (OSError, ValueError, ValidationError):
                # The gate reports a staged file that doesn't parse, and never promotes it.
                continue
        for entry in sorted(staged, key=lambda s: s.staged_at):
            self._add(entry.record)
        self.parts: dict[str, list[str]] = {}
        for rid, record in self.newest.items():
            if record.kind in records.PART_KINDS and record.thread is not None:
                self.parts.setdefault(record.thread, []).append(rid)

    def _add(self, record: RawRecord) -> None:
        self.newest[record.rid] = record

    def files(self) -> dict[str, RawRecord]:
        """The source's files the vault holds as current: their newest version isn't gone."""
        return {rid: r for rid, r in self.newest.items() if r.kind == records.FILE_KIND and not records.is_gone(r)}

    def parts_of(self, rid: str) -> list[RawRecord]:
        """A file's parts whose newest version isn't gone."""
        return [self.newest[p] for p in sorted(self.parts.get(rid, [])) if not records.is_gone(self.newest[p])]


class _Scan:
    def __init__(
        self,
        vault: Vault,
        source: str,
        *,
        config: SourceConfig,
        folder: Path,
        private: Path,
        report: ScanReport,
        extractor: Callable[[], Extractor],
        limits: Limits,
        now: datetime | None,
    ) -> None:
        self.vault = vault
        self.source = source
        self.config = config
        self.folder = folder
        self.report = report
        self.make_extractor = extractor
        self.extractor: Extractor | None = None
        self.limits = limits
        self.at = (now or datetime.now(UTC)).astimezone(UTC)
        self.day = self.at.astimezone(ZoneInfo(vault.config.owner.time_zone)).date().isoformat()
        self.lists = vault.config.never_store
        self.lists_hash = lists_hash(self.lists)
        self.redactor = Redactor.from_config(vault.config.redaction)
        self.held = holds.read(vault.root, source)
        self.cache_path = private / f"{source}.json"
        self.cache = self._load()
        # Under the vault's lock: a promotion between reading raw and reading staging would show a version twice
        # or not at all, and an unchanged file would be read as changed.
        with vault.lock:
            self.holdings = _Holdings(vault, source)
        self.gate = Gate(vault)
        self.batch: list[RawRecord] = []
        self.batch_text = 0
        self.pending: dict[str, _Cached | None] = {}
        # A file's identity as it leaves its path, so the one that arrives elsewhere names where it came from.
        self.leaving: dict[str, list[str]] = {}
        self.moves: dict[str, str] = {}

    # -- the cache ----------------------------------------------------------------------------------------------

    def _load(self) -> dict[str, _Cached]:
        try:
            data: Any = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or cast(dict[str, Any], data).get("schema") != CACHE_SCHEMA:
            return {}
        out: dict[str, _Cached] = {}
        files: Any = cast(dict[str, Any], data).get("files")
        for key, value in cast(dict[str, Any], files if isinstance(files, dict) else {}).items():
            try:
                out[key] = _Cached(**cast(dict[str, Any], value))
            except TypeError:
                continue
        return out

    def _save(self) -> None:
        if not self.pending:
            # Nothing moved: a scan of an unchanged folder writes no cache.
            return
        for key, entry in self.pending.items():
            if entry is None:
                self.cache.pop(key, None)
            else:
                self.cache[key] = entry
        self.pending = {}
        data = {"schema": CACHE_SCHEMA, "files": {k: vars(v) for k, v in sorted(self.cache.items())}}
        tmp = self.cache_path.with_name(f"{self.cache_path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self.cache_path)
        finally:
            tmp.unlink(missing_ok=True)

    # -- the run --------------------------------------------------------------------------------------------------

    def run(self, accept_gone: bool) -> ScanReport:
        report = self.report
        skipped = report.skipped
        repo = folders.repo_root(self.folder)
        unlisted: list[str] = []
        root = self.vault.root.resolve()
        listed = folders.list_folder(self.folder, repo, root, skipped, exclude=self.config.exclude, unlisted=unlisted)
        report.files = len(listed)
        present: set[str] = set()
        todo: list[_Candidate] = []
        for found in listed:
            candidate = self._candidate(found, present)
            if candidate is not None:
                todo.append(candidate)
        known = self.holdings.files()
        self._unlisted(unlisted, known, present)
        leaving = sorted(set(known) - present)
        report.known = len(known)
        if len(leaving) >= GONE_GUARD and len(leaving) * 2 > len(known) and not accept_gone:
            report.guarded = len(leaving)
            return report
        for rid in leaving:
            found_identity = records.identity(known[rid])
            if found_identity is not None:
                self.leaving.setdefault(found_identity, []).append(rid)
        try:
            files, since = 0, time.monotonic()
            for candidate in todo:
                self._one(candidate)
                files += 1
                if self.batch_text >= BATCH_TEXT or files >= BATCH_FILES or time.monotonic() - since >= BATCH_SECONDS:
                    self._flush()
                    files, since = 0, time.monotonic()
        finally:
            if self.extractor is not None:
                self.extractor.close()
        for rid in leaving:
            self._gone(known[rid], self.moves.get(rid))
            self.pending[rid.split(":", 1)[1]] = None
        for key in set(self.cache) - {rid.split(":", 1)[1] for rid in present}:
            self.pending.setdefault(key, None)
        self._flush()
        return report

    def _unlisted(self, unlisted: list[str], known: dict[str, RawRecord], present: set[str]) -> None:
        """A folder that couldn't be listed now is a passing reason (DR-0487): the files the vault holds under it stay
        present, never gone, and the report names it by its path inside the folder, unless a never-store keyword is
        in that path. One `exclude` names is left out, and its files go as excluded files do."""
        exclude = self.config.exclude
        kept = [u for u in unlisted if not (folders.excluded(u, exclude) or folders.excluded(f"{u}/", exclude))]
        for folder in kept:
            prefix = "" if folder == "." else f"{folder}/"
            present.update(rid for rid, record in known.items() if (record.title or "").startswith(prefix))
        if kept:
            self.report.outcomes["folders not listed"] += len(kept)
        self.report.unlisted = [u for u in kept if not neverstore.matches_text(u, self.lists)]

    def _candidate(self, found: folders.Found, present: set[str]) -> _Candidate | None:
        """A listed file the scan looks at, or None with the reason counted. A path holding a never-store keyword is
        checked first, before anything reads, logs or reports it; it stays present, so it never turns gone."""
        skipped = self.report.skipped
        rid = records.file_rid(self.source, found.rel)
        if neverstore.matches_text(found.rel, self.lists):
            skipped["never-store"] += 1
            present.add(rid)
            return None
        if folders.excluded(found.rel, self.config.exclude):
            skipped["excluded"] += 1
            return None
        if found.path.name.startswith(OFFICE_LOCK):
            skipped["lock files"] += 1
            return None
        fmt = format_of(found.path.name)
        if fmt is None:
            dot = found.path.name.rfind(".")
            skipped[f"other {found.path.name[dot:].lower()[:12]}" if dot > 0 else "other (no extension)"] += 1
            return None
        if rid in present:
            skipped["same id"] += 1
            return None
        present.add(rid)
        if folders.placeholder(found.info):
            self.report.outcomes["placeholders"] += 1
            return None
        if owning_id(rid, self.source, self.held) is not None:
            self.report.outcomes["on hold"] += 1
            return None
        return _Candidate(found.rel, found.path, found.info, fmt, rid)

    # -- one file ---------------------------------------------------------------------------------------------------

    def _one(self, c: _Candidate) -> None:
        outcomes = self.report.outcomes
        entry = self.cache.get(c.id)
        newest = self.holdings.newest.get(c.rid)
        if entry is not None and entry.stat() == c.stat and self._settled(entry, newest):
            outcomes["kept out" if entry.kept_out is not None else "same"] += 1
            return
        plain = c.fmt in PLAIN
        try:
            sha, head, data = _read(c.path, keep=plain, cap=self.limits.file_bytes)
        except OSError:
            outcomes["unreadable"] += 1
            return
        if c.info.st_size <= LFS_MAX and head.startswith(LFS_POINTER):
            outcomes["lfs pointers"] += 1
            return
        if entry is not None and entry.sha256 == sha and self._settled(entry, newest):
            self.pending[c.id] = replace(entry, size=c.info.st_size, mtime_ns=c.info.st_mtime_ns)
            outcomes["touched"] += 1
            return
        current = newest is not None and not records.is_gone(newest)
        if plain:
            outcome = self._plain(data, c.info.st_size, c.fmt)
            if isinstance(outcome, Done):
                identity = _text_identity(self.redactor, outcome.extracted)
                if current and newest is not None and records.identity(newest) == identity:
                    self.pending[c.id] = _Cached(c.info.st_size, c.info.st_mtime_ns, sha, identity)
                    outcomes["same"] += 1
                    return
        else:
            if current and newest is not None and records.identity(newest) == sha:
                self.pending[c.id] = _Cached(c.info.st_size, c.info.st_mtime_ns, sha, sha)
                outcomes["same"] += 1
                return
            outcome = self._extract(c.path)
        self._outcome(c, entry, newest, sha, outcome)

    def _settled(self, entry: _Cached, newest: RawRecord | None) -> bool:
        """Whether a file whose stat or bytes the cache knows needs nothing more: kept out while the lists hold, or
        the vault's newest version still carries what the cache says it does."""
        if entry.kept_out is not None:
            return entry.kept_out == self.lists_hash
        if entry.failed is not None or newest is None or records.is_gone(newest):
            return False
        if entry.no_identity:
            return records.identity(newest) is None and "problem" in newest.native
        return entry.identity is not None and records.identity(newest) == entry.identity

    def _plain(self, data: bytes | None, size: int, fmt: Format) -> Outcome:
        """md and txt, decoded in this process with no parser (DR-0485)."""
        if data is None:
            # Past the size cap: `_read` kept none of it.
            return NotRead("size", f"{mib(size)}, over the cap of {mib(self.limits.file_bytes)}", None, size)
        try:
            return Done("", len(data), _plain_text(data, fmt, self.limits))
        except NotReadError as e:
            return NotRead(e.reason, e.detail, None, len(data))

    def _extract(self, path: Path) -> Outcome:
        if self.extractor is None:
            self.extractor = self.make_extractor()
        return self.extractor.extract(path)

    def _outcome(
        self, c: _Candidate, entry: _Cached | None, newest: RawRecord | None, sha: str, outcome: Outcome
    ) -> None:
        outcomes = self.report.outcomes
        stat = c.stat
        if isinstance(outcome, Failed):
            if outcome.why == "unreadable":
                outcomes["unreadable"] += 1
                return
            if entry is None or entry.failed != sha:
                # Tried once more at the next scan; the file keeps its last version meanwhile.
                self.pending[c.id] = _Cached(*stat, sha, failed=sha)
                outcomes["retry"] += 1
                return
            why = (
                "it took longer than the time limit twice"
                if outcome.why == "stopped"
                else "it crashed the reader twice"
            )
            outcome = NotRead(outcome.why, why, sha, c.info.st_size)
        if isinstance(outcome, NotRead):
            built = self._not_read(c, newest, sha, outcome)
            cached = _Cached(*stat, sha, records.identity(built[0]), records.identity(built[0]) is None)
        else:
            built = self._read_whole(c, newest, sha, outcome)
            cached = _Cached(*stat, sha, records.identity(built[0]))
        if any(neverstore.match(r, self.lists) is not None for r in built):
            # Kept out whole (DR-0480): counted, never named; what the vault held of it goes.
            outcomes["kept out"] += 1
            if newest is not None and not records.is_gone(newest):
                self._gone(newest)
            self.pending[c.id] = _Cached(*stat, sha, kept_out=self.lists_hash)
            return
        file_record = self._moved(built[0], newest)
        if newest is None:
            outcomes["moved" if "moved_from" in file_record.native else "new"] += 1
        else:
            outcomes["back" if records.is_gone(newest) else "changed"] += 1
        self._stage(self._fresh(file_record, newest))
        names = {r.rid for r in built[1:]}
        for part in built[1:]:
            self._stage_part(part)
        for old in self.holdings.parts_of(c.rid):
            if old.rid not in names:
                self._stage(records.gone_version(old, at=self.at, day=self.day))
        try:
            after = c.path.stat()
        except OSError:
            after = None
        # Changed while it was read: its records match the bytes the reader hashed, and the next scan looks again.
        self.pending[c.id] = cached if after is not None and (after.st_size, after.st_mtime_ns) == stat else None

    def _ts(self, c: _Candidate, newest: RawRecord | None) -> datetime:
        """The file's modification time; the scan's time for a record coming back after a gone version (DR-0487)."""
        if newest is not None and records.is_gone(newest):
            return self.at
        return datetime.fromtimestamp(c.info.st_mtime_ns / 1e9, UTC)

    def _fresh(self, record: RawRecord, newest: RawRecord | None) -> RawRecord:
        """A record whose version raw already holds, though it isn't the newest (the folder went back to an earlier
        state, or its bytes changed and its text didn't), takes the scan's time, as a return does (DR-0487):
        otherwise the gate would skip it as a version raw holds, and the newest would stay as it is."""
        if newest is None or record.ts == self.at:
            # A record the vault never held can't be one of its versions.
            return record
        if self.vault.rawlog.contains(self.redactor.record(record)[0]):
            return record.model_copy(update={"ts": self.at})
        return record

    def _moved(self, record: RawRecord, newest: RawRecord | None) -> RawRecord:
        """A new path whose identity left another in this scan: it names where it came from, and the gone version of
        the other names it (DR-0487)."""
        identity = records.identity(record)
        if newest is not None or identity is None or not self.leaving.get(identity):
            return record
        old = self.leaving[identity].pop(0)
        self.moves[old] = record.rid
        return record.model_copy(update={"native": {**record.native, "moved_from": old}})

    def _not_read(self, c: _Candidate, newest: RawRecord | None, sha: str, outcome: NotRead) -> list[RawRecord]:
        native: dict[str, Any] = {"format": c.fmt, "problem": outcome.reason}
        if c.fmt not in PLAIN:
            native |= {records.BYTES_HASH: outcome.sha256 or sha, "size": outcome.size or c.info.st_size}
        self.report.not_read.append(c.rid)
        record = records.file_record(
            self.source, c.rel, ts=self._ts(c, newest), fetched=self.at, text=outcome.line, engine=None, native=native
        )
        return [record]

    def _read_whole(self, c: _Candidate, newest: RawRecord | None, sha: str, outcome: Done) -> list[RawRecord]:
        extracted = outcome.extracted
        native: dict[str, Any] = {"format": c.fmt}
        if c.fmt in PLAIN:
            identity = _text_identity(self.redactor, extracted)
            native[records.TEXT_HASH] = identity
        else:
            identity = outcome.sha256 or sha
            native |= {records.BYTES_HASH: identity, "size": outcome.size}
            native["parts"] = len(extracted.parts)
        people = {"creator": extracted.creator, "last_modified_by": extracted.last_modified_by}
        native |= {k: v for k, v in people.items() if v}
        if extracted.authors:
            native["authors"] = list(extracted.authors)
        if extracted.note:
            native["note"] = extracted.note
        text = extracted.text if not extracted.parts else ""
        file_record = records.file_record(
            self.source, c.rel, ts=self._ts(c, newest), fetched=self.at, text=text, engine=extracted.engine,
            native=native,
        )  # fmt: skip
        parts: list[RawRecord] = []
        for part in extracted.parts:
            rid = records.part_rid(file_record.rid, part.kind, part.n)
            ts = self._ts(c, self.holdings.newest.get(rid))
            parts.append(records.part_record(file_record, part, ts=ts, fetched=self.at, engine=extracted.engine))
        return [file_record, *parts]

    def _stage_part(self, part: RawRecord) -> None:
        """A part goes in only when it is new, back, or its title or text, redacted by today's rules, differs from
        its newest version's (DR-0486)."""
        newest = self.holdings.newest.get(part.rid)
        if newest is not None and not records.is_gone(newest):
            title = self.redactor.text(part.title) if part.title is not None else None
            if title == newest.title and self.redactor.text(part.text) == newest.text:
                return
        self._stage(self._fresh(part, newest))

    def _gone(self, newest: RawRecord, moved_to: str | None = None) -> None:
        """A file's gone version, and its parts', each over a version that isn't gone already (DR-0487)."""
        self.report.gone += 1
        self._stage(records.gone_version(newest, at=self.at, day=self.day, moved_to=moved_to))
        for part in self.holdings.parts_of(newest.rid):
            self._stage(records.gone_version(part, at=self.at, day=self.day))

    def _stage(self, record: RawRecord) -> None:
        self.batch.append(record)
        self.batch_text += len(record.text.encode("utf-8"))

    def _flush(self) -> None:
        if self.batch:
            got = self.gate.stage(self.batch)
            staged = self.report.staged
            staged.ready += got.ready
            staged.pending += got.pending
            staged.skipped += got.skipped
            staged.on_hold += got.on_hold
            staged.dropped += got.dropped
            staged.redactions += got.redactions
            for record in self.batch:
                self.holdings.newest[record.rid] = record
        self.batch, self.batch_text = [], 0
        self._save()


def _read(path: Path, *, keep: bool, cap: int) -> tuple[str, bytes, bytes | None]:
    """A file's bytes' sha256, its first bytes, and, when `keep` and it is within the cap, the bytes themselves."""
    digest = hashlib.sha256()
    head = b""
    kept: list[bytes] | None = [] if keep else None
    total = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            if not head:
                head = chunk[:64]
            digest.update(chunk)
            total += len(chunk)
            if kept is not None:
                if total > cap:
                    kept = None
                else:
                    kept.append(chunk)
    return digest.hexdigest(), head, b"".join(kept) if kept is not None else None


def _plain_text(data: bytes, fmt: Format, limits: Limits) -> Extracted:
    text, note = texts.plain(data)
    size = len(text.encode("utf-8"))
    if size > limits.file_text:
        raise NotReadError("text", f"{mib(size)} of text, over the cap of {mib(limits.file_text)}")
    return Extracted(fmt, texts.TEXT_ENGINE, text=text, note=note)


def _text_identity(redactor: Redactor, extracted: Extracted) -> str:
    """An md or txt file's identity: the sha256 of its text as redacted by today's rules, never of the text before
    (DR-0483)."""
    return hashlib.sha256(redactor.text(extracted.text).encode("utf-8")).hexdigest()
