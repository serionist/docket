"""What `read` asks the index, when the index is current for what it asks about (DR-0321, DR-0322, DR-0446, DR-0447).

A read opens the index once, in one read transaction that stays open while it asks, so an update can't commit between a
check and the questions. Two checks, made apart, each at the read's first question of its kind:

- Docs: every doc file under processed/ has the sha256 it was read with, and none came or went. Its `old_paths`, `docs`
  and `doc_covers` rows then say what the files say, since each comes from its doc's file alone.
- A source's raw: its run files are those the index read, by name, size and modification time, none holds a line the
  index reports as a problem, and no record of it lies in another source's folder. Its `record_versions` and `records`
  rows then say where each of its records' lines is.

Anything else (no index, another schema or languages, a file that isn't an index, no FTS5, an update holding the index,
a doc changed, a run new or gone) gives None or False, and `read` parses every doc or loads the source whole instead,
never waiting for the index. The index only narrows: `read` runs the same code on what it gets back as on everything.
Nothing here writes the index.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from docket.errors import DocketError
from docket.index import build
from docket.vault import legacy
from docket.vault.legacy import OldPath
from docket.vault.rawlog import RunOrder, parse_run_file, run_stamps
from docket.vault.spans import Place
from docket.vault.vault import Vault

# SQLite before 3.32 takes at most 999 parameters in one statement.
BATCH = 500
# How long a read waits for an update holding the index: parsing the docs or loading the raw is the fallback, so hardly
# at all.
LOCK_WAIT_SECONDS = 0.05
# The fields a record's neighbours are grouped by, the only names `group` puts in its SQL.
GROUP_FIELDS = ("thread", "container")


def folder(source: str) -> tuple[str, str]:
    """The range of paths from the vault's root under raw/<source>/: every such path sorts at or after the first and
    before the second, since `0` follows `/`, and no other source's does, since a source's name has no `/`."""
    return f"raw/{source}/", f"raw/{source}0"


def _like(text: str) -> str:
    return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")


class ReadIndex:
    """The index held in one read transaction for one read, until `close`."""

    def __init__(self, conn: sqlite3.Connection, root: Path) -> None:
        self.conn = conn
        self.root = root
        self._docs: bool | None = None
        self._raw: dict[str, bool] = {}
        self._orders: dict[str, RunOrder] = {}

    # -- docs ---------------------------------------------------------------------------------------------------------

    def docs_current(self) -> bool:
        """Whether the doc rows say what processed/ says, hashed once per read (DR-0321)."""
        if self._docs is None:
            try:
                self._docs = build.changed_docs(self.conn, self.root) == 0
            except (sqlite3.Error, OSError):
                self._docs = False
        return self._docs

    def old_paths(self, locator: str) -> list[OldPath]:
        """The old paths `legacy.find` could match a locator to, out of every legacy note's (`legacy.keys`)."""
        keys = legacy.keys(locator)
        rows: set[tuple[str, str]] = set()
        for i in range(0, len(keys.folded), BATCH):
            part = keys.folded[i : i + BATCH]
            marks = ", ".join("?" * len(part))
            sql = f"SELECT path, doc FROM old_paths WHERE folded IN ({marks})"  # noqa: S608 - placeholders only
            rows.update(self.conn.execute(sql, part).fetchall())
        rows.update(self.conn.execute("SELECT path, doc FROM old_paths WHERE name = ?", (keys.name,)).fetchall())
        return [OldPath(path, doc) for path, doc in sorted(rows)]

    def supersedes(self) -> list[tuple[str, list[str]]]:
        """Each doc that supersedes others, with the ids it names, in the order `DocStore.all` reads them."""
        rows = self.conn.execute("SELECT path, id, supersedes FROM docs WHERE supersedes != '[]'").fetchall()
        return [(doc, json.loads(listed)) for _, doc, listed in sorted(rows, key=lambda r: self.root / r[0])]

    def covering(self, rid: str) -> list[Path]:
        """The files of the docs that cover a record, sorted as `DocStore.all` sorts them."""
        rows = self.conn.execute(
            "SELECT d.path FROM doc_covers c JOIN docs d ON d.id = c.doc WHERE c.rid = ?", (rid,)
        ).fetchall()
        return sorted(self.root / path for (path,) in rows)

    # -- raw ----------------------------------------------------------------------------------------------------------

    def raw_current(self, source: str) -> bool:
        """Whether a source's rows say where its records' lines are, checked once per read (DR-0446)."""
        if source not in self._raw:
            try:
                self._raw[source] = self._raw_check(source)
            except (sqlite3.Error, OSError):
                self._raw[source] = False
        return self._raw[source]

    def _raw_check(self, source: str) -> bool:
        lo, hi = folder(source)
        stored = {
            str(path): (size, mtime)
            for path, size, mtime in self.conn.execute(
                "SELECT path, size, mtime_ns FROM run_files WHERE path >= ? AND path < ?", (lo, hi)
            )
        }
        if stored != run_stamps(self.root, source):
            return False
        problem = self.conn.execute(
            "SELECT 1 FROM problems WHERE kind = 'raw' AND ((place >= ? AND place < ?) OR message LIKE ? ESCAPE '!') "
            "LIMIT 1",
            (lo, hi, _like(build.foreign_prefix(source)) + "%"),
        ).fetchone()
        return problem is None

    def _order(self, path: str) -> RunOrder:
        if path not in self._orders:
            run = parse_run_file(self.root / "raw", self.root / path)
            self._orders[path] = run.order if run is not None else ("", "", "", 0, "")
        return self._orders[path]

    def places(self, rid: str, source: str) -> list[Place]:
        """Where each version of a record is in its source's run files, in run order and line, as the load reads
        them: by `RunFile.order`, never by the path's text."""
        lo, hi = folder(source)
        rows = self.conn.execute(
            "SELECT path, line, offset, v FROM record_versions WHERE rid = ? AND path >= ? AND path < ?", (rid, lo, hi)
        ).fetchall()
        return sorted((Place(*row) for row in rows), key=lambda p: (self._order(p.path), p.line))

    def has(self, rid: str, source: str) -> bool:
        lo, hi = folder(source)
        row = self.conn.execute(
            "SELECT 1 FROM record_versions WHERE rid = ? AND path >= ? AND path < ? LIMIT 1", (rid, lo, hi)
        ).fetchone()
        return row is not None

    def under(self, rid: str, source: str, kind: str | None) -> list[str]:
        """The records of a source whose latest version names rid as its thread or container, of a kind when one is
        given: what could be under it (DR-0447)."""
        sql = "SELECT rid FROM records WHERE source = ? AND (thread = ? OR container = ?) AND rid != ?"
        args: tuple[str, ...] = (source, rid, rid, rid)
        if kind is not None:
            sql, args = sql + " AND kind = ?", (*args, kind)
        return [str(r) for (r,) in self.conn.execute(sql + " ORDER BY rid", args)]

    def group(self, field: str, value: str, source: str) -> list[tuple[str, str]]:
        """The records of a source whose latest version has this thread or container, with their times to the
        millisecond, in `(ts, rid)` order."""
        if field not in GROUP_FIELDS:
            raise ValueError(f"records are grouped by {' or '.join(GROUP_FIELDS)}, not {field}")
        sql = f"SELECT rid, ts FROM records WHERE source = ? AND {field} = ? ORDER BY ts, rid"  # noqa: S608 - fixed names
        return [(str(rid), str(ts)) for rid, ts in self.conn.execute(sql, (source, value))]

    def close(self) -> None:
        self.conn.close()


def open_read(vault: Vault) -> ReadIndex | None:
    """The index, held in a read transaction from here to `close`, when it opens as this Docket writes it; else
    None."""
    if not build.db_path(vault).exists():
        return None
    try:
        conn = build.open_index(vault, timeout=LOCK_WAIT_SECONDS)
    except (DocketError, sqlite3.Error):
        return None
    try:
        conn.execute("BEGIN")
        # The first read takes the lock that keeps an update from committing until the read is done.
        conn.execute("SELECT count(*) FROM meta").fetchone()
    except sqlite3.Error:
        conn.close()
        return None
    return ReadIndex(conn, vault.root)
