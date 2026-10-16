"""The service's models (DR-0330, DR-0336): the worker kept loaded, and the embedding pass after each index update.

The worker starts in the background when the service does, so the service answers at once; until it says hello,
searches go without vectors and say why. After each index update the pass embeds what is new, one pass at a time, and
at least once a minute it looks again, which also starts a stopped worker again (at most once a minute). The models
are read from the user's config when the service starts; `docket models install` says the service picks a change up
when it starts again. The log holds counts and reasons, never a text (DR-0155).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from docket.errors import DocketError
from docket.index.embedpass import PassReport, fill
from docket.models.client import Worker
from docket.models.provider import Models, Off
from docket.models.runtime import open_models
from docket.vault.vault import Vault

log = logging.getLogger("docket.service")

# How often the pass looks again with no update to wake it: a stopped worker starts again then too.
WAKE_SECONDS = 60.0
STOP_SECONDS = 10.0
# The worker's last lines of stderr that status shows: a traceback's end says why it stopped.
ERROR_LINES = 10


class ModelsStatus(BaseModel):
    """The service's models for its status: off, or a model and whether its worker is up, with why not and the end of
    what the worker wrote to stderr."""

    state: Literal["off", "up", "down"] = "off"
    model: str | None = None
    why: str | None = None
    embedded: int | None = None
    errors: list[str] = []


class ModelsKeeper:
    def __init__(self, vault: Callable[[], Vault], opener: Callable[..., Models | Off] | None = None) -> None:
        self._vault = vault
        self._opener = opener if opener is not None else open_models
        self.models: Models | Off = Off()
        self.last: PassReport | None = None
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Read the user's choice and, when models are on, start the worker and a first pass in the background."""
        self.models = self._opener(start=False)
        if isinstance(self.models, Off):
            if self.models.why:
                log.warning("models are off: %s", self.models.why)
            return
        self._thread = threading.Thread(target=self._loop, args=(self.models,), name="docket-models", daemon=True)
        self._thread.start()
        self.poke()

    def poke(self) -> None:
        """Ask for a pass: after an index update, or to start the worker."""
        self._wake.set()

    def _loop(self, models: Models) -> None:
        while True:
            self._wake.wait(WAKE_SECONDS)
            self._wake.clear()
            if self._stopping.is_set():
                break
            self.run_pass(models)

    def run_pass(self, models: Models) -> PassReport | None:
        if not models.worker.ensure():
            log.warning("the models' worker is down: %s", models.why)
            return None
        try:
            report = fill(self._vault(), models, stop=self._stopping.is_set)
        except (DocketError, OSError, sqlite3.Error) as e:
            log.warning("the embedding pass failed: %s", type(e).__name__)
            return None
        self.last = report
        if report.embedded or report.copied or report.dropped:
            log.info(
                "embedding pass: %d embedded, %d copied, %d of %d chunks have a vector",
                report.embedded, report.copied, report.have, report.total,
            )  # fmt: skip
        if report.why is not None:
            log.warning("the embedding pass stopped: %s", report.why)
        return report

    def for_search(self) -> Models | Off:
        return self.models

    def status(self) -> ModelsStatus:
        models = self.models
        if isinstance(models, Off):
            return ModelsStatus(why=models.why)
        why = models.why
        worker = models.worker
        return ModelsStatus(
            state="up" if why is None else "down",
            model=models.embedder.name,
            why=why,
            embedded=self.last.have if self.last is not None else None,
            errors=worker.stderr().strip().splitlines()[-ERROR_LINES:] if isinstance(worker, Worker) else [],
        )

    def close(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(STOP_SECONDS)
        if isinstance(self.models, Models):
            self.models.close()
