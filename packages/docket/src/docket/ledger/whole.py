"""The whole ledger, `docket ledger show --all --json` (DR-0659): every item, meeting and stream that stands, archived
ones too, each as `docket ledger show` gives it, for a script outside Docket, such as an owner's mirror of the ledger
they came from, to render from and to check field by field.

It carries the op files' stamp, each file's path, size and time, taken under the vault's lock before the read, which
runs under it too, so the stamp is the read's; and the lock's file, so the script can take Docket's own lock
(`filelock.FileLock` on that path) and tell, while it holds it, whether a write landed since.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from docket.index.build import ledger_tables
from docket.ledger import ops, views
from docket.ledger.fold import paused
from docket.ledger.views import LedgerRead
from docket.vault.vault import Vault


class OpFile(BaseModel):
    """An op file as the read found it: its path from the vault's root, its size in bytes and its modified time in
    nanoseconds, as `os.stat` gives them."""

    path: str
    size: int
    mtime_ns: int


class Stamp(BaseModel):
    """The op files, in name order, as they stood when the read was taken, and the vault lock's file."""

    files: list[OpFile]
    lock: str


class Whole(BaseModel):
    """Every item, meeting and stream, each a show's result whole: items by the day each opened, meetings by start,
    streams by name; today, the owner's day they were read on; and the stamp."""

    today: date
    stamp: Stamp
    items: list[LedgerRead]
    meetings: list[LedgerRead]
    streams: list[LedgerRead]


def read(vault: Vault, at: datetime) -> Whole:
    """The whole ledger as the op files stand, today the owner's day at `at`: stamped and read under the vault's
    lock, from the index's tables brought up to date or the op files folded in memory (DR-0645). It writes nothing."""
    tz = ZoneInfo(vault.config.owner.time_zone)
    with vault.lock:
        files = [OpFile(path=rel, size=s.st_size, mtime_ns=s.st_mtime_ns)
                 for rel, path in ops.listing(vault.root)[0] for s in (path.stat(),)]  # fmt: skip
        # The collector paused: a read of every object makes a hundred thousand models, none of them in a cycle.
        with ledger_tables(vault) as conn, paused():
            items, meetings, streams = views.every(conn, tz, at)
    stamp = Stamp(files=files, lock=str(vault.lock.path))
    return Whole(today=at.astimezone(tz).date(), stamp=stamp, items=items, meetings=meetings, streams=streams)


def as_json(whole: Whole) -> str:
    """The JSON the command prints: compact, for a script, at half the size indented; ASCII only, as every read's, so
    a title's text can't put a terminal code on the screen."""
    return whole.model_dump_json(ensure_ascii=True)
