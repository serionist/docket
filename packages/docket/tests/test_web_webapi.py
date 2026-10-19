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
    # day_kit's seed: seven items, one done and one snoozed, so five open, one of them with a call; staging_kit's
    # three records, read as the files stand.
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
