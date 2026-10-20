"""Where sync folders live (DR-0720): `<state>/sync/<sync id>/`, beside the service's pid file and `docket open`'s
redirect page, never in the vault and never committed. The id is `sync-<yyyymmddThhmmss>-<8 hex>`, its time in UTC.

A start removes the folders over 7 days old by their id's time; a forget applied on this machine removes all of them,
since a block or a prompt can quote what it forgets. Only a folder named by a sync id is ever removed, never a link.
Imports stay light: the forget's apply reaches this module on every gate call that applies a new line.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ValidationError

from docket import paths
from docket.errors import DocketError

FOLDER = "sync"
ID = re.compile(r"^sync-(\d{8}T\d{6})-[0-9a-f]{8}$")
STAMP = "%Y%m%dT%H%M%S"
KEEP = timedelta(days=7)
INFO = "sync.json"
CONTEXT = "context.md"
AGENTS = "agents"
BLOCKS = "blocks"
NOTES = "notes"
SCHEMA_VERSION = 1


class Source(BaseModel):
    """A source of the sync: its run's window as `run_open` would compute it at the start, and its agent's files."""

    source: str
    since: datetime
    until: datetime
    prompt: str
    block: str


class Info(BaseModel):
    """`sync.json`: the sync's id, the vault's root, when it was made, the owner's day and zone, what the owed check
    covers (the run's window, or a day), each source, and the Docket that made it."""

    schema_version: int = SCHEMA_VERSION
    sync: str
    vault: str
    made: datetime
    today: date
    zone: str
    owed_since: str
    sources: list[Source]
    docket: str


def root() -> Path:
    return paths.state_dir() / FOLDER


def new_id(now: datetime) -> str:
    return f"sync-{now.astimezone(UTC):{STAMP}}-{secrets.token_hex(4)}"


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def make(sync_id: str) -> Path:
    """A new sync folder with its three subfolders, only its owner's on macOS and Linux; refused when the folder of
    sync folders is a link, so nothing is written through one."""
    top = root()
    if _linked(top):
        raise DocketError(f"{top} is a link; Docket writes sync folders only into a plain folder")
    folder = top / sync_id
    folder.mkdir(mode=0o700, parents=True)
    for sub in (AGENTS, BLOCKS, NOTES):
        (folder / sub).mkdir(mode=0o700)
    return folder


def clear(now: datetime, *, every: bool = False) -> int:
    """Remove the sync folders over 7 days old by their id's time, or with `every` all of them; how many went. A name
    that isn't a sync id and a link are left alone, and so is a folder a file in use keeps, until the next try."""
    top = root()
    if _linked(top) or not top.is_dir():
        return 0
    gone = 0
    for path in sorted(top.iterdir()):
        found = ID.match(path.name)
        if found is None or _linked(path) or not path.is_dir():
            continue
        try:
            made = datetime.strptime(found.group(1), STAMP).replace(tzinfo=UTC)
        except ValueError:
            # Digits that aren't a time: not a name a start gives.
            continue
        if not every and now - made <= KEEP:
            continue
        try:
            shutil.rmtree(path)
        except OSError:
            continue
        gone += 1
    return gone


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(a.resolve()) == os.path.normcase(b.resolve())


def find(sync: str, vault_root: Path) -> tuple[Path, Info]:
    """A sync folder by its id, or its path as `docket sync start` printed it, and its `sync.json`; refused when there
    is none on this machine, it doesn't read, or it is another vault's."""
    name = Path(sync).name
    if ID.match(name) is None:
        raise DocketError(f"{sync!r} isn't a sync: give its id, sync-<time>-<hex>, as docket sync start printed it")
    folder = root() / name
    if name != sync and not _same(Path(sync), folder):
        raise DocketError(f"{sync} isn't where this machine keeps its sync folders, {root()}")
    path = folder / INFO
    if _linked(folder) or not path.is_file():
        raise DocketError(f"no sync {name} on this machine: a start makes one, and one over 7 days old is gone")
    try:
        info = Info.model_validate_json(path.read_bytes())
    except ValidationError:
        raise DocketError(f"{name}'s {INFO} doesn't read; start another sync") from None
    if not _same(Path(info.vault), vault_root):
        raise DocketError(f"{name} is a sync of {info.vault}, not of {vault_root}")
    return folder, info
