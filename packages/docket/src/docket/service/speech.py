"""The service's speech queue (DR-0571): `transcribe(rid)` and `docket transcribe` queue a recording, and one thread
runs the jobs one at a time, each under the machine's speech lock, so a job the CLI runs waits its turn too.

After each index update, on CUDA only, the thread queues by itself the newest recordings whose stored audio reads ok
and that have no Whisper transcript yet (`job.due`), each at most once while the service runs, and never a stored file
this engine staged nothing from for good, which the state folder keeps across restarts: a sync's audio, once promoted,
gets its transcript. On a CPU nothing is queued by itself. The speech choice is read when the service starts.
The log holds counts, never a rid or a text (DR-0155).
"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from docket.errors import DocketError
from docket.speech import job, setup
from docket.speech.setup import Speech
from docket.vault.vault import Vault

log = logging.getLogger("docket.service")

STOP_SECONDS = 10.0
# The outcomes status keeps, newest first.
RECENT = 20
PERCENT = re.compile(r"^\s*(\d{1,3})%$")

JobState = Literal["queued", "running", "staged", "not staged"]


class SpeechJob(BaseModel):
    """One recording's job: where it is, and once it ended, what it did in Docket's words."""

    rid: str
    state: JobState
    # How many jobs run or wait before this one, while it is queued.
    ahead: int = 0
    percent: int | None = None
    line: str | None = None
    at: datetime


class SpeechStatus(BaseModel):
    """The service's speech for its status: off and why, or the model and device, the job running, what waits and
    the last outcomes."""

    state: Literal["off", "idle", "running"] = "off"
    why: str | None = None
    model: str | None = None
    device: str | None = None
    running: SpeechJob | None = None
    queued: list[str] = []
    recent: list[SpeechJob] = []


Run = Callable[..., job.Outcome]


def line(found: SpeechJob) -> str:
    """A job as the tool, the command and the Recording page say it."""
    if found.state == "queued":
        return f"queued for Whisper, {found.ahead} job(s) ahead of it" if found.ahead else "queued for Whisper, next"
    if found.state == "running":
        return "Whisper is transcribing it now" + (f", {found.percent}%" if found.percent is not None else "")
    return found.line or found.state


class SpeechQueue:
    def __init__(
        self,
        vault: Callable[[], Vault],
        *,
        chosen: Callable[[], Speech | str] = setup.chosen,
        run: Run = job.transcribe,
        due: Callable[[Vault, str], list[str]] = job.due,
    ) -> None:
        self._vault = vault
        self._chosen = chosen
        self._run = run
        self._due = due
        self.speech: Speech | str = "speech starts with the service"
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._waiting: deque[str] = deque()
        self._running: SpeechJob | None = None
        self._jobs: dict[str, SpeechJob] = {}
        self._recent: deque[SpeechJob] = deque(maxlen=RECENT)
        # The rids the service queued by itself once: a job that stages nothing isn't queued again by itself.
        self._auto: set[str] = set()
        self._scan = False
        self._engine = ""
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Read the machine's speech choice and start the queue's thread when speech is installed."""
        self.speech = self._chosen()
        if isinstance(self.speech, str):
            return
        self._engine = self.speech.model.engine
        self._thread = threading.Thread(target=self._loop, name="docket-speech", daemon=True)
        self._thread.start()

    def add(self, rid: str) -> SpeechJob:
        """Queue a recording, or say where its job already is; refused when speech is off in the service."""
        if isinstance(self.speech, str):
            raise DocketError(f"speech is off in the service: {self.speech}")
        with self._lock:
            known = self._jobs.get(rid)
            if known is None or known.state not in ("queued", "running"):
                self._waiting.append(rid)
                self._jobs[rid] = SpeechJob(rid=rid, state="queued", at=datetime.now(UTC))
            found = self._shown(rid)
        self._wake.set()
        return found

    def after_update(self) -> None:
        """After an index update: on CUDA, the thread looks for recordings to queue by itself."""
        if isinstance(self.speech, Speech) and self.speech.device == "cuda":
            self._scan = True
            self._wake.set()

    def job(self, rid: str) -> SpeechJob | None:
        """A recording's job as it stands, for the Recording page; None when the service ran none for it."""
        with self._lock:
            return self._shown(rid) if rid in self._jobs else None

    def status(self) -> SpeechStatus:
        speech = self.speech
        if isinstance(speech, str):
            return SpeechStatus(why=speech)
        with self._lock:
            running = self._running
            return SpeechStatus(
                state="running" if running is not None else "idle",
                model=speech.model.name,
                device=speech.device,
                running=running,
                queued=list(self._waiting),
                recent=list(self._recent),
            )

    def _shown(self, rid: str) -> SpeechJob:
        found = self._jobs[rid]
        if found.state != "queued":
            return found
        ahead = list(self._waiting).index(rid) + (self._running is not None)
        return found.model_copy(update={"ahead": ahead})

    def _loop(self) -> None:
        while not self._stopping.is_set():
            if self._scan:
                self._scan = False
                self._queue_due()
            with self._lock:
                rid = self._waiting.popleft() if self._waiting else None
                if rid is not None:
                    self._running = self._jobs[rid] = SpeechJob(rid=rid, state="running", at=datetime.now(UTC))
            if rid is None:
                self._wake.wait()
                self._wake.clear()
                continue
            self._finish(rid, self._one(rid))

    def _queue_due(self) -> None:
        try:
            found = self._due(self._vault(), self._engine)
        except (DocketError, OSError) as e:
            log.warning("speech: looking for recordings to transcribe failed: %s", type(e).__name__)
            return
        fresh = [rid for rid in found if rid not in self._auto]
        for rid in fresh:
            self._auto.add(rid)
            self.add(rid)
        if fresh:
            log.info("speech: queued %d recording(s) by itself", len(fresh))

    def _one(self, rid: str) -> SpeechJob:
        def said(line: str) -> None:
            matched = PERCENT.match(line)
            if matched:
                with self._lock:
                    if self._running is not None:
                        self._running = self._running.model_copy(update={"percent": int(matched.group(1))})
                        self._jobs[rid] = self._running

        try:
            done = self._run(self._vault(), rid, speech=self.speech, wait=True, say=said)
        except DocketError as e:
            return SpeechJob(rid=rid, state="not staged", line=str(e), at=datetime.now(UTC))
        except Exception as e:
            log.warning("speech: a job failed: %s", type(e).__name__)
            return SpeechJob(
                rid=rid, state="not staged", line=f"the job failed ({type(e).__name__})", at=datetime.now(UTC)
            )
        return SpeechJob(rid=rid, state="staged" if done.staged else "not staged", line=done.line, at=datetime.now(UTC))

    def _finish(self, rid: str, ended: SpeechJob) -> None:
        with self._lock:
            self._running = None
            self._jobs[rid] = ended
            self._recent.appendleft(ended)
        log.info("speech: a job ended, %s", ended.state)

    def close(self) -> None:
        """Stop taking jobs. A job running goes on in its worker, which ends once it finds the service gone."""
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(STOP_SECONDS)
