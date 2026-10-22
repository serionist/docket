"""The loop the service runs on: on Windows one that keeps its listening socket after an accept fails, elsewhere
uvicorn's own choice, unchanged (DR-0762).

asyncio's proactor loop, Windows' own, closes the listening socket on any OSError from an accept (`_start_serving` in
`asyncio/proactor_events.py`), and a client that goes while its accept completes is enough to raise one: the process
lives, its task says Running, and nothing answers on the port again. Here the proactor tries the accept again on the
same socket, so `_start_serving` only sees a connection, a cancel, or the error of a socket already closed; and the
loop leaves out the "Task exception was never retrieved" asyncio logs for the task each accept starts beside it, whose
error is the accept's own, logged once here.

The proactor also makes each accepted connection's socket, of a class whose shutdown doesn't fail on a connection its
client already broke off (DR-0787). asyncio's transport shuts its socket down before it closes it and detaches it from
its server (`_call_connection_lost`), and on a connection a browser reset that shutdown raises WinError 10054: the
socket stays open, and the server's `wait_closed()` waits on it for good, so uvicorn's stop never ends and the process
outlives it.

`test_service_accepts.py` holds CPython to what this relies on: `_start_serving` accepts through the proactor's
`accept` and closes the socket on its error, that task's name, and the transport's shutdown before its close.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import sys
from typing import Any, cast

from docket.service.system import SYSTEM

log = logging.getLogger("docket.service")

# A failed accept is tried again after a pause, up to this many times in a row. A socket that fails them all is
# closed as asyncio closes it, and the self-check then ends the service for its registration to start again.
TRIES = 10
PAUSE_SECONDS = 0.1
# What asyncio reports when it collects a task whose exception nobody read, and the coroutine IocpProactor.accept starts
# beside each accept, which raises that accept's error again.
NEVER_RETRIEVED = "Task exception was never retrieved"
ACCEPT_TASK = "IocpProactor.accept.<locals>.accept_coro"


def loop_setup(system: str = SYSTEM) -> str:
    """uvicorn's `loop`: this module's loop on Windows, uvicorn's own choice on every other system."""
    return f"{__name__}:proactor_loop" if system == "Windows" else "auto"


if sys.platform == "win32":  # pragma: no cover - Windows' own loop; test_service_accepts runs it there
    from asyncio.windows_events import IocpProactor, ProactorEventLoop

    class Accepted(socket.socket):
        """An accepted connection's socket, whose shutdown doesn't fail on a connection its client broke off."""

        __slots__ = ()

        def shutdown(self, how: int) -> None:
            # The connection is gone already (a reset is WinError 10054), so there is nothing to shut down, and the
            # transport's close and its server's detach come right after this call only if it returns.
            with contextlib.suppress(ConnectionError):
                super().shutdown(how)

    class KeepAccepting(IocpProactor):
        """Windows' proactor, with an accept that fails tried again on the same listening socket, and each accepted
        connection's socket an Accepted."""

        loop: asyncio.AbstractEventLoop

        def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
            super().set_loop(loop)
            self.loop = loop

        def _get_accept_socket(self, family: int) -> socket.socket:
            # IocpProactor.accept asks here for the socket it accepts a connection into: made as CPython makes it, an
            # Accepted.
            sock = Accepted(family)
            sock.settimeout(0)
            return sock

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

    class KeepAcceptingLoop(ProactorEventLoop):
        """Windows' proactor loop on KeepAccepting, without asyncio's report of each failed accept's own task."""

        def call_exception_handler(self, context: dict[str, Any]) -> None:
            if not accepts_own_task(context):
                super().call_exception_handler(context)

    def accepts_own_task(context: dict[str, Any]) -> bool:
        found: object = context.get("future")
        task = cast("asyncio.Task[Any]", found) if isinstance(found, asyncio.Task) else None
        return (
            context.get("message") == NEVER_RETRIEVED
            and isinstance(context.get("exception"), OSError)
            and task is not None
            and getattr(task.get_coro(), "__qualname__", None) == ACCEPT_TASK
        )

    def proactor_loop() -> asyncio.AbstractEventLoop:
        return KeepAcceptingLoop(KeepAccepting())
