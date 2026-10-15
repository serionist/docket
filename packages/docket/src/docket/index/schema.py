"""The index database: its tables, the versions it was built with, and the SQLite it needs.

The index is a cache, rebuilt from the vault on each machine (DR-0115). Its FTS5 table has a column as written and a
stemmed column per language; it is contentless, so a chunk's text lives once, in `chunks`. Tables other than `chunks`
have natural keys, so their rows dump in key order whatever order they were written in.
"""

from __future__ import annotations

import sqlite3

from docket.errors import DocketError
from docket.index.languages import Languages
from docket.index.text import snowball_version

# 2: the FTS5 rows hold hyphenated compounds joined (DR-0280).
# 3: a common stem form a name's case forms explain is kept, so alias_terms and mentions change.
# 4: legacy notes' old paths in old_paths (DR-0320).
# 5: a stem form counts only beside the alias as written or in a case form, so alias_terms and mentions change
# (DR-0350).
# 6: a case form counts beside a stem form it is cut to, with or without accents, while any is kept, and not when it is
# a common word, so alias_terms and mentions change (DR-0350).
SCHEMA_VERSION = 6
TOKENIZER = "unicode61 remove_diacritics 2"
# remove_diacritics 2 came in SQLite 3.27.
MIN_SQLITE = (3, 27, 0)

TABLES = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE run_files (path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE records (
  rid TEXT PRIMARY KEY, v TEXT NOT NULL, versions INTEGER NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL,
  container TEXT, thread TEXT, ts TEXT NOT NULL, author TEXT, title TEXT, lang TEXT, engine TEXT,
  fidelity TEXT NOT NULL, grp TEXT NOT NULL, path TEXT NOT NULL, line INTEGER NOT NULL
) WITHOUT ROWID;
CREATE INDEX records_thread ON records (thread);
CREATE TABLE record_versions (
  rid TEXT NOT NULL, v TEXT NOT NULL, path TEXT NOT NULL, line INTEGER NOT NULL, PRIMARY KEY (rid, path, line)
) WITHOUT ROWID;
CREATE TABLE segments (
  rid TEXT NOT NULL, ordinal INTEGER NOT NULL, engine TEXT, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
  speaker TEXT, person TEXT, text TEXT NOT NULL, PRIMARY KEY (rid, ordinal)
) WITHOUT ROWID;
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY, owner TEXT NOT NULL, ordinal INTEGER NOT NULL, kind TEXT NOT NULL, grp TEXT NOT NULL,
  source TEXT, ts TEXT, start_ms INTEGER, end_ms INTEGER, line_from INTEGER, line_to INTEGER, para_from INTEGER,
  para_to INTEGER, rid TEXT, doc TEXT, rids TEXT NOT NULL, speakers TEXT NOT NULL, authors TEXT NOT NULL, title TEXT,
  locator TEXT NOT NULL, anchors TEXT NOT NULL, text TEXT NOT NULL, text_hash TEXT NOT NULL, UNIQUE (owner, ordinal)
);
CREATE INDEX chunks_ts ON chunks (ts);
CREATE INDEX chunks_grp ON chunks (grp);
CREATE INDEX chunks_rid ON chunks (rid);
CREATE INDEX chunks_doc ON chunks (doc);
CREATE TABLE docs (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, rev INTEGER NOT NULL, path TEXT NOT NULL, title TEXT NOT NULL,
  by TEXT NOT NULL, supersedes TEXT NOT NULL, superseded_by TEXT, entities TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE doc_files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, doc TEXT) WITHOUT ROWID;
CREATE TABLE doc_covers (doc TEXT NOT NULL, rid TEXT NOT NULL, PRIMARY KEY (doc, rid)) WITHOUT ROWID;
CREATE INDEX doc_covers_rid ON doc_covers (rid);
CREATE TABLE old_paths (
  doc TEXT NOT NULL, path TEXT NOT NULL, folded TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (doc, path)
) WITHOUT ROWID;
CREATE INDEX old_paths_folded ON old_paths (folded);
CREATE INDEX old_paths_name ON old_paths (name);
CREATE TABLE doc_anchors (
  doc TEXT NOT NULL, line INTEGER NOT NULL, ordinal INTEGER NOT NULL, rid TEXT NOT NULL, kind TEXT NOT NULL,
  start INTEGER NOT NULL, end INTEGER, PRIMARY KEY (doc, line, ordinal)
) WITHOUT ROWID;
CREATE TABLE entities (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, path TEXT NOT NULL, names TEXT NOT NULL, status TEXT NOT NULL, role TEXT,
  note TEXT
) WITHOUT ROWID;
CREATE TABLE entity_files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE aliases (
  entity TEXT NOT NULL, ordinal INTEGER NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL, lang TEXT,
  needs TEXT NOT NULL, folded TEXT NOT NULL, PRIMARY KEY (entity, ordinal)
) WITHOUT ROWID;
CREATE TABLE alias_terms (
  entity TEXT NOT NULL, ordinal INTEGER NOT NULL, col TEXT NOT NULL, word INTEGER NOT NULL, term TEXT NOT NULL,
  how TEXT NOT NULL, distance INTEGER NOT NULL, PRIMARY KEY (entity, ordinal, col, word, term)
) WITHOUT ROWID;
CREATE TABLE entity_links (
  entity TEXT NOT NULL, type TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY (entity, type, target)
) WITHOUT ROWID;
CREATE TABLE external_ids (
  entity TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (entity, key, value)
) WITHOUT ROWID;
CREATE INDEX external_ids_value ON external_ids (key, value);
CREATE TABLE mentions (
  chunk INTEGER NOT NULL, entity TEXT NOT NULL, how TEXT NOT NULL, alias INTEGER, PRIMARY KEY (chunk, entity)
) WITHOUT ROWID;
CREATE INDEX mentions_entity ON mentions (entity);
CREATE TABLE problems (kind TEXT NOT NULL, place TEXT NOT NULL, message TEXT NOT NULL,
  PRIMARY KEY (kind, place, message)) WITHOUT ROWID;
"""


def check_sqlite() -> None:
    """The SQLite this Python links: new enough for `remove_diacritics 2`, and built with FTS5."""
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        need = ".".join(str(n) for n in MIN_SQLITE)
        raise DocketError(f"search needs SQLite {need} or later; this Python has {sqlite3.sqlite_version}")
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute(f"CREATE VIRTUAL TABLE probe USING fts5(x, tokenize='{TOKENIZER}')")
    except sqlite3.OperationalError:
        raise DocketError("search needs SQLite's FTS5, and this Python's SQLite was built without it") from None
    finally:
        probe.close()


def expected_meta(langs: Languages) -> dict[str, str]:
    return {
        "schema": str(SCHEMA_VERSION),
        "tokenizer": TOKENIZER,
        "languages": ",".join(langs.codes),
        "snowballstemmer": snowball_version(),
    }


def create(conn: sqlite3.Connection, langs: Languages) -> None:
    columns = ", ".join(langs.columns)
    conn.executescript(
        TABLES
        + f"CREATE VIRTUAL TABLE chunks_fts USING fts5({columns}, content='', tokenize='{TOKENIZER}');\n"
        + "CREATE VIRTUAL TABLE vocab USING fts5vocab(chunks_fts, 'col');\n"
    )
    conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", sorted(expected_meta(langs).items()))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def stale(conn: sqlite3.Connection, langs: Languages) -> str | None:
    """Why an index can't be used as it is, or None: another schema, tokenizer, set of languages or stemmer."""
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        stored = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.DatabaseError:
        return "it isn't an index this Docket can read"
    if version != SCHEMA_VERSION:
        return f"it has schema {version}, and this Docket writes {SCHEMA_VERSION}"
    for key, want in expected_meta(langs).items():
        if stored.get(key) != want:
            return f"it was built with {key} {stored.get(key)!r}, and now it is {want!r}"
    return None
