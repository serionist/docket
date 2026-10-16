"""The worker's protocol and its ONNX Runtime backend, in-process: the backend against stubbed ONNX Runtime and
tokenizers modules, so no model is needed. The real runtime runs in the models CI job."""

from __future__ import annotations

import base64
import importlib
import io
import json
import struct
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from models_kit import FakeBackend, trigram_vector

from docket.models.worker import CPU, CUDA, OnnxBackend, answer, main, serve


def test_hello_and_embed_answer_with_their_request_id() -> None:
    backend = FakeBackend({"dims": 8})
    hello = answer(backend, json.dumps({"id": 7, "op": "hello"}))
    assert hello == {"id": 7, "ok": True, "dims": 8, "device": "cpu", "provider": CPU, "onnxruntime": "fake"}
    embedded = answer(backend, json.dumps({"id": 8, "op": "embed", "texts": ["paper", "papír"]}))
    assert embedded["id"] == 8 and embedded["ok"] is True
    assert base64.b64decode(str(embedded["vectors"])) == trigram_vector("paper", 8) + trigram_vector("papír", 8)


@pytest.mark.parametrize(
    ("line", "error"),
    [
        ("not json", "JSONDecodeError"),
        ("[1, 2]", "BadRequestError"),
        ('{"id": 1, "op": "reboot"}', "BadRequestError"),
        ('{"id": 2, "op": "embed", "texts": "one"}', "BadRequestError"),
        ('{"id": 3, "op": "embed", "texts": [1]}', "BadRequestError"),
    ],
)
def test_a_request_it_cant_read_is_answered_with_its_type(line: str, error: str) -> None:
    got = answer(FakeBackend({}), line)
    assert got["ok"] is False and got["error"] == error


def test_a_failing_embed_names_its_type_and_never_the_text() -> None:
    got = answer(
        FakeBackend({"refuse_on": "the secret price"}),
        json.dumps({"id": 4, "op": "embed", "texts": ["the secret price"]}),
    )
    assert got == {"id": 4, "ok": False, "error": "ValueError"}
    assert "secret" not in json.dumps(got)


def test_serve_answers_each_line_and_skips_blank_ones() -> None:
    lines = (
        "\n" + json.dumps({"id": 1, "op": "hello"}) + "\n\n" + json.dumps({"id": 2, "op": "embed", "texts": []}) + "\n"
    )
    out = io.StringIO()
    assert serve(FakeBackend({}), io.StringIO(lines), out) == 0
    answers = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [a["id"] for a in answers] == [1, 2] and answers[1]["vectors"] == ""


def test_main_wants_one_spec(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([], io.StringIO(), io.StringIO()) == 2
    assert "usage" in capsys.readouterr().err


def test_main_says_the_models_did_not_load_by_type_only(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(['{"model": "missing.onnx"}'], io.StringIO(), io.StringIO()) == 3
    assert capsys.readouterr().err.strip() == "the models didn't load: KeyError"


# -- the ONNX Runtime backend, stubbed -----------------------------------------------------------------------------


class StubSession:
    def __init__(self, path: str, options: Any, providers: list[Any], inputs: list[str]) -> None:
        self.path = path
        self.options = options
        self.providers = providers
        self.inputs = inputs
        self.feeds: list[dict[str, Any]] = []

    def get_inputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=name) for name in self.inputs]

    def get_providers(self) -> list[str]:
        # A CUDA session that couldn't load CUDA runs on the CPU, and says so first.
        return [CPU]

    def run(self, output_names: None, input_feed: dict[str, Any]) -> list[Any]:
        assert output_names is None
        self.feeds.append(input_feed)
        ids = input_feed["input_ids"]
        # Each token's hidden state is its id in every dim, so a mean over the mask is checkable.
        return [np.repeat(ids[:, :, None].astype(np.float32), 4, axis=2)]


class StubRuntime:
    __version__ = "1.30.0"

    def __init__(self, inputs: list[str]) -> None:
        self.inputs = inputs
        self.preloaded = 0
        self.session: StubSession | None = None

    def SessionOptions(self) -> SimpleNamespace:  # noqa: N802 - ONNX Runtime's name
        return SimpleNamespace(intra_op_num_threads=0, inter_op_num_threads=0)

    def InferenceSession(self, path: str, options: Any, providers: list[Any]) -> StubSession:  # noqa: N802
        self.session = StubSession(path, options, providers, self.inputs)
        return self.session

    def preload_dlls(self) -> None:
        self.preloaded += 1


class StubTokenizer:
    def __init__(self) -> None:
        self.limit = 0
        self.padded = False

    @classmethod
    def from_file(cls, path: str) -> StubTokenizer:
        assert path == "tok.json"
        return cls()

    def enable_truncation(self, max_length: int) -> None:
        self.limit = max_length

    def enable_padding(self) -> None:
        self.padded = True

    def encode_batch(self, texts: list[str]) -> list[SimpleNamespace]:
        longest = max(len(t.split()) for t in texts)
        out: list[SimpleNamespace] = []
        for t in texts:
            ids = [len(w) for w in t.split()]
            pad = longest - len(ids)
            out.append(SimpleNamespace(ids=ids + [99] * pad, attention_mask=[1] * len(ids) + [0] * pad))
        return out


def stubbed(monkeypatch: pytest.MonkeyPatch, inputs: list[str]) -> StubRuntime:
    runtime = StubRuntime(inputs)
    tokenizers = SimpleNamespace(Tokenizer=StubTokenizer)
    monkeypatch.setattr(
        importlib, "import_module", lambda name: {"onnxruntime": runtime, "tokenizers": tokenizers}[name]
    )
    return runtime


SPEC: dict[str, Any] = {"model": "m.onnx", "tokenizer": "tok.json", "dims": 4, "max_tokens": 16, "threads": 3}


def test_the_backend_pools_over_the_mask_and_returns_unit_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = stubbed(monkeypatch, ["input_ids", "attention_mask"])
    backend = OnnxBackend({**SPEC, "device": "cpu"})
    session = runtime.session
    assert session is not None and session.providers == [CPU] and session.options.intra_op_num_threads == 3
    assert runtime.preloaded == 0 and backend.tokenizer.limit == 16  # type: ignore[attr-defined]
    data = backend.embed(["ab cdef", "xyz"])
    rows = [struct.unpack("<4f", data[i * 16 : (i + 1) * 16]) for i in range(2)]
    # Every dim is the same mean, so a unit vector holds 0.5 in each; the padding (99) never counts.
    assert all(abs(v - 0.5) < 1e-6 for row in rows for v in row)
    # The first feed is the warm-up the backend runs as it loads.
    assert len(session.feeds) == 2 and "token_type_ids" not in session.feeds[1]
    assert backend.embed([]) == b""


def test_the_backend_feeds_token_types_when_the_model_takes_them(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = stubbed(monkeypatch, ["input_ids", "attention_mask", "token_type_ids"])
    OnnxBackend({**SPEC, "device": "cpu"}).embed(["one two"])
    assert runtime.session is not None
    assert runtime.session.feeds[1]["token_type_ids"].tolist() == [[0, 0]]


def test_a_cuda_backend_loads_the_libraries_and_says_which_provider_it_got(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = stubbed(monkeypatch, ["input_ids", "attention_mask"])
    backend = OnnxBackend({**SPEC, "device": "cuda"})
    assert runtime.preloaded == 1
    assert runtime.session is not None and runtime.session.providers[0] == (CUDA, {"device_id": 0})
    assert backend.describe() == {
        "dims": 4, "device": "cuda", "provider": CPU, "onnxruntime": "1.30.0", "numpy": np.__version__,
    }  # fmt: skip


def test_main_serves_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    stubbed(monkeypatch, ["input_ids", "attention_mask"])
    out = io.StringIO()
    lines = json.dumps({"id": 1, "op": "embed", "texts": ["a bb"]}) + "\n"
    assert main([json.dumps({**SPEC, "device": "cpu"})], io.StringIO(lines), out) == 0
    vectors = base64.b64decode(json.loads(out.getvalue())["vectors"])
    assert len(vectors) == 16
