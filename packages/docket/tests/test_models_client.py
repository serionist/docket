"""The worker's client against a real process running the fake backend: start, queries ahead of batches, timeouts that
skip or stop, restarts at most once a minute, and nothing of a text in a log."""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from models_kit import DIMS, fake_command, fake_worker, trigram_vector

from docket.models import client
from docket.models.client import NO_WINDOW, Worker, WorkerError, worker_command


@pytest.fixture
def stopping() -> Iterator[list[Worker]]:
    started: list[Worker] = []
    yield started
    for w in started:
        w.stop()


def running(stopping: list[Worker], **spec: Any) -> Worker:
    w = fake_worker(**spec)
    stopping.append(w)
    w.start()
    return w


def test_a_worker_says_hello_and_embeds(stopping: list[Worker]) -> None:
    w = running(stopping)
    assert w.up and w.why is None and w.hello is not None
    assert (w.hello.dims, w.hello.device, w.hello.provider, w.hello.versions) == (
        DIMS, "cpu", "CPUExecutionProvider", {"onnxruntime": "fake"},
    )  # fmt: skip
    assert w.query("query: paper price") == trigram_vector("query: paper price")
    assert w.batch(["passage: a", "passage: bb"]) == [trigram_vector("passage: a"), trigram_vector("passage: bb")]
    assert w.batch([]) == []
    assert "fake worker starting" in w.stderr()


def test_a_query_goes_ahead_of_the_batches_waiting(stopping: list[Worker], tmp_path: Path) -> None:
    order = tmp_path / "order.txt"
    w = running(stopping, delay=0.3, order_file=str(order))
    done: list[str] = []
    first = threading.Thread(target=lambda: (w.batch(["first batch"]), done.append("first")))
    first.start()
    while not order.exists():
        time.sleep(0.01)
    query = threading.Thread(target=lambda: (w.query("the query"), done.append("query")))
    query.start()
    while not w._queries:  # pyright: ignore[reportPrivateUsage]
        time.sleep(0.01)
    second = threading.Thread(target=lambda: (w.batch(["second batch"]), done.append("second")))
    second.start()
    for t in (first, query, second):
        t.join(10)
    assert order.read_text(encoding="utf-8").splitlines() == ["first batch", "the query", "second batch"]
    assert done == ["first", "query", "second"]


def test_a_slow_query_skips_and_the_worker_goes_on(stopping: list[Worker]) -> None:
    w = running(stopping, delay=0.6, query_seconds=0.1)
    assert w.query("query: slow") == "the models' worker didn't answer in 0.1 s"
    assert w.up
    assert w.batch(["passage: next"]) == [trigram_vector("passage: next")]


def test_a_batch_past_its_timeout_stops_the_worker(stopping: list[Worker]) -> None:
    w = running(stopping, delay=1.0, batch_seconds=0.1)
    with pytest.raises(WorkerError, match=r"a batch passed 0\.1 s"):
        w.batch(["passage: slow"])
    assert not w.up
    assert w.query("query: after") == "a batch passed 0.1 s, so the models' worker was stopped"
    with pytest.raises(WorkerError, match="a batch passed"):
        w.batch(["passage: again"])


def test_a_worker_that_stops_is_started_again_at_most_once_a_minute(stopping: list[Worker]) -> None:
    now = [100.0]
    w = Worker(fake_command(die_on="query: crash"), clock=lambda: now[0])
    stopping.append(w)
    assert w.ensure() and w.up
    assert w.ensure()
    assert w.query("query: crash") in (
        "the models' worker stopped (exit 5); docket models status shows its errors",
        "the models' worker didn't answer in 2 s",
    )
    deadline = time.monotonic() + 10
    while w.up and time.monotonic() < deadline:
        time.sleep(0.01)
    assert w.why == "the models' worker stopped (exit 5); docket models status shows its errors"
    now[0] += 30
    assert not w.ensure()
    now[0] += 31
    assert w.ensure() and w.up


@pytest.mark.parametrize(
    ("spec", "why"),
    [
        ({"bad_hello": True}, "the models' worker said hello in a form this Docket doesn't read"),
        ({"slow_hello": 1.0, "hello_seconds": 0.1}, "the models' worker didn't say hello in 0.1 s"),
        ({"exit_at_start": True}, "the models' worker stopped as it started (exit 4)"),
    ],
)
def test_a_worker_that_doesnt_say_hello_is_stopped(stopping: list[Worker], spec: dict[str, Any], why: str) -> None:
    w = fake_worker(**spec)
    stopping.append(w)
    with pytest.raises(WorkerError, match=re.escape(why)):
        w.start()
    assert not w.up and w.why is not None and w.why.startswith(why)
    assert w.query("query: x") == w.why
    assert not w.ensure()


def test_ensure_says_why_a_start_failed(stopping: list[Worker]) -> None:
    w = fake_worker(bad_hello=True)
    stopping.append(w)
    assert not w.ensure()
    assert w.why == "the models' worker said hello in a form this Docket doesn't read"


def test_a_worker_that_cant_start_says_why(tmp_path: Path) -> None:
    w = Worker([str(tmp_path / "no-python-here"), "worker.py"])
    assert not w.ensure()
    assert w.why is not None and w.why.startswith("the models' worker can't start: ")


def test_lines_that_arent_answers_are_passed_over(stopping: list[Worker]) -> None:
    w = running(stopping, junk=["not json", "[1, 2]", '{"id": "seven"}', '{"id": 999, "ok": true}'])
    assert w.query("query: still") == trigram_vector("query: still")


@pytest.mark.parametrize(
    ("spec", "why"), [({"refuse_on": "passage: no"}, "ValueError"), ({"wrong_size": True}, "size")]
)
def test_a_refused_or_wrong_answer_fails_a_batch_and_skips_a_query(
    stopping: list[Worker], spec: dict[str, Any], why: str
) -> None:
    w = running(stopping, **spec)
    with pytest.raises(WorkerError, match=why):
        w.batch(["passage: no"])
    text = "passage: no" if "refuse_on" in spec else "query: x"
    got = w.query(text)
    assert isinstance(got, str) and why in got
    assert w.up


def test_a_query_whose_line_cant_be_written_skips(stopping: list[Worker], monkeypatch: pytest.MonkeyPatch) -> None:
    w = running(stopping)
    proc = w._proc  # pyright: ignore[reportPrivateUsage]
    assert proc is not None and proc.stdin is not None

    def broken(_data: bytes) -> int:
        raise BrokenPipeError

    monkeypatch.setattr(proc.stdin, "write", broken)
    assert w.query("query: x") == "the models' worker didn't answer in 2 s"


def test_stop_kills_a_worker_that_ignores_its_stdin(stopping: list[Worker], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "STOP_SECONDS", 0.2)
    w = running(stopping, ignore_eof=True)
    proc = w._proc  # pyright: ignore[reportPrivateUsage]
    started = time.monotonic()
    w.stop()
    assert proc is not None and proc.poll() is not None
    assert time.monotonic() - started < 10
    w.stop()


def test_the_worker_runs_with_no_window_and_a_bare_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def popen(command: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        seen.update(kwargs)
        raise FileNotFoundError(2, "No such file")

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setenv("DOCKET_SECRET_TEST", "x")
    w = Worker(["python", "worker.py"])
    assert not w.ensure()
    assert w.why == "the models' worker can't start: No such file"
    assert seen["creationflags"] == NO_WINDOW
    assert (0x08000000 if sys.platform == "win32" else 0) == NO_WINDOW
    assert "DOCKET_SECRET_TEST" not in seen["env"]


def test_nothing_of_a_text_reaches_a_log(stopping: list[Worker], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    w = running(stopping, refuse_on="passage: the secret price")
    with pytest.raises(WorkerError):
        w.batch(["passage: the secret price"])
    w.query("query: the secret price")
    assert "secret" not in caplog.text


def test_the_command_runs_the_worker_file_isolated(tmp_path: Path) -> None:
    command = worker_command(tmp_path / "python", {"dims": 4})
    assert command[:4] == [str(tmp_path / "python"), "-I", "-X", "utf8"]
    assert Path(command[4]).name == "worker.py" and Path(command[4]).is_file()
    assert command[5] == '{"dims": 4}'
