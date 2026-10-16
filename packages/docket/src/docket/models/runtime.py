"""Opening the models for a command or the service: the user's choice, what the install left, and a worker for it.

`docket search` only embeds a query, so it runs its worker on the CPU whatever the device: the CUDA provider takes
about 8 s to start, the CPU's under 2 s, a query takes milliseconds on either, and both give the same vectors. It
reranks only where the reranker is the CPU's; on a GPU machine the service reranks (DR-0457). `docket eval` measures
what the service does, so it starts its worker where the reranker runs. The service and the embedding pass run on the
device the user chose.

The spec names the reranker only when it is on here: the install timed it within its limit (DR-0455), for the
catalogue's name and revision, and its files are here (DR-0451).
"""

from __future__ import annotations

import os
from typing import Any

from docket.models.catalogue import Embedder, Reranker
from docket.models.client import Worker, worker_command
from docket.models.config import (
    RERANK_ON_SECONDS,
    Choice,
    Device,
    Installed,
    RerankTiming,
    env_dir,
    env_python,
    file_path,
    installed,
    missing_files,
    read,
)
from docket.models.provider import Models, Off


def threads() -> int:
    """The worker's ONNX Runtime threads for the embedder: half the logical cores, so the pass leaves the machine
    room."""
    return max(1, (os.cpu_count() or 2) // 2)


def rerank_spec(reranker: Reranker, batch_seconds: float) -> dict[str, Any]:
    return {
        "name": reranker.name,
        "model": str(file_path(reranker, reranker.model_file)),
        "tokenizer": str(file_path(reranker, reranker.tokenizer_file)),
        "max_tokens": reranker.max_tokens,
        "device": reranker.device,
        "batch_seconds": batch_seconds,
    }


def spec(embedder: Embedder, device: Device, rerank: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model": str(file_path(embedder, embedder.model_file)),
        "tokenizer": str(file_path(embedder, embedder.tokenizer_file)),
        "dims": embedder.dims,
        "max_tokens": embedder.max_tokens,
        "device": device,
        "threads": threads(),
    }
    if rerank is not None:
        out["reranker"] = rerank
    return out


def worker_for(choice: Choice, device: Device, rerank: dict[str, Any] | None = None) -> Worker:
    """A worker running the chosen embedder on a device, and the reranker a spec names, from the chosen device's
    environment (which, for CUDA, has the CPU's provider as well)."""
    return Worker(worker_command(env_python(env_dir(choice.device)), spec(choice.embedder, device, rerank)))


def timed(choice: Choice, found: Installed) -> RerankTiming | str | None:
    """The reranker's timing when it is on here, why it is off, or None when the user chose none."""
    reranker = choice.reranker
    if reranker is None:
        return choice.reranker_why
    timing = found.rerank
    if timing is None or not timing.fits(reranker):
        return f"{reranker.name} isn't timed on this machine; docket models install times it"
    if timing.seconds is None:
        return f"{reranker.name} is off on this machine: {timing.why}; docket models install times it again"
    if not timing.on:
        return (
            f"{reranker.name} is off on this machine: {timing.pairs} pairs took {timing.seconds:.2f} s, past "
            f"{RERANK_ON_SECONDS:g} s; docket models install times it again"
        )
    if missing_files(reranker):
        return f"{reranker.name}'s files are missing; run docket models install"
    return timing


def open_models(
    *, queries_only: bool = False, measure: bool = False, start: bool = True, rerank: bool = True
) -> Models | Off:
    """The models as the user chose them, their worker started unless asked not to; or Off with why they can't run.
    `queries_only` is `docket search`'s worker, `measure` `docket eval`'s; a command that only embeds passes rerank
    False, and its worker loads no reranker."""
    choice = read()
    if isinstance(choice, Off):
        return choice
    found = installed(choice.device)
    if isinstance(found, str):
        return Off(found)
    if missing_files(choice.embedder):
        return Off("the model files are missing; run docket models install")
    timing = timed(choice, found) if rerank else None
    reranker = choice.reranker if isinstance(timing, RerankTiming) else None
    device: Device = choice.device
    if queries_only:
        device = "cpu"
    elif measure:
        device = reranker.device if reranker is not None else "cpu"
    why = timing if isinstance(timing, str) else None
    said = False
    if reranker is not None and reranker.device != device:
        why = (
            "the reranker runs on the GPU in the service; docket search goes without it"
            if queries_only
            else f"{reranker.name} runs on {reranker.device}, and the models on {device} here"
        )
        said, reranker = True, None
    named, pairs = None, 0
    if reranker is not None and isinstance(timing, RerankTiming):
        named, pairs = rerank_spec(reranker, timing.batch_seconds), timing.pairs
    worker = worker_for(choice, device, named)
    if start:
        worker.ensure()
    return Models(
        choice.embedder, worker, found.budget, reranker=reranker, pairs=pairs, rerank_why=why, rerank_said=said
    )
