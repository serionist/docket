"""The service's loop on Windows keeps its listening socket after an accept fails (DR-0762), and the CPython behaviour
it relies on: the stock proactor loop accepts through `IocpProactor.accept` and closes the socket on its OSError. A
pin that fails means CPython changed: read `docket.service.accepts` again before trusting it."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import socket
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


def gone() -> OSError:
    return OSError(22, "The specified network name is no longer available", None, GONE)


def test_windows_gets_this_loop_and_every_other_system_uvicorns_own() -> None:
    assert accepts.loop_setup("Windows") == "docket.service.accepts:proactor_loop"
    assert accepts.loop_setup("Linux") == "auto"
    assert accepts.loop_setup("Darwin") == "auto"


# -- accepts that fail, on the proactor --------------------------------------------------------------------------------


Accept = Callable[[Any, socket.socket], "asyncio.Future[Any]"]


def patch_accept(patch: pytest.MonkeyPatch, make: Callable[[Accept], Accept]) -> None:
    """IocpProactor.accept replaced on the class, so the stock loop and KeepAccepting's own super() both reach it."""
    patch.setattr(events.IocpProactor, "accept", make(events.IocpProactor.accept))


def drop_first(patch: pytest.MonkeyPatch) -> list[int]:
    """The first connection accepted is closed and its accept failed, as Windows fails one whose client went while it
    completed; the list gets the port of each accept dropped."""
    dropped: list[int] = []

    def make(real: Accept) -> Accept:
        def accept(proactor: Any, listener: socket.socket) -> asyncio.Future[Any]:
            accepted = real(proactor, listener)
            if dropped:
                return accepted
            dropped.append(listener.getsockname()[1])
            failed: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

            def settle(done: asyncio.Future[Any]) -> None:
                if done.cancelled():
                    failed.cancel()
                elif done.exception() is not None:
                    failed.set_exception(done.exception())  # pyright: ignore[reportArgumentType]
                else:
                    done.result()[0].close()
                    if not failed.done():
                        failed.set_exception(gone())

            accepted.add_done_callback(settle)
            failed.add_done_callback(lambda f: accepted.cancel() if f.cancelled() else None)
            return failed

        return accept

    patch_accept(patch, make)
    return dropped


def fail_every(patch: pytest.MonkeyPatch, *, close: bool = False) -> list[int]:
    """Every accept fails at once, after closing its listening socket when `close`; the list counts them."""
    calls: list[int] = []

    def make(_real: Accept) -> Accept:
        def accept(_proactor: Any, listener: socket.socket) -> asyncio.Future[Any]:
            calls.append(1)
            if close:
                listener.close()
            failed: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            failed.set_exception(gone())
            return failed

        return accept

    patch_accept(patch, make)
    return calls


class Echo(asyncio.Protocol):
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.Transport)
        self.transport = transport

    def data_received(self, data: bytes) -> None:
        self.transport.write(data)
        self.transport.close()


async def ask(port: int) -> bytes | None:
    """One client's ping, and what came back; None when its connection was dropped."""
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
    answers: list[bytes | None]
    # Whether the server's socket was still open after the first client.
    listening: bool
    # What reached the loop's exception handler.
    errors: list[str]


async def two_clients(settle_seconds: float = 0.5) -> Served:
    loop = asyncio.get_running_loop()
    errors: list[str] = []
    loop.set_exception_handler(lambda _loop, context: errors.append(str(context["message"])))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    port = sock.getsockname()[1]
    served = await loop.create_server(Echo, sock=sock)
    try:
        answers = [await ask(port)]
        # The failed accept's callbacks take their turns on the loop: a socket asyncio closes is closed by now.
        deadline = time.monotonic() + settle_seconds
        while sock.fileno() != -1 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        listening = sock.fileno() != -1
        if listening:
            answers.append(await ask(port))
    finally:
        served.close()
        await served.wait_closed()
    return Served(answers, listening, errors)


@windows_only
def test_cpython_closes_the_listening_socket_after_a_failed_accept() -> None:
    """The pin on CPython: the stock loop accepts through IocpProactor.accept and, on its OSError, closes the socket
    for good. If this fails, KeepAccepting may not be needed any more, or may no longer be reached."""
    with pytest.MonkeyPatch.context() as patch:
        dropped = drop_first(patch)
        served = asyncio.run(two_clients(), loop_factory=events.ProactorEventLoop)
    assert len(dropped) == 1
    assert served.answers == [None]
    assert not served.listening
    assert served.errors == ["Accept failed on a socket"]


@windows_only
def test_the_services_loop_keeps_the_socket_and_serves_the_next_client(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    with pytest.MonkeyPatch.context() as patch:
        dropped = drop_first(patch)
        served = asyncio.run(two_clients(), loop_factory=ours["proactor_loop"])
    assert len(dropped) == 1
    assert served.answers == [None, b"ping"]
    assert served.listening
    assert served.errors == []
    warned = [r.getMessage() for r in caplog.records if r.name == "docket.service"]
    assert warned == [f"an accept failed (OSError, Windows error {GONE}); accepting again"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@windows_only
def test_an_accept_that_keeps_failing_is_passed_on_after_its_tries(caplog: pytest.LogCaptureFixture) -> None:
    """A socket that fails every accept is closed as asyncio closes it, for the self-check to end the service."""
    caplog.set_level(logging.WARNING)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(accepts, "PAUSE_SECONDS", 0.001)
        calls = fail_every(patch)
        served = asyncio.run(two_clients(settle_seconds=5), loop_factory=ours["proactor_loop"])
    assert len(calls) == accepts.TRIES
    assert not served.listening
    assert served.errors == ["Accept failed on a socket"]
    warned = [r for r in caplog.records if r.name == "docket.service"]
    assert len(warned) == accepts.TRIES - 1


@windows_only
def test_the_error_of_a_socket_already_closed_goes_on_at_once() -> None:
    with pytest.MonkeyPatch.context() as patch:
        calls = fail_every(patch, close=True)
        served = asyncio.run(two_clients(), loop_factory=ours["proactor_loop"])
    assert calls == [1]
    assert not served.listening
    # asyncio's own path for a closed socket: nothing to report.
    assert served.errors == []


# -- the service, end to end -------------------------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    # conftest's failing keyring backend leaves the file as the store, as on a machine without a keychain.
    Tokens().ensure()
    return empty_vault(tmp_path, make_config()).root


@windows_only
def test_the_service_serves_the_next_request_after_a_failed_accept(vault: Path) -> None:
    servers: list[uvicorn.Server] = []
    with pytest.MonkeyPatch.context() as patch:
        dropped = drop_first(patch)
        runner = threading.Thread(target=server.serve, args=(vault, 0), kwargs={"running": servers.append})
        runner.start()
        try:
            deadline = time.monotonic() + 60
            while (record := client.recorded()) is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert record is not None
            port = int(record["port"])
            assert client.call("GET", "/api/health", at=port) is None
            answer = client.call("GET", "/api/health", at=port)
            assert answer is not None
            assert answer.status == 200
            assert dropped == [port]
        finally:
            while not servers:
                time.sleep(0.05)
            servers[0].should_exit = True
            runner.join(30)
    assert not runner.is_alive()
    written = (userconfig.log_dir() / server.LOG_FILE).read_text(encoding="utf-8")
    assert f"an accept failed (OSError, Windows error {GONE}); accepting again" in written
    assert "Accept failed on a socket" not in written
