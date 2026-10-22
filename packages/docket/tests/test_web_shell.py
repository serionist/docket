"""The service around the app: / moves on to Today, a failure of the app's reads answers 500 with no words and the log
keeps none, and without a vault the reads say so."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from docket_testkit import index_template
from docket_webkit import OURS, PORT, TOKEN, signed_in
from starlette.testclient import TestClient

from docket.service import calls, logs
from docket.service.app import create_app
from docket.service.sessions import Sessions
from docket.service.state import Settings
from docket.web import panel
from docket.web.display import FAILED


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    root = logging.getLogger()
    levels = {name: logging.getLogger(name).level for name in ("", "docket", *logs.LIBRARIES)}
    path = tmp_path / "log" / "service.log"
    handler = logs.configure(path)
    yield path
    root.removeHandler(handler)
    handler.close()
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


def test_the_start_moves_on_to_today(client: TestClient) -> None:
    answer = client.get("/")
    assert answer.status_code == 303
    assert answer.headers["location"] == "/today"


def test_a_failure_answers_500_with_no_words_and_the_log_keeps_none(
    client: TestClient, log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> object:
        try:
            raise KeyError("Kovács Ödön said the price")
        except KeyError as e:
            raise RuntimeError("Kovács Ödön") from e

    monkeypatch.setattr(calls, "search", broken)
    results = client.get("/api/web/search?query=fleet&sources=chat&since=2026-10-01")
    monkeypatch.setattr(panel, "build", broken)
    in_panel = client.get("/api/web/search/panel?hit=r:calls:rec-0100")
    for answer in (results, in_panel):
        assert (answer.status_code, answer.json()) == (500, {"error": FAILED})
        assert "Ödön" not in answer.text
    logged = log_file.read_text(encoding="utf-8")
    assert "builtins.RuntimeError" in logged
    assert "builtins.KeyError" in logged
    assert "GET /api/web/search failed" in logged
    assert "GET /api/web/search/panel failed" in logged
    assert "Ödön" not in logged


def test_without_a_vault_the_reads_say_so(tmp_path: Path) -> None:
    sessions = Sessions()
    app = create_app(Settings(port=PORT, vault=None, token=lambda: TOKEN), sessions=sessions)
    with TestClient(app, base_url=OURS) as client:
        started = sessions.redeem(sessions.new_code())
        assert started is not None
        client.cookies.set("docket_session", started)
        found = client.get("/api/web/search?query=fleet")
        assert found.status_code == 400
        assert "the service has no vault" in found.json()["error"]
        shell = client.get("/api/web/shell").json()
        assert (shell["vault"], shell["index_line"]) == (None, "the index is being brought up to date")
        assert "the service has no vault" in shell["problem"]
        opened = client.get("/api/web/search/panel?hit=r:calls:rec-0100")
        assert opened.status_code == 400
        assert "the service has no vault" in opened.json()["error"]
