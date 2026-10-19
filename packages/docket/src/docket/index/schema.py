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
# 7: each record version's byte offset in its run file (DR-0445); a line that isn't UTF-8 and a record in another
# source's folder are problems (DR-0446).
# 8: an author written Name <id> mentions the entity whose ids for the record's source hold the id (DR-0471).
# 9: derived_from and doc_lines, what each line cites and every doc's citable lines (DR-0542).
# 10: a decision's status in docs, and decision_lines, the decisions each of a brief's Decisions lines cites, so
# a search hit marks one that cites no confirmed decision (DR-0565).
# 11: the ledger folded from its ops, in items, item_log, item_numbers and ledger_files (DR-0616).
# 12: meetings, points, streams and their logs and numbers, out of item_log, and an item's meeting (DR-0628).
SCHEMA_VERSION = 12
TOKENIZER = "unicode61 remove_diacritics 2"
# remove_diacritics 2 came in SQLite 3.27.
MIN_SQLITE = (3, 27, 0)
# The meta key of the forgotten list's digest: a cache's key like the rest (DR-0115), so it needs no new schema. Nor
# does `skipped`, what the list took from raw: only an index built under a list that takes something reads or writes
# it, and an index from before the list is rebuilt under one.
FORGOTTEN = "forgotten"

TABLES = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE run_files (path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE records (
  rid TEXT PRIMARY KEY, v TEXT NOT NULL, versions INTEGER NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL,
  container TEXT, thread TEXT, ts TEXT NOT NULL, author TEXT, title TEXT, lang TEXT, engine TEXT,
  fidelity TEXT NOT NULL, grp TEXT NOT NULL, path TEXT NOT NULL, line INTEGER NOT NULL
) WITHOUT ROWID;
CREATE INDEX records_thread ON records (thread);
CREATE INDEX records_container ON records (container);
CREATE TABLE record_versions (
  rid TEXT NOT NULL, v TEXT NOT NULL, path TEXT NOT NULL, line INTEGER NOT NULL, offset INTEGER NOT NULL,
  PRIMARY KEY (rid, path, line)
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
  by TEXT NOT NULL, supersedes TEXT NOT NULL, superseded_by TEXT, entities TEXT NOT NULL, status TEXT
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
CREATE TABLE derived_from (
  doc TEXT NOT NULL, line INTEGER NOT NULL, ordinal INTEGER NOT NULL, src TEXT NOT NULL, rev INTEGER NOT NULL,
  src_line INTEGER NOT NULL, hash TEXT NOT NULL, PRIMARY KEY (doc, line, ordinal)
) WITHOUT ROWID;
CREATE INDEX derived_from_src ON derived_from (src);
CREATE TABLE doc_lines (doc TEXT NOT NULL, hash TEXT NOT NULL, line INTEGER NOT NULL, PRIMARY KEY (doc, hash, line))
  WITHOUT ROWID;
CREATE TABLE decision_lines (doc TEXT NOT NULL, line INTEGER NOT NULL, cites TEXT NOT NULL, PRIMARY KEY (doc, line))
  WITHOUT ROWID;
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
CREATE TABLE skipped (rid TEXT PRIMARY KEY, parts INTEGER NOT NULL) WITHOUT ROWID;
"""

# The ledger's tables (DR-0616, DR-0628), which a write also builds in memory when the index can't be had. The logs
# keep rowids: their rows hold each op's line, too long for a table without them. A meeting's or a stream's log row
# keeps the key its add carries and the op a link names (a continues, a from, a to), so an update and a write find the
# meetings an op touches from the rows alone.
LEDGER_TABLES = """
CREATE TABLE ledger_files (
  path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL, highest TEXT
) WITHOUT ROWID;
CREATE TABLE items (
  id TEXT PRIMARY KEY, number INTEGER, claimed INTEGER NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
  who TEXT NOT NULL, stream TEXT NOT NULL, source TEXT NOT NULL, meeting TEXT, opened TEXT NOT NULL, due TEXT,
  description TEXT, notes TEXT, outcome TEXT, state TEXT NOT NULL, until TEXT, closed TEXT, triage TEXT,
  archived INTEGER NOT NULL, materials TEXT NOT NULL, added_by TEXT NOT NULL, last_op TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX items_claimed ON items (claimed);
CREATE TABLE item_log (
  item TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL, after TEXT, day TEXT NOT NULL, at TEXT,
  by TEXT NOT NULL, via TEXT NOT NULL, kind TEXT NOT NULL, batch TEXT, keys TEXT NOT NULL, line TEXT,
  undone_by TEXT, fate TEXT NOT NULL, PRIMARY KEY (item, id, version)
);
CREATE INDEX item_log_id ON item_log (id);
CREATE TABLE item_numbers (
  number INTEGER NOT NULL, item TEXT NOT NULL, claim TEXT NOT NULL, hidden INTEGER NOT NULL, PRIMARY KEY (number, item)
) WITHOUT ROWID;
CREATE INDEX item_numbers_item ON item_numbers (item);
CREATE TABLE meetings (
  id TEXT PRIMARY KEY, number INTEGER, claimed INTEGER NOT NULL, event TEXT, series TEXT, start TEXT NOT NULL,
  "end" TEXT NOT NULL, utc TEXT NOT NULL, day TEXT NOT NULL, subject TEXT NOT NULL, organiser TEXT, "where" TEXT,
  response TEXT, attendees TEXT NOT NULL, stream TEXT, notes TEXT, outcome TEXT, materials TEXT NOT NULL,
  archived INTEGER NOT NULL, adds TEXT NOT NULL, added_by TEXT NOT NULL, last_op TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX meetings_utc ON meetings (utc);
CREATE TABLE points (
  id TEXT PRIMARY KEY, meeting TEXT NOT NULL, n INTEGER, text TEXT NOT NULL, state TEXT NOT NULL, answer TEXT,
  item TEXT, "from" TEXT, "to" TEXT, proposed INTEGER NOT NULL, moved_to TEXT, day TEXT NOT NULL, by TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX points_meeting ON points (meeting);
CREATE TABLE meeting_log (
  item TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL, meeting TEXT NOT NULL, after TEXT, day TEXT NOT NULL,
  at TEXT, by TEXT NOT NULL, via TEXT NOT NULL, kind TEXT NOT NULL, batch TEXT, keys TEXT NOT NULL, line TEXT,
  undone_by TEXT, fate TEXT NOT NULL, key TEXT, link TEXT, PRIMARY KEY (item, id, version)
);
CREATE INDEX meeting_log_id ON meeting_log (id);
CREATE INDEX meeting_log_key ON meeting_log (key) WHERE key IS NOT NULL;
CREATE INDEX meeting_log_link ON meeting_log (link) WHERE link IS NOT NULL;
CREATE TABLE meeting_numbers (
  number INTEGER NOT NULL, meeting TEXT NOT NULL, claim TEXT, held TEXT, hidden INTEGER NOT NULL,
  PRIMARY KEY (number, meeting)
) WITHOUT ROWID;
CREATE INDEX meeting_numbers_meeting ON meeting_numbers (meeting);
CREATE TABLE streams (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL, "where" TEXT, next TEXT, deadline TEXT,
  updated TEXT NOT NULL, adds TEXT NOT NULL, added_by TEXT NOT NULL, last_op TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE stream_log (
  item TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL, stream TEXT NOT NULL, after TEXT, day TEXT NOT NULL,
  at TEXT, by TEXT NOT NULL, via TEXT NOT NULL, kind TEXT NOT NULL, batch TEXT, keys TEXT NOT NULL, line TEXT,
  undone_by TEXT, fate TEXT NOT NULL, key TEXT, PRIMARY KEY (item, id, version)
);
CREATE INDEX stream_log_id ON stream_log (id);
CREATE INDEX stream_log_key ON stream_log (key) WHERE key IS NOT NULL;
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


def expected_meta(langs: Languages, forgotten: str = "") -> dict[str, str]:
    """What an index is built with, `forgotten` the digest of the forgotten list it was built under (DR-0593)."""
    return {
        "schema": str(SCHEMA_VERSION),
        "tokenizer": TOKENIZER,
        "languages": ",".join(langs.codes),
        "snowballstemmer": snowball_version(),
        FORGOTTEN: forgotten,
    }


def create(conn: sqlite3.Connection, langs: Languages, forgotten: str = "") -> None:
    columns = ", ".join(langs.columns)
    conn.executescript(
        TABLES
        + LEDGER_TABLES
        + f"CREATE VIRTUAL TABLE chunks_fts USING fts5({columns}, content='', tokenize='{TOKENIZER}');\n"
        + "CREATE VIRTUAL TABLE vocab USING fts5vocab(chunks_fts, 'col');\n"
    )
    conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", sorted(expected_meta(langs, forgotten).items()))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def stale(conn: sqlite3.Connection, langs: Languages, forgotten: str = "") -> str | None:
    """Why an index can't be used as it is, or None: another schema, tokenizer, set of languages or stemmer, or built
    under another forgotten list than the one in force (DR-0593)."""
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        stored = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.DatabaseError:
        return "it isn't an index this Docket can read"
    if version != SCHEMA_VERSION:
        return f"it has schema {version}, and this Docket writes {SCHEMA_VERSION}"
    for key, want in expected_meta(langs, forgotten).items():
        # An index from before the list has no digest, and was built under none.
        have = stored.get(key, "" if key == FORGOTTEN else None)
        if have == want:
            continue
        if key == FORGOTTEN:
            return "it was built before a forget"
        return f"it was built with {key} {have!r}, and now it is {want!r}"
    return None
