"""One lock per vault, around every write and commit, so two processes never write it at once."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from types import TracebackType

from filelock import FileLock, Timeout

from docket.errors import DocketError

log = logging.getLogger(__name__)

# A git that crashed leaves .git/index.lock behind, and every later git command then fails. One older than this was
# not left by a git still running.
STALE_INDEX_LOCK_SECONDS = 600


class VaultLock:
    def __init__(self, git_dir: Path, timeout: float = 60.0) -> None:
        self.git_dir = git_dir
        self._lock = FileLock(str(git_dir / "docket.lock"), timeout=timeout)

    def __enter__(self) -> VaultLock:
        try:
            self._lock.acquire()
        except Timeout as e:
            raise DocketError(f"another docket process holds {self.git_dir / 'docket.lock'}; try again") from e
        clear_stale_index_lock(self.git_dir)
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._lock.release()


def clear_stale_index_lock(git_dir: Path, now: float | None = None) -> bool:
    path = git_dir / "index.lock"
    try:
        age = (now if now is not None else time.time()) - path.stat().st_mtime
    except FileNotFoundError:
        return False
    if age < STALE_INDEX_LOCK_SECONDS:
        return False
    log.warning("removing %s, left by a git that stopped %d minutes ago", path, age // 60)
    path.unlink(missing_ok=True)
    return True
