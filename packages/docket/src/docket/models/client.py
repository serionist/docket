"""The core's side of the models' worker (DR-0330): its process, one request at a time, a search's query ahead of the
embedding pass's batches, and the timeouts.

A query not answered in 2 s comes back as None, so search skips its vector stage, and the worker goes on: a query
waits behind one batch at most. A rerank counts as a query and waits until its deadline, plus a little; after a query
that passed its time the worker counts as busy until it answers anything, and a rerank then skips without asking
(DR-0451, DR-0454). Only a hello or a batch that passes its own timeout stops the worker, and a stopped
worker is started again at most once a minute, by whoever keeps it (the service, or a command that needs it). Its
stderr is kept for `docket models status` and never logged: a traceback can quote a text.
"""

from __future__ import annotations

import base64
import contextlib
import itertools
import json
import os
import struct
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, cast

from docket.errors import DocketError

QUERY_SECONDS = 2.0
HELLO_SECONDS = 30.0
BATCH_SECONDS = 60.0
RESTART_SECONDS = 60.0
STOP_SECONDS = 5.0
# A rerank's answer may come this long after its deadline: the worker stops between batches, not inside one.
RERANK_GRACE = 0.2
# A rerank with no budget (docket eval, the question set's floors) waits this long at most.
RERANK_UNBOUNDED = 30.0
PAST_BUDGET = "the reranker passed its budget"
STDERR_KEEP = 64 * 1024
# What Python needs to start on each system, as a mapper gets it (DR-0123), and nothing the service holds.
KEEP_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL")
# The service runs under pythonw on Windows: a console child would flash a window at every start.
NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class WorkerError(DocketError):
    """The worker can't answer: it isn't running, it stopped, or a request passed its timeout."""


@dataclass(frozen=True)
class RerankerHello:
    """The reranker as the worker loaded it: its name, the provider its session got, or why it is off."""

    name: str
    provider: str | None
    off: str | None


@dataclass(frozen=True)
class Hello:
    dims: int
    device: str
    provider: str
    versions: dict[str, str]
    reranker: RerankerHello | None = None


@dataclass(frozen=True)
class Skip:
    """Why a rerank came back without scores; `budget` when it ran out of time, which the provider counts."""

    why: str
    budget: bool = False


@dataclass
class _Pending:
    done: threading.Event = field(default_factory=threading.Event)
    answer: dict[str, Any] | None = None


class Worker:
    """One worker process, started by `start` and by `ensure`, at most once a minute."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        query_seconds: float = QUERY_SECONDS,
        hello_seconds: float = HELLO_SECONDS,
        batch_seconds: float = BATCH_SECONDS,
        restart_seconds: float = RESTART_SECONDS,
    ) -> None:
        self.command = list(command)
        self.env = env if env is not None else {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
        self.clock = clock
        self.query_seconds = query_seconds
        self.hello_seconds = hello_seconds
        self.batch_seconds = batch_seconds
        self.restart_seconds = restart_seconds
        self.hello: Hello | None = None
        self.why: str | None = "the models' worker hasn't started"
        self.started: float | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._pending: dict[int, _Pending] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._batch_lock = threading.Lock()
        self._turn = threading.Condition()
        self._queries = 0
        self._stderr = bytearray()
        # A query passed its time and the worker hasn't answered since: it is working through something long.
        self.busy = False

    @property
    def up(self) -> bool:
        return self.hello is not None

    def stderr(self) -> str:
        with self._lock:
            return self._stderr.decode("utf-8", "replace")

    # -- the process --------------------------------------------------------------------------------------------

    def start(self) -> Hello:
        """Start the worker and wait for its hello; a failure stops it and says why."""
        self.stop()
        self.started = self.clock()
        self.why = "the models' worker is starting"
        proc = subprocess.Popen(  # noqa: S603 - the environment's own Python and Docket's worker file
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            creationflags=NO_WINDOW,
        )
        self._proc = proc
        pipes = cast(tuple[IO[bytes], IO[bytes]], (proc.stdout, proc.stderr))
        threading.Thread(target=self._read, args=(proc, pipes[0]), daemon=True).start()
        threading.Thread(target=self._keep_stderr, args=(pipes[1],), daemon=True).start()
        answer = self._ask(proc, {"op": "hello"}, self.hello_seconds)
        if answer is None:
            code = proc.poll()
            self._fail(
                proc,
                f"the models' worker didn't say hello in {self.hello_seconds:g} s"
                if code is None
                else f"the models' worker stopped as it started (exit {code}); docket models status --check shows its "
                "errors",
            )
            raise WorkerError(self.why or "")
        try:
            known = ("id", "ok", "dims", "device", "provider", "reranker")
            versions = {k: str(v) for k, v in answer.items() if k not in known}
            hello = Hello(
                int(answer["dims"]),
                str(answer["device"]),
                str(answer["provider"]),
                versions,
                _reranker_hello(answer.get("reranker")),
            )
        except (KeyError, TypeError, ValueError):
            self._fail(proc, "the models' worker said hello in a form this Docket doesn't read")
            raise WorkerError(self.why or "") from None
        self.hello = hello
        self.why = None
        return hello

    def ensure(self) -> bool:
        """Whether the worker is up, starting it when it is down and its last start was a minute ago or more."""
        if self.up:
            return True
        if self.started is not None and self.clock() - self.started < self.restart_seconds:
            return False
        try:
            self.start()
        except (WorkerError, OSError) as e:
            if isinstance(e, OSError):
                self.why = f"the models' worker can't start: {e.strerror or type(e).__name__}"
            return False
        return True

    def stop(self) -> None:
        """Close the worker's stdin, so it ends; kill it if it doesn't within a few seconds."""
        proc, self._proc = self._proc, None
        self.hello = None
        if proc is None:
            return
        _close_stdin(proc)
        try:
            proc.wait(STOP_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _fail(self, proc: subprocess.Popen[bytes], why: str) -> None:
        if self._proc is proc:
            self._proc = None
            self.hello = None
        self.why = why
        with contextlib.suppress(OSError):
            proc.kill()
        proc.wait()
        _close_stdin(proc)

    def _read(self, proc: subprocess.Popen[bytes], pipe: IO[bytes]) -> None:
        with pipe:
            for raw in pipe:
                try:
                    answer: object = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(answer, dict):
                    continue
                data = cast(dict[str, Any], answer)
                request_id: object = data.get("id")
                if not isinstance(request_id, int):
                    continue
                self.busy = False
                with self._lock:
                    pending = self._pending.pop(request_id, None)
                if pending is not None:
                    pending.answer = data
                    pending.done.set()
        code = proc.wait()
        _close_stdin(proc)
        if self._proc is proc:
            self._proc = None
            self.hello = None
            self.why = f"the models' worker stopped (exit {code}); docket models status --check shows its errors"
        with self._lock:
            left, self._pending = self._pending, {}
        for pending in left.values():
            pending.done.set()

    def _keep_stderr(self, pipe: IO[bytes]) -> None:
        with pipe:
            for chunk in pipe:
                with self._lock:
                    self._stderr.extend(chunk)
                    del self._stderr[:-STDERR_KEEP]

    # -- requests -----------------------------------------------------------------------------------------------

    def _ask(self, proc: subprocess.Popen[bytes], request: dict[str, Any], seconds: float) -> dict[str, Any] | None:
        """Send a request and wait for its answer; None when it didn't come in time or the worker stopped."""
        stdin = cast(IO[bytes], proc.stdin)
        pending = _Pending()
        with self._lock:
            request_id = next(self._ids)
            self._pending[request_id] = pending
        line = json.dumps({**request, "id": request_id}, ensure_ascii=True) + "\n"
        try:
            with self._send_lock:
                stdin.write(line.encode("ascii"))
                stdin.flush()
        except OSError:
            with self._lock:
                self._pending.pop(request_id, None)
            return None
        if not pending.done.wait(seconds):
            with self._lock:
                self._pending.pop(request_id, None)
            return None
        return pending.answer

    def _vectors(self, answer: dict[str, Any], count: int) -> list[bytes]:
        hello = self.hello
        if not answer.get("ok") or hello is None:
            raise WorkerError(f"the models' worker refused a request: {answer.get('error', 'no answer')}")
        data = base64.b64decode(str(answer["vectors"]))
        size = hello.dims * 4
        if len(data) != size * count:
            raise WorkerError("the models' worker answered with vectors of the wrong size")
        return [data[i * size : (i + 1) * size] for i in range(count)]

    def query(self, text: str) -> bytes | str:
        """A query's vector, or why there is none: the worker is down, refused, or didn't answer in time. Search then
        goes without vectors, and the worker goes on."""
        proc = self._proc
        if proc is None or not self.up:
            return self.why or "the models' worker isn't running"
        with self._turn:
            self._queries += 1
        try:
            answer = self._ask(proc, {"op": "embed", "texts": [text]}, self.query_seconds)
        finally:
            with self._turn:
                self._queries -= 1
                self._turn.notify_all()
        if answer is None:
            if self.why is None:
                self.busy = True
            return self.why or f"the models' worker didn't answer in {self.query_seconds:g} s"
        try:
            return self._vectors(answer, 1)[0]
        except WorkerError as e:
            return str(e)

    def rerank(
        self, query: str, texts: Sequence[str], deadline: float | None, now: Callable[[], float] = time.time
    ) -> list[float] | Skip:
        """The reranker's score for each text, or why there are none. A deadline (wall-clock seconds) bounds the wait,
        none waits RERANK_UNBOUNDED; ahead of the pass's batches, as a query is."""
        proc, hello = self._proc, self.hello
        if proc is None or hello is None:
            return Skip(self.why or "the models' worker isn't running")
        if hello.reranker is None:
            return Skip("the models' worker runs no reranker")
        if hello.reranker.off is not None:
            return Skip(hello.reranker.off)
        if self.busy:
            return Skip("the models' worker is busy: this search's query passed its time", budget=True)
        if not texts:
            return []
        wait = RERANK_UNBOUNDED if deadline is None else max(0.0, deadline - now()) + RERANK_GRACE
        with self._turn:
            self._queries += 1
        try:
            request = {"op": "rerank", "query": query, "texts": list(texts), "deadline": deadline}
            answer = self._ask(proc, request, wait)
        finally:
            with self._turn:
                self._queries -= 1
                self._turn.notify_all()
        if answer is None:
            if proc.poll() is not None or self.why is not None:
                return Skip(self.why or "the models' worker stopped")
            return Skip(PAST_BUDGET, budget=True)
        if not answer.get("ok"):
            error = answer.get("error")
            if error == "PastBudget":
                return Skip(PAST_BUDGET, budget=True)
            return Skip(f"the reranker refused a request: {error}")
        data = base64.b64decode(str(answer.get("scores", "")))
        if len(data) != 4 * len(texts):
            return Skip("the reranker answered with scores of the wrong size")
        return list(struct.unpack(f"<{len(texts)}f", data))

    def batch(self, texts: Sequence[str]) -> list[bytes]:
        """The embedding pass's vectors, after any query waiting; a batch past its timeout stops the worker."""
        if not texts:
            return []
        with self._batch_lock:
            with self._turn:
                while self._queries:
                    self._turn.wait()
            proc = self._proc
            if proc is None or not self.up:
                raise WorkerError(self.why or "the models' worker isn't running")
            answer = self._ask(proc, {"op": "embed", "texts": list(texts)}, self.batch_seconds)
            if answer is None:
                code = proc.poll()
                if code is not None:
                    # It ended mid-batch, and its reader has said so with the exit code.
                    raise WorkerError(self.why or f"the models' worker stopped (exit {code})")
                self._fail(proc, f"a batch passed {self.batch_seconds:g} s, so the models' worker was stopped")
                raise WorkerError(self.why or "")
            return self._vectors(answer, len(texts))


def _reranker_hello(raw: object) -> RerankerHello | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError
    data = cast(dict[str, Any], raw)
    provider, off = data.get("provider"), data.get("off")
    return RerankerHello(
        str(data["name"]), None if provider is None else str(provider), None if off is None else str(off)
    )


def _close_stdin(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(OSError):
        cast(IO[bytes], proc.stdin).close()


def worker_command(python: Path, spec: dict[str, Any]) -> list[str]:
    """The worker's command line: the environment's Python, isolated, running Docket's worker file with its spec."""
    worker = Path(__file__).with_name("worker.py")
    return [str(python), "-I", "-X", "utf8", str(worker), json.dumps(spec, ensure_ascii=True)]
