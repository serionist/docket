"""Builders the ledger's tests share: ops made by hand, in the shape the writer makes them, op files on disk, and the
ledger's tables in the index as rows to compare."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from docket.index import ledger as ledger_index
from docket.index.build import db_path
from docket.index.schema import LEDGER_TABLES
from docket.ledger import ops
from docket.ledger.ops import Line, Op
from docket.vault.vault import Vault

NOW = datetime(2026, 10, 18, 9, 14, 2, tzinfo=UTC)
DAY = date(2026, 10, 18)
ADD_FIELDS = {"kind": "reply", "title": "Send Ada the dates", "who": "Ada Example", "stream": "pilot",
              "source": "chat:C0TEST/1791000001.000100"}  # fmt: skip
Rows = dict[str, list[tuple[Any, ...]]]


def ulid(n: int, ms: int = 1_790_000_000_000) -> str:
    """An id with a known order: the nth one of a millisecond."""
    return ops.encode((ms << ops.RANDOM_BITS) + n)


def op(kind: str, n: int, item: int | None = None, **keys: Any) -> Op:
    """The op with id ulid(n), on the item whose add is ulid(item), by Claude on desk."""
    base: dict[str, Any] = {
        "schema_version": 1, "id": ulid(n), "item": ulid(n if item is None else item), "op": kind, "day": DAY,
        "machine": "desk", "by": "claude", "via": "cli",
    }  # fmt: skip
    if kind == "add":
        base |= {"number": n, "fields": dict(ADD_FIELDS)}
    return Op(**(base | keys))


def line(o: Op, n: int = 1) -> Line:
    return Line(o, o.text(), ops.rel_of(o), n)


def lines(*made: Op) -> list[Line]:
    return [line(o, n) for n, o in enumerate(made, start=1)]


def write_ops(root: Path, made: Iterable[Op]) -> list[str]:
    """Append ops to their files under root, as the writer lays them out; the files written."""
    by_file: dict[str, list[str]] = {}
    for o in made:
        by_file.setdefault(ops.rel_of(o), []).append(o.text())
    for rel, texts in by_file.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(before + "".join(f"{t}\n" for t in texts), encoding="utf-8", newline="\n")
    return sorted(by_file)


def fresh_tables() -> sqlite3.Connection:
    """The ledger's tables, empty, in memory."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(LEDGER_TABLES)
    return conn


def tables(conn: sqlite3.Connection) -> Rows:
    """The ledger's tables' rows, in key order."""
    return {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall()) for t in ledger_index.TABLES}  # noqa: S608


def rebuilt(root: Path) -> Rows:
    """What a rebuild of the ledger's tables gives for the op files under root."""
    conn = fresh_tables()
    try:
        ledger_index.rebuild(conn, root)
        return tables(conn)
    finally:
        conn.close()


def caught_up(vault: Vault) -> Rows:
    """The index's ledger tables brought up to date from the op files, as a write brings them."""
    conn = sqlite3.connect(db_path(vault), isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        ledger_index.update(conn, vault.root)
        conn.execute("COMMIT")
        return tables(conn)
    finally:
        conn.close()
