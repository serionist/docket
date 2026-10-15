"""`docket models install|status|remove` at the command line, with the install stubbed and the fake worker."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from models_kit import fake_command, make_env

from docket import userconfig
from docket.cli import main
from docket.models import install, runtime
from docket.models.catalogue import E5_SMALL
from docket.models.client import Worker
from docket.models.config import Choice


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
    code, out = run(capsys, "status", "--check")
    assert code == 0 and "env cpu    ready, batches of 8,192 characters" in out
    assert "worker     up: CPUExecutionProvider, 64 dims" in out


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
