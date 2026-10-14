from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from docket.errors import DocketError
from docket.index import schema
from docket.index.languages import resolve


def test_a_new_index_has_every_table_and_a_column_per_language() -> None:
    conn = sqlite3.connect(":memory:")
    schema.create(conn, resolve(["en", "hu", "de"]))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
    for table in ("records", "segments", "chunks", "chunks_fts", "docs", "doc_anchors", "entities", "aliases",
                  "alias_terms", "mentions", "problems", "vocab"):  # fmt: skip
        assert table in names
    conn.execute("INSERT INTO chunks_fts (rowid, folded, stem_en, stem_de, stem_hu) VALUES (1, 'a', 'b', 'c', 'd')")
    assert schema.stale(conn, resolve(["en", "hu", "de"])) is None


def test_fts5_is_in_this_python() -> None:
    schema.check_sqlite()


def test_an_old_sqlite_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 26, 0))
    with pytest.raises(DocketError, match=r"needs SQLite 3\.27\.0 or later"):
        schema.check_sqlite()


def test_a_sqlite_without_fts5_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    real = sqlite3.connect

    class NoFts:
        def __init__(self) -> None:
            self.conn = real(":memory:")

        def execute(self, sql: str, *args: Any) -> Any:
            if "fts5" in sql:
                raise sqlite3.OperationalError("no such module: fts5")
            return self.conn.execute(sql, *args)

        def close(self) -> None:
            self.conn.close()

    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: NoFts())
    with pytest.raises(DocketError, match="built without it"):
        schema.check_sqlite()


def test_an_index_from_another_build_is_stale() -> None:
    langs = resolve(["en", "hu"])
    conn = sqlite3.connect(":memory:")
    schema.create(conn, langs)
    assert schema.stale(conn, resolve(["en"])) is not None and "languages" in str(schema.stale(conn, resolve(["en"])))
    conn.execute("UPDATE meta SET value = '0.0.1' WHERE key = 'snowballstemmer'")
    assert "snowballstemmer" in str(schema.stale(conn, langs))
    conn.execute("PRAGMA user_version = 99")
    assert "schema 99" in str(schema.stale(conn, langs))
    assert schema.stale(sqlite3.connect(":memory:"), langs) == "it isn't an index this Docket can read"
