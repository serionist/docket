"""Opening the models for a command or the service: the user's choice, what the install left, and a worker for it.

A command that only embeds queries (`docket search`, `docket eval`) runs its worker on the CPU whatever the device: the
CUDA provider takes about 8 s to start, the CPU's under 2 s, a query takes milliseconds on either, and both give the
same vectors. The embedding pass runs on the device the user chose.
"""

from __future__ import annotations

import os
from typing import Any

from docket.models.catalogue import Embedder
from docket.models.client import Worker, worker_command
from docket.models.config import Choice, Device, env_dir, env_python, file_path, installed, missing_files, read
from docket.models.provider import Models, Off


def threads() -> int:
    """The worker's ONNX Runtime threads: half the cores, so the pass leaves the machine room."""
    return max(1, (os.cpu_count() or 2) // 2)


def spec(embedder: Embedder, device: Device) -> dict[str, Any]:
    return {
        "model": str(file_path(embedder, embedder.model_file)),
        "tokenizer": str(file_path(embedder, embedder.tokenizer_file)),
        "dims": embedder.dims,
        "max_tokens": embedder.max_tokens,
        "device": device,
        "threads": threads(),
    }


def worker_for(choice: Choice, device: Device) -> Worker:
    """A worker running the chosen embedder on a device, from the chosen device's environment (which, for CUDA, has
    the CPU's provider as well)."""
    return Worker(worker_command(env_python(env_dir(choice.device)), spec(choice.embedder, device)))


def open_models(*, queries_only: bool = False, start: bool = True) -> Models | Off:
    """The models as the user chose them, their worker started unless asked not to; or Off with why they can't run."""
    choice = read()
    if isinstance(choice, Off):
        return choice
    found = installed(choice.device)
    if isinstance(found, str):
        return Off(found)
    if missing_files(choice.embedder):
        return Off("the model files are missing; run docket models install")
    worker = worker_for(choice, "cpu" if queries_only else choice.device)
    if start:
        worker.ensure()
    return Models(choice.embedder, worker, found.budget)
