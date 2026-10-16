"""The models' test doubles: a deterministic embedder and reranker, a worker process that runs them with Docket's own
Python, and the same in-process for the index and search tests. No model, no ONNX Runtime, no network.

A text's vector is its character trigrams hashed into a few dims, signed and normalized: texts sharing letters lie
close, so a test can plant a chunk only the vector list finds by giving it the query's letters and none of its words.
A pair's score is its two vectors' dot product, unless the text carries a planted score (`[score 0.9]`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import sqlite3
import struct
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from docket import userconfig
from docket.index.vectors import load_extension
from docket.models import catalogue, config, runtime
from docket.models.catalogue import RERANK_BATCH, Embedder, ModelFile, Reranker
from docket.models.client import Skip, Worker
from docket.models.provider import Models
from docket.models.worker import PastBudget, RerankerOff

FAKE_WORKER = Path(__file__).with_name("fake_worker.py")


def _cant_search() -> str | None:
    probe = sqlite3.connect(":memory:")
    try:
        return load_extension(probe)
    finally:
        probe.close()


# A Python whose SQLite can't load extensions searches without vectors, and says so (DR-0337); a test that scans
# vectors runs where it can, and always where DOCKET_TEST_VECTORS asks, as the models CI job does.
CANT_SEARCH = _cant_search()
needs_vectors = pytest.mark.skipif(
    CANT_SEARCH is not None and not os.environ.get("DOCKET_TEST_VECTORS"),
    reason=f"this Python can't search vectors: {CANT_SEARCH}",
)
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


FAKE_RERANKER = Reranker(
    name="fake-reranker",
    repo="example/fake-reranker",
    revision="89abcdef0123456789abcdef0123456789abcdef",
    device="cpu",
    model_file=ModelFile("onnx/model.onnx", 5, hashlib.sha256(b"three").hexdigest()),
    tokenizer_file=ModelFile("tokenizer.json", 4, hashlib.sha256(b"four").hexdigest()),
    max_tokens=256,
    pairs=30,
    licence="Apache-2.0",
    trained_on="trained on invented pairs",
)
PLANTED = re.compile(r"\[score (-?[0-9.]+)\]")


def pair_score(query: str, text: str) -> float:
    """A pair's score: the planted one when the text carries `[score x]`, else its two vectors' dot product."""
    planted = PLANTED.search(text)
    if planted:
        return float(planted.group(1))
    a = struct.unpack(f"<{DIMS}f", trigram_vector(query))
    b = struct.unpack(f"<{DIMS}f", trigram_vector(text))
    return sum(x * y for x, y in zip(a, b, strict=True))


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
        out: dict[str, object] = {
            "dims": self.dims, "device": "cpu", "provider": "CPUExecutionProvider", "onnxruntime": "fake",
        }  # fmt: skip
        rerank = self.spec.get("reranker")
        if rerank is not None:
            off = self.spec.get("reranker_off")
            provider = None if off else self.spec.get("rerank_provider", "CPUExecutionProvider")
            out["reranker"] = {"name": rerank["name"], "provider": provider, "off": off}
            if self.spec.get("bad_reranker_hello"):
                out["reranker"] = "loaded"
        return out

    def rerank(self, query: str, texts: list[str], deadline: float | None) -> bytes:
        if self.spec.get("reranker") is None or self.spec.get("reranker_off"):
            raise RerankerOff
        if self.spec.get("rerank_refuse"):
            raise ValueError
        if self.spec.get("rerank_die"):
            sys.exit(5)
        # Batch by batch, as the real reranker goes: none starts that its last one's time says ends past the deadline.
        took = float(self.spec["reranker"].get("batch_seconds", 0))
        for _ in range(0, max(1, len(texts)), RERANK_BATCH):
            if deadline is not None and time.time() + took > deadline:
                raise PastBudget
            began = time.time()
            time.sleep(float(self.spec.get("rerank_delay", 0)))
            took = time.time() - began
        data = struct.pack(f"<{len(texts)}f", *(pair_score(query, t) for t in texts))
        return data[:-4] if self.spec.get("rerank_wrong_size") else data

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
        # The reranks asked: (query, texts, deadline); and how the next ones answer, when a test sets it.
        self.reranks: list[tuple[str, list[str], float | None]] = []
        self.rerank_answer: Skip | None = None

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

    def rerank(self, query: str, texts: Sequence[str], deadline: float | None) -> list[float] | Skip:
        self.reranks.append((query, list(texts), deadline))
        if self.rerank_answer is not None:
            return self.rerank_answer
        return [pair_score(query, t) for t in texts]

    def stop(self) -> None:
        self.up = False


def fake_models(budget: int = 8192, *, pairs: int = 0, **kwargs: Any) -> tuple[Models, InProcess]:
    """The fake embedder in-process, and the fake reranker on with `pairs`."""
    worker = InProcess()
    reranker = FAKE_RERANKER if pairs else None
    return Models(FAKE, worker, budget, reranker=reranker, pairs=pairs, **kwargs), worker


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


# -- models chosen on this machine, as the CLI and the service read them ----------------------------------------------

GPU_RERANKER = dataclasses.replace(FAKE_RERANKER, name="fake-gpu-reranker", device="cuda")


def choose(block: dict[str, Any]) -> None:
    """The models block of the user's config; the rest, such as the service's token store, stays."""
    userconfig.set_section("models", block)


def choose_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(catalogue.EMBEDDERS, FAKE.name, FAKE)
    choose({"device": "cpu", "embedder": FAKE.name})


def timing(reranker: Reranker, seconds: float | None = 0.42, why: str | None = None) -> dict[str, Any]:
    return {
        "name": reranker.name,
        "revision": reranker.revision,
        "pairs": reranker.pairs,
        "seconds": seconds,
        "why": why,
    }


def rerank_worker(monkeypatch: pytest.MonkeyPatch, **knobs: Any) -> None:
    def worker(choice: config.Choice, device: str, rerank: dict[str, Any] | None = None) -> Worker:
        return Worker(fake_command(**({"reranker": rerank, **knobs} if rerank is not None else {})))

    monkeypatch.setattr(runtime, "worker_for", worker)


def with_reranker(monkeypatch: pytest.MonkeyPatch, reranker: Reranker = FAKE_RERANKER, seconds: float = 0.42) -> None:
    monkeypatch.setitem(catalogue.EMBEDDERS, FAKE.name, FAKE)
    monkeypatch.setitem(catalogue.RERANKERS, reranker.name, reranker)
    choose({"device": reranker.device, "embedder": FAKE.name, "reranker": reranker.name})
    make_env(reranker.device, reranker=timing(reranker, seconds))
    monkeypatch.setattr(runtime, "missing_files", lambda model: [])
