"""Vectors in the index (DR-0336): a table per model, kept in step with the chunks by every update and rebuild.

A table holds each chunk's vector with the hash of the input it was made from, and the chunk's time and source, so
search's filters need no join. An update keeps every table in step inside its own transaction and with no worker:
when a chunk goes, its vector moves to a temp table keyed by input hash; when a chunk comes, the vector for its input
comes back with the new id, time and source, from what moved, from another chunk with the same input, or, in a
rebuild, from the file being replaced. So no vector outlives its chunk, a reused id never keeps an old vector, and a
rebuild embeds nothing it had. The embedding pass (`embedpass`) embeds what is left.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, cast

from docket.models.catalogue import Embedder, input_hash, passage_input

META = "vec."
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


def table_of(embedder: Embedder) -> VecTable:
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
