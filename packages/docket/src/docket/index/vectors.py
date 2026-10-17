"""Vectors in the index (DR-0336): a table per model, kept in step with the chunks by every update and rebuild.

A table holds each chunk's vector with the hash of the input it was made from, and the chunk's time and source, so
search's filters need no join. An update keeps every table in step inside its own transaction and with no worker:
when a chunk goes, its vector moves to a temp table keyed by input hash; when a chunk comes, the vector for its input
comes back with the new id, time and source, from what moved, from another chunk with the same input, or, in a
rebuild, from the file being replaced. So no vector outlives its chunk, a reused id never keeps an old vector, and a
rebuild embeds nothing it had. The embedding pass (`embedpass`) embeds what is left.
"""

from __future__ import annotations

import importlib
import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from docket.models.catalogue import Embedding, input_hash, passage_input

META = "vec."
# The scan reads the table through a map: 67 ms over 100,000 vectors of 384 dims against 205 ms read page by page.
MAP_BYTES = 1 << 30
SCAN_LIMIT = 200
# A table's key comes from the catalogue; checked anyway, since it is written into SQL.
KEY = re.compile(r"^[a-z0-9_]{1,64}$")


@dataclass(frozen=True)
class VecTable:
    key: str
    model: str
    revision: str
    dims: int
    prefix: str

    @property
    def name(self) -> str:
        return f"chunk_vec_{self.key}"

    def input(self, title: str | None, text: str) -> str:
        return passage_input(self.prefix, title, text)


def table_of(embedder: Embedding) -> VecTable:
    return VecTable(embedder.key, embedder.name, embedder.revision, embedder.dims, embedder.passage_prefix)


def tables(conn: sqlite3.Connection, schema: str = "main") -> list[VecTable]:
    """The vector tables an index holds, from its meta rows."""
    found: list[VecTable] = []
    for key, value in conn.execute(f"SELECT key, value FROM {schema}.meta WHERE key LIKE 'vec.%' ORDER BY key"):  # noqa: S608 - a schema name of ours
        try:
            data: Any = json.loads(value)
            table = VecTable(
                str(key)[len(META) :], str(data["model"]), str(data["revision"]), int(data["dims"]), str(data["prefix"])
            )
        except (ValueError, KeyError, TypeError):
            continue
        if KEY.match(table.key):
            found.append(table)
    return found


def create(conn: sqlite3.Connection, table: VecTable) -> None:
    if not KEY.match(table.key):
        raise ValueError(f"a vector table's key is lowercase letters, digits and _: {table.key!r}")
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {table.name} (chunk INTEGER PRIMARY KEY, input_hash TEXT NOT NULL, ts TEXT, "
        "source TEXT, vec BLOB NOT NULL)"
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS {table.name}_input ON {table.name} (input_hash)")
    about = {"model": table.model, "revision": table.revision, "dims": table.dims, "prefix": table.prefix}
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (META + table.key, json.dumps(about, sort_keys=True))
    )


def screened(conn: sqlite3.Connection, table: VecTable) -> str | None:
    """The settings' fingerprint an API table's vectors passed the screen under (DR-0538), or None."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (META + table.key,)).fetchone()
    try:
        data: Any = json.loads(row[0]) if row is not None else {}
    except ValueError:
        return None
    found: object = cast(dict[str, Any], data).get("screened") if isinstance(data, dict) else None
    return found if isinstance(found, str) else None


def mark_screened(conn: sqlite3.Connection, table: VecTable, fingerprint: str) -> None:
    """Record that every vector in an API table passed the screen under these settings."""
    about = {"model": table.model, "revision": table.revision, "dims": table.dims, "prefix": table.prefix}
    about["screened"] = fingerprint
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (META + table.key, json.dumps(about, sort_keys=True))
    )


def having(conn: sqlite3.Connection, table: VecTable, chunks: Sequence[int]) -> set[int]:
    """The chunks that hold a vector in the table."""
    ids = list(chunks)
    out: set[int] = set()
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        marks = ", ".join("?" * len(part))
        out |= {int(c) for (c,) in conn.execute(f"SELECT chunk FROM {table.name} WHERE chunk IN ({marks})", part)}  # noqa: S608 - a checked key
    return out


def drop(conn: sqlite3.Connection, table: VecTable) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {table.name}")
    conn.execute("DELETE FROM meta WHERE key = ?", (META + table.key,))


def holds(conn: sqlite3.Connection, schema: str, name: str) -> bool:
    row = conn.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()  # noqa: S608
    return row is not None


def coverage(conn: sqlite3.Connection, table: VecTable) -> tuple[int, int]:
    """How many chunks have a vector in the table, of how many."""
    have = int(conn.execute(f"SELECT count(*) FROM {table.name}").fetchone()[0])  # noqa: S608 - a checked key
    total = int(conn.execute("SELECT count(*) FROM chunks").fetchone()[0])
    return have, total


class Keeper:
    """Keeps an index's vector tables in step with its chunks, inside the transaction that writes them."""

    def __init__(self, conn: sqlite3.Connection, kept: list[VecTable], carry: str | None = None) -> None:
        self.conn = conn
        self.kept = kept
        # The attached file a rebuild replaces, and its tables, to take vectors from.
        self.carry = carry
        self.carried = {t.name for t in kept if carry is not None and holds(conn, carry, t.name)}
        for table in kept:
            conn.execute(
                f"CREATE TEMP TABLE IF NOT EXISTS moved_{table.key} (input_hash TEXT PRIMARY KEY, vec BLOB NOT NULL)"
            )

    def drop(self, owner: str) -> None:
        """Before an owner's chunks go: their vectors move aside by input hash, and their rows go."""
        for table in self.kept:
            chunks = "SELECT id FROM chunks WHERE owner = ?"
            self.conn.execute(
                f"INSERT OR IGNORE INTO temp.moved_{table.key} (input_hash, vec) "  # noqa: S608 - checked keys
                f"SELECT input_hash, vec FROM main.{table.name} WHERE chunk IN ({chunks})",
                (owner,),
            )
            self.conn.execute(f"DELETE FROM main.{table.name} WHERE chunk IN ({chunks})", (owner,))  # noqa: S608

    def add(self, chunk: int, title: str | None, text: str, ts: str | None, source: str | None) -> None:
        """After a chunk is written: its vector, when one for its input moved aside, sits on another chunk, or sits in
        the file a rebuild replaces."""
        for table in self.kept:
            wanted = input_hash(table.input(title, text))
            places = [f"temp.moved_{table.key}", f"main.{table.name}"]
            if table.name in self.carried:
                places.append(f"{self.carry}.{table.name}")
            for place in places:
                row = self.conn.execute(f"SELECT vec FROM {place} WHERE input_hash = ? LIMIT 1", (wanted,)).fetchone()  # noqa: S608
                if row is not None:
                    self.conn.execute(
                        f"INSERT OR REPLACE INTO main.{table.name} (chunk, input_hash, ts, source, vec) "  # noqa: S608
                        "VALUES (?, ?, ?, ?, ?)",
                        (chunk, wanted, ts, source, cast(bytes, row[0])),
                    )
                    break

    def close(self) -> None:
        for table in self.kept:
            self.conn.execute(f"DROP TABLE IF EXISTS temp.moved_{table.key}")


# -- the scan ----------------------------------------------------------------------------------------------------


class QueryVectors(Protocol):
    """What search needs of the models: their embedder, and a query's vector or why there is none."""

    @property
    def embedder(self) -> Embedding: ...

    def query_vector(self, words: str) -> bytes | str: ...


def load_extension(conn: sqlite3.Connection) -> str | None:
    """Load sqlite-vec on one connection, or say why it can't be (DR-0337)."""
    try:
        sqlite_vec: Any = importlib.import_module("sqlite_vec")
    except ImportError:
        return "sqlite-vec has no build for this system, so search goes without vectors"
    if not hasattr(conn, "enable_load_extension"):
        return "this Python's SQLite can't load extensions, so search goes without vectors"
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)
    return None


def scan(
    db: Path, table: VecTable, vector: bytes, filters: tuple[str, list[object]], limit: int = SCAN_LIMIT
) -> list[tuple[int, str]] | str:
    """The chunks nearest a query's vector, by cosine distance, under the search's own filters (they name the table
    `c`, whose ts and source are the chunk's), each with the hash of its input; or why the scan can't run."""
    conn = sqlite3.connect(db, timeout=5.0)
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute(f"PRAGMA mmap_size = {MAP_BYTES}")
        why = load_extension(conn)
        if why is not None:
            return why
        if not holds(conn, "main", table.name):
            return f"the index has no vectors for {table.model} yet"
        try:
            return [
                (int(chunk), str(hashed))
                for chunk, hashed in conn.execute(
                    f"SELECT chunk, input_hash FROM {table.name} c WHERE 1 = 1{filters[0]} "  # noqa: S608 - a checked name, filters of placeholders
                    "ORDER BY vec_distance_cosine(c.vec, ?), chunk LIMIT ?",
                    [*filters[1], vector, limit],
                )
            ]
        except sqlite3.Error as e:
            return f"the vector scan failed: {type(e).__name__}"
    finally:
        conn.close()
