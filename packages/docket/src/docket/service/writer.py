"""How the service runs its writes and keeps its index current.

Writes go through one thread, one at a time, in the order they came; reads and searches run beside them. Every
write also takes the vault's file lock, as the command line's do, so the service and the CLI never write at once.

Index updates run outside the write queue, since `index.update()` takes only the index's own lock: one at a time, at
start, after each of the service's writes and before each search. A search waits for an update that started after it
asked, so it finds what the CLI wrote a moment ago; searches arriving together share one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from docket.errors import DocketError

log = logging.getLogger(__name__)

# How long a search waits for its index update before it says the index is being built (a first build of a large
# vault takes minutes).
UPDATE_WAIT_SECONDS = 30.0
# What a search past that wait answers; `docket search` reads it to say why it searches by itself.
BUILDING = "the index is being built; try again in a minute"


class IndexBuildingError(DocketError):
    """A search waited its while and the index update goes on: a state to wait out, not a failure."""


class Writer:
    """One thread for every write the service makes."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="docket-writer")

    async def run[T](self, fn: Callable[[], T]) -> T:
        return await asyncio.wrap_future(self._pool.submit(fn))

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)


class IndexUpdater:
    """Runs `update` one at a time; a caller waits only for an update that started after it asked."""

    def __init__(self, update: Callable[[], object], wait_seconds: float = UPDATE_WAIT_SECONDS) -> None:
        self.update = update
        self.wait_seconds = wait_seconds
        self.runs = 0
        self._current: asyncio.Task[None] | None = None
        self._next: asyncio.Future[None] | None = None

    def poke(self) -> asyncio.Future[None]:
        """Ask for an update that starts after now; one already asked for and not started yet is shared."""
        if self._next is None:
            self._next = asyncio.get_running_loop().create_future()
        waiting = self._next
        if self._current is None:
            self._start(waiting)
        return waiting

    async def fresh(self) -> None:
        """Wait for an update that started after this call. Past the wait, the update goes on and the caller hears
        that the index is being built."""
        waiting = self.poke()
        try:
            await asyncio.wait_for(asyncio.shield(waiting), self.wait_seconds)
        except TimeoutError:
            raise IndexBuildingError(BUILDING) from None

    async def idle(self) -> None:
        """Wait until no update runs or waits to run: for a clean stop, and for tests."""
        while self._current is not None:
            await asyncio.shield(self._current)

    def _start(self, waiting: asyncio.Future[None]) -> None:
        self._next = None
        self._current = asyncio.ensure_future(self._run(waiting))

    async def _run(self, waiting: asyncio.Future[None]) -> None:
        try:
            self.runs += 1
            await asyncio.to_thread(self.update)
        except Exception as e:
            # A failed update leaves the old index to search. The type only: a message can quote a vault's content.
            log.warning("the index update failed: %s", type(e).__name__)
        finally:
            waiting.set_result(None)
            self._current = None
            if self._next is not None:
                self._start(self._next)
