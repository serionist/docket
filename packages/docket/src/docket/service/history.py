"""The run history (DR-0301): one line per ended sync run, so the Sync page shows recent runs across a restart.

`<private>/sync/runs.jsonl`, beside `folders.json` in the vault's private folder: per machine, never committed. A line
holds ids, counts, times and Docket's own reason, never a capture's arguments or result, a title, a session or an
agent id. Only the service writes it, a whole line at a time under this module's lock, off the writer thread: no
other Docket writes it, and it holds nothing the vault keeps.

It is disposable. Nothing decides from it; windows, marks and the retry and waiting sets read the cursor files.
Deleting it loses only the page's list, a line that doesn't read is skipped, and a later format starts a new file.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from docket.errors import DocketError
from docket.vault.home import Home, make_private
from docket.vault.staging import write_replacing

log = logging.getLogger("docket.service.history")

FILE = "sync/runs.jsonl"
# The newest runs kept, in memory and on disk; the file is rewritten with them once it holds twice as many lines.
KEEP = 500
TRIM_AT = 2 * KEEP
WHY_CHARS = 500
IDS = 20

# How a run ended (DR-0302): a whole close, or the way it dropped.
CLOSED = "closed"
UNBOUND = "unbound"
IDLE = "idle"
TOO_LONG = "too-long"
REPLACED = "replaced"
STOPPED = "stopped"
SHORT = "short"
COUNT = "count"
MAPPER = "mapper"
REFUSED = "refused"
FAILED = "failed"
DROPS = (UNBOUND, IDLE, TOO_LONG, REPLACED, STOPPED, SHORT, COUNT, MAPPER, REFUSED, FAILED)


class Staged(BaseModel):
    model_config = ConfigDict(frozen=True)

    pending: int = 0
    ready: int = 0
    skipped: int = 0
    on_hold: int = 0


class Mark(BaseModel):
    model_config = ConfigDict(frozen=True)

    before: datetime | None = None
    after: datetime | None = None
    moved: bool = False


class Counts(BaseModel):
    """Retry and waiting ids: handed to a run at its open, or owed after its close."""

    model_config = ConfigDict(frozen=True)

    retry: int = 0
    waiting: int = 0


class SetAside(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str | None
    reason: str
    tool: str | None = None
    key: str | None = None


@dataclass(frozen=True)
class Closing:
    """What a whole close adds to its run's line, from its `RunClosed`."""

    staged: Staged
    dropped: dict[str, int]
    set_aside: list[SetAside]
    waiting: list[str]
    mark: Mark
    cursor_problem: str | None
    owed: Counts
    expired: list[str]
    held_in_raw: list[str]


class RunLine(BaseModel):
    """One ended run. A line of another version, or one that doesn't read, is skipped."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    v: Literal[1] = 1
    run: str
    source: str
    final: bool = False
    backfill: bool = False
    opened: datetime
    ended: datetime
    since: datetime | None = None
    until: datetime | None = None
    outcome: Literal["closed", "dropped"]
    kind: str
    why: str | None = None
    captures: int = 0
    bytes: int = 0
    told_to_skip: int = 0
    held: int = 0
    handed: Counts = Counts()
    skipped: dict[str, int] = {}
    staged: Staged | None = None
    dropped: dict[str, int] = {}
    set_aside: list[SetAside] = []
    set_aside_count: int = 0
    waiting: list[str] = []
    waiting_count: int = 0
    mark: Mark | None = None
    cursor_problem: str | None = None
    owed: Counts | None = None
    expired: list[str] = []
    expired_count: int = 0
    held_in_raw: list[str] = []
    held_in_raw_count: int = 0

    @property
    def whole(self) -> bool:
        return self.outcome == "closed"


def capped_why(why: str | None) -> str | None:
    if why is None or len(why) <= WHY_CHARS:
        return why
    return why[: WHY_CHARS - 1] + "…"


def first_ids(ids: Iterable[str]) -> list[str]:
    return sorted(ids)[:IDS]


class RunHistory:
    """The recent runs, newest last: read once from the file, then kept in step with it."""

    def __init__(self, home_of: Callable[[], Home] | None) -> None:
        self._home_of = home_of
        self._lock = threading.Lock()
        self._lines: deque[RunLine] = deque(maxlen=KEEP)
        self._home: Home | None = None
        self._on_disk = 0
        self._loaded = False
        # Why the history isn't kept across a restart, when it isn't.
        self.problem: str | None = None

    def load(self) -> None:
        with self._lock:
            self._where()

    def add(self, line: RunLine) -> None:
        """Keep a run's line in memory, and append it to the file; a write that fails is named, never raised."""
        with self._lock:
            home = self._where()
            self._lines.append(line)
            if home is None:
                return
            try:
                self._append(home, line)
            except (DocketError, OSError) as e:
                self._failed(e)

    def recent(self, source: str | None = None, k: int = 20) -> list[RunLine]:
        """The newest k runs, newest first, of one source or of all. A file that couldn't be read at start, such as a
        vault.yaml with a typo since fixed, is read now."""
        with self._lock:
            self._where()
            lines = [line for line in reversed(self._lines) if source is None or line.source == source]
        return lines[:k]

    def find(self, run: str) -> RunLine | None:
        with self._lock:
            self._where()
            return next((line for line in reversed(self._lines) if line.run == run), None)

    # -- inside ----------------------------------------------------------------------------------------------------

    def _where(self) -> Home | None:
        """Where the vault keeps its private files, once it opens; the file is read into memory the first time."""
        if self._home_of is None:
            return None
        if self._home is None:
            # Any failure, not only Docket's: a vault.yaml that doesn't decode must not stop the service starting.
            try:
                self._home = self._home_of()
            except Exception as e:
                self._failed(e)
                return None
            self.problem = None
        if not self._loaded:
            self._read(self._home.private / FILE)
            self._loaded = True
        return self._home

    def _read(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
        except (OSError, UnicodeDecodeError) as e:
            self._failed(e)
            return
        read: list[RunLine] = []
        lines = text.splitlines()
        for raw in lines:
            try:
                read.append(RunLine.model_validate_json(raw))
            except ValueError:  # pydantic's ValidationError among them
                continue
        # Lines from runs that ended before the file could be read go after the older ones.
        self._lines = deque([*read, *self._lines], maxlen=KEEP)
        self._on_disk = len(lines)

    def _append(self, home: Home, line: RunLine) -> None:
        path = home.private / FILE
        make_private(home.git_dir, path.parent)
        if path.is_symlink():
            raise DocketError(f"{path} is a link; the run history is a plain file in the vault's private folder")
        if self._on_disk + 1 > TRIM_AT:
            text = "".join(kept.model_dump_json() + "\n" for kept in self._lines)
            write_replacing(home, path, text, "sync-runs")
            self._on_disk = len(self._lines)
        else:
            with path.open("a", encoding="utf-8", newline="\n") as out:
                out.write(line.model_dump_json() + "\n")
            self._on_disk += 1
        self.problem = None

    def _failed(self, error: Exception) -> None:
        # Docket's own message, else the type only: an OS error's message names a path or what it held.
        why = str(error) if isinstance(error, DocketError) else type(error).__name__
        # Once per problem: the page asks every few seconds while a run is open.
        if why != self.problem:
            log.warning("the run history can't be kept: %s", type(error).__name__)
        self.problem = why
