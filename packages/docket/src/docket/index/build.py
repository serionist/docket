"""Building the index from the vault, and keeping it up to date.

`rebuild` is the contract: it reads the working tree (`raw/`, `processed/`, `entities/`; never `staging/`, never git
objects) into a new file, sorted, and replaces the old one, so two rebuilds of one vault give the same database.
`update` runs the same indexer on the live file for what changed since: new run files, docs whose bytes changed, and
the entity files; anything it can't follow (a run file changed or removed, another schema or set of languages) makes it
rebuild. Raw lines are read leniently: a line that doesn't load is a problem and the rest is indexed (DR-0115).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from filelock import FileLock
from pydantic import BaseModel, ValidationError

from docket.errors import DocketError
from docket.index import matching
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
from docket.index.schema import check_sqlite, create, stale
from docket.index.text import Analyzer, fold, words
from docket.vault import legacy
from docket.vault.entities import entity_files, load_entities
from docket.vault.processed import doc_path, parse_doc
from docket.vault.rawlog import RunFile, parse_run_file
from docket.vault.records import Attachment, RawRecord
from docket.vault.segments import segments
from docket.vault.vault import Vault

INDEX_DIR = "index"
DB_NAME = "docket.sqlite"
LOCK_NAME = "docket.lock"
REPLACE_SECONDS = 5.0
REPLACE_WAIT = 0.1
BUSY_SECONDS = 5.0
JOURNALS = ("-journal", "-wal", "-shm")


class BuildReport(BaseModel):
    """What a rebuild or an update did."""

    rebuilt: bool
    reason: str | None = None
    runs: int = 0
    docs: int = 0
    entities: bool = False
    counts: dict[str, int] = {}
    problems: list[str] = []
    seconds: float = 0.0


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


def read_run(root: Path, run: RunFile) -> tuple[list[tuple[int, RawRecord]], list[tuple[str, str]]]:
    """A run file's records with their lines, read leniently: a line that doesn't load is a problem, and a field this
    Docket doesn't know is left out and reported."""
    rel = _rel(root, run.path)
    records: list[tuple[int, RawRecord]] = []
    problems: list[tuple[str, str]] = []
    with run.path.open(encoding="utf-8", errors="replace", newline="\n") as f:
        for n, line in enumerate(f, start=1):
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
                records.append((n, RawRecord.model_validate(fields)))
            except ValidationError as e:
                err = e.errors()[0]
                where = ".".join(str(p) for p in err["loc"]) or "record"
                problems.append((place, f"not a raw record: {where}: {err['msg']}"))
    return records, problems


class Indexer:
    """Writes the index's rows for records, threads, docs and entities, on a connection inside a transaction."""

    def __init__(self, conn: sqlite3.Connection, vault: Vault, analyzer: Analyzer, *, collect: bool) -> None:
        self.conn = conn
        self.root = vault.root
        self.vault = vault
        self.analyzer = analyzer
        self.tz = ZoneInfo(vault.config.owner.time_zone)
        self.collect = collect
        self.new_words: dict[str, set[str]] = defaultdict(set)
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
            if self.collect:
                for col, value in zip(self.analyzer.columns, values, strict=True):
                    self.new_words[col].update(fold(w) for w in words(value))

    def drop_owner(self, owner: str) -> None:
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
                for line, record in records:
                    seen.add(record.rid)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO record_versions (rid, v, path, line) VALUES (?, ?, ?, ?)",
                        (record.rid, record.v, rel, line),
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
            for table in ("doc_covers", "doc_anchors", "old_paths"):
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
            meta = doc.meta
            doc_id = meta.id
            chunks, anchors = doc_chunks(doc, self._info, self.tz)
            self.conn.execute(
                "INSERT INTO docs (id, type, rev, path, title, by, supersedes, superseded_by, entities) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    meta.id, meta.type, meta.rev, rel, doc_title(doc), meta.by, json.dumps(meta.supersedes),
                    json.dumps(meta.entities),
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
            self.add_chunks(chunks)
        self.conn.execute("INSERT INTO doc_files (path, sha256, doc) VALUES (?, ?, ?)", (rel, _sha(data), doc_id))

    def sync_docs(self, covering: set[str]) -> int:
        """Docs whose bytes changed, new or removed, and docs that cover a record that changed. Returns how many."""
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
        for rel, path in current.items():
            data = path.read_bytes()
            if stored.get(rel) != _sha(data) or rel in forced:
                self.index_doc_file(path, data)
                changed += 1
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
    tables = ("records", "chunks", "docs", "entities", "mentions", "problems")
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
    conn = _connect(tmp)
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        create(conn, langs)
        conn.execute("BEGIN")
        conn.executemany(
            "INSERT INTO problems (kind, place, message) VALUES ('language', 'vault.yaml', ?)",
            [(p,) for p in sorted(set(langs.problems))],
        )
        indexer = Indexer(conn, vault, analyzer, collect=False)
        _, runs = indexer.add_runs(vault.rawlog.run_files())
        docs = indexer.sync_docs(set())
        indexer.sync_entities(force=True)
        matching.refresh(conn, analyzer, full=True)
        conn.execute("COMMIT")
        conn.execute("INSERT INTO chunks_fts (chunks_fts) VALUES ('optimize')")
        report = BuildReport(
            rebuilt=True, reason=reason, runs=runs, docs=docs, entities=True, counts=_counts(conn),
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
        conn = _connect(db)
        try:
            why = stale(conn, langs)
            new, gone = _changed_runs(conn, vault) if why is None else ([], 0)
            if why is None and gone:
                why = f"{gone} indexed run file(s) changed or went (a rewritten history)"
            if why is not None:
                conn.close()
                return _rebuild(vault, why, started)
            analyzer = Analyzer(langs)
            conn.execute("BEGIN IMMEDIATE")
            try:
                indexer = Indexer(conn, vault, analyzer, collect=True)
                touched, runs = indexer.add_runs(new)
                docs = indexer.sync_docs(touched)
                entities = indexer.sync_entities()
                if runs or docs or entities:
                    matching.refresh(conn, analyzer, full=entities, new_words=indexer.new_words)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            report = BuildReport(
                rebuilt=False, runs=runs, docs=docs, entities=entities, counts=_counts(conn), problems=_problems(conn),
                seconds=round(time.monotonic() - started, 3),
            )  # fmt: skip
        finally:
            conn.close()
    return report


def _reader(db: Path) -> sqlite3.Connection:
    """The index opened to read: by its plain path, since a URI can't name a file on a network share, and writable
    underneath, so SQLite rolls back a hot journal an interrupted update left; `query_only` keeps every statement a
    read."""
    conn = sqlite3.connect(db, timeout=BUSY_SECONDS)
    conn.execute("PRAGMA query_only = ON")
    return conn


def open_index(vault: Vault) -> sqlite3.Connection:
    """The index to read, refused when there is none or it was built for another Docket or other languages."""
    check_sqlite()
    db = db_path(vault)
    if not db.exists():
        raise DocketError("there is no index yet; run docket index rebuild")
    conn = _reader(db)
    why = stale(conn, languages_of(vault))
    if why is not None:
        conn.close()
        raise DocketError(f"the index can't be used: {why}; run docket index rebuild")
    return conn


def status(vault: Vault) -> IndexStatus:
    check_sqlite()
    db = db_path(vault)
    langs = languages_of(vault)
    base = IndexStatus(exists=db.exists(), path=_rel(vault.root, db), languages=list(langs.codes))
    if not base.exists:
        return base
    conn = _reader(db)
    try:
        base.stale = stale(conn, langs)
        if base.stale is not None:
            return base
        base.columns = list(langs.columns)
        base.counts = _counts(conn)
        base.problems = _problems(conn)
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
