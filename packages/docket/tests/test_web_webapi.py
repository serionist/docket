"""The browser's own JSON routes under /api/web (DR-0709): the shell's read on the invented working week devkit ui
serves, the session cookie only, and the OpenAPI document the app's types come from."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import ui_world
from docket_testkit import copied
from docket_webkit import OURS, PORT, TOKEN
from starlette.testclient import TestClient
from starlette.types import Message, Scope

from docket import paths
from docket.errors import DocketError
from docket.ledger import day as ledger_day
from docket.service.app import ServiceApp, create_app
from docket.service.sessions import COOKIE, Sessions
from docket.service.state import Settings
from docket.service.writer import IndexUpdater
from docket.vault.gate import Gate
from docket.vault.staging import Staging
from docket.vault.vault import Vault
from docket.web import webapi

SCHEMA = Path(__file__).parents[1] / "web" / "openapi.json"


def world(tmp_path: Path, name: str) -> Path:
    return copied(tmp_path, lambda d: ui_world.build(d / "vault", name).root, f"ui-world-{name}")


def client_of(vault: Path | None, *, signed: bool = True) -> TestClient:
    sessions = Sessions()
    app = create_app(Settings(port=PORT, vault=vault, token=lambda: TOKEN), sessions=sessions)
    client = TestClient(app, base_url=OURS, follow_redirects=False)
    if signed:
        started = sessions.redeem(sessions.new_code())
        assert started is not None
        client.cookies.set(COOKIE, started)
    return client


@pytest.fixture
def week(tmp_path: Path) -> Iterator[TestClient]:
    with client_of(world(tmp_path, "week")) as client:
        yield client


def test_the_shell_counts_the_week_s_untriaged_items_waiting_records_and_due_syncs(week: TestClient) -> None:
    answer = week.get("/api/web/shell")
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "no-store"
    shell = answer.json()
    # day_kit's seed: seven items, one done and one snoozed, so five open, one of them with a call; then the week's
    # fourteen more, twelve open, eight of them with a call, and one whose snooze ended yesterday, untriaged;
    # staging_kit's three records, read as the files stand.
    assert shell["untriaged"] == 9
    assert shell["staging"] == 3
    assert shell["sync_due"] == ["chat"], "the live source, never synced"
    assert shell["vault"] == "vault" and shell["machine"] == "test-box", "the machine the tests name"
    assert shell["zone"] == "Europe/Budapest"
    assert shell["problem"] is None
    assert shell["index_line"]


def test_an_empty_world_counts_nothing_and_its_live_source_is_due(tmp_path: Path) -> None:
    with client_of(world(tmp_path, "empty")) as client:
        shell = client.get("/api/web/shell").json()
    assert (shell["untriaged"], shell["staging"], shell["sync_due"]) == (0, 0, ["chat"])


def test_a_service_without_a_vault_says_why_and_counts_nothing() -> None:
    with client_of(None) as client:
        shell = client.get("/api/web/shell").json()
    assert shell["problem"].startswith("the service has no vault")
    assert (shell["vault"], shell["untriaged"], shell["staging"], shell["sync_due"]) == (None, None, None, [])


def test_a_vault_that_doesn_t_read_says_why(tmp_path: Path) -> None:
    with client_of(tmp_path / "gone") as client:
        shell = client.get("/api/web/shell").json()
    assert shell["problem"] and shell["vault"] is None


def files(*roots: Path) -> dict[Path, tuple[int, int]]:
    """Every file under the roots, with its size and modification time."""
    return {p: (p.stat().st_size, p.stat().st_mtime_ns) for root in roots for p in root.rglob("*") if p.is_file()}


def test_the_shell_s_read_writes_nothing_and_leaves_the_sweep_to_the_staging_page(tmp_path: Path) -> None:
    vault = Vault.open(world(tmp_path, "week"))
    # A record decided by hand before a crash let its staged file go: the gate's sweep would delete it now.
    staging = Staging(vault.root, vault.home)
    first = staging.scan().entries[0]
    staging.remember([(first.staged.record.rid, first.key)], "private")
    state = paths.state_dir()
    client = client_of(vault.root)
    app = client.app
    assert isinstance(app, ServiceApp)
    # The service's own index update at start writes the index; the read alone is what is watched here.
    app.service.updater = IndexUpdater(lambda: None)
    with client:
        before = files(vault.root, state)
        shell = client.get("/api/web/shell").json()
        assert files(vault.root, state) == before, "the shell's read wrote nothing: no sweep, no lock, no state"
        assert shell["staging"] == 3, "the staged files as they stand"
        # The Staging page's own read sweeps, and the shell counts what it left.
        assert len(Gate(vault).list().entries) == 2
        assert client.get("/api/web/shell").json()["staging"] == 2


def test_a_part_that_doesn_t_read_leaves_its_count_out(week: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*_args: object, **_kwargs: object) -> None:
        raise DocketError("the forgotten list doesn't read")

    def unreadable(*_args: object, **_kwargs: object) -> None:
        raise OSError("staging doesn't read")

    monkeypatch.setattr(ledger_day, "read", refused)
    monkeypatch.setattr(Staging, "scan", unreadable)
    shell = week.get("/api/web/shell").json()
    assert (shell["untriaged"], shell["staging"], shell["sync_due"]) == (None, None, [])
    assert shell["problem"] is None


def test_the_routes_take_the_browser_s_session_only(tmp_path: Path) -> None:
    vault = world(tmp_path, "empty")
    with client_of(vault, signed=False) as stranger:
        assert stranger.get("/api/web/shell").status_code == 401
        token = stranger.get("/api/web/shell", headers={"Authorization": f"Bearer {TOKEN}"})
        assert token.status_code == 403, "the token is Claude's and the CLI's, never a page's"
        assert "session only" in token.json()["detail"]


def through_the_cap(headers: dict[str, str], chunks: list[bytes]) -> tuple[int, int, bytes | None]:
    """A write through WriteBodies alone: how many of its messages were read, the status sent, and the body the app
    behind got, or None when it was never called."""
    got: list[bytes] = []
    pulled = 0
    sent: list[Message] = []

    async def app(scope: Scope, receive: Any, send: Any) -> None:
        got.append((await receive())["body"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> Message:
        nonlocal pulled
        pulled += 1
        return {"type": "http.request", "body": chunks[pulled - 1], "more_body": pulled < len(chunks)}

    async def send(message: Message) -> None:
        sent.append(message)

    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/web/ledger/write",
        "headers": raw,
        "query_string": b"",
    }
    asyncio.run(webapi.WriteBodies(app)(scope, receive, send))
    return pulled, sent[0]["status"], got[0] if got else None


def test_a_write_s_body_is_checked_before_any_of_it_is_read() -> None:
    """A Content-Length over the cap, or a body that isn't JSON, is refused with none of it read; a body with no
    length is read only until it passes the cap; one under it reaches the route whole (DR-0709)."""
    piece = b"x" * (64 * 1024)
    jsonish = {"Content-Type": "application/json"}
    over = str(webapi.MAX_BODY + 1)
    assert through_the_cap({**jsonish, "Content-Length": over}, [piece] * 5) == (0, 413, None)
    assert through_the_cap({"Content-Type": "text/plain"}, [piece]) == (0, 415, None)
    assert through_the_cap(jsonish, [piece] * 10) == (5, 413, None)
    assert through_the_cap({**jsonish, "Content-Length": "2"}, [piece] * 10) == (5, 413, None)
    assert through_the_cap(jsonish, [b'{"op":', b'"done"}']) == (2, 200, b'{"op":"done"}')


def test_a_write_whose_client_goes_is_left_and_the_route_hears_what_comes_after_its_body() -> None:
    """A client gone before its body came gets no answer and reaches no route; a route that listens on after the
    body it was handed hears what the connection says next, such as the client going (DR-0709)."""
    heard: list[str] = []
    sent: list[Message] = []
    messages: list[Message] = []

    async def app(scope: Scope, receive: Any, send: Any) -> None:
        heard.append((await receive())["type"])
        heard.append((await receive())["type"])

    async def receive() -> Message:
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/web/ledger/save",
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"",
    }
    messages[:] = [{"type": "http.disconnect"}]
    asyncio.run(webapi.WriteBodies(app)(scope, receive, send))
    assert (heard, sent) == ([], [])
    messages[:] = [{"type": "http.request", "body": b"{}", "more_body": False}, {"type": "http.disconnect"}]
    asyncio.run(webapi.WriteBodies(app)(scope, receive, send))
    assert heard == ["http.request", "http.disconnect"]


def test_openapi_json_is_what_the_routes_say() -> None:
    assert SCHEMA.read_text(encoding="utf-8") == webapi.openapi_text()
    document = json.loads(webapi.openapi_text())
    assert list(document["paths"]) == [
        "/api/web/ledger", "/api/web/ledger/changed", "/api/web/ledger/open", "/api/web/ledger/panel",
        "/api/web/ledger/save", "/api/web/ledger/text", "/api/web/ledger/who", "/api/web/ledger/write",
        "/api/web/meetings", "/api/web/meetings/panel", "/api/web/queue", "/api/web/queue/panel", "/api/web/shell",
    ]  # fmt: skip
    assert set(document["components"]["schemas"]["Shell"]["required"]) == set(webapi.Shell.model_fields)


def test_the_world_script_refuses_what_it_doesn_t_know(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ui_world.main([]) == 2
    assert "usage" in capsys.readouterr().err
    with pytest.raises(ValueError, match="no world"):
        ui_world.build(tmp_path / "vault", "month")
