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
from models_kit import FakeBackend, pair_score, trigram_vector

from docket.models.worker import CPU, CUDA, OnnxBackend, PastBudget, RerankerOff, answer, main, serve


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
        if path == "broken.onnx":
            raise RuntimeError("a model that doesn't load")

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
        self.sessions: list[StubSession] = []

    def SessionOptions(self) -> SimpleNamespace:  # noqa: N802 - ONNX Runtime's name
        return SimpleNamespace(intra_op_num_threads=-1, inter_op_num_threads=0)

    def InferenceSession(self, path: str, options: Any, providers: list[Any]) -> StubSession:  # noqa: N802
        made = StubSession(path, options, providers, self.inputs)
        self.sessions.append(made)
        # The embedder's session, which the tests below read; a reranker's is the second.
        self.session = self.session or made
        return made

    def preload_dlls(self) -> None:
        self.preloaded += 1


class StubTokenizer:
    # What token_to_id answers for "<pad>": tokenizers without one answer None.
    pad: int | None = 1

    def __init__(self) -> None:
        self.limit = 0
        self.strategy = ""
        self.padded: tuple[int, str] | None = None

    @classmethod
    def from_file(cls, path: str) -> StubTokenizer:
        assert path in ("tok.json", "rtok.json")
        return cls()

    def enable_truncation(self, max_length: int, strategy: str = "longest_first") -> None:
        self.limit = max_length
        self.strategy = strategy

    def enable_padding(self, pad_id: int = 0, pad_token: str = "[PAD]") -> None:  # noqa: S107 - a token, no secret
        self.padded = (pad_id, pad_token)

    def token_to_id(self, token: str) -> int | None:
        return self.pad if token == "<pad>" else None  # noqa: S105 - a token, no secret

    def encode_batch(self, texts: list[Any]) -> list[SimpleNamespace]:
        # A pair is its text's words, then its query's: the stub model's score is the first word's length.
        words = [f"{t[1]} {t[0]}".split() if isinstance(t, tuple) else str(t).split() for t in texts]
        longest = max(len(w) for w in words)
        out: list[SimpleNamespace] = []
        for each in words:
            ids = [len(w) for w in each]
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


# -- the rerank op and the ONNX Runtime reranker -------------------------------------------------------------------

RERANK = {"name": "stub", "model": "r.onnx", "tokenizer": "rtok.json", "max_tokens": 8, "device": "cpu"}


def test_rerank_answers_a_score_per_text_in_order() -> None:
    backend = FakeBackend({"reranker": {"name": "fake-reranker"}})
    request = {"id": 9, "op": "rerank", "query": "paper price", "texts": ["the paper", "a boat"], "deadline": None}
    got = answer(backend, json.dumps(request))
    assert got["id"] == 9 and got["ok"] is True
    scores = struct.unpack("<2f", base64.b64decode(str(got["scores"])))
    assert scores == pytest.approx([pair_score("paper price", "the paper"), pair_score("paper price", "a boat")])


@pytest.mark.parametrize(
    ("request_", "error"),
    [
        ({"op": "rerank", "query": 3, "texts": ["a"]}, "BadRequestError"),
        ({"op": "rerank", "query": "q", "texts": ["a"], "deadline": True}, "BadRequestError"),
        ({"op": "rerank", "query": "q", "texts": ["a"], "deadline": "soon"}, "BadRequestError"),
        ({"op": "rerank", "query": "q", "texts": "a"}, "BadRequestError"),
    ],
)
def test_a_rerank_it_cant_read_is_answered_with_its_type(request_: dict[str, Any], error: str) -> None:
    got = answer(FakeBackend({"reranker": {"name": "fake-reranker"}}), json.dumps({"id": 1, **request_}))
    assert got == {"id": 1, "ok": False, "error": error}


def test_a_rerank_with_no_reranker_or_past_its_deadline_names_its_type_only() -> None:
    request = {"id": 2, "op": "rerank", "query": "the secret price", "texts": ["the secret price"], "deadline": 5}
    assert answer(FakeBackend({}), json.dumps(request)) == {"id": 2, "ok": False, "error": "RerankerOff"}
    got = answer(FakeBackend({"reranker": {"name": "fake-reranker"}}), json.dumps(request))
    assert got == {"id": 2, "ok": False, "error": "PastBudget"} and "secret" not in json.dumps(got)


def reranking(monkeypatch: pytest.MonkeyPatch, inputs: list[str], **rerank: Any) -> tuple[OnnxBackend, StubRuntime]:
    runtime = stubbed(monkeypatch, inputs)
    clock = iter(range(1000))
    backend = OnnxBackend({**SPEC, "device": "cpu", "reranker": {**RERANK, **rerank}}, clock=lambda: next(clock))
    return backend, runtime


def test_the_reranker_scores_each_pair_in_batches_of_ten(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, runtime = reranking(monkeypatch, ["input_ids", "attention_mask", "token_type_ids"])
    session = runtime.sessions[1]
    # One thread per physical core, ONNX Runtime's default; the tokenizer cuts the longer side and pads with <pad>.
    assert session.options.intra_op_num_threads == 0 and session.providers == [CPU]
    assert backend.reranker is not None and backend.reranker.tokenizer.strategy == "longest_first"  # type: ignore[attr-defined]
    assert backend.reranker.tokenizer.padded == (1, "<pad>")  # type: ignore[attr-defined]
    texts = [f"{'w' * n} tail" for n in range(1, 24)]
    scores = struct.unpack("<23f", backend.rerank("the question", texts, None))
    # The stub's score is the text's first word's length.
    assert list(scores) == [float(n) for n in range(1, 24)]
    # The warm-up's batch, then three of at most ten pairs, each with its token types.
    assert [len(f["input_ids"]) for f in session.feeds] == [1, 10, 10, 3]
    assert session.feeds[1]["token_type_ids"].tolist()[0] == [0] * len(session.feeds[1]["input_ids"][0])
    assert backend.describe()["reranker"] == {"name": "stub", "provider": CPU, "off": None}


def test_the_reranker_starts_no_batch_it_expects_to_end_past_its_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = stubbed(monkeypatch, ["input_ids", "attention_mask"])
    # The warm-up's two readings, then: a first batch the install's 3 s doesn't fit before 12; then one that starts at
    # 20, takes 5 s, and leaves the next, at 26, no room before 30.
    times = iter([0.0, 0.0, 10.0, 20.0, 25.0, 26.0])
    spec = {**SPEC, "device": "cpu", "reranker": {**RERANK, "batch_seconds": 3}}
    backend = OnnxBackend(spec, clock=lambda: next(times))
    with pytest.raises(PastBudget):
        backend.rerank("q", ["word"] * 25, 12.0)
    assert len(runtime.sessions[1].feeds) == 1
    with pytest.raises(PastBudget):
        backend.rerank("q", ["word"] * 25, 30.0)
    assert len(runtime.sessions[1].feeds) == 2 and "token_type_ids" not in runtime.sessions[1].feeds[1]


def test_a_reranker_on_the_wrong_provider_or_that_fails_to_load_is_off_and_vectors_go_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A CUDA reranker whose session says it runs on the CPU: about 4 s for 10 pairs, so it is off.
    backend, runtime = reranking(monkeypatch, ["input_ids", "attention_mask"], device="cuda")
    assert runtime.sessions[1].providers[0] == (CUDA, {"device_id": 0, "arena_extend_strategy": "kSameAsRequested"})
    assert backend.describe()["reranker"] == {
        "name": "stub", "provider": None, "off": f"the reranker's session runs on {CPU}, not {CUDA}",
    }  # fmt: skip
    assert len(backend.embed(["still here"])) == 16
    with pytest.raises(RerankerOff):
        backend.rerank("q", ["a"], None)
    backend, _ = reranking(monkeypatch, ["input_ids", "attention_mask"], model="broken.onnx")
    assert backend.describe()["reranker"] == {
        "name": "stub", "provider": None, "off": "the reranker didn't load: RuntimeError",
    }  # fmt: skip
    assert len(backend.embed(["still here"])) == 16


def test_a_tokenizer_with_no_pad_token_pads_with_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(StubTokenizer, "pad", None)
    backend, _ = reranking(monkeypatch, ["input_ids", "attention_mask"])
    assert backend.reranker is not None and backend.reranker.tokenizer.padded == (0, "<pad>")  # type: ignore[attr-defined]


def test_a_spec_without_a_reranker_loads_none_and_says_nothing_of_one(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = stubbed(monkeypatch, ["input_ids", "attention_mask"])
    backend = OnnxBackend({**SPEC, "device": "cpu"})
    assert len(runtime.sessions) == 1 and "reranker" not in backend.describe()
    with pytest.raises(RerankerOff):
        backend.rerank("q", ["a"], None)
