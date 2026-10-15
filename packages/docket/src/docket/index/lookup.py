"""What `read` asks the index about docs, when the index is current for processed/ (DR-0321, DR-0322).

The index is current when every doc file under processed/ has the sha256 it was read with, and none came or went: its
`old_paths`, `docs` and `doc_covers` rows then say what the files say, since each comes from its doc's file alone.
`open_current` checks that inside a read transaction that stays open while the caller asks, so an update can't commit
between the check and the questions. Anything else (no index, another schema or languages, a file that isn't an index,
no FTS5, a doc changed, an update holding the index) gives None, and `read` parses every doc instead, never waiting
for the index. The index only narrows: `read` runs the same code on what it gets back as on every doc. Nothing here
writes the index.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from docket.errors import DocketError
from docket.index import build
from docket.vault import legacy
from docket.vault.legacy import OldPath
from docket.vault.vault import Vault

# SQLite before 3.32 takes at most 999 parameters in one statement.
BATCH = 500
# How long a read waits for an update holding the index: parsing the docs is the fallback, so hardly at all.
LOCK_WAIT_SECONDS = 0.05


class DocIndex:
    """A current index, held in one read transaction until `close`."""

    def __init__(self, conn: sqlite3.Connection, root: Path) -> None:
        self.conn = conn
        self.root = root

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

    def close(self) -> None:
        self.conn.close()


def open_current(vault: Vault) -> DocIndex | None:
    """The index, held in a read transaction, when its doc rows say what processed/ says; else None."""
    if not build.db_path(vault).exists():
        return None
    try:
        conn = build.open_index(vault, timeout=LOCK_WAIT_SECONDS)
    except (DocketError, sqlite3.Error):
        return None
    try:
        conn.execute("BEGIN")
        if build.changed_docs(conn, vault.root) == 0:
            return DocIndex(conn, vault.root)
    except (sqlite3.Error, OSError):
        pass
    conn.close()
    return None
