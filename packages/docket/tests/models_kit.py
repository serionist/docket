"""The models' test doubles: a deterministic embedder, a worker process that runs it with Docket's own Python, and the
same embedder in-process for the index and search tests. No model, no ONNX Runtime, no network.

A text's vector is its character trigrams hashed into a few dims, signed and normalized: texts sharing letters lie
close, so a test can plant a chunk only the vector list finds by giving it the query's letters and none of its words.
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from docket.models import config
from docket.models.catalogue import Embedder, ModelFile
from docket.models.client import Worker
from docket.models.provider import Models

FAKE_WORKER = Path(__file__).with_name("fake_worker.py")
DIMS = 64

FAKE = Embedder(
    name="fake",
    repo="example/fake-embedder",
    revision="0123456789abcdef0123456789abcdef01234567",
    model_file=ModelFile("model.onnx", 3, hashlib.sha256(b"one").hexdigest()),
    tokenizer_file=ModelFile("tokenizer.json", 3, hashlib.sha256(b"two").hexdigest()),
    dims=DIMS,
    max_tokens=512,
    query_prefix="query: ",
    passage_prefix="passage: ",
    licence="MIT",
)


def trigram_vector(text: str, dims: int = DIMS) -> bytes:
    """A text's vector: its prefix cut off, its lowercase character trigrams hashed into `dims` signed buckets."""
    body = text.split(": ", 1)[1] if text.startswith(("query: ", "passage: ")) else text
    folded = f"  {body.lower()}  "
    values = [0.0] * dims
    for i in range(len(folded) - 2):
        digest = hashlib.blake2b(folded[i : i + 3].encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dims
        values[bucket] += 1.0 if digest[4] & 1 else -1.0
    norm = sum(v * v for v in values) ** 0.5 or 1.0
    return struct.pack(f"<{dims}f", *(v / norm for v in values))


class FakeBackend:
    """The worker's backend for tests, with knobs for the ways a real one fails."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.dims = int(spec.get("dims", DIMS))
        self.calls = 0

    def describe(self) -> dict[str, object]:
        time.sleep(float(self.spec.get("slow_hello", 0)))
        if self.spec.get("bad_hello"):
            return {"dims": "many"}
        return {"dims": self.dims, "device": "cpu", "provider": "CPUExecutionProvider", "onnxruntime": "fake"}

    def embed(self, texts: list[str]) -> bytes:
        self.calls += 1
        order = self.spec.get("order_file")
        if order:
            with Path(order).open("a", encoding="utf-8") as f:
                f.write(texts[0] + "\n")
        if self.spec.get("die_on") in texts:
            sys.exit(5)
        if self.spec.get("refuse_on") in texts:
            raise ValueError
        time.sleep(float(self.spec.get("delay", 0)))
        data = b"".join(trigram_vector(t, self.dims) for t in texts)
        return data[:-4] if self.spec.get("wrong_size") else data


def fake_command(**spec: Any) -> list[str]:
    return [sys.executable, str(FAKE_WORKER), json.dumps({"dims": DIMS, **spec})]


def fake_worker(**spec: Any) -> Worker:
    timeouts = {
        k: spec.pop(k) for k in ("query_seconds", "hello_seconds", "batch_seconds", "restart_seconds") if k in spec
    }
    return Worker(fake_command(**spec), **timeouts)


class InProcess:
    """The worker's two calls answered in-process, for tests of the index and search: a query can be made slow or
    missing, and every batch is counted."""

    def __init__(self) -> None:
        self.up = True
        self.why: str | None = None
        self.batches: list[list[str]] = []
        self.queries: list[str] = []
        self.ensured = 0

    def ensure(self) -> bool:
        self.ensured += 1
        return self.up

    def query(self, text: str) -> bytes | str:
        self.queries.append(text)
        if not self.up:
            return self.why or "the models' worker isn't running"
        return trigram_vector(text)

    def batch(self, texts: Sequence[str]) -> list[bytes]:
        self.batches.append(list(texts))
        return [trigram_vector(t) for t in texts]

    def stop(self) -> None:
        self.up = False


def fake_models(budget: int = 8192) -> tuple[Models, InProcess]:
    worker = InProcess()
    return Models(FAKE, worker, budget), worker


def make_env(device: config.Device, **marker: Any) -> None:
    env = config.env_dir(device)
    python = config.env_python(env)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"")
    found = {
        "device": device, "budget": 8192, "requirements": config.requirements_hash(device),
        "python": config.python_version(), **marker,
    }  # fmt: skip
    (env / config.MARKER).write_text(json.dumps(found), encoding="utf-8")
