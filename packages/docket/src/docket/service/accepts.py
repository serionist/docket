"""The loop the service runs on: on Windows one that keeps its listening socket after an accept fails, elsewhere
uvicorn's own choice, unchanged (DR-0762).

asyncio's proactor loop, Windows' own, closes the listening socket on any OSError from an accept (`_start_serving` in
`asyncio/proactor_events.py`), and a client that goes while its accept completes is enough to raise one: the process
lives, its task says Running, and nothing answers on the port again. Here the proactor tries the accept again on the
same socket, so `_start_serving` only sees a connection, a cancel, or the error of a socket already closed.
`test_service_accepts.py` holds CPython to what this relies on: `_start_serving` accepts through the proactor's
`accept`, and closes the socket on its error.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
from typing import Any

from docket.service.system import SYSTEM

log = logging.getLogger("docket.service")

# A failed accept is tried again after a pause, up to this many times in a row. A socket that fails them all is
# closed as asyncio closes it, and the self-check then ends the service for its registration to start again.
TRIES = 10
PAUSE_SECONDS = 0.1


def loop_setup(system: str = SYSTEM) -> str:
    """uvicorn's `loop`: this module's loop on Windows, uvicorn's own choice on every other system."""
    return f"{__name__}:proactor_loop" if system == "Windows" else "auto"


if sys.platform == "win32":  # pragma: no cover - Windows' own loop; test_service_accepts runs it there
    from asyncio.windows_events import IocpProactor, ProactorEventLoop

    class KeepAccepting(IocpProactor):
        """Windows' proactor, with an accept that fails tried again on the same listening socket."""

        loop: asyncio.AbstractEventLoop

        def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
            super().set_loop(loop)
            self.loop = loop

        def accept(self, listener: socket.socket) -> asyncio.Future[Any]:
            # A task is a future: _start_serving keeps it, cancels it when the server closes, and reads its result.
            return self.loop.create_task(self._accept(listener))

        async def _accept(self, listener: socket.socket) -> Any:
            tried = 1
            while True:
                try:
                    return await super().accept(listener)
                except OSError as e:
                    # A closed socket's error goes on as it is, as does the last of a run that never accepted.
                    if listener.fileno() == -1 or tried >= TRIES:
                        raise
                    log.warning(
                        "an accept failed (%s, Windows error %s); accepting again", type(e).__name__, e.winerror
                    )
                tried += 1
                await asyncio.sleep(PAUSE_SECONDS)

    def proactor_loop() -> asyncio.AbstractEventLoop:
        return ProactorEventLoop(KeepAccepting())
