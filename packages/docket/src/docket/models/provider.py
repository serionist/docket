"""What search and the embedding pass see of the models: an embedder and the worker that runs it, or why there are none.

The pass's batches are cut by a budget that the install measures on the machine, worth about 0.3 s of work (DR-0330):
texts are sorted by length, and a batch takes as many as fit, counting each at its batch's longest, since a batch pads
to it. The core has no tokenizer, so a text's cost is its length in characters, at most what the model's token limit
lets through; the install times the same cost, so the budget stays in step with it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from docket.models.catalogue import Embedder

# Past the model's 512 tokens the tokenizer cuts a text, so a longer one costs no more.
CHAR_CAP = 2048
DEFAULT_BUDGET = {"cpu": 8192, "cuda": 65536}


def cost(text: str) -> int:
    return max(1, min(len(text), CHAR_CAP))


def batches(texts: Sequence[str], budget: int) -> list[list[int]]:
    """The texts' places in batches, shortest first: each batch holds as many as fit the budget at its longest's cost,
    and one text alone is always a batch."""
    order = sorted(range(len(texts)), key=lambda i: (cost(texts[i]), i))
    out: list[list[int]] = []
    current: list[int] = []
    for i in order:
        # Sorted, so this text is the batch's longest.
        if current and cost(texts[i]) * (len(current) + 1) > budget:
            out.append(current)
            current = []
        current.append(i)
    if current:
        out.append(current)
    return out


class Runner(Protocol):
    """What the models need of a worker: `client.Worker`, or a test's double."""

    @property
    def up(self) -> bool: ...

    @property
    def why(self) -> str | None: ...

    def ensure(self) -> bool: ...

    def query(self, text: str) -> bytes | str: ...

    def batch(self, texts: Sequence[str]) -> list[bytes]: ...

    def stop(self) -> None: ...


@dataclass(frozen=True)
class Off:
    """No vectors, and why: None when models aren't set up, which needs no saying."""

    why: str | None = None


class Models:
    """An embedder and its worker."""

    def __init__(self, embedder: Embedder, worker: Runner, budget: int) -> None:
        self.embedder = embedder
        self.worker = worker
        self.budget = budget

    @property
    def why(self) -> str | None:
        """Why the worker can't answer now, or None when it is up."""
        return None if self.worker.up else (self.worker.why or "the models' worker isn't running")

    def query_vector(self, words: str) -> bytes | str:
        """A query's vector, or why there is none."""
        return self.worker.query(self.embedder.query(words))

    def passage_vectors(self, inputs: Sequence[str]) -> list[bytes]:
        """Each input's vector, in batches by the budget; raises WorkerError when the worker can't."""
        out: dict[int, bytes] = {}
        for batch in batches(inputs, self.budget):
            out.update(zip(batch, self.worker.batch([inputs[i] for i in batch]), strict=True))
        return [out[i] for i in range(len(inputs))]

    def close(self) -> None:
        self.worker.stop()
