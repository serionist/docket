"""The service's speech queue (DR-0571): one job at a time, each job's place and outcome, the jobs it queues by itself
after an index update on CUDA only, the `transcribe` tool and its REST route, the status and the Recording page's line,
and `docket transcribe` asking the service. The job itself stands in; no audio is transcribed here."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from docket_webkit import OURS, PORT, TOKEN, parse
from mcp import Client
from mcp.types import TextContent
from media_kit import CALL, in_raw, media_vault
from starlette.testclient import TestClient

from docket import cli
from docket.cli import main
from docket.errors import DocketError
from docket.service import client as service_client
from docket.service import state as service_state
from docket.service.app import create_app
from docket.service.sessions import COOKIE, Sessions
from docket.service.speech import SpeechJob, SpeechQueue, line
from docket.service.state import Service, Settings
from docket.service.tools import create_mcp
from docket.speech import catalogue, job
from docket.speech.setup import Speech
from docket.vault.vault import Vault

CUDA = Speech(catalogue.WHISPER_TINY, "cuda", Path("python"))
CPU = Speech(catalogue.WHISPER_TINY, "cpu", Path("python"))
NO_VAULT = cast(Vault, None)


class Runner:
    """`job.transcribe`, standing in: it says a progress line, waits where a test holds it, and ends as its rid says."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.hold: dict[str, threading.Event] = {}
        self.started = threading.Event()

    def __call__(
        self, vault: Vault, rid: str, *, speech: Speech, wait: bool, say: Callable[[str], None]
    ) -> job.Outcome:
        self.calls.append((rid, wait))
        say("transcribing 0:20 of audio with whisper tiny on cuda")
        say("  50%")
        self.started.set()
        if rid in self.hold:
            self.hold[rid].wait(10)
        if rid.endswith("refused"):
            raise DocketError("nothing transcribed: a hold covers it")
        if rid.endswith("boom"):
            raise RuntimeError("a message that must not reach the outcome")
        if rid.endswith("silent"):
            return job.Outcome(rid, False, f"Whisper heard no speech in {rid}'s stored audio; nothing staged")
        return job.Outcome(f"{rid}/whisper", True, f"staged {rid}/whisper: 2 segment(s), en")


def until(check: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


@pytest.fixture
def runner() -> Runner:
    return Runner()


@pytest.fixture
def queue(runner: Runner) -> Iterator[SpeechQueue]:
    found = SpeechQueue(lambda: NO_VAULT, chosen=lambda: CUDA, run=runner, due=lambda v, e: [])
    found.start()
    yield found
    for gate in runner.hold.values():
        gate.set()
    found.close()


def idle(queue: SpeechQueue) -> bool:
    status = queue.status()
    return status.state == "idle" and not status.queued


def test_one_job_runs_at_a_time_and_each_says_its_place(queue: SpeechQueue, runner: Runner) -> None:
    runner.hold["plaud:a"] = threading.Event()
    queue.add("plaud:a")
    assert runner.started.wait(10)
    queue.add("plaud:b")
    queue.add("plaud:c")
    until(lambda: queue.status().running is not None and queue.status().running.percent == 50)  # type: ignore[union-attr]
    status = queue.status()
    assert status.state == "running" and status.running is not None and status.running.rid == "plaud:a"
    assert status.queued == ["plaud:b", "plaud:c"] and (status.model, status.device) == ("tiny", "cuda")
    assert queue.add("plaud:b").ahead == 1, "asked again, it keeps its place"
    assert queue.job("plaud:c") == queue.job("plaud:c") and queue.job("plaud:c").ahead == 2  # type: ignore[union-attr]
    assert queue.add("plaud:a").state == "running"
    runner.hold["plaud:a"].set()
    until(lambda: idle(queue))
    assert runner.calls == [("plaud:a", True), ("plaud:b", True), ("plaud:c", True)], (
        "each waits for the machine's lock"
    )
    assert [j.rid for j in queue.status().recent] == ["plaud:c", "plaud:b", "plaud:a"]
    ended = queue.job("plaud:a")
    assert ended is not None and ended.state == "staged" and ended.line == "staged plaud:a/whisper: 2 segment(s), en"
    assert queue.job("plaud:other") is None
    # Asked for again once it ended: it runs again.
    queue.add("plaud:a")
    until(lambda: len(runner.calls) == 4 and idle(queue))


def test_each_job_ends_in_its_own_words(queue: SpeechQueue) -> None:
    for rid in ("plaud:refused", "plaud:boom", "plaud:silent"):
        queue.add(rid)
    until(lambda: idle(queue) and len(queue.status().recent) == 3)
    said = {j.rid: (j.state, j.line) for j in queue.status().recent}
    assert said == {
        "plaud:refused": ("not staged", "nothing transcribed: a hold covers it"),
        "plaud:boom": ("not staged", "the job failed (RuntimeError)"),
        "plaud:silent": ("not staged", "Whisper heard no speech in plaud:silent's stored audio; nothing staged"),
    }


def test_without_speech_the_queue_is_off_and_says_why() -> None:
    off = SpeechQueue(lambda: NO_VAULT, chosen=lambda: "speech isn't installed on this machine", run=Runner())
    assert off.status().why == "speech starts with the service"
    off.start()
    with pytest.raises(DocketError, match="speech is off in the service: speech isn't installed on this machine"):
        off.add("plaud:a")
    off.after_update()
    status = off.status()
    assert status.state == "off" and status.why == "speech isn't installed on this machine"
    off.close()


def test_after_an_update_cuda_queues_what_is_due_once_and_a_cpu_nothing(runner: Runner) -> None:
    asked: list[object] = []

    def due(vault: Vault, engine: str) -> list[str]:
        assert engine == CUDA.model.engine
        asked.append(vault)
        return ["plaud:new", "plaud:old"]

    cuda = SpeechQueue(lambda: NO_VAULT, chosen=lambda: CUDA, run=runner, due=due)
    cuda.start()
    cuda.after_update()
    until(lambda: len(runner.calls) == 2 and idle(cuda))
    cuda.after_update()
    until(lambda: len(asked) == 2)
    time.sleep(0.05)
    assert [rid for rid, _ in runner.calls] == ["plaud:new", "plaud:old"], "each is queued by itself once"
    cuda.close()
    cpu = SpeechQueue(lambda: NO_VAULT, chosen=lambda: CPU, run=runner, due=due)
    cpu.start()
    cpu.after_update()
    time.sleep(0.05)
    assert len(asked) == 2, "a CPU queues nothing by itself"
    cpu.close()


def test_a_scan_that_fails_queues_nothing(runner: Runner) -> None:
    def broken(vault: Vault, engine: str) -> list[str]:
        raise DocketError("the index can't be read")

    found = SpeechQueue(lambda: NO_VAULT, chosen=lambda: CUDA, run=runner, due=broken)
    found.start()
    found.after_update()
    time.sleep(0.1)
    assert runner.calls == [] and idle(found)
    found.close()


def test_a_job_s_line() -> None:
    at = datetime.now(UTC)
    assert line(SpeechJob(rid="plaud:a", state="queued", at=at)) == "queued for Whisper, next"
    assert line(SpeechJob(rid="plaud:a", state="queued", ahead=2, at=at)) == "queued for Whisper, 2 job(s) ahead of it"
    assert line(SpeechJob(rid="plaud:a", state="running", at=at)) == "Whisper is transcribing it now"
    assert line(SpeechJob(rid="plaud:a", state="running", percent=40, at=at)) == "Whisper is transcribing it now, 40%"
    assert line(SpeechJob(rid="plaud:a", state="staged", line="staged it", at=at)) == "staged it"
    assert line(SpeechJob(rid="plaud:a", state="not staged", at=at)) == "not staged"


# -- the service -----------------------------------------------------------------------------------------------------


@pytest.fixture
def faked(monkeypatch: pytest.MonkeyPatch, runner: Runner) -> Runner:
    """Every service made from here runs the stand-in job, with speech on CUDA."""

    def made(vault: Callable[[], Vault]) -> SpeechQueue:
        return SpeechQueue(vault, chosen=lambda: CUDA, run=runner, due=lambda v, e: [])

    monkeypatch.setattr(service_state, "SpeechQueue", made)
    return runner


def test_the_tool_queues_a_recording_and_answers_with_its_place(tmp_path: Path, faked: Runner) -> None:
    vault = media_vault(tmp_path)
    service = Service(Settings(port=PORT, vault=vault.root, token=lambda: TOKEN))
    faked.hold[CALL] = threading.Event()
    service.speech.start()

    async def run() -> tuple[Any, Any, Any]:
        async with Client(create_mcp(service)) as mcp:
            first = await mcp.call_tool("transcribe", {"rid": CALL})
            second = await mcp.call_tool("transcribe", {"rid": "plaud:rec-b"})
            bad = await mcp.call_tool("transcribe", {"rid": f"{CALL}/whisper"})
            return first, second, bad

    try:
        first, second, bad = asyncio.run(run())
        assert first.structured_content["rid"] == CALL and first.structured_content["state"] in ("queued", "running")
        text = second.content[0]
        assert isinstance(text, TextContent) and text.text == "plaud:rec-b: queued for Whisper, 1 job(s) ahead of it"
        assert bad.is_error and "is a Whisper transcript; give its recording's rid" in str(bad.content[0])
        assert service.status().speech.queued == ["plaud:rec-b"]
    finally:
        faked.hold[CALL].set()
        service.speech.close()
        service.writer.close()


def test_the_route_takes_the_token_and_the_page_shows_the_job(tmp_path: Path, faked: Runner) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    sessions = Sessions()
    app = create_app(Settings(port=PORT, vault=vault.root, token=lambda: TOKEN), sessions=sessions)
    bearer = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app, base_url=OURS, follow_redirects=False) as http:
        queued = http.post(f"/api/transcribe?rid={CALL}", headers=bearer)
        assert queued.status_code == 200 and queued.json()["rid"] == CALL
        until(lambda: http.get("/api/speech", headers=bearer).json()["recent"] != [])
        status = http.get("/api/status", headers=bearer).json()["speech"]
        assert status["state"] == "idle" and status["recent"][0]["state"] == "staged"
        started = sessions.redeem(sessions.new_code())
        assert started is not None
        http.cookies.set(COOKIE, started)
        assert http.post(f"/api/transcribe?rid={CALL}").status_code in (401, 403), "a page never queues a job"
        shown = parse(http.get(f"/recording?rid={CALL}").text).find("p", class_="speech-job")
    assert shown is not None and shown.text() == f"Whisper: staged {CALL}/whisper: 2 segment(s), en"


def test_the_command_asks_the_service_or_runs_the_job_here(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = media_vault(tmp_path)
    ran: list[str] = []

    def here(found: Vault, rid: str, *, wait: bool, say: Any) -> job.Outcome:
        ran.append(rid)
        return job.Outcome(f"{rid}/whisper", True, "staged it here")

    monkeypatch.setattr(job, "transcribe", here)
    monkeypatch.setattr(cli, "_service_transcribe", lambda found, rid: "queued for Whisper, next")
    assert main(["transcribe", CALL, "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert (
        out
        == f"{CALL}: queued for Whisper, next; its outcome shows on its Recording page and in docket service status\n"
    )
    assert main(["transcribe", CALL, "--here", "--vault", str(vault.root)]) == 0
    assert ran == [CALL] and capsys.readouterr().out == "staged it here\n"


def test_the_command_takes_the_service_s_answer_only_when_it_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = media_vault(tmp_path)
    monkeypatch.setattr(cli, "_service_token", lambda found: None)
    assert cli._service_transcribe(vault, CALL) is None  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(cli, "_service_token", lambda found: TOKEN)
    answers: list[Any] = [
        None,
        service_client.Answer(400, {"error": "speech is off in the service"}),
        service_client.Answer(200, {"rid": CALL}),
        service_client.Answer(200, {"rid": CALL, "state": "queued", "ahead": 1, "at": "2026-10-18T09:00:00Z"}),
    ]
    monkeypatch.setattr(service_client, "call", lambda *a, **k: answers.pop(0))
    said = [cli._service_transcribe(vault, CALL) for _ in range(4)]  # pyright: ignore[reportPrivateUsage]
    assert said == [None, None, None, "queued for Whisper, 1 job(s) ahead of it"]
