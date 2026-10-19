"""The browser's own JSON routes under /api/web (DR-0709): the shell's read on the invented working week devkit ui
serves, the session cookie only, and the OpenAPI document the app's types come from."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import ui_world
from docket_testkit import copied
from docket_webkit import OURS, PORT, TOKEN
from starlette.testclient import TestClient

from docket.errors import DocketError
from docket.ledger import day as ledger_day
from docket.service.app import create_app
from docket.service.sessions import COOKIE, Sessions
from docket.service.state import Settings
from docket.vault.gate import Gate
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
    # day_kit's seed: seven items, one done and one snoozed, so five open, one of them with a call; staging_kit's
    # three records.
    assert shell["untriaged"] == 4
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


def test_a_part_that_doesn_t_read_leaves_its_count_out(week: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*_args: object, **_kwargs: object) -> None:
        raise DocketError("the forgotten list doesn't read")

    monkeypatch.setattr(ledger_day, "read", refused)
    monkeypatch.setattr(Gate, "list", refused)
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


def test_openapi_json_is_what_the_routes_say() -> None:
    assert SCHEMA.read_text(encoding="utf-8") == webapi.openapi_text()
    document = json.loads(webapi.openapi_text())
    assert list(document["paths"]) == ["/api/web/shell"]
    assert set(document["components"]["schemas"]["Shell"]["required"]) == set(webapi.Shell.model_fields)


def test_the_world_script_refuses_what_it_doesn_t_know(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ui_world.main([]) == 2
    assert "usage" in capsys.readouterr().err
    with pytest.raises(ValueError, match="no world"):
        ui_world.build(tmp_path / "vault", "month")
