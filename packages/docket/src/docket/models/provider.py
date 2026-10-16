"""What search and the embedding pass see of the models: an embedder and the worker that runs it, the reranker when it
is on here, or why there are none.

The pass's batches are cut by a budget that the install measures on the machine, worth about 0.3 s of work (DR-0330):
texts are sorted by length, and a batch takes as many as fit, counting each at its batch's longest, since a batch pads
to it. The core has no tokenizer, so a text's cost is its length in characters, at most what the model's token limit
lets through; the install times the same cost, so the budget stays in step with it.

A rerank has a budget from when the search asks (DR-0454): the deadline goes to the worker, which starts no batch it
expects to end past it. The provider counts reranks done and those past the budget, and after three past it in a row
stops asking for five minutes.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from docket.models.catalogue import Embedder, Reranker
from docket.models.client import PAST_BUDGET, Skip

# Past the model's 512 tokens the tokenizer cuts a text, so a longer one costs no more.
CHAR_CAP = 2048
DEFAULT_BUDGET = {"cpu": 8192, "cuda": 65536}
# A search's rerank has this long from when it asks (DR-0454).
RERANK_SECONDS = 0.75
PAUSE_AFTER = 3
PAUSE_SECONDS = 300.0


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

    def rerank(self, query: str, texts: Sequence[str], deadline: float | None) -> list[float] | Skip: ...

    def stop(self) -> None: ...


@dataclass(frozen=True)
class Off:
    """No vectors, and why: None when models aren't set up, which needs no saying."""

    why: str | None = None


@dataclass
class RerankCounts:
    """Reranks done and past their budget since the models opened, and the pause after several in a row."""

    done: int = 0
    past_budget: int = 0
    in_a_row: int = 0
    paused_until: float | None = None


class Models:
    """An embedder and its worker, and the reranker the worker runs when it is on here: `rerank_why` says why it
    isn't, and `rerank_said` asks every search to say so, as `docket search` does where the reranker is the GPU's."""

    def __init__(
        self,
        embedder: Embedder,
        worker: Runner,
        budget: int,
        *,
        reranker: Reranker | None = None,
        pairs: int = 0,
        rerank_why: str | None = None,
        rerank_said: bool = False,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.embedder = embedder
        self.worker = worker
        self.budget = budget
        self.reranker = reranker
        self.pairs = pairs if reranker is not None else 0
        self.rerank_why = rerank_why
        self.rerank_said = rerank_said
        self.counts = RerankCounts()
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()

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

    def rerank(self, query: str, texts: Sequence[str], budget: float | None) -> list[float] | str:
        """The reranker's score for each text, or why there are none: past the budget (seconds from now; None for no
        budget), paused after several past it in a row, or the worker's reason."""
        with self._lock:
            until = self.counts.paused_until
            if until is not None and self._clock() < until:
                back = datetime.fromtimestamp(self._wall() + until - self._clock()).astimezone()
                return (
                    f"the reranker passed its budget {PAUSE_AFTER} times in a row, so search goes without it until "
                    f"{back:%H:%M}"
                )
        got = self.worker.rerank(query, texts, None if budget is None else self._wall() + budget)
        with self._lock:
            if not isinstance(got, Skip):
                self.counts.done += 1
                self.counts.in_a_row = 0
                return got
            if not got.budget:
                return got.why
            self.counts.past_budget += 1
            self.counts.in_a_row += 1
            if self.counts.in_a_row >= PAUSE_AFTER:
                self.counts.in_a_row = 0
                self.counts.paused_until = self._clock() + PAUSE_SECONDS
            if got.why == PAST_BUDGET and budget is not None:
                return f"the reranker passed its {budget:g} s budget"
            return got.why

    def close(self) -> None:
        self.worker.stop()
