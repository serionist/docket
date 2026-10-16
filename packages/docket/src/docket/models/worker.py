"""The models' worker: one process that loads the embedder and answers JSON lines on stdin and stdout (DR-0330).

It runs as `<env python> -I -X utf8 worker.py '<spec>'` in the environment `docket models install` built (DR-0332),
which holds ONNX Runtime, tokenizers and numpy and nothing of Docket's, so this file imports only the standard library
at the top and its backend's packages when the backend loads. The spec is JSON: the model and tokenizer files, the
dims, the token limit, the device and the threads.

A request is `{"id": n, "op": "hello"}` or `{"id": n, "op": "embed", "texts": [...]}`; the answer carries the same id
and `ok`, the vectors as base64 of float32, little-endian, one row per text, or `error` with an exception's type only,
since its message can quote a text. The core builds each text, prefixes included; the worker only embeds. Anything a
library prints goes to stderr, which the client keeps and never logs.
"""

from __future__ import annotations

import base64
import importlib
import json
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol, TextIO, cast

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

# ONNX Runtime's providers, by the device the spec names.
CUDA = "CUDAExecutionProvider"
CPU = "CPUExecutionProvider"


class Backend(Protocol):
    def describe(self) -> dict[str, object]: ...

    def embed(self, texts: list[str]) -> bytes: ...


# The parts of ONNX Runtime and tokenizers the backend uses, typed here: neither ships stubs strict mode reads.
class _Options(Protocol):
    intra_op_num_threads: int
    inter_op_num_threads: int


class _Input(Protocol):
    @property
    def name(self) -> str: ...


class _Session(Protocol):
    def get_inputs(self) -> Sequence[_Input]: ...

    def get_providers(self) -> list[str]: ...

    def run(self, output_names: None, input_feed: dict[str, Any]) -> Sequence[Any]: ...


class _Runtime(Protocol):
    __version__: str

    def SessionOptions(self) -> _Options: ...  # noqa: N802 - ONNX Runtime's name

    def InferenceSession(self, path: str, options: _Options, providers: list[Any]) -> _Session: ...  # noqa: N802

    def preload_dlls(self) -> None: ...


class _Encoding(Protocol):
    @property
    def ids(self) -> list[int]: ...

    @property
    def attention_mask(self) -> list[int]: ...


class _Tokenizer(Protocol):
    def enable_truncation(self, max_length: int) -> None: ...

    def enable_padding(self) -> None: ...

    def encode_batch(self, texts: list[str]) -> list[_Encoding]: ...


class BadRequestError(ValueError):
    """A request the worker can't read: not JSON, not an object, an unknown op or texts that aren't a list of text."""


def _texts(request: dict[str, Any]) -> list[str]:
    texts: object = request.get("texts")
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in cast(list[object], texts)):
        raise BadRequestError
    return cast(list[str], texts)


def answer(backend: Backend, line: str) -> dict[str, object]:
    """One request's answer, never an exception: an error is its type's name."""
    request_id: object = None
    try:
        data: object = json.loads(line)
        if not isinstance(data, dict):
            raise BadRequestError
        request = cast(dict[str, Any], data)
        request_id = request.get("id")
        op = request.get("op")
        if op == "hello":
            return {"id": request_id, "ok": True, **backend.describe()}
        if op == "embed":
            vectors = backend.embed(_texts(request))
            return {"id": request_id, "ok": True, "vectors": base64.b64encode(vectors).decode("ascii")}
        raise BadRequestError
    except Exception as e:  # every failure is answered, by its type alone
        return {"id": request_id, "ok": False, "error": type(e).__name__}


def serve(backend: Backend, stdin: TextIO, stdout: TextIO) -> int:
    """Answer each line until stdin closes."""
    for line in stdin:
        if not line.strip():
            continue
        stdout.write(json.dumps(answer(backend, line), ensure_ascii=True) + "\n")
        stdout.flush()
    return 0


class OnnxBackend:
    """The embedder on ONNX Runtime: tokens to the model, mean pooling over the attention mask, unit length."""

    def __init__(self, spec: dict[str, Any]) -> None:
        import numpy  # noqa: PLC0415 - only the worker's environment has it

        onnxruntime = cast(_Runtime, importlib.import_module("onnxruntime"))
        tokenizers: Any = importlib.import_module("tokenizers")
        self.np = numpy
        self.dims = int(spec["dims"])
        self.device = str(spec["device"])
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = int(spec["threads"])
        options.inter_op_num_threads = 1
        providers: list[Any] = [CPU]
        if self.device == "cuda":
            # The CUDA and cuDNN wheels' libraries, which ONNX Runtime doesn't find on its own on Windows.
            onnxruntime.preload_dlls()
            providers = [(CUDA, {"device_id": 0}), CPU]
        self.session = onnxruntime.InferenceSession(str(spec["model"]), options, providers=providers)
        self.inputs = {i.name for i in self.session.get_inputs()}
        tokenizer = cast(_Tokenizer, tokenizers.Tokenizer.from_file(str(spec["tokenizer"])))
        tokenizer.enable_truncation(int(spec["max_tokens"]))
        tokenizer.enable_padding()
        self.tokenizer = tokenizer
        self.versions = {"onnxruntime": str(onnxruntime.__version__), "numpy": str(numpy.__version__)}
        # CUDA's first run sets up its kernels for about 6 s, after which any length takes milliseconds: done here,
        # before the hello, so no query waits for it.
        self.embed(["warm up"])

    def describe(self) -> dict[str, object]:
        # The provider a session got, which is the CPU's when CUDA failed to load: the install checks it.
        return {"dims": self.dims, "device": self.device, "provider": self.session.get_providers()[0], **self.versions}

    def embed(self, texts: list[str]) -> bytes:
        np = self.np
        if not texts:
            return b""
        encoded = self.tokenizer.encode_batch(texts)
        ids: NDArray[np.int64] = np.array([e.ids for e in encoded], dtype=np.int64)
        mask: NDArray[np.int64] = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        feed: dict[str, NDArray[np.int64]] = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = cast("NDArray[np.float32]", self.session.run(None, feed)[0])
        summed = (hidden * mask[:, :, None]).sum(axis=1)
        pooled = summed / np.maximum(mask.sum(axis=1, keepdims=True), 1)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        unit = pooled / np.maximum(norms, 1e-12)
        return unit.astype("<f4").tobytes()


def main(argv: list[str], stdin: TextIO, stdout: TextIO) -> int:
    if len(argv) != 1:
        print("usage: worker.py '<spec as JSON>'", file=sys.stderr)
        return 2
    try:
        backend = OnnxBackend(json.loads(argv[0]))
    except Exception as e:  # the client reads the exit code; the type goes to stderr
        print(f"the models didn't load: {type(e).__name__}", file=sys.stderr)
        return 3
    return serve(backend, stdin, stdout)


if __name__ == "__main__":  # pragma: no cover - the worker's process; main() is tested in-process
    real = sys.stdout
    # A library's print goes to stderr, so it can't break a JSON line.
    sys.stdout = sys.stderr
    sys.exit(main(sys.argv[1:], sys.stdin, real))
