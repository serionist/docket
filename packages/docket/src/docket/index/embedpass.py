"""The embedding pass (DR-0336): vectors for the chunks that have none, after an update and never inside one.

Each round reads a batch of chunks with no vector and closes its statement before the worker is called, so no open
read holds the index while the worker works (with a rollback journal an update's COMMIT would wait for it). An input
another chunk already has a vector for is copied, not embedded. The batch is written under the index's lock in a short
transaction, each vector only for a chunk whose input is still the one embedded: a rebuild or an update may have
replaced it meanwhile. When the configured model's table covers every chunk, the tables of other models go.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from docket.index import vectors
from docket.index.build import db_path, index_lock, read_connection, write_connection
from docket.index.vectors import VecTable
from docket.models.catalogue import Embedder, input_hash
from docket.models.client import WorkerError
from docket.vault.vault import Vault

ROWS = 256


class Embeds(Protocol):
    """What the pass needs of the models: their embedder and its passages' vectors."""

    @property
    def embedder(self) -> Embedder: ...

    def passage_vectors(self, inputs: Sequence[str]) -> list[bytes]: ...


@dataclass
class PassReport:
    model: str
    embedded: int = 0
    copied: int = 0
    have: int = 0
    total: int = 0
    dropped: list[str] = field(default_factory=list[str])
    # Why the pass stopped before every chunk had its vector: the worker, or a table a rebuild took away.
    why: str | None = None


@dataclass(frozen=True)
class _Row:
    chunk: int
    hash: str
    input: str


def _missing(conn: sqlite3.Connection, table: VecTable, rows: int) -> tuple[list[_Row], dict[str, bytes]]:
    """A batch of chunks with no vector, with their inputs, and the vectors other chunks already have for them."""
    found = conn.execute(
        f"SELECT c.id, c.title, c.text FROM chunks c WHERE NOT EXISTS "  # noqa: S608 - a checked table name
        f"(SELECT 1 FROM {table.name} v WHERE v.chunk = c.id) ORDER BY c.id LIMIT ?",
        (rows,),
    ).fetchall()
    batch = [_Row(chunk, input_hash(text := table.input(title, body)), text) for chunk, title, body in found]
    hashes = sorted({r.hash for r in batch})
    have: dict[str, bytes] = {}
    for start in range(0, len(hashes), 500):
        part = hashes[start : start + 500]
        marks = ", ".join("?" * len(part))
        have.update(
            conn.execute(f"SELECT input_hash, vec FROM {table.name} WHERE input_hash IN ({marks})", part).fetchall()  # noqa: S608
        )
    return batch, have


def _write(conn: sqlite3.Connection, table: VecTable, batch: list[_Row], vecs: dict[str, bytes]) -> int:
    """Each vector for a chunk whose input is still the one embedded; returns how many were written."""
    written = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for row in batch:
            now = conn.execute("SELECT title, text, ts, source FROM chunks WHERE id = ?", (row.chunk,)).fetchone()
            if now is None or input_hash(table.input(now[0], now[1])) != row.hash:
                continue
            cur = conn.execute(
                f"INSERT OR IGNORE INTO {table.name} (chunk, input_hash, ts, source, vec) VALUES (?, ?, ?, ?, ?)",  # noqa: S608
                (row.chunk, row.hash, now[2], now[3], vecs[row.hash]),
            )
            written += cur.rowcount
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return written


def fill(
    vault: Vault,
    models: Embeds,
    *,
    progress: Callable[[int, int], None] | None = None,
    stop: Callable[[], bool] = lambda: False,
    rows: int = ROWS,
) -> PassReport:
    """Embed every chunk with no vector in the configured model's table, making the table when the index has none."""
    table = vectors.table_of(models.embedder)
    report = PassReport(model=models.embedder.name)
    db = db_path(vault)
    if not db.exists():
        report.why = "there is no index yet"
        return report
    with index_lock(vault):
        conn = write_connection(db)
        try:
            if table.key not in {t.key for t in vectors.tables(conn)}:
                conn.execute("BEGIN IMMEDIATE")
                vectors.create(conn, table)
                conn.execute("COMMIT")
        finally:
            conn.close()
    idle = 0
    while not stop():
        conn = read_connection(db)
        try:
            batch, have = _missing(conn, table, rows)
        except sqlite3.OperationalError:
            report.why = _stopped(conn, table)
            break
        finally:
            conn.close()
        if not batch:
            break
        new = sorted({r.input for r in batch if r.hash not in have})
        try:
            made = models.passage_vectors(new)
        except WorkerError as e:
            report.why = str(e)
            break
        vecs = {**have, **{input_hash(text): vec for text, vec in zip(new, made, strict=True)}}
        with index_lock(vault):
            conn = write_connection(db)
            try:
                written = _write(conn, table, batch, vecs)
            except sqlite3.OperationalError:
                report.why = _stopped(conn, table)
                break
            finally:
                conn.close()
        report.embedded += len(new)
        report.copied += sum(1 for r in batch if r.hash in have)
        # A batch the index changed under twice running is left for the next pass.
        idle = idle + 1 if written == 0 else 0
        if idle >= 2:
            report.why = "the index kept changing under the pass; the next one goes on"
            break
        if progress is not None:
            progress(*_coverage(db, table))
    report.have, report.total = _coverage(db, table)
    if report.why is None and not stop() and report.have == report.total:
        report.dropped = _drop_others(vault, table)
    return report


def _stopped(conn: sqlite3.Connection, table: VecTable) -> str:
    """Why a statement on the vector table failed: the table went with a rebuild, or the index stayed busy past the
    connection's timeout, which the next pass waits out."""
    try:
        gone = not vectors.holds(conn, "main", table.name)
    except sqlite3.OperationalError:
        gone = False
    if gone:
        return "the vector table went with a rebuild"
    return "the index was busy past its timeout, so the pass stopped; the next one goes on from there"


def _coverage(db: Path, table: VecTable) -> tuple[int, int]:
    conn = read_connection(db)
    try:
        return vectors.coverage(conn, table)
    except sqlite3.OperationalError:
        return 0, 0
    finally:
        conn.close()


def _drop_others(vault: Vault, table: VecTable) -> list[str]:
    """Once the configured model's table is whole, the tables of the models before it go."""
    with index_lock(vault):
        conn = write_connection(db_path(vault))
        try:
            others = [t for t in vectors.tables(conn) if t.key != table.key]
            if others:
                conn.execute("BEGIN IMMEDIATE")
                for other in others:
                    vectors.drop(conn, other)
                conn.execute("COMMIT")
            return [t.model for t in others]
        finally:
            conn.close()
