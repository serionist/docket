from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from docket_testkit import index_template
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from starlette.testclient import TestClient

from docket import userconfig
from docket.errors import DocketError
from docket.service import client, logs, server
from docket.service.server import Prepared, bind, prepare, socket_options
from docket.service.token import ROTATE, Tokens


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Path:
    shutil.copytree(template, tmp_path / "vault")
    return tmp_path / "vault"


@pytest.fixture
def token() -> str:
    # conftest's failing keyring backend leaves the file as the store, as on a machine without a keychain.
    return Tokens().ensure().value


@pytest.fixture
def prepared(vault: Path, token: str) -> Iterator[Prepared]:
    found = prepare(vault, 0)
    yield found
    found.close()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# -- prepare: the lock, the token, the socket, the record ------------------------------------------------------------


def test_port_zero_is_bound_by_serve_and_recorded(prepared: Prepared, vault: Path, token: str) -> None:
    assert prepared.port != 0
    record = client.recorded()
    assert record is not None
    assert (record["pid"], record["port"], record["vault"]) == (os.getpid(), prepared.port, str(vault))
    assert token not in client.service_file().read_text(encoding="utf-8")
    ours = TestClient(prepared.app, base_url=f"http://127.0.0.1:{prepared.port}")
    assert ours.get("/api/health").status_code == 200
    assert ours.get("/api/health", headers={"Host": "127.0.0.1:0"}).status_code == 421


def test_closing_puts_back_every_logging_level_the_service_set(vault: Path, token: str) -> None:
    names = ("", "docket", *logs.LIBRARIES)
    logging.getLogger("docket").setLevel(logging.DEBUG)
    before = {name: logging.getLogger(name).level for name in names}
    try:
        ready = prepare(vault, 0)
        assert logging.getLogger("docket").level == logging.INFO
        ready.close()
        assert {name: logging.getLogger(name).level for name in names} == before
    finally:
        logging.getLogger("docket").setLevel(logging.NOTSET)


def test_a_second_service_is_refused_naming_the_first(prepared: Prepared, vault: Path) -> None:
    with pytest.raises(DocketError, match=f"already runs \\(pid {os.getpid()}, port {prepared.port}\\)"):
        prepare(vault, 0)
    # Under pythonw there is no console: the refusal is in the log.
    prepared.handler.flush()
    written = (userconfig.log_dir() / "service.log").read_text(encoding="utf-8")
    assert f"the service didn't start: the service already runs (pid {os.getpid()}" in written


def test_closing_removes_the_record_and_frees_the_lock(vault: Path, token: str) -> None:
    first = prepare(vault, 0)
    first.close()
    assert client.recorded() is None
    second = prepare(vault, 0)
    second.close()


def test_a_record_another_process_wrote_is_left_alone(vault: Path, token: str) -> None:
    ready = prepare(vault, 0)
    client.service_file().write_text(json.dumps({"pid": os.getpid() + 1, "port": 1}), encoding="utf-8")
    ready.close()
    assert client.recorded() == {"pid": os.getpid() + 1, "port": 1}


def test_no_token_stops_serve_with_the_way_out_and_frees_the_lock(vault: Path) -> None:
    with pytest.raises(DocketError, match=ROTATE):
        prepare(vault, 0)
    written = (userconfig.log_dir() / "service.log").read_text(encoding="utf-8")
    assert "the service didn't start: there is no service token yet" in written
    Tokens().ensure()
    prepare(vault, 0).close()


def test_a_port_in_use_is_named(vault: Path, token: str) -> None:
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        taken = holder.getsockname()[1]
        with pytest.raises(DocketError, match=f"port {taken} on 127.0.0.1 is in use"):
            prepare(vault, taken)
    # As the task starts it, under pythonw, the reason has no console: it is in the log.
    written = (userconfig.log_dir() / "service.log").read_text(encoding="utf-8")
    assert f"the service didn't start: port {taken} on 127.0.0.1 is in use" in written
    prepare(vault, 0).close()


def test_another_bind_failure_is_raised_as_it_is(vault: Path, token: str) -> None:
    with pytest.raises(OSError):
        bind(free_port(), options=[(socket.SOL_SOCKET, 0x7FFF, 1)])

    def refused(port: int, options: object = None) -> socket.socket:
        raise OSError(22, "an option the system doesn't take")

    # A context of its own: undoing the test's monkeypatch would also undo conftest's temp folders.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(server, "bind", refused)
        with pytest.raises(OSError):
            prepare(vault, 0)
    prepare(vault, 0).close()


def test_the_socket_options_per_system() -> None:
    assert socket_options("Linux") == [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]
    windows = socket_options("Windows")
    assert windows[0][1] == vars(socket).get("SO_EXCLUSIVEADDRUSE", ~socket.SO_REUSEADDR)


@pytest.mark.skipif(os.name != "nt", reason="Windows lets a port be shared unless the first socket is exclusive")
def test_on_windows_no_other_socket_binds_the_services_port() -> None:
    held = bind(0)
    try:
        port = held.getsockname()[1]
        with socket.socket() as other:
            other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                other.bind(("127.0.0.1", port))
    finally:
        held.close()


def test_the_startup_path_works_without_console_streams(
    vault: Path, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pythonw on Windows: no console, so sys.stdout and sys.stderr are None.
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    ready = prepare(vault, 0)
    ready.close()


# -- serve, in this process --------------------------------------------------------------------------------------------


def test_serve_runs_until_stopped_and_cleans_up(vault: Path, token: str) -> None:
    servers: list[uvicorn.Server] = []
    said: list[str] = []
    runner = threading.Thread(
        target=server.serve, args=(vault, 0), kwargs={"said": said.append, "running": servers.append}
    )
    runner.start()
    try:
        port = _wait_for_port()
        assert said == [f"Docket serves on http://127.0.0.1:{port} (log: {userconfig.log_dir() / 'service.log'})"]
        answer = client.call("GET", "/api/status", token=token, at=port)
        assert answer is not None
        assert (answer.status, answer.body["pid"]) == (200, os.getpid())
    finally:
        while not servers:
            time.sleep(0.05)
        servers[0].should_exit = True
        runner.join(30)
    assert not runner.is_alive()
    assert client.recorded() is None


# -- the self-check ----------------------------------------------------------------------------------------------------


@contextlib.contextmanager
def closed_port() -> Iterator[int]:
    """A port nothing listens on, held bound for the test, so no other test's server takes it meanwhile."""
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        yield int(held.getsockname()[1])


def a_check(port: int, stopped: list[str], ended: list[int], **given: Any) -> server.SelfCheck:
    settings: dict[str, Any] = {"every": 0.01, "timeout": 0.5, "misses": 2, "stop_seconds": 0.05}
    return server.SelfCheck(port, stop=lambda: stopped.append("stop"), end=ended.append, **{**settings, **given})


def test_the_self_check_stops_the_service_and_ends_it_when_nothing_listens_on_its_port(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    stopped: list[str] = []
    ended: list[int] = []
    with closed_port() as port:
        check = a_check(port, stopped, ended)
        check.start()
        check.thread.join(30)
    assert not check.thread.is_alive()
    assert check.failed
    assert stopped == ["stop"]
    # The server didn't stop in its time here, so the process ends, non-zero for the registration to start it again.
    assert ended == [server.UNREACHABLE_EXIT]
    assert server.UNREACHABLE_EXIT != 0
    lines = [r.getMessage() for r in caplog.records if r.name == "docket.service"]
    assert lines == [
        f"the service couldn't reach its own port {port}; it tries again in 0 s",
        f"the service can't reach its own port {port}; it stops, for its registration to start it again",
        "the service didn't stop in 0 s; it ends now",
    ]


def test_a_service_that_stops_in_time_is_not_ended() -> None:
    stopped: list[str] = []
    ended: list[int] = []
    with closed_port() as port:
        check = a_check(port, stopped, ended, stop_seconds=30)
        check.stop = check.close
        check.start()
        check.thread.join(30)
    assert check.failed
    assert ended == []


def test_the_self_check_leaves_a_port_that_answers_and_forgives_one_miss() -> None:
    answers = iter([True, False, True, False, True, True])
    asked: list[int] = []

    def reach(port: int, _timeout: float) -> bool:
        asked.append(port)
        found = next(answers, None)
        if found is None:
            check.close()
            return True
        return found

    stopped: list[str] = []
    ended: list[int] = []
    check = a_check(4321, stopped, ended, reach=reach)
    check.start()
    check.thread.join(30)
    assert len(asked) == 7
    assert set(asked) == {4321}
    assert (check.failed, stopped, ended) == (False, [], [])


def test_a_miss_after_serve_ended_stops_nothing() -> None:
    def reach(_port: int, _timeout: float) -> bool:
        check.close()
        return False

    stopped: list[str] = []
    ended: list[int] = []
    check = a_check(4321, stopped, ended, misses=1, reach=reach)
    check.start()
    check.thread.join(30)
    assert (check.failed, stopped, ended) == (False, [], [])


def test_the_self_check_reaches_a_listening_port_and_misses_a_closed_one() -> None:
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen(1)
        assert server.reachable(listening.getsockname()[1], 5)
    with closed_port() as port:
        assert not server.reachable(port, 0.5)


def test_serve_exits_with_an_error_when_its_port_stops_listening(vault: Path, token: str) -> None:
    servers: list[uvicorn.Server] = []
    raised: list[DocketError] = []

    def run() -> None:
        try:
            server.serve(vault, 0, running=servers.append)
        except DocketError as e:
            raised.append(e)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(server, "CHECK_SECONDS", 0.05)
        patch.setattr(server, "CHECK_TIMEOUT", 0.5)
        runner = threading.Thread(target=run)
        runner.start()
        try:
            port = _wait_for_port()
            # What asyncio's proactor did on Windows after a failed accept: the listening socket closed under a
            # process that went on running.
            listening = servers[0].servers[0]
            listening.get_loop().call_soon_threadsafe(listening.close)
            runner.join(30)
        finally:
            if runner.is_alive():
                servers[0].should_exit = True
                runner.join(30)
    assert not runner.is_alive()
    assert [str(e) for e in raised] == [
        f"the service stopped: it couldn't reach its own port {port}; see {userconfig.log_dir() / 'service.log'}"
    ]
    assert client.recorded() is None
    written = (userconfig.log_dir() / "service.log").read_text(encoding="utf-8")
    assert f"the service can't reach its own port {port}; it stops, for its registration to start it again" in written
    assert "it ends now" not in written


def _wait_for_port(other_process: bool = False) -> int:
    """The port of the service that came up: this test's, or one in another process (on Windows a venv's python.exe
    starts the real interpreter as a child, so its pid isn't the one Popen gives)."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        record = client.recorded()
        if record is not None and (record.get("pid") != os.getpid()) == other_process:
            answer = client.call("GET", "/api/health", at=record["port"])
            if answer is not None and answer.status == 200:
                return int(record["port"])
        time.sleep(0.1)
    raise AssertionError("the service didn't start")


# -- serve, as its own process, end to end -----------------------------------------------------------------------------


def test_the_service_as_a_process_answers_mcp_with_the_token_and_refuses_a_forged_host(vault: Path, token: str) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-m", "docket", "serve", "--port", "0", "--vault", str(vault)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        port = _wait_for_port(other_process=True)
        assert client.call("GET", "/api/status", at=port) == client.Answer(401, {})
        status = client.call("GET", "/api/status", token=token, at=port)
        assert status is not None
        assert status.body["pid"] == client.recorded()["pid"]  # pyright: ignore[reportOptionalSubscript]

        forged = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        forged.request("GET", "/api/health", headers={"Host": "evil.example"})
        assert forged.getresponse().status == 421
        forged.close()

        async def search() -> str:
            http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, trust_env=False)
            async with http, Client(streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http)) as mcp:
                tools = {tool.name for tool in (await mcp.list_tools()).tools}
                assert tools == {
                    "search",
                    "read",
                    "verify",
                    "briefs",
                    "decisions",
                    "doc_put",
                    "run_open",
                    "ingest",
                    "media_put",
                    "transcribe",
                    "ledger",
                    "ledger_batch",
                }
                result = await mcp.call_tool("search", {"query": "fleet dashboard battery"})
                assert not result.is_error
                first = result.content[0]
                assert isinstance(first, TextContent)
                return first.text

        assert "calls:rec-0101" in asyncio.run(search())
    finally:
        proc.terminate()
        proc.wait(30)
    assert client.call("GET", "/api/health", at=port) is None


def test_the_cli_finds_the_port_in_the_record_then_the_config_then_the_default() -> None:
    assert client.port() == client.DEFAULT_PORT
    userconfig.set_service(port=9123)
    assert client.port() == 9123
    client.service_file().parent.mkdir(parents=True, exist_ok=True)
    client.service_file().write_text(json.dumps({"pid": 1, "port": 9456}), encoding="utf-8")
    assert client.port() == 9456
    client.service_file().write_text("[1, 2]", encoding="utf-8")
    assert client.recorded() is None
    assert client.port() == 9123
