"""`docket models install|status|remove` at the command line, with the install stubbed and the fake worker."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from docket_testkit import copy_vault, index_template, make_record
from models_kit import FAKE, fake_command, make_env, needs_vectors

from docket import cli, userconfig
from docket.cli import main
from docket.errors import DocketError
from docket.index.embedpass import PassReport
from docket.models import catalogue, install, runtime
from docket.models.catalogue import E5_SMALL
from docket.models.client import Worker
from docket.models.config import Choice
from docket.service import client
from docket.vault.vault import Vault


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main(["models", *args])
    return code, capsys.readouterr().out


def choose(block: dict[str, Any]) -> None:
    path = userconfig.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"models": block}), encoding="utf-8")


def test_status_says_models_are_off_and_how_to_turn_them_on(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "status")
    assert code == 0 and out == "models     off (docket models install turns them on)\n"
    choose({"device": "tpu"})
    code, out = run(capsys, "status")
    assert "models     off: the models block in" in out and "names device 'tpu'" in out
    code, out = run(capsys, "status", "--check")
    assert code == 1 and "! models are off" in out


def test_status_shows_the_choice_the_environment_and_the_files(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    choose({"device": "cpu"})
    code, out = run(capsys, "status", "--check")
    assert code == 1 and "models     e5-small on cpu" in out
    assert "files      missing: onnx/model.onnx, tokenizer.json" in out
    assert "! the models aren't installed for cpu on this machine; run docket models install" in out
    make_env("cpu")
    monkeypatch.setattr(runtime, "worker_for", lambda choice, device: Worker(fake_command()))
    monkeypatch.setattr(install, "vectors_problem", lambda: None)
    code, out = run(capsys, "status", "--check")
    assert code == 0 and "env cpu    ready, batches of 8,192 characters" in out
    assert "worker     up: CPUExecutionProvider, 64 dims" in out and "search     uses the vectors" in out
    monkeypatch.setattr(install, "vectors_problem", lambda: "the Python Docket runs on can't load SQLite extensions")
    code, out = run(capsys, "status")
    assert "search     ! the Python Docket runs on can't load SQLite extensions" in out


def test_a_check_that_fails_shows_the_workers_last_lines(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    choose({"device": "cpu"})
    make_env("cpu", requirements="old")
    code, out = run(capsys, "status", "--check")
    assert code == 1 and "env cpu    the models' environment is from another Docket or Python" in out
    make_env("cpu")
    monkeypatch.setattr(runtime, "worker_for", lambda choice, device: Worker(fake_command(bad_hello=True)))
    code, out = run(capsys, "status", "--check")
    assert code == 1
    assert "! the models' worker said hello in a form this Docket doesn't read" in out
    assert "fake worker starting" in out


def test_install_and_remove_run_their_steps(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def fake_install(device: str, *, say: Any) -> Choice:
        asked.append(device)
        say("ready")
        return Choice("cpu", E5_SMALL)

    monkeypatch.setattr(install, "install", fake_install)
    code, out = run(capsys, "install", "--device", "cpu")
    assert code == 0 and asked == ["cpu"] and out == "ready\n"
    code, out = run(capsys, "remove")
    assert code == 0 and out.endswith("models are off on this machine; keyword search goes on\n")


# -- the commands that use the models --------------------------------------------------------------------------------

ZEBRA = "chat:C0OPS/1791480000.000100"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    """A vault whose models are the fake embedder, installed, its worker the fake one as a process."""
    choose_fake(monkeypatch)
    make_env("cpu")
    monkeypatch.setattr(runtime, "missing_files", lambda embedder: [])
    monkeypatch.setattr(runtime, "worker_for", lambda choice, device: Worker(fake_command()))
    v = copy_vault(template, tmp_path)
    v.write_run("chat", [make_record(rid=ZEBRA, container="chat:C0OPS", text="Quarterly zebra migration budget.")])
    return v


def choose_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(catalogue.EMBEDDERS, FAKE.name, FAKE)
    choose({"device": "cpu", "embedder": FAKE.name})


def at(vault: Vault, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main([*args, "--vault", str(vault.root)])
    return code, capsys.readouterr().out


@needs_vectors
def test_index_update_embeds_and_search_and_eval_use_the_vectors(
    vault: Vault, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "PROGRESS_SECONDS", 0.0)
    code, out = at(vault, capsys, "index", "update")
    lines = out.splitlines()
    assert code == 0 and lines[-1].startswith("vectors    fake  ") and lines[-1].endswith(" embedded)")
    # The progress lines, with PROGRESS_SECONDS at 0: one after each batch.
    assert any(line.startswith("vectors    ") and " of " in line and "fake" not in line for line in lines)
    code, out = at(vault, capsys, "search", "zebbra migrashun", "--no-update")
    first = out.split("\n\n")[1]
    assert code == 0 and ZEBRA in first and "no vectors" not in out
    code, out = at(vault, capsys, "search", "zebbra migrashun", "--no-update", "--no-models")
    assert ZEBRA not in out
    questions = tmp_path / "q.yaml"
    questions.write_text(
        'schema_version: 1\nasked: "2026-10-14T12:00:00+02:00"\nquestions:\n'
        f'  - {{q: zebbra migrashun, want: ["r:{ZEBRA}"], kind: paraphrase, lang: en}}\n',
        encoding="utf-8",
    )
    code, out = at(vault, capsys, "eval", str(questions))
    assert code == 0 and "vectors   fake: " in out
    code, out = at(vault, capsys, "eval", str(questions), "--no-models")
    assert "vectors   " not in out
    code, out = at(vault, capsys, "index", "rebuild")
    assert code == 0 and "(0 embedded)" in out


def test_index_update_says_why_it_can_embed_nothing(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out = at(vault, capsys, "index", "update", "--no-models")
    assert code == 0 and "vectors    " not in out
    monkeypatch.setattr(runtime, "worker_for", lambda choice, device: Worker(fake_command(bad_hello=True)))
    code, out = at(vault, capsys, "index", "update")
    assert "! the models' worker said hello in a form this Docket doesn't read" in out
    monkeypatch.setattr(
        runtime, "worker_for", lambda choice, device: Worker(fake_command(refuse_on="__never__", delay=0))
    )
    choose({"device": "tpu"})
    code, out = at(vault, capsys, "index", "update")
    assert "! the models block in" in out and "names device 'tpu'" in out
    choose({"provider": "none"})
    code, out = at(vault, capsys, "index", "update")
    assert "vectors" not in out and "!" not in out.replace("! processed", "")


def test_a_pass_the_worker_stops_says_why(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "fill", lambda vault, models, progress: fail_report())
    code, out = at(vault, capsys, "index", "update")
    assert code == 0 and "! the models' worker stopped (exit 5)" in out


def fail_report() -> PassReport:
    return PassReport(model="fake", why="the models' worker stopped (exit 5)")


class Token:
    def __init__(self, why: str | None = None) -> None:
        self.why = why

    def read(self) -> str:
        if self.why is not None:
            raise DocketError(self.why)
        return "t0ken-for-tests-only"


UP = {"state": "up", "model": "fake", "why": None, "embedded": 3, "errors": []}


class Service:
    """The running service's /api/models, as the CLI asks for it: None when nothing answers."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, vault: Vault) -> None:
        self.body: dict[str, Any] | None = UP
        self.asked: list[str] = []
        monkeypatch.setattr(client, "call", self.call)
        monkeypatch.setattr(client, "recorded", lambda: {"vault": str(vault.root)})
        monkeypatch.setattr(cli, "_tokens", Token)

    def call(self, method: str, path: str, *, token: str | None = None, at: int | None = None) -> client.Answer | None:
        self.asked.append(path)
        return None if self.body is None else client.Answer(200, self.body)


def test_the_pass_is_left_to_a_service_that_runs_for_the_vault_with_the_model(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    service = Service(monkeypatch, vault)
    code, out = at(vault, capsys, "index", "update")
    assert code == 0 and "vectors    the service embeds what is new" in out and service.asked == ["/api/models"]
    # A worker that is down comes back on the service's own clock: the pass is still the service's.
    service.body = {**UP, "state": "down", "why": "the models' worker stopped (exit 5)"}
    code, out = at(vault, capsys, "index", "update")
    assert "vectors    the service embeds what is new" in out


@pytest.mark.parametrize(
    "body", [{**UP, "state": "off", "model": None}, {**UP, "model": "another-model"}], ids=["off", "another model"]
)
def test_a_service_started_before_the_install_leaves_the_pass_to_the_command_and_it_says_so(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    Service(monkeypatch, vault).body = body
    code, out = at(vault, capsys, "index", "update")
    assert code == 0 and "vectors    the service embeds what is new" not in out
    assert (
        "! the service runs without fake, so this command embeds; docket service stop, then docket service install, "
        "and the service embeds after its updates"
    ) in out
    assert out.splitlines()[-1].startswith("vectors    fake  ")


def test_the_command_embeds_when_no_service_answers_for_the_vault(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    service = Service(monkeypatch, vault)
    service.body = None
    code, out = at(vault, capsys, "index", "update")
    assert code == 0 and "the service" not in out and out.splitlines()[-1].startswith("vectors    fake  ")
    # One for another vault isn't asked; one whose token can't be read can't be either.
    service.body = UP
    monkeypatch.setattr(client, "recorded", lambda: {"vault": str(vault.root.parent)})
    code, out = at(vault, capsys, "index", "update")
    assert "the service" not in out and service.asked == ["/api/models"]
    monkeypatch.setattr(client, "recorded", lambda: {"vault": str(vault.root)})
    monkeypatch.setattr(cli, "_tokens", lambda: Token("no token yet"))
    code, out = at(vault, capsys, "index", "update")
    assert "the service" not in out and service.asked == ["/api/models"]


def test_status_shows_the_vaults_vectors_and_the_services_worker(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out = run(capsys, "status", "--vault", str(vault.root))
    assert code == 0 and "vectors    " not in out
    # An index built before the models were on has no table for them yet.
    choose({"provider": "none"})
    at(vault, capsys, "index", "update")
    choose_fake(monkeypatch)
    code, out = run(capsys, "status", "--vault", str(vault.root))
    assert "vectors    none yet in index/docket.sqlite; docket index update embeds" in out
    at(vault, capsys, "index", "update")
    code, out = run(capsys, "status", "--vault", str(vault.root))
    assert re.search(r"vectors    fake  (\d+) of \1 chunks \(index/docket\.sqlite\)", out), out
    assert "service" not in out
    service = Service(monkeypatch, vault)
    service.body = {
        **UP, "state": "down", "why": "the models' worker stopped (exit 5)",
        "errors": ["Traceback (most recent call last):", "MemoryError"],
    }  # fmt: skip
    code, out = run(capsys, "status", "--vault", str(vault.root))
    lines = out.splitlines()
    assert lines[-3:] == ["service    fake, down: the models' worker stopped (exit 5)", *service.body["errors"]]
    with pytest.raises(DocketError, match="no vault at or above"):
        cli._models_vectors(vault.root.parent / "elsewhere", on=True)
