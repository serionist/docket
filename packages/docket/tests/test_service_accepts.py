"""The service's loop on Windows keeps its listening socket after an accept fails (DR-0762) and closes a connection its
client reset, and the CPython behaviour it relies on: the stock proactor loop accepts through `IocpProactor.accept`,
closes the socket on its OSError, reports the task that accept starts beside it by its name, and leaves a connection
open when the shutdown before its close fails. A pin that fails means CPython changed: read `docket.service.accepts`
again before trusting it.

The failed accepts are real: while the loop is blocked, one client takes the accept already posted and three more
reset in the backlog, so each accept the loop posts next fails as Windows fails one whose client went. The failed
shutdown is made, as a browser's reset fails one only in a race."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import gc
import importlib
import logging
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from docket_testkit import empty_vault, make_config

from docket import userconfig
from docket.service import accepts, client, server
from docket.service.token import Tokens

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="the proactor loop is Windows' own")
# Looked up, not imported: neither exists for the type checker on another system.
events: Any = importlib.import_module("asyncio.windows_events") if WINDOWS else None
ours: Any = vars(accepts)

# ERROR_NETNAME_DELETED: what Windows fails an accept with when its client went while it completed.
GONE = 64
# IocpProactor.accept leaves the socket it made for an accept that failed to the collector, which closes it with a
# ResourceWarning: stock asyncio's way, here as in any loop on Windows, so only those warnings are let through. The
# service's loop makes that socket an Accepted.
left_to_the_collector = pytest.mark.filterwarnings(
    # Python 3.12's words, then 3.14's.
    "ignore:Exception ignored (in. |while finalizing socket )<(socket.socket|docket.service.accepts.Accepted) "
    ":pytest.PytestUnraisableExceptionWarning"
)


def gone() -> OSError:
    return OSError(22, "The specified network name is no longer available", None, GONE)


def test_windows_gets_this_loop_and_every_other_system_uvicorns_own() -> None:
    assert accepts.loop_setup("Windows") == "docket.service.accepts:proactor_loop"
    assert accepts.loop_setup("Linux") == "auto"
    assert accepts.loop_setup("Darwin") == "auto"


# -- accepts that fail, on the proactor --------------------------------------------------------------------------------


def reset(port: int) -> None:
    """A client that connects and resets: SO_LINGER on with no time, so its close sends a reset."""
    with socket.socket() as gone_client:
        gone_client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0))
        gone_client.connect(("127.0.0.1", port))


def clients_that_go(port: int) -> socket.socket:
    """Run while the loop is blocked: one connection takes the accept already posted, then three reset in the backlog,
    so the next accepts fail with ERROR_NETNAME_DELETED. The held connection is the caller's to close."""
    held = socket.create_connection(("127.0.0.1", port), timeout=5)
    for _ in range(3):
        reset(port)
    return held


class Echo(asyncio.Protocol):
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.Transport)
        self.transport = transport

    def data_received(self, data: bytes) -> None:
        self.transport.write(data)
        self.transport.close()


async def ask(port: int) -> bytes | None:
    """One client's ping, and what came back; None when its connection was refused or dropped."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 5)
    except (OSError, TimeoutError):
        return None
    try:
        writer.write(b"ping")
        await writer.drain()
        answer = await asyncio.wait_for(reader.read(100), 5)
    except (OSError, TimeoutError):
        answer = b""
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
    return answer or None


@dataclass
class Served:
    # The ping after the failed accepts, when the socket was still listening.
    answer: bytes | None
    # Whether the server's socket was still open after them.
    listening: bool
    # What reached the loop's exception handler: the message, and the coroutine of the task it is about.
    reported: list[tuple[str, str | None]]


Reported = list[tuple[str, str | None]]


def collect(reported: Reported) -> Callable[[asyncio.AbstractEventLoop, dict[str, Any]], None]:
    def handler(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        task: Any = context.get("future")
        coro = task.get_coro() if isinstance(task, asyncio.Task) else None
        reported.append((str(context["message"]), getattr(coro, "__qualname__", None)))

    return handler


async def listening_after(sock: socket.socket, seconds: float) -> bool:
    """Whether the socket is still open once the failed accepts' callbacks had their turns, or `seconds` went by."""
    deadline = time.monotonic() + seconds
    while sock.fileno() != -1 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    return sock.fileno() != -1


def listener(loop: asyncio.AbstractEventLoop, reported: Reported) -> socket.socket:
    loop.set_exception_handler(collect(reported))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    return sock


async def clients_go_then_one_asks() -> Served:
    loop = asyncio.get_running_loop()
    reported: Reported = []
    sock = listener(loop, reported)
    port = sock.getsockname()[1]
    served = await loop.create_server(Echo, sock=sock)
    await asyncio.sleep(0.1)
    # The loop is blocked from here until the sleep ends: the resets land with no accept posted.
    held = clients_that_go(port)
    time.sleep(0.3)
    try:
        listening = await listening_after(sock, 0.5)
        answer = await ask(port) if listening else None
    finally:
        held.close()
        served.close()
        await served.wait_closed()
    # A task whose error nobody read is reported when it is collected.
    gc.collect()
    return Served(answer, listening, reported)


async def accepts_that_fail(settle_seconds: float) -> Served:
    loop = asyncio.get_running_loop()
    reported: Reported = []
    sock = listener(loop, reported)
    served = await loop.create_server(Echo, sock=sock)
    try:
        listening = await listening_after(sock, settle_seconds)
    finally:
        served.close()
        await served.wait_closed()
    return Served(None, listening, reported)


@windows_only
@left_to_the_collector
def test_cpython_closes_the_listening_socket_after_a_failed_accept() -> None:
    """The pin on CPython: the stock loop, on an accept's OSError, closes the socket for good and reports the task
    IocpProactor.accept started beside it by the name KeepAcceptingLoop leaves out. If this fails, the fix may not be
    needed any more, or may no longer be reached."""
    served = asyncio.run(clients_go_then_one_asks(), loop_factory=events.ProactorEventLoop)
    assert not served.listening
    assert ("Accept failed on a socket", None) in served.reported
    assert (ours["NEVER_RETRIEVED"], ours["ACCEPT_TASK"]) in served.reported


@windows_only
@left_to_the_collector
def test_the_services_loop_keeps_the_socket_and_serves_the_next_client(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    served = asyncio.run(clients_go_then_one_asks(), loop_factory=ours["proactor_loop"])
    assert served.listening
    assert served.answer == b"ping"
    assert served.reported == []
    warned = [r.getMessage() for r in caplog.records if r.name == "docket.service"]
    assert warned
    assert set(warned) == {f"an accept failed (OSError, Windows error {GONE}); accepting again"}
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def fail_every(patch: pytest.MonkeyPatch, *, close: bool = False) -> list[int]:
    """IocpProactor.accept, on the class so KeepAccepting's super() reaches it, failing every accept at once, after
    closing its listening socket when `close`; the list counts them."""
    calls: list[int] = []

    def accept(_proactor: Any, listening: socket.socket) -> asyncio.Future[Any]:
        calls.append(1)
        if close:
            listening.close()
        failed: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        failed.set_exception(gone())
        return failed

    patch.setattr(events.IocpProactor, "accept", accept)
    return calls


@windows_only
def test_an_accept_that_keeps_failing_is_passed_on_after_its_tries(caplog: pytest.LogCaptureFixture) -> None:
    """A socket that fails every accept is closed as asyncio closes it, for the self-check to end the service."""
    caplog.set_level(logging.WARNING)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(accepts, "PAUSE_SECONDS", 0.001)
        calls = fail_every(patch)
        served = asyncio.run(accepts_that_fail(5), loop_factory=ours["proactor_loop"])
    assert len(calls) == accepts.TRIES
    assert not served.listening
    assert served.reported == [("Accept failed on a socket", None)]
    warned = [r for r in caplog.records if r.name == "docket.service"]
    assert len(warned) == accepts.TRIES - 1


@windows_only
def test_the_error_of_a_socket_already_closed_goes_on_at_once() -> None:
    with pytest.MonkeyPatch.context() as patch:
        calls = fail_every(patch, close=True)
        served = asyncio.run(accepts_that_fail(0.5), loop_factory=ours["proactor_loop"])
    assert calls == [1]
    assert not served.listening
    # asyncio's own path for a closed socket: nothing to report.
    assert served.reported == []


@windows_only
def test_the_loop_reports_every_other_error_nobody_read() -> None:
    async def fails() -> None:
        raise gone()

    async def main() -> Reported:
        reported: Reported = []
        asyncio.get_running_loop().set_exception_handler(collect(reported))
        task = asyncio.ensure_future(fails())
        await asyncio.sleep(0.05)
        assert task.done()
        del task
        gc.collect()
        return reported

    reported = asyncio.run(main(), loop_factory=ours["proactor_loop"])
    assert reported == [(ours["NEVER_RETRIEVED"], "test_the_loop_reports_every_other_error_nobody_read.<locals>.fails")]


# -- connections a client reset, on the proactor -----------------------------------------------------------------------

# WSAECONNRESET: what Windows fails a shutdown with on a connection its client reset.
RESET = 10054


class Resets:
    """socket.socket.shutdown failing, while `failing`, as Windows fails it on a connection a browser reset; `asked`
    counts the calls."""

    def __init__(self, patch: pytest.MonkeyPatch) -> None:
        self.failing = True
        self.asked = 0
        real = socket.socket.shutdown

        def shutdown(sock: socket.socket, how: int) -> None:
            self.asked += 1
            if self.failing:
                raise ConnectionResetError(
                    errno.ECONNRESET, "An existing connection was forcibly closed by the remote host", None, RESET
                )
            real(sock, how)

        patch.setattr(socket.socket, "shutdown", shutdown)


class Held(asyncio.Protocol):
    """The server's side of a connection, kept for the test to close."""

    def __init__(self, made: list[asyncio.Transport]) -> None:
        self.made = made

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.Transport)
        self.made.append(transport)


@dataclass
class Closed:
    # Whether the accepted connection's socket was still open after its transport closed.
    left_open: bool
    # Whether the server's wait_closed() returned once the server closed too.
    stopped: bool
    reported: Reported


async def a_reset_connection_closed(resets: Resets) -> Closed:
    loop = asyncio.get_running_loop()
    reported: Reported = []
    loop.set_exception_handler(collect(reported))
    made: list[asyncio.Transport] = []
    served = await loop.create_server(lambda: Held(made), "127.0.0.1", 0)
    port = served.sockets[0].getsockname()[1]
    with socket.create_connection(("127.0.0.1", port), timeout=5):
        deadline = time.monotonic() + 5
        while not made and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        transport: Any = made[0]
        accepted = transport.get_extra_info("socket")
        transport.close()
        await asyncio.sleep(0.1)
    left_open = accepted.fileno() != -1
    served.close()
    try:
        await asyncio.wait_for(served.wait_closed(), 2)
        stopped = True
    except TimeoutError:
        stopped = False
    if left_open:
        # CPython's leak undone, so the test leaves no socket behind: the transport's last step again, with a shutdown
        # that works, closes the socket and lets the server go.
        resets.failing = False
        transport._call_connection_lost(None)
        await asyncio.wait_for(served.wait_closed(), 5)
    return Closed(left_open, stopped, reported)


@windows_only
def test_cpython_leaves_a_connection_open_when_its_shutdown_fails() -> None:
    """The pin on CPython: the transport shuts its socket down before it closes it and detaches it from its server, so
    a shutdown that raises leaves the socket open and the server's wait_closed() waiting for good, which kept uvicorn's
    stop from ending. If this fails, Accepted may not be needed any more."""
    with pytest.MonkeyPatch.context() as patch:
        resets = Resets(patch)
        closed = asyncio.run(a_reset_connection_closed(resets), loop_factory=events.ProactorEventLoop)
    assert resets.asked >= 1
    assert closed.left_open
    assert not closed.stopped
    assert [m for m, _ in closed.reported if "_call_connection_lost" in m] != []


@windows_only
def test_the_services_loop_closes_a_connection_its_client_reset() -> None:
    with pytest.MonkeyPatch.context() as patch:
        resets = Resets(patch)
        closed = asyncio.run(a_reset_connection_closed(resets), loop_factory=ours["proactor_loop"])
    assert resets.asked >= 1
    assert not closed.left_open
    assert closed.stopped
    assert closed.reported == []


@windows_only
def test_an_accepted_socket_s_shutdown_still_raises_every_other_error() -> None:
    with ours["Accepted"](socket.AF_INET) as never_connected, pytest.raises(OSError) as raised:
        never_connected.shutdown(socket.SHUT_RDWR)
    assert not isinstance(raised.value, ConnectionError)


# -- the service, end to end -------------------------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    # conftest's failing keyring backend leaves the file as the store, as on a machine without a keychain.
    Tokens().ensure()
    return empty_vault(tmp_path, make_config()).root


@windows_only
@left_to_the_collector
def test_the_service_serves_the_next_request_after_failed_accepts(vault: Path) -> None:
    servers: list[uvicorn.Server] = []
    runner = threading.Thread(target=server.serve, args=(vault, 0), kwargs={"running": servers.append})
    runner.start()
    try:
        deadline = time.monotonic() + 60
        while (record := client.recorded()) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert record is not None
        port = int(record["port"])
        first = client.call("GET", "/api/health", at=port)
        assert first is not None
        assert first.status == 200
        blocked, release = threading.Event(), threading.Event()

        def block() -> None:
            blocked.set()
            release.wait(10)

        servers[0].servers[0].get_loop().call_soon_threadsafe(block)
        assert blocked.wait(10)
        held = clients_that_go(port)
        time.sleep(0.3)
        release.set()
        try:
            answer = client.call("GET", "/api/health", at=port)
            assert answer is not None
            assert answer.status == 200
        finally:
            held.close()
        gc.collect()
    finally:
        while not servers:
            time.sleep(0.05)
        servers[0].should_exit = True
        runner.join(30)
    assert not runner.is_alive()
    written = (userconfig.log_dir() / server.LOG_FILE).read_text(encoding="utf-8")
    assert f"an accept failed (OSError, Windows error {GONE}); accepting again" in written
    assert "Accept failed on a socket" not in written
    assert "never retrieved" not in written
