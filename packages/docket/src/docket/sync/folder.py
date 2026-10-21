"""Where sync folders live (DR-0746): `<state>/sync/<sync id>/`, beside the service's pid file and `docket open`'s
redirect page, never in the vault and never committed. The id is `sync-<yyyymmddThhmmss>-<8 hex>`, its time in UTC.

`docket sync recordings` adds each recording's files under `recordings/<source>/`, its prompt under
`agents/<source>/` and its block's path under `blocks/<source>/`, and lists them in `recordings/<source>.json`, a file
per source, so two sources' calls at once never lose each other's.

A start removes the folders over 7 days old by their id's time. The forget's apply, on every gate call while there is
a list, removes the folders started before its newest line, since a block or a prompt can quote what it forgets: one
listing of the folder, so a removal a file in use stopped is tried again, and a sync started after the forget stays.
Only a folder named by a sync id is ever removed, never a link. Imports stay light, as the apply reaches this module.

The service reads a batch file for `ledger_batch` by `file` only from a sync folder of its own vault (DR-0724).
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
from collections.abc import Callable
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
RECORDINGS = "recordings"
SCHEMA_VERSION = 1
# A batch file the service reads: a sync's draft is a few hundred lines, so far less.
BATCH_BYTES = 4 * 2**20


class Source(BaseModel):
    """A source of the sync: its run's window as `run_open` would compute it at the start, and its agent's files."""

    source: str
    since: datetime
    until: datetime
    prompt: str
    block: str


class Recording(BaseModel):
    """A recording given an agent of its own: its rid and key, its title and start, its prompt, its block and the
    files that hold it, by absolute path."""

    rid: str
    key: str
    title: str | None
    start: datetime
    prompt: str
    block: str
    files: list[str]


class Recordings(BaseModel):
    """`recordings/<source>.json`: a source's recordings given agents in this sync."""

    schema_version: int = SCHEMA_VERSION
    source: str
    recordings: list[Recording]


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
    for sub in (AGENTS, BLOCKS, RECORDINGS):
        (folder / sub).mkdir(mode=0o700)
    return folder


def clear(now: datetime) -> int:
    """Remove the sync folders over 7 days old by their id's time; how many went."""
    return clear_made(lambda made: now - made > KEEP)


def clear_before(at: datetime) -> int:
    """Remove the sync folders started before `at`, a forget's time, or in its second, which their ids can't tell
    apart; how many went. A folder started later stays."""
    return clear_made(lambda made: made <= at)


def clear_made(gone: Callable[[datetime], bool]) -> int:
    """Remove the sync folders whose id's time `gone` takes; how many went. A name that isn't a sync id and a link are
    left alone, and so is a folder a file in use keeps, until the next try."""
    top = root()
    if _linked(top) or not top.is_dir():
        return 0
    removed = 0
    for path in sorted(top.iterdir()):
        found = ID.match(path.name)
        if found is None or _linked(path) or not path.is_dir():
            continue
        try:
            made = datetime.strptime(found.group(1), STAMP).replace(tzinfo=UTC)
        except ValueError:
            # Digits that aren't a time: not a name a start gives.
            continue
        if not gone(made):
            continue
        try:
            shutil.rmtree(path)
        except OSError:
            continue
        removed += 1
    return removed


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


def next_start(info: Info) -> datetime | None:
    """When the next sync of the same vault on this machine started, after this one, if one did: where this sync's
    writes end. A folder that doesn't read, or is another vault's, is passed over."""
    top = root()
    if _linked(top) or not top.is_dir():
        return None
    later: list[datetime] = []
    for path in top.iterdir():
        if ID.match(path.name) is None or path.name == info.sync or _linked(path) or not (path / INFO).is_file():
            continue
        try:
            other = Info.model_validate_json((path / INFO).read_bytes())
        except ValidationError:
            continue
        if other.made > info.made and _same(Path(other.vault), Path(info.vault)):
            later.append(other.made)
    return min(later, default=None)


def batch_file(given: str, vault_root: Path) -> str:
    """The text of the batch file `ledger_batch` takes by `file` (DR-0724): an absolute path to a file in a sync folder
    of this machine whose `sync.json` names this vault, where it resolves, links followed, and at most 4 MB of UTF-8.
    Any other path is refused, as `media_put` reads only the media folders."""
    path = Path(given)
    if not path.is_absolute():
        raise DocketError(f"file is the batch file's absolute path, not {given!r}")
    top = root()
    outside = DocketError(f"{given} isn't in a sync folder; the service reads a batch file only from one, under {top}")
    if _linked(top):
        raise outside
    try:
        parts = path.resolve().relative_to(top.resolve()).parts
    except ValueError:
        raise outside from None
    if len(parts) < 2 or ID.match(parts[0]) is None:
        raise outside
    folder, _ = find(parts[0], vault_root)
    file = folder.joinpath(*parts[1:])
    if not file.is_file():
        raise DocketError(f"no batch file {given}")
    if file.stat().st_size > BATCH_BYTES:
        raise DocketError(f"{given} is over {BATCH_BYTES:,} bytes, too big for a batch file")
    try:
        return file.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise DocketError(f"{given} isn't UTF-8; write the batch as UTF-8") from None


def recordings_file(path: Path, source: str) -> Path:
    return path / RECORDINGS / f"{source}.json"


def recordings(path: Path, source: str) -> list[Recording]:
    """A source's recordings given agents in this sync folder, none when it has no list; refused when its list
    doesn't read."""
    file = recordings_file(path, source)
    if not file.is_file():
        return []
    try:
        return Recordings.model_validate_json(file.read_bytes()).recordings
    except ValidationError:
        raise DocketError(f"{file} doesn't read; start another sync") from None
