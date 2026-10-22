"""`docket sync pull` (#305): a source's run made by its own `pull.py`, each line posted to the run as the capture hook
would, the run closed, the audio put side by side, and what waits to be classified written to files; a pull that
stops leaves the run unclosed. The service is the real app over the invented `memo` source; the pull script is an
invented one reading its lines from a file. Invented memos only: Ada Example and Ben Sample."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import make_record
from recording_kit import lines
from starlette.testclient import TestClient
from sync_kit import BEARER, app_of, as_structured, memo, service_client, sync_template, sync_vault

from docket.cli import main
from docket.errors import DocketError
from docket.sync import pull, start
from docket.vault.gate import Gate
from docket.vault.vault import Vault

# Writes the lines of the phase it is asked for from the file DOCKET_TEST_PULL names; `exit` ends it with that code.
SCRIPT = """\
import json
import os
import sys

request = json.loads(sys.stdin.read())
lines = json.loads(open(os.environ["DOCKET_TEST_PULL"], encoding="utf-8").read())
with open(os.environ["DOCKET_TEST_PULL"] + ".asked", "a", encoding="utf-8") as asked:
    asked.write(json.dumps(request) + "\\n")
for line in lines.get(request["phase"], []):
    if "exit" in line:
        sys.exit(line["exit"])
    sys.stdout.write(line["raw"] + "\\n" if "raw" in line else json.dumps(line) + "\\n")
    sys.stdout.flush()
"""
M1, M2 = memo("m1", 1, text="Ada Example: send Ben the plan"), memo("m2", 1, text="Ben Sample: the plan is fine")


def capture(tool: str, result: Any, **arguments: Any) -> dict[str, Any]:
    return {"capture": {"tool": tool, "arguments": arguments, "result": result}}


RUN = [
    capture("list_memos", [{"type": "text", "text": json.dumps([M1, M2])}]),
    capture("get_memo", as_structured(M1), id="m1"),
    {"fetched": "m1"},
    capture("get_memo", as_structured(M2), id="m2"),
    {"fetched": "m2"},
    {"skip": {"id": "m3", "reason": "gone"}},
]


@pytest.fixture
def vault(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    v = sync_vault(sync_template(tmp_path_factory), tmp_path)
    (v.root.parent / ".claude" / "skills" / "sources" / "memo" / "pull.py").write_text(SCRIPT, encoding="utf-8")
    monkeypatch.setenv("DOCKET_TEST_PULL", str(tmp_path / "lines.json"))
    return v


def lines_are(tmp_path: Path, **phases: list[dict[str, Any]]) -> None:
    (tmp_path / "lines.json").write_text(json.dumps(phases), encoding="utf-8")


def asked(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (tmp_path / "lines.json.asked").read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with service_client(vault) as made:
        yield made


def post_to(client: TestClient) -> pull.Post:
    def post(path: str, body: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]] | None:
        answer = client.post(path, json=body, headers=BEARER)
        return answer.status_code, answer.json()

    return post


def test_a_pull_posts_each_call_to_its_run_closes_it_and_writes_what_waits(
    vault: Vault, client: TestClient, tmp_path: Path
) -> None:
    started = start.start(vault, [], datetime.now(UTC))
    prompt = Path(started.info.sources[0].prompt).read_text(encoding="utf-8")
    # A source with a pull.py gets the pull's prompt, and no slice of the ledger.
    assert prompt.startswith("You are the memo agent of a Docket sync. Pull memo into the vault")
    assert f"`docket sync pull {started.info.sync} memo --vault {vault.root}`" in prompt
    assert not (started.folder / "context" / "memo.md").exists()
    lines_are(tmp_path, run=RUN)
    pulled = pull.pull(vault, started.info.sync, "memo", post=post_to(client))
    request = asked(tmp_path)[0]
    assert request["phase"] == "run" and request["contract"] == 1 and request["window"]["since"]
    said = pull.lines(pulled)
    assert said[0].startswith(
        f"RUN:      memo | {pulled.run} | captures 3 | ready 0, pending 2 | skipped 0 | mark moved"
    )
    assert said[0].endswith("| pulled; final run none due; audio 0 put, 0 refused")
    waiting = started.folder / "recordings" / "memo" / "pending"
    assert said[1:] == [
        f"classify memo:m1: memo:m1 | {waiting / 'm1.notes.md'}",
        f"classify memo:m2: memo:m2 | {waiting / 'm2.notes.md'}",
    ]
    assert (waiting / "m1.notes.md").read_text(encoding="utf-8").splitlines() == [
        "waits to be classified: memo:m1, its notes",
        "== memo:m1 | memo | Dentist with Cleo",
        "#1 Ada Example: send Ben the plan",
    ]
    assert sorted(e.staged.record.rid for e in Gate(vault).list("memo", "pending").entries) == ["memo:m1", "memo:m2"]
    # The run is closed: the service holds none open.
    assert app_of(client).service.runs.listing() == []


def test_a_stop_leaves_the_run_unclosed_and_stages_nothing(vault: Vault, client: TestClient, tmp_path: Path) -> None:
    started = start.start(vault, [], datetime.now(UTC))
    lines_are(tmp_path, run=[RUN[0], {"stop": "memo isn't signed in"}])
    with pytest.raises(pull.PullStoppedError, match="the pull stopped: memo isn't signed in; the run isn't closed"):
        pull.pull(vault, started.info.sync, "memo", post=post_to(client))
    (left,) = app_of(client).service.runs.listing()
    assert (left["source"], left["captures"], left["closing"]) == ("memo", 1, False)
    assert Gate(vault).list("memo").entries == []


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ([{"raw": "not json"}], "the pull wrote a line that isn't JSON"),
        ([{"raw": "[1, 2]"}], "the pull wrote a line that isn't a JSON object"),
        ([{"capture": {"arguments": {}}}], "the pull wrote a capture line without its tool"),
        ([RUN[0], {"exit": 3}], r"the pull failed \(exit 3\)"),
    ],
)
def test_a_line_that_isn_t_one_or_a_failed_exit_stops_the_pull(
    vault: Vault, client: TestClient, tmp_path: Path, given: list[dict[str, Any]], said: str
) -> None:
    started = start.start(vault, [], datetime.now(UTC))
    lines_are(tmp_path, run=given)
    with pytest.raises(pull.PullStoppedError, match=said):
        pull.pull(vault, started.info.sync, "memo", post=post_to(client))
    assert Gate(vault).list("memo").entries == []


def test_the_audio_is_put_side_by_side_and_each_refusal_said(vault: Vault, client: TestClient, tmp_path: Path) -> None:
    started = start.start(vault, [], datetime.now(UTC))
    lines_are(tmp_path, run=RUN, media=[{"media": {"id": "m1", "url": "https://example.invalid/m1.mp3"}},
                                        {"media": {"id": "m2", "url": "https://example.invalid/m2.mp3"}},
                                        {"capture": {"tool": "get_memo"}}])  # fmt: skip
    real = post_to(client)
    # Both puts wait for each other: one after the other, the barrier breaks and both are refused.
    both = threading.Barrier(2, timeout=10)
    put: list[dict[str, Any]] = []

    def post(path: str, body: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]] | None:
        if path == "/api/media":
            both.wait()
            put.append(body)
            return (200, {}) if body["rid"] == "memo:m1" else (400, {"error": "memo:m2 is held"})
        answer = real(path, body, timeout)
        if path == "/api/runs" and answer is not None and answer[0] == 200 and not body.get("final"):
            answer = (200, answer[1] | {"steps": answer[1]["steps"] + "\n8. Media: media_put(rid, url)",
                                        "media": ["m0"]})  # fmt: skip
        return answer

    pulled = pull.pull(vault, started.info.sync, "memo", post=post)
    assert asked(tmp_path)[1] == {"contract": 1, "phase": "media", "window": None, "kept": [], "held": [],
                                  "ids": ["m0", "m1", "m2"], "server": None}  # fmt: skip
    assert sorted(b["rid"] for b in put) == ["memo:m1", "memo:m2"]
    assert (pulled.put, pulled.refused) == (1, ["memo:m2: memo:m2 is held"])
    assert "refused  memo:m2: memo:m2 is held" in pull.lines(pulled)


def test_a_held_record_is_never_written_out_to_classify(vault: Vault, client: TestClient, tmp_path: Path) -> None:
    started = start.start(vault, [], datetime.now(UTC))
    lines_are(tmp_path, run=RUN)
    pull.pull(vault, started.info.sync, "memo", post=post_to(client))
    holds = vault.root / "state" / "holds" / "memo.txt"
    holds.parent.mkdir(parents=True, exist_ok=True)
    holds.write_text("m2\n", encoding="utf-8")
    vault.commit(["state/holds/memo.txt"], "Hold m2")
    # A hold staged records wait under stops the next run's open: the files are what the pull wrote last.
    waiting = pull._waiting(vault, started.folder, "memo")  # pyright: ignore[reportPrivateUsage]
    assert [w.rid for w in waiting] == ["memo:m1"]


def test_a_source_without_a_pull_py_or_outside_the_sync_is_refused(vault: Vault, client: TestClient) -> None:
    started = start.start(vault, [], datetime.now(UTC))
    with pytest.raises(DocketError, match="slack isn't a source of sync-"):
        pull.pull(vault, started.info.sync, "slack", post=post_to(client))
    (vault.root.parent / ".claude" / "skills" / "sources" / "memo" / "pull.py").unlink()
    with pytest.raises(DocketError, match=r"memo has no pull\.py beside its skill"):
        pull.pull(vault, started.info.sync, "memo", post=post_to(client))
    with pytest.raises(DocketError, match="the run didn't open: the service isn't running"):
        (vault.root.parent / ".claude" / "skills" / "sources" / "memo" / "pull.py").write_text(SCRIPT, encoding="utf-8")
        pull.pull(vault, started.info.sync, "memo", post=lambda *_: None)


def test_the_media_route_takes_the_bearer_token_only_and_says_a_refusal(client: TestClient) -> None:
    assert client.post("/api/media", json={"rid": "memo:m1", "url": "https://example.invalid/a"}).status_code == 401
    answer = client.post("/api/media", json={"rid": "memo:m1", "url": "https://example.invalid/a"}, headers=BEARER)
    assert answer.status_code == 400 and answer.json()["error"]


def test_the_command_says_why_when_no_token_is_there_to_reach_the_service(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    started = start.start(vault, [], datetime.now(UTC))
    assert main(["sync", "pull", started.info.sync, "memo", "--vault", str(vault.root)]) == 2
    assert "token" in capsys.readouterr().err


def test_a_pull_past_its_time_limit_is_stopped(tmp_path: Path) -> None:
    script = tmp_path / "pull.py"
    script.write_text("import sys, time\nsys.stdin.read()\ntime.sleep(60)\n", encoding="utf-8")
    with pytest.raises(pull.PullStoppedError, match="the pull ran past 1 s and was stopped"):
        list(pull.child(script, {"contract": 1}, time_limit=1.0))


def test_a_close_another_run_s_close_holds_off_is_asked_again(vault: Vault, client: TestClient, tmp_path: Path) -> None:
    """The sync's agents close side by side, and the service refuses a close while another maps: the run stays open,
    so the pull waits and asks again rather than lose what it fetched."""
    started = start.start(vault, [], datetime.now(UTC))
    lines_are(tmp_path, run=RUN)
    real = post_to(client)
    refused: list[str] = []
    waited: list[float] = []

    def post(path: str, body: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]] | None:
        if path.endswith("/close") and not refused:
            refused.append(path)
            return 400, {"error": "another run is closing; its run stays open: call ingest again in a minute"}
        return real(path, body, timeout)

    pulled = pull.pull(vault, started.info.sync, "memo", post=post, pause=waited.append)
    assert len(refused) == 1 and waited == [10.0] and pulled.closed["pending"] == 2


def test_a_pull_that_ends_before_it_reads_its_request_says_its_exit(tmp_path: Path) -> None:
    script = tmp_path / "pull.py"
    script.write_text("import sys\nsys.exit(4)\n", encoding="utf-8")
    with pytest.raises(pull.PullStoppedError, match=r"the pull failed \(exit 4\)"):
        list(pull.child(script, {"contract": 1, "pad": "x" * 2_000_000}))


def test_a_final_run_due_is_pulled_by_id_and_a_pull_with_nothing_fetched_says_nothing_waits(
    vault: Vault, client: TestClient, tmp_path: Path
) -> None:
    """The final run the service would open for ids that waited a week, stood in for here: its own run, its ids by
    id, its close final; and media due with nothing fetched puts nothing."""
    started = start.start(vault, [], datetime.now(UTC))
    lines_are(tmp_path, run=[RUN[0]], final=[RUN[1]], media=[])
    real = post_to(client)
    final: list[str] = []

    def post(path: str, body: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]] | None:
        if path == "/api/runs" and body.get("final"):
            return 200, {"run": "run-final", "since": None, "until": None, "waiting": [{"id": "m1"}], "retry": []}
        if path.startswith("/api/runs/run-final/"):
            final.append(path.rpartition("/")[2] + ("+final" if body.get("final") else ""))
            return 200, {}
        answer = real(path, body, timeout)
        if path == "/api/runs" and answer is not None and answer[0] == 200:
            answer = (200, answer[1] | {"steps": answer[1]["steps"] + "\n8. Media: media_put(rid, url)"})
        return answer

    pulled = pull.pull(vault, started.info.sync, "memo", post=post)
    assert final == ["captures", "close+final"]
    assert asked(tmp_path)[1]["ids"] == ["m1"] and asked(tmp_path)[1]["phase"] == "final"
    assert pulled.final == "run-final, 1 captures"
    # Media is due, but nothing was fetched: no media phase.
    assert len(asked(tmp_path)) == 2
    assert pull.lines(pulled)[-1] == "nothing waits to be classified"


def test_a_run_the_service_refuses_to_open_says_why(vault: Vault) -> None:
    started = start.start(vault, [], datetime.now(UTC))
    with pytest.raises(DocketError, match="the run didn't open: memo isn't live"):
        pull.pull(vault, started.info.sync, "memo", post=lambda *_: (400, {"error": "memo isn't live"}))


def test_a_line_past_the_cap_stops_the_pull(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pull, "LINE_CAP", 100)
    script = tmp_path / "pull.py"
    script.write_text("import sys\nsys.stdin.read()\nprint('{\"skip\": \"' + 'x' * 500 + '\"}')\n", encoding="utf-8")
    with pytest.raises(pull.PullStoppedError, match="a line of the pull passed"):
        list(pull.child(script, {"contract": 1}))


def test_a_transcript_waiting_is_written_a_segment_a_line_with_the_times_a_cut_takes() -> None:
    record = make_record(rid="memo:m1/transcript", source="memo", kind="transcript", container="memo:m1",
                         segments=lines([("Ben Sample", "the dates"), ("", "a  pause")]))  # fmt: skip
    assert pull._segments(record) == [  # pyright: ignore[reportPrivateUsage]
        "== memo:m1/transcript | transcript | a segment a line; a cut is the times at its start, mm:ss-mm:ss",
        "@00:00-00:19 Ben Sample: the dates",
        "@00:20-00:39 a pause",
    ]


def test_the_http_post_asks_the_service_with_its_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from docket.service import client as service_client_module

    seen: list[tuple[str, str, Any, Any]] = []

    def call_json(method: str, path: str, body: Any, *, token: str, timeout: float) -> Any:
        seen.append((method, path, body, token))
        return None if path == "/gone" else service_client_module.Answer(200, {"run": "r"})

    monkeypatch.setattr(service_client_module, "call_json", call_json)
    post = pull.http("t0ken")
    assert post("/api/runs", {"source": "memo"}, 1.0) == (200, {"run": "r"})
    assert post("/gone", {}, 1.0) is None
    assert seen[0] == ("POST", "/api/runs", {"source": "memo"}, "t0ken")


def test_the_media_route_puts_what_media_put_puts(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from docket.service import calls

    async def media_put(service: Any, rid: str, *, path: str | None, url: str | None) -> tuple[Any, str]:
        return calls.MediaPut(rid=rid, ext="m4a", bytes=12), "put"

    monkeypatch.setattr(calls, "media_put", media_put)
    answer = client.post("/api/media", json={"rid": "memo:m1", "url": "https://example.invalid/a"}, headers=BEARER)
    assert answer.status_code == 200 and answer.json() == {"rid": "memo:m1", "ext": "m4a", "bytes": 12}
