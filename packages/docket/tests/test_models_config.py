"""The user's choice of models (their config's `models:` block), where things live, and what an install left: a block
that doesn't read turns models off with the reason, never more."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from models_kit import FAKE, make_env

from docket import userconfig
from docket.index import build
from docket.models import config, runtime
from docket.models.catalogue import E5_SMALL
from docket.models.client import Worker
from docket.models.config import Choice, Installed, read
from docket.models.provider import Models, Off


def set_block(block: Any) -> None:
    path = userconfig.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"service": {"port": 9999}, "models": block}), encoding="utf-8")


def test_no_block_is_off_with_nothing_to_say() -> None:
    assert read() == Off()


@pytest.mark.parametrize(
    ("block", "said"),
    [
        (["cpu"], "isn't a map of provider, device and embedder"),
        ({"provider": "local", "devise": "cpu"}, "keys this Docket doesn't know (devise)"),
        ({"provider": "api"}, "names the api provider, which isn't there yet"),
        ({"provider": "cloud"}, "names provider 'cloud'; it is local or none"),
        ({"device": "tpu"}, "names device 'tpu'; it is cpu or cuda"),
        ({"embedder": "large"}, "names an embedder this Docket doesn't have (e5-small)"),
    ],
)
def test_a_block_that_doesnt_read_turns_models_off_and_says_why(block: Any, said: str) -> None:
    set_block(block)
    got = read()
    assert isinstance(got, Off) and got.why is not None
    assert said in got.why and got.why.endswith("so models are off")
    assert str(userconfig.config_path()) in got.why


@pytest.mark.parametrize(
    "content", [b"models:\n  provider: local\n\tdevice: cpu\n", b"models: {device: cpu}\n# caf\xe9\n"]
)
def test_a_config_that_isnt_yaml_turns_models_off_and_says_why(content: bytes) -> None:
    path = userconfig.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    assert read() == Off(f"{path} doesn't read as YAML, so models are off")
    # A rebuild then keeps no vector table, and nothing raises on the way.
    assert build.configured_table() is None


def test_provider_none_is_off_quietly_and_a_good_block_is_a_choice() -> None:
    set_block({"provider": "none", "device": "cuda"})
    assert read() == Off()
    set_block({"provider": "local"})
    assert read() == Choice("cpu", E5_SMALL)
    set_block({"device": "cuda", "embedder": "e5-small"})
    assert read() == Choice("cuda", E5_SMALL)


def test_write_and_forget_keep_the_rest_of_the_config() -> None:
    set_block(None)
    config.write("cuda", E5_SMALL)
    data = userconfig.load()
    assert data["models"] == {"provider": "local", "device": "cuda", "embedder": "e5-small"}
    assert data["service"] == {"port": 9999}
    config.forget()
    assert "models" not in userconfig.load() and userconfig.load()["service"] == {"port": 9999}


def test_the_folders_follow_the_env_var_or_the_users_own(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(os.environ[config.ENV_VAR])
    assert config.env_dir("cuda") == root / "env-cuda"
    assert config.files_dir() == root / "files"
    assert config.file_path(E5_SMALL, E5_SMALL.model_file) == root.joinpath(
        "files", "intfloat", "multilingual-e5-small", E5_SMALL.revision, "onnx", "model.onnx"
    )
    monkeypatch.delenv(config.ENV_VAR)
    assert config.env_dir("cpu").parent.name == "models" and config.env_dir("cpu").name == "env-cpu"
    assert config.files_dir().name == "models"
    monkeypatch.setattr(sys, "platform", "win32")
    assert config.env_python(Path("e")) == Path("e", "Scripts", "python.exe")
    monkeypatch.setattr(sys, "platform", "linux")
    assert config.env_python(Path("e")) == Path("e", "bin", "python")


def test_the_requirements_are_pinned_with_hashes() -> None:
    for device in config.DEVICES:
        text = config.requirements(device)
        assert "--hash=sha256:" in text and "numpy==2.5.3" in text
        assert len(config.requirements_hash(device)) == 64
    assert "onnxruntime==1.30.0" in config.requirements("cpu")
    assert "onnxruntime-gpu==1.30.0" in config.requirements("cuda")


def test_installed_says_what_the_install_left_or_why_it_cant_be_used() -> None:
    assert config.installed("cpu") == "the models aren't installed for cpu on this machine; run docket models install"
    make_env("cpu")
    assert config.installed("cpu") == Installed("cpu", 8192, config.requirements_hash("cpu"), config.python_version())
    make_env("cpu", requirements="old")
    assert (
        config.installed("cpu") == "the models' environment is from another Docket or Python; run docket models install"
    )
    make_env("cpu", python="2.7")
    assert isinstance(config.installed("cpu"), str)
    (config.env_dir("cpu") / config.MARKER).write_text("{", encoding="utf-8")
    assert config.installed("cpu") == "the models' environment can't be read; run docket models install"


def put_files(sizes: dict[str, int] | None = None) -> None:
    for f in FAKE.files:
        path = config.file_path(FAKE, f)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * (sizes or {}).get(f.path, f.size))


def test_missing_files_are_those_absent_or_of_another_size() -> None:
    assert config.missing_files(FAKE) == list(FAKE.files)
    put_files({"tokenizer.json": 5})
    assert config.missing_files(FAKE) == [FAKE.tokenizer_file]
    put_files()
    assert config.missing_files(FAKE) == []


def test_open_models_says_why_it_cant_and_gives_a_worker_when_it_can(monkeypatch: pytest.MonkeyPatch) -> None:
    assert runtime.open_models() == Off()
    set_block({"device": "cuda"})
    assert runtime.open_models() == Off(
        "the models aren't installed for cuda on this machine; run docket models install"
    )
    make_env("cuda")
    assert runtime.open_models() == Off("the model files are missing; run docket models install")
    monkeypatch.setattr(runtime, "missing_files", lambda embedder: [])
    started: list[str] = []
    monkeypatch.setattr(Worker, "ensure", lambda self: started.append(self.command[-1]) or True)
    models = runtime.open_models(queries_only=True)
    assert isinstance(models, Models) and models.budget == 8192 and models.embedder is E5_SMALL
    spec = json.loads(started[0])
    # A command that only asks queries runs on the CPU, from the CUDA environment's Python.
    assert spec["device"] == "cpu" and spec["dims"] == 384 and spec["threads"] >= 1
    assert spec["model"].endswith("model.onnx")
    models = runtime.open_models(start=False)
    assert isinstance(models, Models) and len(started) == 1
    assert json.loads(models.worker.command[-1])["device"] == "cuda"  # type: ignore[attr-defined]
    assert str(config.env_python(config.env_dir("cuda"))) == models.worker.command[0]  # type: ignore[attr-defined]


def test_threads_are_half_the_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    assert runtime.threads() == 8
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert runtime.threads() == 1
