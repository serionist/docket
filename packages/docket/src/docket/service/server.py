"""`docket serve`: one service per user, bound to 127.0.0.1 on one port.

It takes a lock in the state folder, so a second one names the pid that runs; reads the token from the store install
recorded and never makes one; binds the socket itself, so the app's allowed hosts name the port it really got (also
with `--port 0`); records its pid and port in `service.json`; and hands the socket to uvicorn, on the loop `accepts`
chooses. While it serves, a self-check connects to its own port, and ends it when nothing listens there any more, so
its registration starts it again (DR-0762).
"""

from __future__ import annotations

import errno
import json
import logging
import os
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from filelock import FileLock, Timeout

from docket import __version__, userconfig
from docket.errors import DocketError
from docket.service import accepts, client, logs
from docket.service.app import ServiceApp, create_app
from docket.service.state import Settings
from docket.service.system import SYSTEM
from docket.service.token import Tokens

log = logging.getLogger("docket.service")

HOST = "127.0.0.1"
LOCK_FILE = "service.lock"
LOG_FILE = "service.log"
# The self-check: a connect to the service's own port this often, given this long; this many misses in a row end the
# service, and it gets this long to stop before the process ends without it.
CHECK_SECONDS = 30.0
CHECK_TIMEOUT = 5.0
CHECK_MISSES = 2
STOP_SECONDS = 30.0
# What the process exits with when the self-check ends it without the server stopping: what a DocketError from serve
# exits with, which every registration starts again after (DR-0154).
UNREACHABLE_EXIT = 2


@dataclass
class Prepared:
    """A service ready to run: its lock held, its socket bound, its app built, its record written."""

    app: ServiceApp
    sock: socket.socket
    port: int
    lock: FileLock
    handler: logs.ServiceLog

    def close(self) -> None:
        mine = client.recorded()
        if mine is not None and mine.get("pid") == os.getpid():
            client.service_file().unlink(missing_ok=True)
        self.sock.close()
        self.lock.release()
        _drop(self.handler)


def socket_options(system: str = SYSTEM) -> list[tuple[int, int, int]]:
    """On Windows the service takes its port for itself, so no other process binds it beside the service; elsewhere
    it may reuse a port a stopped service left in TIME_WAIT, which never lets two listen."""
    if system == "Windows":
        # SO_EXCLUSIVEADDRUSE is ~SO_REUSEADDR in Winsock; the socket module names it only on Windows.
        exclusive: int = vars(socket).get("SO_EXCLUSIVEADDRUSE", ~socket.SO_REUSEADDR)
        return [(socket.SOL_SOCKET, exclusive, 1)]
    return [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]


# EADDRINUSE and EACCES, and Winsock's own numbers for them.
IN_USE = {errno.EADDRINUSE, errno.EACCES, 10048, 10013}


def bind(port: int, options: list[tuple[int, int, int]] | None = None) -> socket.socket:
    """A listening socket on 127.0.0.1 only."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        for level, name, value in socket_options() if options is None else options:
            sock.setsockopt(level, name, value)
        sock.bind((HOST, port))
        sock.listen(128)
    except OSError as e:
        sock.close()
        if e.errno in IN_USE or getattr(e, "winerror", None) in IN_USE:
            raise DocketError(
                f"port {port} on {HOST} is in use by another program; free it, or give Docket another with "
                "`docket service install --port <n>` (and the same in Claude Code's MCP config)"
            ) from None
        raise
    return sock


def prepare(vault: Path | None = None, port: int | None = None, *, tokens: Tokens | None = None) -> Prepared:
    settings = userconfig.service()
    vault = vault or settings.vault
    wanted = port if port is not None else (settings.port or client.DEFAULT_PORT)
    state = userconfig.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    # The log first: under pythonw on Windows a refusal has no console to go to, and the log is where status sends
    # the user. A refusal names only a port, a pid or the token's store, never content.
    handler = logs.configure(userconfig.log_dir() / LOG_FILE)
    lock = FileLock(str(state / LOCK_FILE), timeout=0)
    try:
        try:
            lock.acquire()
        except Timeout:
            running = client.recorded() or {}
            raise DocketError(
                f"the service already runs (pid {running.get('pid', '?')}, port {running.get('port', '?')}); "
                "`docket service status` says more"
            ) from None
        try:
            store = tokens or Tokens()
            token = store.read()
            sock = bind(wanted)
        except BaseException:
            lock.release()
            raise
    except DocketError as e:
        log.error("the service didn't start: %s", e)
        _drop(handler)
        raise
    except BaseException:
        _drop(handler)
        raise
    actual: int = sock.getsockname()[1]
    started = datetime.now(UTC)
    app = create_app(Settings(port=actual, vault=vault, token=lambda: token, store=store.where(), started=started))
    record = {
        "pid": os.getpid(),
        "port": actual,
        "vault": str(vault) if vault else None,
        "version": __version__,
        "started": started.isoformat(),
    }
    target = client.service_file()
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(target)
    log.info("serving on %s:%d", HOST, actual)
    return Prepared(app=app, sock=sock, port=actual, lock=lock, handler=handler)


def _drop(handler: logs.ServiceLog) -> None:
    logs.release(handler)


def reachable(port: int, timeout: float) -> bool:
    """Whether something listens on the service's port: a connect, closed at once, so no request is logged."""
    try:
        with socket.create_connection((HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


class SelfCheck:
    """A thread that connects to the service's own port every `every` seconds. After `misses` in a row it logs, has the
    server stop, and ends the process if the server hasn't stopped in `stop_seconds`: a service that lives but takes no
    connection is restarted by nothing, one that exits is started again by its registration."""

    def __init__(
        self,
        port: int,
        *,
        stop: Callable[[], None],
        every: float,
        timeout: float,
        misses: int,
        stop_seconds: float,
        reach: Callable[[int, float], bool] = reachable,
        end: Callable[[int], object] = os._exit,
    ) -> None:
        self.port = port
        self.stop = stop
        self.every = every
        self.timeout = timeout
        self.misses = misses
        self.stop_seconds = stop_seconds
        self.reach = reach
        self.end = end
        self.failed = False
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, name="docket-self-check", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.done.set()

    def _run(self) -> None:
        missed = 0
        while not self.done.wait(self.every):
            if self.reach(self.port, self.timeout):
                missed = 0
                continue
            missed += 1
            if self.done.is_set():
                return
            if missed < self.misses:
                log.warning("the service couldn't reach its own port %d; it tries again in %d s", self.port, self.every)
                continue
            self.failed = True
            log.error(
                "the service can't reach its own port %d; it stops, for its registration to start it again", self.port
            )
            self.stop()
            if not self.done.wait(self.stop_seconds):
                log.error("the service didn't stop in %d s; it ends now", self.stop_seconds)
                self.end(UNREACHABLE_EXIT)
            return


def config(app: ServiceApp) -> uvicorn.Config:
    """How uvicorn serves the app: no logging setup or access log of its own, h11, no websockets, the app's lifespan,
    and the loop `accepts` chooses for this system."""
    return uvicorn.Config(
        app,
        log_config=None,
        access_log=False,
        server_header=False,
        http="h11",
        ws="none",
        lifespan="on",
        loop=accepts.loop_setup(),
    )


def serve(
    vault: Path | None = None,
    port: int | None = None,
    *,
    said: Callable[[str], None] = lambda _line: None,
    running: Callable[[uvicorn.Server], None] = lambda _server: None,
) -> None:
    """Run the service until it is stopped. `running` gets the server, so a caller can stop it. A DocketError when the
    self-check stopped it, so the process exits non-zero."""
    ready = prepare(vault, port)
    try:
        said(f"Docket serves on http://{HOST}:{ready.port} (log: {userconfig.log_dir() / LOG_FILE})")
        server = uvicorn.Server(config(ready.app))

        def stop() -> None:
            server.should_exit = True

        check = SelfCheck(
            ready.port,
            stop=stop,
            every=CHECK_SECONDS,
            timeout=CHECK_TIMEOUT,
            misses=CHECK_MISSES,
            stop_seconds=STOP_SECONDS,
        )
        running(server)
        check.start()
        try:
            server.run(sockets=[ready.sock])
        finally:
            check.close()
        if check.failed:
            raise DocketError(
                f"the service stopped: it couldn't reach its own port {ready.port}; see "
                f"{userconfig.log_dir() / LOG_FILE}"
            )
    finally:
        ready.close()
