"""The capture hook, `docket hook post-tool-use`, fed what Claude Code really hands it: the probe's inputs (step 1 of
the plan, Claude Code 2.1.300) with their content replaced by invented text, in `fixtures/post-tool-use/`."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sync_kit import BEARER, as_structured, as_text, memo, service_client, sync_template, sync_vault

from docket import capture, entry, paths
from docket.service import client, server
from docket.service.token import Tokens
from docket.vault import cursors
from docket.vault.vault import Vault

FIXTURES = Path(__file__).parent / "fixtures" / "post-tool-use"
SESSION = "5e55a0e1-0000-4000-8000-000000000041"
RUN = "run-20261015T091200-3f9a1c2e4b5d6e7f"


def fixture(name: str, **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    data.update(changes)
    return data


def line(reply: dict[str, Any] | None) -> str:
    assert reply is not None
    assert reply["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    return str(reply["hookSpecificOutput"]["additionalContext"])


def write_marker(*runs: dict[str, Any]) -> None:
    folder = paths.state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "runs.json").write_text(json.dumps({"runs": list(runs)}), encoding="utf-8")


def a_marked_run(**fields: Any) -> dict[str, Any]:
    return {"run": RUN, "source": "memo", "session": SESSION, "agent": None, "tools": ["get_memo"], **fields}


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, Any]]]:
    """Every request the hook would make, answered 200 with a kept capture; the token is never read."""
    made: list[tuple[str, str, dict[str, Any]]] = []

    def fake(method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        made.append((method, path, body))
        return 200, {"capture": 3, "used": 3 * 1024, "budget": 400 * 1024}

    monkeypatch.setattr(capture, "_call", fake)
    return made


# -- what the probe found ----------------------------------------------------------------------------------------------


def test_the_probe_saw_the_shapes_the_hook_reads() -> None:
    structured = fixture("structured")
    assert isinstance(structured["tool_response"], str)
    assert json.loads(structured["tool_response"])["speakers"] == ["Ada Example", "Ben Sample"]
    assert isinstance(fixture("text-blocks")["tool_response"], list)
    assert fixture("subagent")["agent_id"]
    assert fixture("subagent")["session_id"] == structured["session_id"]
    failure = fixture("failure")
    assert failure["hook_event_name"] == "PostToolUseFailure"
    assert "tool_response" not in failure
    assert fixture("saved-to-file")["mcp_server"] == {"name": "probe", "source": "dynamic"}


@pytest.mark.parametrize(
    ("name", "own"),
    [
        ("mcp__probe__small", "small"),
        ("mcp__plugin_docket_docket__run_open", "run_open"),
        ("mcp__claude_ai_Plaud__get__note", "get__note"),
        ("mcp__probe", None),
        ("mcp__probe__", None),
        ("Bash", None),
        (None, None),
    ],
)
def test_a_tools_own_name_is_what_follows_its_server(name: object, own: str | None) -> None:
    assert capture.own_name(name) == own


def test_a_result_saved_to_a_file_is_never_whole() -> None:
    assert not capture.whole(fixture("saved-to-file")["tool_response"])
    assert not capture.whole([{"type": "text", "text": fixture("saved-to-file")["tool_response"]}])
    assert capture.whole(fixture("structured")["tool_response"])
    assert capture.whole(fixture("text-blocks")["tool_response"])
    assert capture.whole({"id": "x"})


def test_a_run_is_read_from_run_opens_result_in_each_shape() -> None:
    opened = {"run": RUN, "source": "memo", "steps": "How this run goes"}
    assert capture.opened_run(json.dumps(opened)) == RUN
    assert capture.opened_run(as_text(opened)) == RUN
    assert capture.opened_run(opened) == RUN
    assert capture.opened_run([{"type": "text", "text": "not json"}, {"type": "image"}, 3]) is None
    assert capture.opened_run(json.dumps({"run": "run-made-up", "steps": "x"})) is None
    assert capture.opened_run(json.dumps({"run": RUN})) is None
    assert capture.opened_run(None) is None


# -- the marker --------------------------------------------------------------------------------------------------------


def test_no_marker_means_no_run() -> None:
    assert capture.marker_runs() == []


def test_a_marker_read_mid_rename_is_tried_again_then_taken_as_no_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture, "READ_PAUSE", 0)
    folder = paths.state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "runs.json").write_text("{", encoding="utf-8")
    assert capture.marker_runs() == []
    for text in ("[]", '{"runs": 3}', '{"runs": [1, {"run": "a"}]}'):
        (folder / "runs.json").write_text(text, encoding="utf-8")
        assert capture.marker_runs() in ([], [{"run": "a"}])


def test_the_wanted_run_is_this_sessions_and_agents_and_lists_the_tool() -> None:
    runs = [a_marked_run(session="other"), a_marked_run(agent="a1", run="run-a"), a_marked_run(), {"run": 3}]
    assert capture.wanted(runs, SESSION, None, "get_memo") == RUN
    assert capture.wanted(runs, SESSION, "a1", "get_memo") == "run-a"
    assert capture.wanted(runs, SESSION, None, "list_memos") is None


# -- one call ----------------------------------------------------------------------------------------------------------


def test_run_opens_result_binds_the_run_to_this_session(calls: list[Any]) -> None:
    opened = json.dumps({"run": RUN, "source": "memo", "steps": "How this run goes"})
    event = fixture("structured", tool_name="mcp__plugin_docket_docket__run_open", tool_response=opened)
    assert line(capture.handle(event)) == f"docket: run {RUN} is this session's"
    assert calls == [("POST", f"/api/runs/{RUN}/session", {"session": SESSION, "agent": None})]
    inside = fixture("subagent", tool_name="mcp__docket__run_open", tool_response=opened)
    capture.handle(inside)
    assert calls[-1][2]["agent"] == fixture("subagent")["agent_id"]


def test_a_bind_the_service_refuses_tells_claude_to_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture, "_call", lambda *_a: (400, {"error": f"run {RUN} is already tied to a session"}))
    opened = json.dumps({"run": RUN, "steps": "x"})
    said = line(capture.handle(fixture("structured", tool_name="mcp__docket__run_open", tool_response=opened)))
    assert said == (
        f"docket: run {RUN} isn't tied to this session (run {RUN} is already tied to a session); stop: its captures "
        "won't be kept"
    )


def test_a_listed_tool_of_this_sessions_run_is_posted_unchanged(calls: list[Any]) -> None:
    write_marker(a_marked_run())
    event = fixture("structured", tool_name="mcp__memos__get_memo", tool_input={"id": "m1"})
    said = line(capture.handle(event))
    assert said == f"docket: kept capture 3 of run {RUN} (get_memo), 3 KB of 400 KB"
    method, path, body = calls[0]
    assert (method, path) == ("POST", f"/api/runs/{RUN}/captures")
    assert body["result"] == event["tool_response"]
    assert body["arguments"] == {"id": "m1"}
    assert (body["session"], body["agent"], body["tool_use_id"]) == (SESSION, None, event["tool_use_id"])
    assert body["tool_name"] == "mcp__memos__get_memo"


def test_a_result_claude_code_saved_to_a_file_is_not_kept(calls: list[Any]) -> None:
    write_marker(a_marked_run(tools=["big"]))
    said = line(capture.handle(fixture("saved-to-file", tool_name="mcp__probe__big")))
    assert said == "docket: not kept: big (too large)"
    assert calls == []


@pytest.mark.parametrize(
    "event",
    [
        3,
        {"hook_event_name": "PostToolUseFailure", "tool_name": "mcp__memos__get_memo", "session_id": SESSION},
        {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": SESSION},
        {"hook_event_name": "PostToolUse", "tool_name": "mcp__memos__get_memo"},
        {"hook_event_name": "PostToolUse", "tool_name": "mcp__memos__get_memo", "session_id": SESSION, "agent_id": 3},
        {"hook_event_name": "PostToolUse", "tool_name": "mcp__memos__list_memos", "session_id": SESSION},
        {"hook_event_name": "PostToolUse", "tool_name": "mcp__memos__get_memo", "session_id": "another"},
        {"hook_event_name": "PostToolUse", "tool_name": "mcp__docket__run_open", "session_id": "another"},
    ],
)
def test_anything_no_run_of_this_session_wants_says_nothing_and_reads_no_token(
    event: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(*_args: object) -> Any:
        raise AssertionError("the hook made a request")

    monkeypatch.setattr(capture, "_call", never)
    write_marker(a_marked_run())
    assert capture.handle(event) is None


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        (None, "the service isn't running"),
        ((400, {"error": "run x is closing"}), "run x is closing"),
        ((401, {"detail": "the run routes take the bearer token only"}), "the run routes take the bearer token only"),
        ((502, {}), "the service answered 502"),
    ],
)
def test_a_capture_the_service_didnt_keep_is_named(monkeypatch: pytest.MonkeyPatch, answer: Any, why: str) -> None:
    monkeypatch.setattr(capture, "_call", lambda *_a: answer)
    write_marker(a_marked_run())
    said = line(capture.handle(fixture("text-one", tool_name="mcp__memos__get_memo")))
    assert said == f"docket: not kept: get_memo ({why})"


def test_an_odd_answer_still_gives_a_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture, "_call", lambda *_a: (200, {"capture": 1, "used": "lots"}))
    write_marker(a_marked_run())
    said = line(capture.handle(fixture("text-one", tool_name="mcp__memos__get_memo", tool_input="odd")))
    assert said == f"docket: kept capture 1 of run {RUN} (get_memo), ? KB of ? KB"


def test_main_prints_one_json_line_or_nothing_and_always_exits_0(calls: list[Any]) -> None:
    write_marker(a_marked_run())
    out = io.StringIO()
    event = json.dumps(fixture("structured", tool_name="mcp__memos__get_memo")).encode("utf-8")
    assert capture.main(io.BytesIO(event), out) == 0
    assert line(json.loads(out.getvalue()))
    quiet = io.StringIO()
    assert capture.main(io.BytesIO(b'{"hook_event_name": "SessionStart"}'), quiet) == 0
    assert quiet.getvalue() == ""
    broken = io.StringIO()
    assert capture.main(io.BytesIO(b"\xff not json"), broken) == 0
    assert line(json.loads(broken.getvalue())) == "docket: the capture hook failed (UnicodeDecodeError)"


def test_a_control_character_never_reaches_claude() -> None:
    assert line(capture.say("docket: not kept: x (\x1b]52;)")) == "docket: not kept: x (?]52;)"


# -- the entry -------------------------------------------------------------------------------------------------------


def test_the_entry_answers_the_hook_before_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []
    monkeypatch.setattr(capture, "main", lambda: ran.append("hook") or 0)
    assert entry.main(["hook", "post-tool-use"]) == 0
    assert ran == ["hook"]
    monkeypatch.setattr(sys, "argv", ["docket", "--version"])
    assert entry.main() == 0


def test_the_cli_names_the_same_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    from docket.cli import main

    ran: list[str] = []
    monkeypatch.setattr(capture, "main", lambda: ran.append("hook") or 0)
    assert main(["hook", "post-tool-use"]) == 0
    assert ran == ["hook"]


def test_the_no_run_path_loads_none_of_the_heavy_imports(tmp_path: Path) -> None:
    probe = (
        "import sys\n"
        "from docket.entry import main\n"
        "sys.argv = ['docket', 'hook', 'post-tool-use']\n"
        "main()\n"
        "heavy = ['typer', 'keyring', 'pydantic', 'yaml', 'importlib.metadata', 'docket.index', 'docket.cli']\n"
        "print(sorted(m for m in heavy if m in sys.modules))\n"
    )
    event = json.dumps(fixture("structured", tool_name="mcp__memos__get_memo"))
    env = {**os.environ, "DOCKET_STATE_DIR": str(tmp_path / "state")}
    done = subprocess.run(
        [sys.executable, "-c", probe], input=event, capture_output=True, text=True, env=env, check=True, timeout=60
    )
    assert done.stdout.strip() == "[]"


def test_the_package_version_is_read_only_when_asked() -> None:
    import docket

    assert isinstance(docket.__version__, str)
    with pytest.raises(AttributeError, match="no attribute 'nope'"):
        docket.nope  # pyright: ignore[reportAttributeAccessIssue]  # noqa: B018


# -- against a service -------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return sync_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return sync_vault(template, tmp_path)


def test_a_whole_run_driven_by_the_hook_against_the_service(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    with service_client(vault) as http:

        def through(method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            answer = http.request(method, path, json=body, headers=BEARER)
            return answer.status_code, answer.json()

        monkeypatch.setattr(capture, "_call", through)
        opened = http.post("/api/runs", json={"source": "memo"}, headers=BEARER).json()
        bound = capture.handle(
            fixture("structured", tool_name="mcp__plugin_docket_docket__run_open", tool_response=json.dumps(opened))
        )
        assert line(bound) == f"docket: run {opened['run']} is this session's"
        a, b = memo("h1"), memo("h2")
        listing = fixture("text-blocks", tool_name="mcp__memos__list_memos", tool_response=as_text([a, b]))
        assert "kept capture 1" in line(capture.handle(listing))
        for n, item in enumerate((a, b), start=2):
            fetch = fixture(
                "structured",
                tool_name="mcp__memos__get_memo",
                tool_use_id=f"toolu_{n}",
                tool_input={"id": item["id"]},
                tool_response=as_structured(item),
            )
            assert f"kept capture {n} of run" in line(capture.handle(fetch))
        # Claude Code's file note for a big result is refused, and the count stays the highest kept number.
        big = fixture("saved-to-file", tool_name="mcp__memos__get_memo", tool_use_id="toolu_9")
        assert line(capture.handle(big)) == "docket: not kept: get_memo (too large)"
        closed = http.post(
            f"/api/runs/{opened['run']}/close",
            json={"captures": 3, "skipped": [{"id": "h3", "reason": "too-large"}]},
            headers=BEARER,
        ).json()
        assert (closed["pending"], closed["moved"]) == (2, True)
        cursor = cursors.read(vault.root, "test-box")["memo"]
        assert set(cursor.retry) == {"h3"}


def test_the_hook_reaches_a_real_service_with_its_token(vault: Vault) -> None:
    token = Tokens().ensure().value
    servers: list[Any] = []
    runner = threading.Thread(target=server.serve, args=(vault.root, 0), kwargs={"running": servers.append})
    runner.start()
    try:
        port = _wait_for_port()
        opened = client.call_json("POST", "/api/runs", {"source": "memo"}, token=token, at=port)
        assert opened is not None
        assert opened.status == 200, opened.body
        event = fixture(
            "structured", tool_name="mcp__plugin_docket_docket__run_open", tool_response=json.dumps(opened.body)
        )
        assert line(capture.handle(event)) == f"docket: run {opened.body['run']} is this session's"
        fetch = fixture("structured", tool_name="mcp__memos__get_memo", tool_response=as_structured(memo("r1")))
        assert "kept capture 1" in line(capture.handle(fetch))
    finally:
        while not servers:
            time.sleep(0.05)
        servers[0].should_exit = True
        runner.join(30)
    assert not runner.is_alive()


def test_a_token_that_cant_be_read_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    write_marker(a_marked_run())
    said = line(capture.handle(fixture("structured", tool_name="mcp__memos__get_memo")))
    assert said.startswith("docket: not kept: get_memo (the token can't be read: ")


def _wait_for_port() -> int:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        record = client.recorded()
        if record is not None and record.get("pid") == os.getpid():
            answer = client.call("GET", "/api/health", at=record["port"])
            if answer is not None and answer.status == 200:
                return int(record["port"])
        time.sleep(0.1)
    raise AssertionError("the service didn't start")


def test_the_marker_is_the_services_own(vault: Vault) -> None:
    with service_client(vault) as http:
        opened = http.post("/api/runs", json={"source": "memo"}, headers=BEARER).json()
        marked = json.loads((paths.state_dir() / "runs.json").read_text(encoding="utf-8"))
        assert [r["run"] for r in marked["runs"]] == [opened["run"]]
    assert not (paths.state_dir() / "runs.json").exists()
