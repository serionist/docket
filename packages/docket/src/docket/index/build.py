"""Building the index from the vault, and keeping it up to date.

`rebuild` is the contract: it reads the working tree (`raw/`, `processed/`, `entities/`; never `staging/`, never git
objects) into a new file, sorted, and replaces the old one, so two rebuilds of one vault give the same database.
A decision is the one exception: its status is read from git, and a confirmed or rejected one is indexed as its
last commit holds it, the file noted when it says otherwise (DR-0603).
`update` runs the same indexer on the live file for what changed since: new run files, docs whose bytes changed, the
entity files and the ledger's op files (DR-0616); anything it can't follow (a run file changed or removed, another
schema or set of languages) makes it rebuild. Raw lines are read leniently: a line that doesn't load is a problem and
the rest is indexed (DR-0115).
Both leave out what the forgotten list takes, the records and the doc lines its docs' rule drops, and the index is built
under the list's digest: one built under another is rebuilt by `update` and refused by `open_index` (DR-0593).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from collections.abc import Collection, Generator, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, cast
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout
from pydantic import BaseModel, ValidationError

from docket.errors import DocketError
from docket.index import flags, matching, vectors
from docket.index import ledger as ledger_index
from docket.index.chunks import (
    COLUMNS,
    Chunk,
    Message,
    RecordInfo,
    doc_chunks,
    doc_title,
    group_of,
    iso,
    record_chunks,
    record_owner,
    thread_chunks,
    thread_owner,
)
from docket.index.languages import Languages, resolve
from docket.index.schema import LEDGER_TABLES, check_sqlite, create, stale
from docket.index.text import Analyzer
from docket.ledger.fold import Ledger, paused
from docket.models import config as models_config
from docket.models.provider import Off
from docket.plaintext import fold, words
from docket.vault import decision_form, forgetdocs, forgotten, legacy
from docket.vault.anchors import trailing_anchors
from docket.vault.docstore import DocStore
from docket.vault.entities import entity_files, load_entities
from docket.vault.forgotten import Forgotten
from docket.vault.processed import ProcessedDoc, doc_path, parse_doc
from docket.vault.rawlog import RunFile, parse_run_file
from docket.vault.records import Attachment, RawRecord
from docket.vault.segments import segments
from docket.vault.staging import rid_hash
from docket.vault.vault import Vault

INDEX_DIR = "index"
DB_NAME = "docket.sqlite"
LOCK_NAME = "docket.lock"
# Docs at earlier revs, by vault root, doc and rev, for the docs' rule to follow refs to: a rev's text never changes.
EARLIER_KEPT = 4096
_EARLIER: dict[tuple[Path, str, int], ProcessedDoc] = {}
REPLACE_SECONDS = 5.0
REPLACE_WAIT = 0.1
BUSY_SECONDS = 5.0
JOURNALS = ("-journal", "-wal", "-shm")
# How long a ledger write or a ledger read waits for the index before it folds the op files in memory: a
# rebuild can hold the index for minutes, and a write never waits on a cache (DR-0619).
LEDGER_WAIT = 1.0


class BuildReport(BaseModel):
    """What a rebuild or an update did."""

    rebuilt: bool
    reason: str | None = None
    runs: int = 0
    docs: int = 0
    entities: bool = False
    # The ledger's op lines read: every one on a rebuild, those the op files gained on an update (DR-0616).
    ops: int = 0
    counts: dict[str, int] = {}
    problems: list[str] = []
    seconds: float = 0.0


class VectorCoverage(BaseModel):
    model: str
    have: int
    total: int


class IndexStatus(BaseModel):
    exists: bool
    path: str
    stale: str | None = None
    languages: list[str] = []
    columns: list[str] = []
    counts: dict[str, int] = {}
    new_runs: int = 0
    changed_runs: int = 0
    changed_docs: int = 0
    entities_changed: bool = False
    problems: list[str] = []
    vectors: list[VectorCoverage] = []
    # Lines citing a doc line that changed, and correction lines not applied yet, as the index last saw them (DR-0542).
    flagged: int = 0
    unapplied: int = 0


class RecordLocation(BaseModel):
    rid: str
    path: str
    line: int
    versions: int


def db_path(vault: Vault) -> Path:
    return vault.root / INDEX_DIR / DB_NAME


def languages_of(vault: Vault) -> Languages:
    return resolve(vault.config.owner.languages)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _stamp(path: Path) -> tuple[int, int]:
    """A run file's size and modification time: a history rewrite can keep the size, never the time."""
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def _known_fields(data: dict[Any, Any]) -> tuple[dict[str, object], list[str]]:
    """A raw line's fields without those this Docket doesn't know, at the top and in each attachment (DR-0020 lets a
    newer Docket add optional ones), and the names left out."""
    fields: dict[str, object] = {str(k): v for k, v in data.items()}
    unknown = sorted(set(fields) - set(RawRecord.model_fields))
    fields = {k: v for k, v in fields.items() if k not in unknown}
    attachments = fields.get("attachments")
    if isinstance(attachments, list):
        known = set(Attachment.model_fields)
        kept: list[object] = []
        for item in cast(list[Any], attachments):
            if not isinstance(item, dict):
                kept.append(item)
                continue
            entry = {str(k): v for k, v in cast(dict[Any, Any], item).items()}
            unknown += [f"attachments.{k}" for k in set(entry) - known]
            kept.append({k: v for k, v in entry.items() if k in known})
        fields["attachments"] = kept
    return fields, sorted(set(unknown))


def foreign_prefix(source: str) -> str:
    """The start of the problem reported for a record of this source found in another source's folder: read looks for
    it, and loads both sources whole (DR-0446)."""
    return f"a record of {source} in "


class RunLine(NamedTuple):
    """A record as a run file holds it: its line, and the byte its line starts at (DR-0445)."""

    line: int
    offset: int
    record: RawRecord


def read_run(root: Path, run: RunFile) -> tuple[list[RunLine], list[tuple[str, str]]]:
    """A run file's records with their lines and offsets, read leniently: a line that doesn't load is a problem, and
    a field this Docket doesn't know is left out and reported. The file is read as bytes and split at each newline
    byte, which UTF-8 never puts inside a character, so the lines are those text mode gives. A line that isn't UTF-8
    (indexed with its bad bytes replaced) and a record in another source's folder are indexed and reported, so read
    loads their sources whole (DR-0446)."""
    rel = _rel(root, run.path)
    records: list[RunLine] = []
    problems: list[tuple[str, str]] = []
    offset = 0
    with run.path.open("rb") as f:
        for n, raw in enumerate(f, start=1):
            start, offset = offset, offset + len(raw)
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                line = raw.decode("utf-8", errors="replace")
                problems.append((f"{rel}:{n}", "not UTF-8: indexed with its bad bytes replaced"))
            if not line.strip():
                continue
            place = f"{rel}:{n}"
            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                problems.append((place, f"not JSON: {e.msg}"))
                continue
            if not isinstance(data, dict):
                problems.append((place, "not a raw record: a JSON object per line"))
                continue
            fields, unknown = _known_fields(cast(dict[Any, Any], data))
            if unknown:
                problems.append((place, f"fields this Docket doesn't know, left out: {', '.join(unknown)}"))
            try:
                record = RawRecord.model_validate(fields)
            except ValidationError as e:
                err = e.errors()[0]
                where = ".".join(str(p) for p in err["loc"]) or "record"
                problems.append((place, f"not a raw record: {where}: {err['msg']}"))
                continue
            if record.source != run.source:
                problems.append((place, f"{foreign_prefix(record.source)}{run.source}'s folder"))
            records.append(RunLine(n, start, record))
    return records, problems


class _Left:
    """Raw as the forgotten list leaves it, for the docs' rule (DR-0580): the rows this build wrote, which hold nothing
    the list takes, and the rids whose lines it skipped."""

    def __init__(self, conn: sqlite3.Connection, listed: Forgotten, skipped: set[str]) -> None:
        self.conn = conn
        self.listed = listed
        self.skipped = skipped

    def gone(self, rid: str) -> bool:
        if self.listed.whole(rid):
            return True
        if self.conn.execute("SELECT 1 FROM records WHERE rid = ?", (rid,)).fetchone() is not None:
            return False
        return rid in self.skipped or self.listed.versioned(rid)

    def timed(self, rid: str) -> bool:
        return self.conn.execute("SELECT 1 FROM segments WHERE rid = ? LIMIT 1", (rid,)).fetchone() is not None

    def lands(self, rid: str, start: int, last: int) -> bool:
        """Whether the time lands on a transcript under the rid, by the whole seconds it spans (`time_range`)."""
        rows = self.conn.execute(
            "SELECT min(s.start_ms), max(s.end_ms) FROM records r JOIN segments s ON s.rid = r.rid WHERE r.kind = "
            "'transcript' AND r.source = ? AND (r.thread = ? OR r.container = ?) AND r.rid != ? GROUP BY r.rid",
            (rid.partition(":")[0], rid, rid, rid),
        ).fetchall()
        return any(lo // 1000 <= start and last <= -(-hi // 1000) for lo, hi in rows)

    def media(self, rid: str) -> bool:
        return rid_hash(rid) in self.listed.media


class Indexer:
    """Writes the index's rows for records, threads, docs and entities, on a connection inside a transaction. What the
    forgotten list takes it leaves out: the records, and the doc lines the docs' rule drops (DR-0593)."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        vault: Vault,
        analyzer: Analyzer,
        *,
        collect: bool,
        vectors: vectors.Keeper | None = None,
        listed: Forgotten | None = None,
    ) -> None:
        self.conn = conn
        self.vectors = vectors
        self.root = vault.root
        self.vault = vault
        self.analyzer = analyzer
        self.tz = ZoneInfo(vault.config.owner.time_zone)
        self.collect = collect
        self.new_words: dict[str, set[str]] = defaultdict(set)
        self.listed = listed if listed is not None else forgotten.load(vault.root, vault.home)
        # What the list took from raw, kept in `skipped` so an update judges as the rebuild before it did: each rid
        # whose lines it skipped, and the folder files whose parts it takes.
        self.skipped: set[str] = set()
        self._parts: set[str] = set()
        if self.listed.acts:
            for rid, parts in conn.execute("SELECT rid, parts FROM skipped"):
                self.skipped.add(rid)
                if parts:
                    self._parts.add(rid)
        # What the docs' rule makes of the docs a sync indexes, and each decision's text at HEAD, read once.
        self._fates: dict[str, forgetdocs.Fate] = {}
        self._head: dict[str, str | None] = {}
        # The column names are the index's own, never from a user.
        marks = ", ".join("?" * len(analyzer.columns))
        cols = ", ".join(analyzer.columns)
        self.insert_sql = f"INSERT INTO chunks ({COLUMNS}) VALUES ({', '.join('?' * len(COLUMNS.split(', ')))})"  # noqa: S608
        self.fts_sql = f"INSERT INTO chunks_fts (rowid, {cols}) VALUES (?, {marks})"  # noqa: S608
        self.delete_sql = f"INSERT INTO chunks_fts (chunks_fts, rowid, {cols}) VALUES ('delete', ?, {marks})"  # noqa: S608

    # -- chunks -------------------------------------------------------------------------------------------------

    def add_chunks(self, chunks: list[Chunk]) -> None:
        for chunk in chunks:
            cur = self.conn.execute(self.insert_sql, chunk.row())
            values = self.analyzer.index_values(chunk.text)
            self.conn.execute(self.fts_sql, (cur.lastrowid, *values))
            if self.vectors is not None:
                self.vectors.add(cast(int, cur.lastrowid), chunk.title, chunk.text, chunk.ts, chunk.source)
            if self.collect:
                for col, value in zip(self.analyzer.columns, values, strict=True):
                    self.new_words[col].update(fold(w) for w in words(value))

    def drop_owner(self, owner: str) -> None:
        if self.vectors is not None:
            self.vectors.drop(owner)
        rows = self.conn.execute("SELECT id, text FROM chunks WHERE owner = ?", (owner,)).fetchall()
        for chunk_id, text in rows:
            self.conn.execute(self.delete_sql, (chunk_id, *self.analyzer.index_values(text)))
            self.conn.execute("DELETE FROM mentions WHERE chunk = ?", (chunk_id,))
        self.conn.execute("DELETE FROM chunks WHERE owner = ?", (owner,))

    # -- records ------------------------------------------------------------------------------------------------

    def _order(self, rel: str) -> tuple[str, str, str, int, str]:
        run = parse_run_file(self.root / "raw", self.root / rel)
        return run.order if run is not None else ("", "", "", 0, "")

    def add_runs(self, runs: list[RunFile]) -> tuple[set[str], int]:
        """Index the records of new run files; a record's latest version is the last in run order. Returns the rids
        whose rows changed and how many run files were read."""
        touched: set[str] = set()
        threads: set[str] = set()
        by_source: dict[str, list[RunFile]] = defaultdict(list)
        for run in runs:
            by_source[run.source].append(run)
        # A folder file's parts follow it from the run files read before these.
        reading = self.listed.reading(self._parts)
        for source in sorted(by_source):
            latest: dict[str, tuple[tuple[tuple[str, str, str, int, str], int], str, RawRecord]] = {}
            seen: set[str] = set()
            for run in sorted(by_source[source], key=lambda r: r.order):
                rel = _rel(self.root, run.path)
                stamp = _stamp(run.path)
                records, problems = read_run(self.root, run)
                self.conn.execute("INSERT INTO run_files (path, size, mtime_ns) VALUES (?, ?, ?)", (rel, *stamp))
                self.conn.executemany(
                    "INSERT OR IGNORE INTO problems (kind, place, message) VALUES ('raw', ?, ?)", problems
                )
                for line, offset, record in records:
                    if reading.skips(record):
                        self.skipped.add(record.rid)
                        continue
                    seen.add(record.rid)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO record_versions (rid, v, path, line, offset) VALUES (?, ?, ?, ?, ?)",
                        (record.rid, record.v, rel, line, offset),
                    )
                    latest[record.rid] = ((run.order, line), rel, record)
            changed: list[tuple[str, int, RawRecord]] = []
            for rid in sorted(latest):
                (order, line), rel, record = latest[rid]
                stored = self.conn.execute("SELECT path, line FROM records WHERE rid = ?", (rid,)).fetchone()
                if stored is not None and (self._order(stored[0]), stored[1]) > (order, line):
                    continue
                changed.append((rel, line, record))
            for rel, line, record in sorted(changed, key=lambda c: (c[2].ts, c[2].rid)):
                threads |= self.index_record(record, rel, line)
                touched.add(record.rid)
            self.conn.executemany(
                "UPDATE records SET versions = (SELECT count(DISTINCT v) FROM record_versions WHERE rid = ?) "
                "WHERE rid = ?",
                [(rid, rid) for rid in sorted(seen)],
            )
        if self.listed.acts:
            self._parts = set(reading.files)
            self.conn.execute("DELETE FROM skipped")
            self.conn.executemany(
                "INSERT INTO skipped (rid, parts) VALUES (?, ?)",
                [(rid, int(rid in self._parts)) for rid in sorted(self.skipped | self._parts)],
            )
        self.rewindow(threads)
        return touched, len(runs)

    def index_record(self, record: RawRecord, rel: str, line: int) -> set[str]:
        """A record's row, segments and chunks, replacing what an earlier version left. Returns the message threads
        whose windows must be cut again."""
        threads: set[str] = set()
        old = self.conn.execute("SELECT thread, kind FROM records WHERE rid = ?", (record.rid,)).fetchone()
        # An edited message is cut again in the windows it was in. A parent has no thread of its own: its rid is the
        # thread its replies name.
        if old is not None and old[1] == "message":
            threads.add(old[0] or record.rid)
        self.drop_owner(record_owner(record.rid))
        self.conn.execute("DELETE FROM segments WHERE rid = ?", (record.rid,))
        self.conn.execute(
            "INSERT OR REPLACE INTO records (rid, v, versions, source, kind, container, thread, ts, author, title, "
            "lang, engine, fidelity, grp, path, line) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.rid, record.v, record.source, record.kind, record.container, record.thread, iso(record.ts),
                record.author, record.title, record.lang, record.engine, record.fidelity, group_of(record), rel, line,
            ),
        )  # fmt: skip
        # Segments are a field of the record, checked when its line is read: a malformed one is that line's problem.
        segs = segments(record)
        self.conn.executemany(
            "INSERT INTO segments (rid, ordinal, engine, start_ms, end_ms, speaker, text) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(record.rid, n, record.engine, s.start_ms, s.end_ms, s.speaker, s.text) for n, s in enumerate(segs)],
        )
        self.add_chunks(record_chunks(record, segs))
        if record.kind == "message" and record.thread:
            threads.add(record.thread)
        return threads

    def rewindow(self, threads: set[str]) -> None:
        for thread in sorted(threads):
            self.drop_owner(thread_owner(thread))
            rows = self.conn.execute(
                "SELECT r.rid, c.ts, c.text, r.author, r.source FROM records r JOIN chunks c "
                "ON c.owner = 'r ' || r.rid AND c.ordinal = 0 WHERE r.kind = 'message' AND (r.thread = ? OR r.rid = ?) "
                "ORDER BY c.ts, r.rid",
                (thread, thread),
            ).fetchall()
            if rows:
                messages = [Message(r[0], r[1], r[2], r[3]) for r in rows]
                self.add_chunks(thread_chunks(thread, rows[0][4], messages))

    # -- docs ---------------------------------------------------------------------------------------------------

    def _info(self, rid: str) -> RecordInfo | None:
        row = self.conn.execute("SELECT ts, grp, source FROM records WHERE rid = ?", (rid,)).fetchone()
        return None if row is None else RecordInfo(row[0], row[1], row[2])

    def drop_doc_file(self, rel: str) -> None:
        row = self.conn.execute("SELECT doc FROM doc_files WHERE path = ?", (rel,)).fetchone()
        if row is not None and row[0] is not None:
            doc = row[0]
            self.drop_owner(f"d {doc}")
            for table in ("doc_covers", "doc_anchors", "old_paths", "derived_from", "doc_lines", "decision_lines"):
                self.conn.execute(f"DELETE FROM {table} WHERE doc = ?", (doc,))  # noqa: S608 - fixed table names
            self.conn.execute("DELETE FROM docs WHERE id = ?", (doc,))
        self.conn.execute("DELETE FROM doc_files WHERE path = ?", (rel,))
        self.conn.execute("DELETE FROM problems WHERE kind = 'doc' AND place = ?", (rel,))

    def index_doc_file(self, path: Path, data: bytes) -> None:
        rel = _rel(self.root, path)
        self.drop_doc_file(rel)
        doc_id: str | None = None
        try:
            doc = parse_doc(data.decode("utf-8"), lenient=True)
            if doc_path(doc.meta.id).as_posix() != rel:
                raise DocketError(f"its id {doc.meta.id} belongs at {doc_path(doc.meta.id)}")
        except (DocketError, UnicodeDecodeError) as e:
            why = "isn't UTF-8" if isinstance(e, UnicodeDecodeError) else str(e)
            self.conn.execute("INSERT INTO problems (kind, place, message) VALUES ('doc', ?, ?)", (rel, why))
        else:
            if doc.meta.type == "decision":
                doc = self._decision(rel, doc, data)
            # The doc as a forget leaves it, so one the apply hasn't rewritten yet reads as it will (DR-0593); a doc it
            # removes keeps its file's row, so the docs still read as current.
            fate = self._fates.get(doc.meta.id)
            left = doc if fate is None else fate.doc
            if left is not None:
                doc_id = left.meta.id
                self._add_doc(rel, left)
        self.conn.execute("INSERT INTO doc_files (path, sha256, doc) VALUES (?, ?, ?)", (rel, _sha(data), doc_id))

    def _add_doc(self, rel: str, doc: ProcessedDoc) -> None:
        meta = doc.meta
        chunks, anchors = doc_chunks(doc, self._info, self.tz)
        self.conn.execute(
            "INSERT INTO docs (id, type, rev, path, title, by, supersedes, superseded_by, entities, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                meta.id, meta.type, meta.rev, rel, doc_title(doc), meta.by, json.dumps(meta.supersedes),
                json.dumps(meta.entities), meta.status,
            ),
        )  # fmt: skip
        self.conn.executemany(
            "INSERT INTO doc_covers (doc, rid) VALUES (?, ?)", [(meta.id, rid) for rid in sorted(meta.covers)]
        )
        self.conn.executemany(
            "INSERT INTO old_paths (doc, path, folded, name) VALUES (?, ?, ?, ?)",
            [legacy.index_row(e) for e in legacy.old_paths([doc])],
        )
        self.conn.executemany(
            "INSERT INTO doc_anchors (doc, line, ordinal, rid, kind, start, end) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(meta.id, a.line, a.ordinal, a.rid, a.kind, a.start, a.end) for a in anchors],
        )
        self.conn.executemany(
            "INSERT INTO derived_from (doc, line, ordinal, src, rev, src_line, hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            flags.ref_rows(doc),
        )
        if meta.type == "brief":
            self.conn.executemany(
                "INSERT INTO decision_lines (doc, line, cites) VALUES (?, ?, ?)",
                [
                    (meta.id, n, json.dumps(sorted({r.doc for r in trailing_anchors(line).refs})))
                    for n, line in decision_form.decisions_lines(doc)
                ],
            )
        self.conn.executemany(
            "INSERT OR IGNORE INTO doc_lines (doc, hash, line) VALUES (?, ?, ?)",
            [(meta.id, found, n) for n, found in sorted(doc.citable().items())],
        )
        self.add_chunks(chunks)

    def _committed(self, rel: str) -> str | None:
        """A decision's text at HEAD, read once per build."""
        if rel not in self._head:
            proc = self.vault.git.run("cat-file", "blob", f"HEAD:./{rel}", check=False)
            self._head[rel] = proc.stdout if proc.returncode == 0 else None
        return self._head[rel]

    def _decision(self, rel: str, doc: ProcessedDoc, data: bytes) -> ProcessedDoc:
        """A decision as every read takes it, from its last commit (DR-0603); a final one whose file says otherwise is
        noted as edited on disk, not taken in."""
        text = self._committed(rel)
        found = decision_form.as_committed(doc, decision_form.committed_doc(text))
        if found.meta.status in decision_form.FINAL and decision_form.edited(data.decode("utf-8"), text):
            row = (rel, decision_form.EDITED)
            self.conn.execute("INSERT INTO problems (kind, place, message) VALUES ('doc', ?, ?)", row)
        return found

    def _judge(self, due: list[tuple[Path, bytes]]) -> None:
        """What the docs' rule makes of the docs about to be indexed, as every read takes them, once the records are in
        (DR-0580). The rule reads what their refs lead to and nothing else, so an update that indexes one doc reads
        that doc and what it cites, not every doc."""
        docs = self._as_read({_rel(self.root, path): data for path, data in due})
        raw = _Left(self.conn, self.listed, self.skipped)
        self._fates = forgetdocs.fates(docs.values(), raw, self.listed, current=self._current, older=self._earlier)

    def _as_read(self, files: dict[str, bytes]) -> dict[str, ProcessedDoc]:
        """Doc files by their ids, as every read takes them, decisions from their last commit, read together; a file
        that isn't a doc where its id says is left out, as the index leaves it."""
        found: dict[str, tuple[str, ProcessedDoc]] = {}
        for rel, data in files.items():
            try:
                doc = parse_doc(data.decode("utf-8"), lenient=True)
            except (DocketError, UnicodeDecodeError):
                continue
            if doc_path(doc.meta.id).as_posix() == rel:
                found[doc.meta.id] = (rel, doc)
        decisions = [rel for rel, doc in found.values() if doc.meta.type == "decision" and rel not in self._head]
        if decisions:
            self._head.update(DocStore(self.vault).committed_texts(decisions))
        return {
            doc_id: (
                decision_form.as_committed(doc, decision_form.committed_doc(self._committed(rel)))
                if doc.meta.type == "decision"
                else doc
            )
            for doc_id, (rel, doc) in found.items()
        }

    def _current(self, doc_ids: Collection[str]) -> dict[str, ProcessedDoc]:
        """Docs a ref names, by id, as they read now."""
        files: dict[str, bytes] = {}
        for doc_id in doc_ids:
            rel = doc_path(doc_id).as_posix()
            with contextlib.suppress(OSError):
                files[rel] = (self.root / rel).read_bytes()
        return self._as_read(files)

    def _earlier(self, wanted: Collection[tuple[str, int]]) -> dict[tuple[str, int], ProcessedDoc]:
        """Docs at the earlier revs refs name, as pinning reads them (DR-0540): each doc's history read for all its
        revs at once, each rev's text kept for the process, since a rev never changes."""
        # By the vault's root, not its git dir: folder vaults in one host repo share it, and each has its own docs.
        root = self.vault.root
        out = {key: _EARLIER[(root, *key)] for key in wanted if (root, *key) in _EARLIER}
        missing = sorted(set(wanted) - set(out))
        if not missing:
            return out
        histories = DocStore(self.vault).histories([doc_id for doc_id, _ in missing])
        for doc_id, rev in missing:
            sha = next((sha for n, sha in histories.get(doc_id, []) if n == rev), None)
            if sha is None:
                continue
            proc = self.vault.git.run("cat-file", "blob", f"{sha}:./{doc_path(doc_id).as_posix()}", check=False)
            doc = decision_form.committed_doc(proc.stdout if proc.returncode == 0 else None)
            if doc is not None:
                if len(_EARLIER) >= EARLIER_KEPT:
                    _EARLIER.clear()
                _EARLIER[(root, doc_id, rev)] = out[(doc_id, rev)] = doc
        return out

    def sync_docs(self, covering: set[str]) -> int:
        """Docs whose bytes changed, new or removed, and docs that cover a record that changed. Returns how many. Under
        a forgotten list that reaches docs, the ones to index are read first, for the docs' rule to judge together."""
        folder = self.root / "processed"
        current = {_rel(self.root, p): p for p in sorted(folder.rglob("*.md"))} if folder.is_dir() else {}
        stored = dict(self.conn.execute("SELECT path, sha256 FROM doc_files").fetchall())
        forced = {
            r[0]
            for rid in sorted(covering)
            for r in self.conn.execute(
                "SELECT f.path FROM doc_covers c JOIN doc_files f ON f.doc = c.doc WHERE c.rid = ?", (rid,)
            )
        }
        changed = 0
        for rel in sorted(set(stored) - set(current)):
            self.drop_doc_file(rel)
            changed += 1
        due: list[tuple[Path, bytes]] = []
        for rel, path in current.items():
            data = path.read_bytes()
            if stored.get(rel) != _sha(data) or rel in forced:
                if self.listed.digest:
                    due.append((path, data))
                else:
                    self.index_doc_file(path, data)
                changed += 1
        if due:
            self._judge(due)
            for path, data in due:
                self.index_doc_file(path, data)
        if changed:
            self.supersede()
        return changed

    def supersede(self) -> None:
        by: dict[str, str] = {}
        for doc_id, listed in self.conn.execute("SELECT id, supersedes FROM docs ORDER BY id"):
            for target in json.loads(listed):
                by.setdefault(target, doc_id)
        self.conn.execute("UPDATE docs SET superseded_by = NULL")
        self.conn.executemany("UPDATE docs SET superseded_by = ? WHERE id = ?", sorted((v, k) for k, v in by.items()))

    # -- entities -----------------------------------------------------------------------------------------------

    def sync_entities(self, force: bool = False) -> bool:
        hashes = {_rel(self.root, p): _sha(p.read_bytes()) for p in entity_files(self.root)}
        stored = dict(self.conn.execute("SELECT path, sha256 FROM entity_files").fetchall())
        if not force and stored == hashes:
            return False
        matching.write_entities(self.conn, load_entities(self.root), hashes)
        return True


def doc_hashes(root: Path) -> dict[str, str]:
    """Every doc file under processed/, by its path from the vault's root, with the sha256 of its bytes."""
    folder = root / "processed"
    if not folder.is_dir():
        return {}
    # Every read checks these, and Path.relative_to costs as much as the hashing. rglob gives each path as the
    # folder's text and the rest, so the text after the root and its separator is the path from the root.
    cut = len(str(root / "_")) - 1
    return {str(p)[cut:].replace(os.sep, "/"): _sha(p.read_bytes()) for p in folder.rglob("*.md")}


def changed_docs(conn: sqlite3.Connection, root: Path) -> int:
    """How many doc files came, went or changed since the index read processed/: none means its doc rows say what the
    files say."""
    current = doc_hashes(root)
    stored = dict(conn.execute("SELECT path, sha256 FROM doc_files").fetchall())
    return sum(1 for p in set(current) | set(stored) if current.get(p) != stored.get(p))


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = ("records", "chunks", "docs", "entities", "mentions", "items", "problems")
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}  # noqa: S608 - fixed names


def _problems(conn: sqlite3.Connection) -> list[str]:
    return [f"{place}: {message}" for _, place, message in conn.execute("SELECT kind, place, message FROM problems")]


def _settle(db: Path) -> None:
    """Open the old index once, so SQLite rolls back a hot journal a crashed update left before the file is replaced;
    one that won't open goes, with its journal files."""
    if not db.exists():
        return
    try:
        conn = sqlite3.connect(db)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        for path in (db, *(db.with_name(db.name + suffix) for suffix in JOURNALS)):
            path.unlink(missing_ok=True)


def _replace(tmp: Path, db: Path) -> None:
    """Windows refuses to replace a file another process holds open: try for a few seconds, then say so."""
    deadline = time.monotonic() + REPLACE_SECONDS
    while True:
        try:
            tmp.replace(db)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise DocketError(
                    f"{db} is open in another process (the service, or another docket command); "
                    "stop it, then run docket index rebuild"
                ) from None
            time.sleep(REPLACE_WAIT)


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, isolation_level=None, timeout=BUSY_SECONDS)


def _rebuild(vault: Vault, reason: str | None, started: float) -> BuildReport:
    langs = languages_of(vault)
    analyzer = Analyzer(langs)
    db = db_path(vault)
    tmp = db.with_name(f"{db.name}.{os.getpid()}.tmp")
    # Under the lock no other rebuild is writing one, so every temp file here is left from a rebuild that was killed.
    for left in db.parent.glob(f"{db.name}.*.tmp"):
        with contextlib.suppress(OSError):
            left.unlink()
    # Read once: the digest the index is built under is the list its rows leave out (DR-0593).
    listed = forgotten.load(vault.root, vault.home)
    conn = _connect(tmp)
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        create(conn, langs, listed.digest)
        keeper = _carry(conn, db)
        conn.execute("BEGIN")
        conn.executemany(
            "INSERT INTO problems (kind, place, message) VALUES ('language', 'vault.yaml', ?)",
            [(p,) for p in sorted(set(langs.problems))],
        )
        indexer = Indexer(conn, vault, analyzer, collect=False, vectors=keeper, listed=listed)
        _, runs = indexer.add_runs(vault.rawlog.run_files())
        docs = indexer.sync_docs(set())
        indexer.sync_entities(force=True)
        ops = ledger_index.rebuild(conn, vault.root, listed.ops)
        matching.refresh(conn, analyzer, full=True)
        if keeper is not None:
            keeper.close()
        conn.execute("COMMIT")
        if keeper is not None and keeper.carry is not None:
            conn.execute(f"DETACH DATABASE {keeper.carry}")
        conn.execute("INSERT INTO chunks_fts (chunks_fts) VALUES ('optimize')")
        report = BuildReport(
            rebuilt=True, reason=reason, runs=runs, docs=docs, entities=True, ops=ops, counts=_counts(conn),
            problems=_problems(conn),
        )  # fmt: skip
        conn.close()
        _settle(db)
        _replace(tmp, db)
    finally:
        conn.close()
        tmp.unlink(missing_ok=True)
    report.seconds = round(time.monotonic() - started, 3)
    return report


def configured_table() -> vectors.VecTable | None:
    """The vector table of the embedder the user's config names, whether or not a worker runs: a rebuild keeps it."""
    choice = models_config.read()
    return None if isinstance(choice, Off) else vectors.table_of(choice.embedder)


def _carry(conn: sqlite3.Connection, old: Path) -> vectors.Keeper | None:
    """A rebuild's keeper for the configured embedder's table, taking vectors from the file it replaces when that file
    can be read; one that can't is rebuilt without them, and the pass embeds again."""
    table = configured_table()
    if table is None:
        return None
    vectors.create(conn, table)
    carry: str | None = None
    if old.exists():
        # SQLite refuses a file that isn't a database as it attaches it.
        with contextlib.suppress(sqlite3.DatabaseError):
            conn.execute("ATTACH DATABASE ? AS old", (str(old),))
            conn.execute("SELECT count(*) FROM old.sqlite_master").fetchone()
            carry = "old"
    return vectors.Keeper(conn, [table], carry=carry)


def write_connection(db: Path) -> sqlite3.Connection:
    """The index opened to write, in autocommit, as updates open it: the caller takes the index's lock first."""
    return _connect(db)


def read_connection(db: Path) -> sqlite3.Connection:
    return _reader(db)


def index_lock(vault: Vault) -> FileLock:
    """The index's own lock: updates and rebuilds hold it throughout, the embedding pass for each batch it writes."""
    return _lock(vault)


def _lock(vault: Vault) -> FileLock:
    folder = vault.root / INDEX_DIR
    folder.mkdir(exist_ok=True)
    return FileLock(folder / LOCK_NAME)


def rebuild(vault: Vault, reason: str | None = None) -> BuildReport:
    """The index from scratch: a new file from the vault's working tree, replacing the old one."""
    check_sqlite()
    started = time.monotonic()
    with _lock(vault):
        return _rebuild(vault, reason, started)


def _changed_runs(conn: sqlite3.Connection, vault: Vault) -> tuple[list[RunFile], int]:
    """The run files not indexed yet, and how many indexed ones went or were written again (a rewritten history)."""
    stored = {path: (size, mtime) for path, size, mtime in conn.execute("SELECT path, size, mtime_ns FROM run_files")}
    runs = {_rel(vault.root, r.path): r for r in vault.rawlog.run_files()}
    gone = sum(1 for rel, stamp in stored.items() if rel not in runs or _stamp(runs[rel].path) != stamp)
    return [r for rel, r in runs.items() if rel not in stored], gone


def update(vault: Vault) -> BuildReport:
    """Bring the index up to date with the vault; rebuild when there is none, or it can't be followed."""
    check_sqlite()
    started = time.monotonic()
    langs = languages_of(vault)
    with _lock(vault):
        db = db_path(vault)
        if not db.exists():
            return _rebuild(vault, "there was no index", started)
        # An index built under another forgotten list can't be followed, as for another schema (DR-0593): the list can
        # move with no run file moving.
        listed = forgotten.load(vault.root, vault.home)
        conn = _connect(db)
        try:
            why = stale(conn, langs, listed.digest)
            new, gone = _changed_runs(conn, vault) if why is None else ([], 0)
            if why is None and gone:
                why = f"{gone} indexed run file(s) changed or went (a rewritten history)"
            if why is not None:
                conn.close()
                return _rebuild(vault, why, started)
            analyzer = Analyzer(langs)
            conn.execute("BEGIN IMMEDIATE")
            try:
                kept = vectors.tables(conn)
                keeper = vectors.Keeper(conn, kept) if kept else None
                indexer = Indexer(conn, vault, analyzer, collect=True, vectors=keeper, listed=listed)
                touched, runs = indexer.add_runs(new)
                docs = indexer.sync_docs(touched)
                entities = indexer.sync_entities()
                ops = ledger_index.update(conn, vault.root, listed.ops)
                if runs or docs or entities:
                    matching.refresh(conn, analyzer, full=entities, new_words=indexer.new_words)
                if keeper is not None:
                    keeper.close()
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            report = BuildReport(
                rebuilt=False, runs=runs, docs=docs, entities=entities, ops=ops, counts=_counts(conn),
                problems=_problems(conn), seconds=round(time.monotonic() - started, 3),
            )  # fmt: skip
        finally:
            conn.close()
    return report


@contextlib.contextmanager
def ledger_tables(vault: Vault, tables: sqlite3.Connection | None = None) -> Generator[sqlite3.Connection]:
    """The ledger's tables as the op files stand (DR-0616, DR-0619): the index's, brought up to date, when it opens as
    this Docket writes it and its lock comes within `LEDGER_WAIT`; else every op file folded into a database in
    memory. The index's lock is held until the caller is done, so no update moves the tables while it asks them. With
    `tables`, the caller's own, as they are and left open: a dry run's, holding what a write would leave. Either reads
    each op the forgotten list masks as its mask (DR-0695), so a list that doesn't read stops the ledger too."""
    if tables is not None:
        yield tables
        return
    listed = forgotten.load(vault.root, vault.home)
    held = _ledger_index(vault, listed)
    if held is None:
        conn = sqlite3.connect(":memory:", isolation_level=None)
        try:
            conn.executescript(LEDGER_TABLES)
            conn.execute("BEGIN")
            ledger_index.rebuild(conn, vault.root, listed.ops)
            conn.execute("COMMIT")
            yield conn
        finally:
            conn.close()
        return
    conn, lock = held
    try:
        yield conn
    finally:
        conn.close()
        lock.release()


@contextlib.contextmanager
def ledger_tables_of(ledger: Ledger) -> Generator[sqlite3.Connection]:
    """A fold's tables, in memory: the ledger as a write not yet made would leave it, for a dry run to ask."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        conn.executescript(LEDGER_TABLES)
        conn.execute("BEGIN")
        with paused():
            ledger_index.fill(conn, ledger)
        conn.execute("COMMIT")
        yield conn
    finally:
        conn.close()


def _ledger_index(vault: Vault, listed: Forgotten) -> tuple[sqlite3.Connection, FileLock] | None:
    """The index with its ledger tables brought up to date under the list in force, under its lock, which the caller
    releases; None when there is no index, it was built for another Docket, other languages or another forgotten
    list, or it can't be had within `LEDGER_WAIT`."""
    db = db_path(vault)
    if not db.exists():
        return None
    lock = _lock(vault)
    try:
        lock.acquire(timeout=LEDGER_WAIT)
    except Timeout:
        return None
    conn = sqlite3.connect(db, isolation_level=None, timeout=LEDGER_WAIT)
    try:
        usable = stale(conn, languages_of(vault), listed.digest) is None
        if usable:
            conn.execute("BEGIN IMMEDIATE")
            try:
                ledger_index.update(conn, vault.root, listed.ops)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    except sqlite3.Error:
        # Another process holds the file past the wait: the op files are folded in memory instead.
        usable = False
    except BaseException:
        conn.close()
        lock.release()
        raise
    if usable:
        return conn, lock
    conn.close()
    lock.release()
    return None


def _reader(db: Path, timeout: float = BUSY_SECONDS) -> sqlite3.Connection:
    """The index opened to read: by its plain path, since a URI can't name a file on a network share, and writable
    underneath, so SQLite rolls back a hot journal an interrupted update left; `query_only` keeps every statement a
    read. `timeout` is how long a statement waits for an update's lock."""
    conn = sqlite3.connect(db, timeout=timeout)
    conn.execute("PRAGMA query_only = ON")
    return conn


def open_index(vault: Vault, timeout: float = BUSY_SECONDS) -> sqlite3.Connection:
    """The index to read, refused when there is none or it was built for another Docket, other languages or another
    forgotten list: a search refuses rather than answer from rows a forget took (DR-0593). Its statements wait up to
    `timeout` seconds for an update's lock."""
    check_sqlite()
    db = db_path(vault)
    if not db.exists():
        raise DocketError("there is no index yet; run docket index rebuild")
    digest = forgotten.load(vault.root, vault.home).digest
    conn = _reader(db, timeout)
    why = stale(conn, languages_of(vault), digest)
    if why is not None:
        conn.close()
        raise DocketError(f"the index can't be used: {why}; run docket index rebuild")
    return conn


def vector_counts(vault: Vault) -> list[VectorCoverage]:
    """Each model's vectors in the index, of how many chunks: the counts alone, for a page that shows them on every
    load, without status()'s hash of every doc and entity file. Empty when there is no index this Docket can read."""
    try:
        conn = open_index(vault)
    except DocketError:
        return []
    try:
        return [
            VectorCoverage(model=table.model, have=have, total=total)
            for table in vectors.tables(conn)
            for have, total in [vectors.coverage(conn, table)]
        ]
    except sqlite3.Error:
        # A lock an update holds past the wait: the page leaves the vectors out rather than fail.
        return []
    finally:
        conn.close()


def status(vault: Vault) -> IndexStatus:
    check_sqlite()
    db = db_path(vault)
    langs = languages_of(vault)
    base = IndexStatus(exists=db.exists(), path=_rel(vault.root, db), languages=list(langs.codes))
    if not base.exists:
        return base
    conn = _reader(db)
    try:
        base.stale = stale(conn, langs, forgotten.load(vault.root, vault.home).digest)
        if base.stale is not None:
            return base
        base.columns = list(langs.columns)
        base.counts = _counts(conn)
        base.problems = _problems(conn)
        base.flagged, base.unapplied = flags.counts(conn)
        for table in vectors.tables(conn):
            have, total = vectors.coverage(conn, table)
            base.vectors.append(VectorCoverage(model=table.model, have=have, total=total))
        new, base.changed_runs = _changed_runs(conn, vault)
        base.new_runs = len(new)
        base.changed_docs = changed_docs(conn, vault.root)
        hashes = {_rel(vault.root, p): _sha(p.read_bytes()) for p in entity_files(vault.root)}
        base.entities_changed = hashes != dict(conn.execute("SELECT path, sha256 FROM entity_files").fetchall())
    finally:
        conn.close()
    return base


def locate(vault: Vault, rid: str) -> RecordLocation | None:
    """Where a record's latest version is in the raw log, from the index."""
    conn = open_index(vault)
    try:
        row = conn.execute("SELECT path, line, versions FROM records WHERE rid = ?", (rid,)).fetchone()
    finally:
        conn.close()
    return None if row is None else RecordLocation(rid=rid, path=row[0], line=row[1], versions=row[2])


def kept(vault: Vault, source: str, since: datetime, until: datetime) -> list[str]:
    """The rids of a source's records in raw whose time falls in a window, from the index: what a sync run can skip."""
    conn = open_index(vault)
    try:
        rows = conn.execute(
            "SELECT rid FROM records WHERE source = ? AND ts >= ? AND ts <= ? ORDER BY rid",
            (source, iso(since), iso(until)),
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


# A recording no current summary covers: no minutes, recording note or thread summary, not superseded, covering it, a
# record under it (`<rid>/...`, between `<rid>/` and `<rid>0` byte by byte) or a record of its thread, as the Recording
# page and search find a recording's summary.
UNNOTED = (
    "SELECT r.rid, r.ts, r.title FROM records r WHERE r.source = ? AND r.kind = 'recording' "
    "AND r.ts >= ? AND r.ts <= ? AND NOT EXISTS (SELECT 1 FROM doc_covers c JOIN docs d ON d.id = c.doc "
    "WHERE d.superseded_by IS NULL AND d.type IN ('minutes', 'recording-note', 'thread-summary') AND (c.rid = r.rid "
    "OR (c.rid >= r.rid || '/' AND c.rid < r.rid || '0') "
    "OR c.rid IN (SELECT rid FROM records WHERE thread = r.thread))) ORDER BY r.ts, r.rid"
)


class Unnoted(NamedTuple):
    rid: str
    ts: datetime
    title: str | None


def unnoted(vault: Vault, windows: dict[str, tuple[datetime, datetime]]) -> list[Unnoted]:
    """Each source's recordings in raw whose time falls in its window and that no current summary covers, oldest
    first: the recordings `docket sync recordings` gives an agent each (DR-0747)."""
    conn = open_index(vault)
    try:
        out = [
            Unnoted(str(rid), datetime.fromisoformat(str(ts)), title)
            for source, (since, until) in sorted(windows.items())
            for rid, ts, title in conn.execute(UNNOTED, (source, iso(since), iso(until)))
        ]
    finally:
        conn.close()
    return sorted(out, key=lambda r: (r.ts, r.rid))


# The current summary covering a recording, as UNNOTED looks for one.
NOTED = (
    "SELECT d.id FROM records r JOIN doc_covers c ON (c.rid = r.rid OR (c.rid >= r.rid || '/' AND c.rid < r.rid || "
    "'0') OR c.rid IN (SELECT rid FROM records WHERE thread = r.thread)) JOIN docs d ON d.id = c.doc WHERE r.rid = ? "
    "AND d.superseded_by IS NULL AND d.type IN ('minutes', 'recording-note', 'thread-summary') ORDER BY d.id LIMIT 1"
)


def noted(vault: Vault, rids: Iterable[str]) -> dict[str, str]:
    """The current summary covering each recording that has one, by its rid: what the sync's summary says of the
    recordings it gave agents (#272). A read: the index as it stands."""
    conn = open_index(vault)
    try:
        return {rid: str(row[0]) for rid in rids if (row := conn.execute(NOTED, (rid,)).fetchone()) is not None}
    finally:
        conn.close()


def holds_kind(vault: Vault, source: str, kind: str) -> bool:
    """Whether raw holds a record of a source whose latest version is of this kind, from the index: one query, so a run
    asks it before any scan of raw."""
    conn = open_index(vault)
    try:
        row = conn.execute("SELECT 1 FROM records WHERE source = ? AND kind = ? LIMIT 1", (source, kind)).fetchone()
    finally:
        conn.close()
    return row is not None


def stored_under(vault: Vault, source: str, natives: Iterable[str]) -> set[str]:
    """The native ids under which raw holds a record of a source: its rid, thread or container is `<source>:<id>` or
    sits under it."""
    conn = open_index(vault)
    found: set[str] = set()
    try:
        for native in natives:
            ref = f"{source}:{native}"
            # LIKE with `!` as its escape, so an id's own % and _ match only themselves.
            under = ref.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "/%"
            row = conn.execute(
                "SELECT 1 FROM records WHERE source = ? AND (rid = ? OR container = ? OR thread = ? "
                "OR rid LIKE ? ESCAPE '!' OR container LIKE ? ESCAPE '!' OR thread LIKE ? ESCAPE '!') LIMIT 1",
                (source, ref, ref, ref, under, under, under),
            ).fetchone()
            if row is not None:
                found.add(native)
    finally:
        conn.close()
    return found
