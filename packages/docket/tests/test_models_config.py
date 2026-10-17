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
from docket.models.catalogue import BGE_RERANKER, E5_SMALL, MMARCO_RERANKER
from docket.models.client import Worker
from docket.models.config import ApiChoice, Choice, Installed, RerankTiming, read
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
        ({"provider": "api", "embedder": "large"}, "names an embedder the api provider doesn't have (voyage-4)"),
        ({"provider": "api", "api_monthly_tokens": 0}, "gives api_monthly_tokens as 0; it is a whole number"),
        ({"provider": "api", "api_monthly_tokens": True}, "gives api_monthly_tokens as True"),
        ({"provider": "cloud"}, "names provider 'cloud'; it is local, api or none"),
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
    # A block #67's install wrote names no reranker: the embedder is on, and the reranker says how to get it.
    assert read() == Choice("cpu", E5_SMALL, None, "no reranker is installed; docket models install adds it")
    set_block({"device": "cuda", "embedder": "e5-small", "reranker": "none"})
    assert read() == Choice("cuda", E5_SMALL)


def test_write_and_forget_keep_the_rest_of_the_config() -> None:
    set_block(None)
    config.write("cuda", E5_SMALL)
    data = userconfig.load()
    assert data["models"] == {"provider": "local", "device": "cuda", "embedder": "e5-small", "reranker": "none"}
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


# -- the reranker (#88) ----------------------------------------------------------------------------------------------

TIMED = RerankTiming(MMARCO_RERANKER.name, MMARCO_RERANKER.revision, 30, 0.42)


def test_an_unknown_reranker_turns_only_the_reranker_off_and_a_known_one_is_chosen() -> None:
    set_block({"reranker": "a-big-one"})
    got = read()
    assert isinstance(got, Choice) and got.embedder is E5_SMALL and got.reranker is None
    assert got.reranker_why is not None and "names a reranker this Docket doesn't have" in got.reranker_why
    assert got.reranker_why.endswith("so the reranker is off")
    set_block({"reranker": "mmarco-minilm"})
    assert read() == Choice("cpu", E5_SMALL, MMARCO_RERANKER)
    config.write("cpu", E5_SMALL, MMARCO_RERANKER)
    assert userconfig.load()["models"]["reranker"] == "mmarco-minilm"


def test_a_timing_is_on_within_its_limit_and_tied_to_its_reranker() -> None:
    assert TIMED.on and TIMED.batch_seconds == pytest.approx(0.14) and TIMED.fits(MMARCO_RERANKER)
    assert not RerankTiming("mmarco-minilm", "old", 30, 0.42).fits(MMARCO_RERANKER)
    assert not RerankTiming("mmarco-minilm", MMARCO_RERANKER.revision, 30, 0.87).on
    off = RerankTiming("mmarco-minilm", MMARCO_RERANKER.revision, 30, None, "it didn't load")
    assert not off.on and off.batch_seconds == 0.0


def test_the_marker_keeps_the_rerankers_timing_and_a_marker_from_67_still_reads() -> None:
    make_env("cpu")
    found = config.installed("cpu")
    assert isinstance(found, Installed) and found.rerank is None, "a #67 marker: the embedder on, the reranker untimed"
    config.mark(Installed("cpu", 8192, config.requirements_hash("cpu"), config.python_version(), TIMED))
    assert config.installed("cpu") == Installed(
        "cpu", 8192, config.requirements_hash("cpu"), config.python_version(), TIMED
    )
    for broken in ({"name": "mmarco-minilm"}, "fast", {"name": "x", "revision": "y", "pairs": "many", "seconds": 1}):
        make_env("cpu", reranker=broken)
        found = config.installed("cpu")
        assert isinstance(found, Installed) and found.rerank is None


def models_here(
    monkeypatch: pytest.MonkeyPatch, timing: RerankTiming | None, device: config.Device = "cpu", **block: Any
) -> list[str]:
    set_block({"device": device, "reranker": MMARCO_RERANKER.name, **block})
    make_env(device)
    if timing is not None:
        config.mark(Installed(device, 8192, config.requirements_hash(device), config.python_version(), timing))
    monkeypatch.setattr(runtime, "missing_files", lambda model: [])
    started: list[str] = []
    monkeypatch.setattr(Worker, "ensure", lambda self: started.append(self.command[-1]) or True)
    return started


def test_the_spec_names_the_reranker_only_when_it_is_on_here(monkeypatch: pytest.MonkeyPatch) -> None:
    started = models_here(monkeypatch, TIMED)
    models = runtime.open_models()
    assert isinstance(models, Models) and models.reranker is MMARCO_RERANKER and models.pairs == 30
    rerank = json.loads(started[-1])["reranker"]
    assert rerank["name"] == "mmarco-minilm" and rerank["device"] == "cpu" and rerank["max_tokens"] == 256
    assert rerank["batch_seconds"] == pytest.approx(0.14) and rerank["model"].endswith("model.onnx")
    for timing, why in [
        (None, "mmarco-minilm isn't timed on this machine; docket models install times it"),
        (
            RerankTiming("mmarco-minilm", MMARCO_RERANKER.revision, 30, 0.87),
            "mmarco-minilm is off on this machine: 30 pairs took 0.87 s, past 0.6 s; "
            "docket models install times it again",
        ),
        (
            RerankTiming("mmarco-minilm", MMARCO_RERANKER.revision, 30, None, "the worker didn't load it"),
            "mmarco-minilm is off on this machine: the worker didn't load it; docket models install times it again",
        ),
    ]:
        started = models_here(monkeypatch, timing)
        models = runtime.open_models()
        assert isinstance(models, Models) and models.reranker is None and models.rerank_why == why
        assert not models.rerank_said and "reranker" not in json.loads(started[-1])


def test_a_reranker_whose_files_are_missing_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    models_here(monkeypatch, TIMED)
    monkeypatch.setattr(runtime, "missing_files", lambda model: [] if model is E5_SMALL else list(model.files))
    models = runtime.open_models(start=False)
    assert (
        isinstance(models, Models)
        and models.rerank_why == "mmarco-minilm's files are missing; run docket models install"
    )


def test_where_a_command_reranks(monkeypatch: pytest.MonkeyPatch) -> None:
    gpu = RerankTiming(BGE_RERANKER.name, BGE_RERANKER.revision, 50, 0.13)
    started = models_here(monkeypatch, gpu, device="cuda", reranker=BGE_RERANKER.name)
    # docket search runs on the CPU, where the GPU's reranker doesn't: every search says so.
    search = runtime.open_models(queries_only=True)
    assert isinstance(search, Models) and search.reranker is None and search.rerank_said
    assert search.rerank_why == "the reranker runs on the GPU in the service; docket search goes without it"
    assert json.loads(started[-1])["device"] == "cpu"
    # docket eval starts where the reranker runs, and the service on the chosen device.
    for opened in (runtime.open_models(measure=True), runtime.open_models()):
        assert isinstance(opened, Models) and opened.reranker is BGE_RERANKER and opened.pairs == 50
        spec = json.loads(started[-1])
        assert spec["device"] == "cuda" and spec["reranker"]["device"] == "cuda"
    # A choice of the GPU's reranker for CPU models says so, rather than run it where it is slow.
    started = models_here(monkeypatch, gpu, device="cpu", reranker=BGE_RERANKER.name)
    models = runtime.open_models()
    assert (
        isinstance(models, Models)
        and models.rerank_why == "bge-reranker-v2-m3 runs on cuda, and the models on cpu here"
    )
    # With no reranker, docket eval runs on the CPU.
    started = models_here(monkeypatch, None, device="cuda", reranker="none")
    assert isinstance(runtime.open_models(measure=True), Models) and json.loads(started[-1])["device"] == "cpu"


def test_a_command_that_only_embeds_loads_no_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    started = models_here(monkeypatch, TIMED)
    models = runtime.open_models(rerank=False)
    assert isinstance(models, Models) and models.reranker is None and models.rerank_why is None
    assert "reranker" not in json.loads(started[-1])


def test_the_api_provider_reads_its_reranker_or_none() -> None:
    set_block({"provider": "api", "reranker": "none"})
    got = read()
    assert isinstance(got, ApiChoice) and got.reranker is None and got.reranker_why is None
    set_block({"provider": "api", "reranker": "big"})
    got = read()
    assert isinstance(got, ApiChoice) and got.reranker is None
    assert got.reranker_why is not None and "names a reranker the api provider doesn't have" in got.reranker_why
    set_block({"provider": "api"})
    got = read()
    assert isinstance(got, ApiChoice) and got.reranker is not None and got.reranker.name == "rerank-2.5-lite"
