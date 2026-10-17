"""What may leave the machine for an API provider (#89, DR-0538): the screen a chunk, a query and a rerank pass before
anything is sent, under the vault's settings in force (#134), read again on every call.

- Chunks come only from the index, which never reads `staging/` (`build.py`).
- A chunk is held back unless every record it comes from belongs to a source in the settings in force that isn't
  private: raw is written once, so a source made private later still has its records here. A record's source is its
  rid's prefix; a source that is there but off may send, since off pauses capture and judges nothing.
- A chunk is held back when one of its records, or for a doc's chunk the records its doc covers, matches the
  never-store lists as intake reads a record, when one can't be read here, when its title or text says a listed
  keyword, or when the redactor of the settings in force would change its input, its title or its text: intake
  redacts each field on its own, so an anchored pattern meets a text that the joined input hides. A doc that covers no
  record at all is screened on its text alone.
- A query is held back when it holds a value from any never-store list, or the redactor would change it.
- A rerank sends only chunks holding a vector in the API's table while the table's fingerprint is the current
  settings' one: the embedding pass wrote a vector only for a chunk it passed, and screens every chunk again when the
  fingerprint moves.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from collections.abc import Generator, Iterable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from docket.errors import DocketError
from docket.index import vectors
from docket.index.vectors import VecTable
from docket.vault import inforce, neverstore
from docket.vault.config import VaultConfig
from docket.vault.redact import Redactor
from docket.vault.spans import RawLookup
from docket.vault.vault import Vault

HELD_QUERY = "the query names something the vault's settings keep on this machine, so it isn't sent to the API"
STALE = "the vault's settings changed since its chunks were checked, so the reranker waits for the next embedding pass"
UNREADABLE = "the vault's settings can't be read, so nothing is sent to the API"


# The settings in force as one search or one batch read them, for every check it makes: a search asks about five
# times, two of them inside the rerank's deadline, and each read would open vault.yaml again.
_READ: ContextVar[tuple[Path, VaultConfig] | None] = ContextVar("docket_screen_settings", default=None)


def not_allowed(provider: str) -> str:
    return f"this vault doesn't allow sending its text to {provider}: docket models allow {provider}, at your console"


def fingerprint(config: VaultConfig) -> str:
    """The settings a chunk was screened under: the never-store lists, the redaction settings, and each source's
    presence, sensitivity and `enabled`. A vector table keeps the one its vectors passed."""
    data = {
        "never_store": config.never_store.model_dump(mode="json"),
        "redaction": config.redaction.model_dump(mode="json"),
        "sources": {name: [s.sensitivity, s.enabled] for name, s in sorted(config.sources.items())},
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Row:
    """A chunk as the screen reads it: its id, title and text, the input sent for it, its records and its doc."""

    chunk: int
    title: str | None
    text: str
    input: str
    rids: tuple[str, ...]
    doc: str | None


class Screen:
    """The screen for one vault, one provider and its vector table, with the settings in force read on each call."""

    def __init__(self, vault: Vault, provider: str, table: VecTable) -> None:
        self.root = vault.root
        self.home = vault.home
        self.provider = provider
        self.table = table

    def settings(self) -> VaultConfig:
        """The settings in force: as the search or the batch around this call read them, else read now. Read as
        `Vault.open` reads them (DR-0521), without opening the rest of the vault, and with no git call while HEAD
        stays."""
        held = _READ.get()
        if held is not None and held[0] == self.root:
            return held[1]
        return inforce.load(self.root, self.home).in_force

    @contextlib.contextmanager
    def reading(self) -> Generator[VaultConfig | None]:
        """Read the settings in force once for every check inside: a search's, or a batch's of the embedding pass.
        Settings that don't read leave every check inside to say so."""
        try:
            config = inforce.load(self.root, self.home).in_force
        except (DocketError, OSError):
            yield None
            return
        token = _READ.set((self.root, config))
        try:
            yield config
        finally:
            _READ.reset(token)

    def allowed(self) -> str | None:
        """Why the vault doesn't let its text go to the provider now, or None (DR-0537)."""
        try:
            config = self.settings()
        except (DocketError, OSError):
            return UNREADABLE
        return None if config.api_provider == self.provider else not_allowed(self.provider)

    def query(self, text: str) -> str | None:
        """Why a query stays on this machine, never naming what matched; None when it may go."""
        try:
            config = self.settings()
        except (DocketError, OSError):
            return UNREADABLE
        if says_listed(text, config) or Redactor.from_config(config.redaction).text(text) != text:
            return HELD_QUERY
        return None

    def held(self, conn: sqlite3.Connection, rows: Sequence[Row], config: VaultConfig, lookup: RawLookup) -> set[int]:
        """The chunks of `rows` the settings in force keep on this machine."""
        redactor = Redactor.from_config(config.redaction)
        return {row.chunk for row in rows if _holds(conn, row, config, redactor, lookup)}

    def lookup(self) -> RawLookup:
        """Records by rid, for one pass: each source's run files read once."""
        return RawLookup(self.root)

    def sendable(self, conn: sqlite3.Connection, chunks: Sequence[int]) -> set[int] | str:
        """The chunks a rerank may send: those holding a vector in the table, while the table's fingerprint is the
        settings in force's on this search, computed now and never kept; or why none may. Their sources are checked
        again, from the rows alone: an update gives a new chunk the vector of another with the same input, and the new
        one's records can come from a source the pass never saw it under."""
        try:
            config = self.settings()
            if vectors.screened(conn, self.table) != fingerprint(config):
                return STALE
            having = vectors.having(conn, self.table, chunks)
            return {row.chunk for row in rows(conn, self.table, having) if _sources_kept(conn, row, config) is not None}
        except (DocketError, OSError, sqlite3.Error):
            return UNREADABLE


def of(models: object) -> Screen | None:
    """An API provider's screen, when the models have one."""
    guard = getattr(models, "guard", None)
    return guard if isinstance(guard, Screen) else None


def says_listed(text: str, config: VaultConfig) -> bool:
    """Whether a query holds a value from a never-store list: the people and keywords lists as an entity's value is
    held to them, on the whole query and, for people, on each word and pair of neighbouring words; the containers list
    on each word."""
    lists = config.never_store
    if neverstore.listed(text, lists) is not None:
        return True
    words = text.split()
    pieces = [*words, *(f"{a} {b}" for a, b in pairwise(words))]
    if any(neverstore.listed(piece, lists) == "people" for piece in pieces):
        return True
    return any(neverstore.in_container(word, lists) for word in words)


def rows(conn: sqlite3.Connection, table: VecTable, chunks: Iterable[int]) -> list[Row]:
    """The chunks as the screen reads them, with each one's input for the table."""
    ids = list(chunks)
    out: list[Row] = []
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        marks = ", ".join("?" * len(part))
        for chunk, title, text, rids, doc in conn.execute(
            f"SELECT id, title, text, rids, doc FROM chunks WHERE id IN ({marks}) ORDER BY id",  # noqa: S608 - placeholders
            part,
        ):
            found: object = json.loads(rids) if rids else []
            named = tuple(str(r) for r in cast(list[object], found) if r) if isinstance(found, list) else ()
            out.append(Row(chunk, title, text, table.input(title, text), named, doc))
    return out


def _covered(conn: sqlite3.Connection, doc: str) -> list[str]:
    return [str(rid) for (rid,) in conn.execute("SELECT rid FROM doc_covers WHERE doc = ?", (doc,))]


def _sources_kept(conn: sqlite3.Connection, row: Row, config: VaultConfig) -> list[str] | None:
    """The chunk's records by rid, or None when one comes from a source that isn't in the settings in force or is
    private there: a record's source is its rid's prefix, read without the record."""
    rids = sorted({*row.rids, *(_covered(conn, row.doc) if row.doc else [])} - {""})
    for rid in rids:
        entry = config.sources.get(rid.partition(":")[0] if ":" in rid else "")
        if entry is None or entry.sensitivity == "private":
            return None
    return rids


def _holds(conn: sqlite3.Connection, row: Row, config: VaultConfig, redactor: Redactor, lookup: RawLookup) -> bool:
    rids = _sources_kept(conn, row, config)
    if rids is None:
        return True
    for rid in rids:
        try:
            record = lookup.latest(rid)
        except (OSError, ValueError, ValidationError, DocketError):
            return True
        if record is None or neverstore.match(record, config.never_store) is not None:
            return True
        # Each field on its own, as intake redacts it: a pattern anchored on a record's text meets it there, though
        # the chunk's text starts with the record's title.
        if redactor.record(record)[1].total():
            return True
    texts = [t for t in (row.title, row.text) if t]
    if any(neverstore.matches_text(t, config.never_store) for t in texts):
        return True
    return any(redactor.text(t) != t for t in (row.input, *texts))
