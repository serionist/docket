"""`docket models install` and `remove` with no network and no uv: the files come from a local HTTP server on a free
port that redirects and serves ranges as the hub does, uv is a stub, and the worker is the fake one."""

from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from models_kit import FAKE, fake_command

from docket.errors import DocketError
from docket.models import catalogue, config, install
from docket.models.catalogue import Embedder, ModelFile
from docket.models.client import Worker
from docket.models.config import Choice
from docket.models.provider import Off

ONE = b"the model's bytes " * 50
TWO = b'{"tokenizer": true}'


def small(name: str = "fake") -> Embedder:
    return Embedder(
        name=name,
        repo="example/fake-embedder",
        revision=FAKE.revision,
        model_file=ModelFile("onnx/model.onnx", len(ONE), hashlib.sha256(ONE).hexdigest()),
        tokenizer_file=ModelFile("tokenizer.json", len(TWO), hashlib.sha256(TWO).hexdigest()),
        dims=FAKE.dims,
        max_tokens=512,
        query_prefix="query: ",
        passage_prefix="passage: ",
        licence="MIT",
    )


class Hub(http.server.BaseHTTPRequestHandler):
    files: ClassVar[dict[str, bytes]] = {}
    ranges = True
    seen: ClassVar[list[tuple[str, str | None]]] = []

    def do_GET(self) -> None:
        self.seen.append((self.path, self.headers.get("Range")))
        if "/resolve/" in self.path:
            # The hub sends a file on to its CDN, as huggingface.co does.
            self.send_response(302)
            self.send_header("Location", "/cdn/" + self.path.split("/resolve/", 1)[1].split("/", 1)[1])
            self.end_headers()
            return
        data = self.files.get(self.path.removeprefix("/cdn"))
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        wanted = self.headers.get("Range")
        if wanted and self.ranges:
            start = int(wanted.removeprefix("bytes=").rstrip("-"))
            self.send_response(206)
            data = data[start:]
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[Hub]]:
    Hub.files = {"/onnx/model.onnx": ONE, "/tokenizer.json": TWO}
    Hub.ranges = True
    Hub.seen = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hub)
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    monkeypatch.setattr(install, "HUB", f"http://127.0.0.1:{server.server_address[1]}")
    yield Hub
    server.shutdown()
    server.server_close()


def test_a_file_is_fetched_through_the_redirect_and_kept_when_its_hash_holds(hub: type[Hub]) -> None:
    embedder = small()
    assert install.fetch(embedder, embedder.model_file) is True
    target = config.file_path(embedder, embedder.model_file)
    assert target.read_bytes() == ONE and not target.with_name("model.onnx.part").exists()
    assert hub.seen[0] == (f"/example/fake-embedder/resolve/{FAKE.revision}/onnx/model.onnx", None)
    assert install.fetch(embedder, embedder.model_file) is False


def test_a_broken_download_resumes_from_its_part(hub: type[Hub]) -> None:
    embedder = small()
    target = config.file_path(embedder, embedder.model_file)
    target.parent.mkdir(parents=True)
    target.with_name("model.onnx.part").write_bytes(ONE[:100])
    assert install.fetch(embedder, embedder.model_file)
    assert target.read_bytes() == ONE
    assert hub.seen[-1] == ("/cdn/onnx/model.onnx", "bytes=100-")


def test_a_server_that_ignores_the_range_sends_it_whole(hub: type[Hub]) -> None:
    hub.ranges = False
    embedder = small()
    target = config.file_path(embedder, embedder.model_file)
    target.parent.mkdir(parents=True)
    target.with_name("model.onnx.part").write_bytes(b"x" * 100)
    assert install.fetch(embedder, embedder.model_file)
    assert target.read_bytes() == ONE


def test_a_part_as_long_as_the_file_starts_over(hub: type[Hub]) -> None:
    embedder = small()
    target = config.file_path(embedder, embedder.model_file)
    target.parent.mkdir(parents=True)
    target.with_name("model.onnx.part").write_bytes(b"x" * len(ONE))
    assert install.fetch(embedder, embedder.model_file)
    assert target.read_bytes() == ONE


def test_a_file_whose_hash_differs_is_removed_and_refused(hub: type[Hub]) -> None:
    hub.files["/tokenizer.json"] = b'{"tokenizer": fake}'
    embedder = small()
    with pytest.raises(DocketError, match=r"tokenizer\.json didn't match its pinned sha256 and was removed"):
        install.fetch(embedder, embedder.tokenizer_file)
    target = config.file_path(embedder, embedder.tokenizer_file)
    assert not target.exists() and not target.with_name("tokenizer.json.part").exists()


def done(code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, out, err)


@pytest.mark.parametrize(
    ("wanted", "smi", "answer", "system", "device"),
    [
        ("auto", None, done(), "linux", "cpu"),
        ("auto", "nvidia-smi", done(0, "GPU 0: An Example GPU"), "win32", "cuda"),
        ("auto", "nvidia-smi", done(9, "", "NVIDIA-SMI has failed"), "linux", "cpu"),
        ("auto", "nvidia-smi", done(0, "GPU 0: An Example GPU"), "darwin", "cpu"),
        ("cpu", None, done(), "darwin", "cpu"),
        ("cuda", None, done(), "linux", "cuda"),
    ],
)
def test_the_device_is_the_one_asked_for_or_found(
    monkeypatch: pytest.MonkeyPatch,
    *,
    wanted: str,
    smi: str | None,
    answer: subprocess.CompletedProcess[str],
    system: str,
    device: str,
) -> None:
    monkeypatch.setattr(sys, "platform", system)
    assert install.pick_device(wanted, which=lambda _: smi, run=lambda *a, **k: answer) == device


def test_cuda_on_a_mac_and_an_unknown_device_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(DocketError, match="CUDA runs on Windows and Linux"):
        install.pick_device("cuda")
    with pytest.raises(DocketError, match=r"--device is auto, cpu or cuda, not 'tpu'"):
        install.pick_device("tpu")


class Uv:
    """uv as the install calls it: a venv makes the environment's Python, an install takes the hashed file."""

    def __init__(self, fail_at: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail_at = fail_at

    def __call__(self, args: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        step = args[1]
        if step == self.fail_at:
            return done(2, "", "error: no matching distribution\nhint: check the index")
        if step == "venv":
            python = config.env_python(Path(args[-1]))
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"")
        return done()


def fake_start(choice: Choice, device: str) -> Worker:
    return Worker(fake_command(dims=choice.embedder.dims))


def test_install_builds_fetches_checks_times_and_turns_models_on(
    hub: type[Hub], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(catalogue.EMBEDDERS, "fake", small())
    uv = Uv()
    said: list[str] = []
    found = install.install(
        "cpu", embedder_name="fake", say=said.append, which=lambda name: f"/bin/{name}", run=uv, start=fake_start
    )
    assert found.device == "cpu" and install.MIN_BUDGET <= found.budget <= install.MAX_BUDGET
    assert uv.calls[0][:5] == ["/bin/uv", "venv", "--clear", "--python", sys.executable]
    assert uv.calls[1][:3] == ["/bin/uv", "pip", "install"] and "--require-hashes" in uv.calls[1]
    assert uv.calls[1][-1].endswith("requirements-cpu.txt")
    assert config.installed("cpu") == found
    assert config.read() == Choice("cpu", small())
    assert said[0].startswith("models: fake (MIT) on cpu; downloads 0 MB of model files and about 50 MB of packages")
    assert "onnx/model.onnx: downloaded" in said and said[-1].startswith("ready: fake on cpu")
    # Again: the environment and the files are there, so only the check and the timing run.
    uv.calls.clear()
    said.clear()
    install.install("cpu", embedder_name="fake", say=said.append, which=lambda n: f"/bin/{n}", run=uv, start=fake_start)
    assert uv.calls == [] and "onnx/model.onnx: already here" in said


def test_a_failing_uv_step_says_which_and_shows_its_last_lines(hub: type[Hub], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(catalogue.EMBEDDERS, "fake", small())
    with pytest.raises(DocketError, match=r"installing the models' packages failed \(uv exit 2\):\nerror: no matching"):
        install.install("cpu", embedder_name="fake", say=lambda _: None, which=lambda n: n, run=Uv("pip"))
    assert config.installed("cpu") != "" and config.read() != Choice("cpu", small())


def test_install_refuses_without_sqlite_vec_without_uv_or_for_an_unknown_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(DocketError, match="no embedder 'large'; there is e5-small"):
        install.install("cpu", embedder_name="large", say=lambda _: None)
    with pytest.raises(DocketError, match="built with uv, which isn't on PATH"):
        install.install("cpu", say=lambda _: None, which=lambda _: None)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(DocketError, match="vectors need sqlite-vec, which has no build for this system"):
        install.install("cpu", say=lambda _: None)
    assert install.vectors_problem() is not None


@pytest.mark.parametrize(
    ("spec", "device", "said"),
    [
        ({"dims": 32}, "cpu", "the worker's model gives 32 dims, not 64"),
        ({}, "cuda", "ONNX Runtime fell back to the CPU: CUDA 13 needs an NVIDIA driver of R580 or later"),
        ({"bad_hello": True}, "cpu", "said hello in a form this Docket doesn't read\nfake worker starting"),
    ],
)
def test_the_check_refuses_a_worker_that_isnt_right(spec: dict[str, Any], device: str, said: str) -> None:
    worker = Worker(fake_command(**spec))
    with pytest.raises(DocketError, match=said):
        install.check_and_time(worker, small(), device)  # type: ignore[arg-type]
    assert not worker.up


def test_the_budget_is_what_the_machine_embeds_in_its_time() -> None:
    clock = iter([0.0, 2.0])
    worker = Worker(fake_command())
    # 16 inputs of 1,209 characters (the prefix and 1,200) in 2 s: 9,672 a second, 2,901 in 0.3 s.
    assert install.check_and_time(worker, small(), "cpu", clock=lambda: next(clock)) == 2901


def test_remove_deletes_the_environments_the_files_and_the_choice(
    hub: type[Hub], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(catalogue.EMBEDDERS, "fake", small())
    install.install("cpu", embedder_name="fake", say=lambda _: None, which=lambda n: n, run=Uv(), start=fake_start)
    said: list[str] = []
    install.remove(said.append)
    assert not config.env_dir("cpu").exists()
    assert not config.files_dir().exists()
    assert config.read() == Off() and said[-1] == "models are off on this machine; keyword search goes on"
    install.remove(said.append)
    assert json.dumps(said).count("removed") == 2
