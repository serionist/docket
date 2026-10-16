"""The service keeps the models' worker and runs the embedding pass after its index updates (DR-0330, DR-0336):
searches through it carry vectors once the pass has run, say why when they can't, and the log holds no text."""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import index_template, make_record
from models_kit import FAKE, fake_models, fake_worker, needs_vectors
from starlette.testclient import TestClient
from test_index_vectors import choose

from docket.errors import DocketError
from docket.index import rebuild
from docket.models.provider import Models, Off
from docket.service import models as service_models
from docket.service.app import create_app
from docket.service.models import ModelsKeeper
from docket.service.state import Settings
from docket.vault.vault import Vault

TOKEN = "t0ken-for-tests-only"  # noqa: S105 - an invented token
PORT = 8799
BEARER = {"Authorization": f"Bearer {TOKEN}"}
ZEBRA = "chat:C0OPS/1791480000.000100"


def wait_for(done: Callable[[], bool], seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    while not done():
        assert time.monotonic() < deadline, "waited too long"
        time.sleep(0.02)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    choose(monkeypatch)
    shutil.copytree(template, tmp_path / "vault")
    v = Vault.open(tmp_path / "vault")
    v.write_run(
        "chat", [make_record(rid=ZEBRA, container="chat:C0OPS", text="Quarterly zebra migration budget.")], None
    )
    return v


def opener(models: Models | Off) -> Callable[..., Models | Off]:
    return lambda **_: models


def test_the_keeper_runs_a_pass_at_start_and_after_each_poke(vault: Vault) -> None:
    rebuild(vault)
    models, worker = fake_models()
    keeper = ModelsKeeper(lambda: vault, opener(models))
    keeper.start()
    try:
        wait_for(lambda: keeper.last is not None)
        first = keeper.last
        assert first is not None and first.have == first.total > 0 and worker.ensured >= 1
        assert keeper.status().model_dump() == {
            "state": "up", "model": "fake", "why": None, "embedded": first.have, "errors": [],
        }  # fmt: skip
        vault.write_run("chat", [make_record(rid="chat:C0OPS/1791480100.000100", text="Another zebra.")], None)
        from docket.index import update

        update(vault)
        keeper.poke()
        wait_for(lambda: keeper.last is not None and keeper.last.total == first.total + 1)
    finally:
        keeper.close()
    assert not worker.up and keeper.status().state == "down"


def test_the_keeper_looks_again_unasked_and_finds_a_worker_that_came_back(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_models, "WAKE_SECONDS", 0.05)
    rebuild(vault)
    models, worker = fake_models()
    worker.up, worker.why = False, "the models' worker stopped (exit 5)"
    keeper = ModelsKeeper(lambda: vault, opener(models))
    keeper.start()
    try:
        # The poke at start, then a look on the clock alone.
        wait_for(lambda: worker.ensured >= 2)
        assert keeper.last is None
        worker.up, worker.why = True, None
        wait_for(lambda: keeper.last is not None)
    finally:
        keeper.close()
    assert keeper.last is not None and keeper.last.have == keeper.last.total > 0


def test_status_shows_the_end_of_what_a_worker_wrote_to_stderr(vault: Vault) -> None:
    worker = fake_worker(bad_hello=True)
    keeper = ModelsKeeper(lambda: vault, opener(Off()))
    keeper.models = Models(FAKE, worker, 8192)
    try:
        assert not worker.ensure()
        # Its stderr is read on a thread of its own.
        wait_for(lambda: "fake worker starting" in keeper.status().errors)
        status = keeper.status()
    finally:
        worker.stop()
    assert status.state == "down" and status.why == "the models' worker said hello in a form this Docket doesn't read"
    assert len(status.errors) <= service_models.ERROR_LINES


def test_a_keeper_with_models_off_says_why_and_starts_nothing(vault: Vault, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, "docket.service")
    keeper = ModelsKeeper(lambda: vault, opener(Off("the models block in config.yaml doesn't read")))
    keeper.start()
    keeper.close()
    assert keeper.status().model_dump() == {
        "state": "off", "model": None, "why": "the models block in config.yaml doesn't read", "embedded": None,
        "errors": [],
    }  # fmt: skip
    assert "models are off: the models block in config.yaml doesn't read" in caplog.text
    quiet = ModelsKeeper(lambda: vault, opener(Off()))
    quiet.start()
    assert quiet.status().state == "off" and quiet.for_search() == Off()


def test_a_pass_that_cant_run_logs_its_reason_and_never_a_text(vault: Vault, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, "docket.service")
    models, worker = fake_models()
    keeper = ModelsKeeper(lambda: vault, opener(models))
    keeper.models = models
    worker.up, worker.why = False, "the models' worker stopped (exit 5)"
    assert keeper.run_pass(models) is None
    assert "the models' worker is down: the models' worker stopped (exit 5)" in caplog.text
    worker.up = True

    def no_vault() -> Vault:
        raise DocketError("the service has no vault")

    assert ModelsKeeper(no_vault, opener(models)).run_pass(models) is None
    assert "the embedding pass failed: DocketError" in caplog.text
    report = keeper.run_pass(models)
    assert report is not None and report.why == "there is no index yet"
    assert "the embedding pass stopped: there is no index yet" in caplog.text
    rebuild(vault)
    report = keeper.run_pass(models)
    assert report is not None and report.embedded > 0
    assert f"embedding pass: {report.embedded} embedded" in caplog.text
    assert "zebra" not in caplog.text.lower()


@pytest.fixture
def client(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Any]]:
    models, worker = fake_models()
    monkeypatch.setattr(service_models, "open_models", opener(models))
    app = create_app(Settings(port=PORT, vault=vault.root, token=lambda: TOKEN, store="the system keychain"))
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as found:
        yield found, worker


@needs_vectors
def test_the_service_searches_with_vectors_once_its_pass_has_run(client: tuple[TestClient, Any]) -> None:
    http, worker = client
    service = http.app.service  # type: ignore[attr-defined]
    # The first pass can run before the index exists and find nothing: wait for one that embedded every chunk.
    wait_for(lambda: (last := service.models.last) is not None and last.total > 0 and last.have == last.total)
    answer = http.get("/api/search", params={"query": "zebbra migrashun"}, headers=BEARER)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["models"] == {"state": "on", "model": "fake", "why": None}
    assert body["groups"][0]["hits"][0]["found_by"] == "vector"
    status = http.get("/api/status", headers=BEARER).json()
    assert status["models"]["state"] == "up" and status["models"]["model"] == "fake"
    assert http.get("/api/models", headers=BEARER).json() == status["models"]
    assert http.get("/api/models").status_code == 401
    worker.up, worker.why = False, "the models' worker stopped (exit 5)"
    answer = http.get("/api/search", params={"query": "zebbra migrashun"}, headers=BEARER).json()
    assert answer["models"] == {"state": "skipped", "model": "fake", "why": "the models' worker stopped (exit 5)"}
