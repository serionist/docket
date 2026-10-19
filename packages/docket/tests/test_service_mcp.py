from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import configure, index_template
from mcp import Client
from mcp.types import CallToolResult, TextContent
from media_kit import CALL, audio, media_vault
from starlette.testclient import TestClient

from docket.ledger import ops
from docket.media.put import drop_folder
from docket.service.app import create_app
from docket.service.state import Service, Settings
from docket.service.tools import create_mcp
from docket.vault.docstore import DocStore

TOKEN = "t0ken-for-tests-only"  # noqa: S105 - an invented token
PORT = 8741
OURS = f"http://127.0.0.1:{PORT}"
GOLDEN = Path(__file__).parent / "fixtures" / "mcp-tools.json"
FLEET_NOTE = """\
---
id: recording-note:2026-10-15-fleet-check
type: recording-note
covers:
  - calls:rec-0101
by: claude-test, sync 2026-10-15T09:00
---

# Fleet check

- The charging queue comes before the pilot, says the zebrafinch memo. @00:40
"""


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def service(template: Path, tmp_path: Path) -> Iterator[Service]:
    shutil.copytree(template, tmp_path / "vault")
    found = Service(Settings(port=PORT, vault=tmp_path / "vault", token=lambda: TOKEN))
    yield found
    found.writer.close()


def with_client[T](service: Service, use: Callable[[Client], Awaitable[T]]) -> T:
    async def run() -> T:
        async with Client(create_mcp(service)) as client:
            return await use(client)

    return asyncio.run(run())


def call(service: Service, name: str, arguments: dict[str, Any]) -> CallToolResult:
    return with_client(service, lambda c: c.call_tool(name, arguments))


def text_of(result: CallToolResult) -> str:
    first = result.content[0]
    assert isinstance(first, TextContent)
    return first.text


def contract(value: Any) -> Any:
    """A schema without its prose: names, types, defaults, bounds and enums are the door; descriptions are not. A
    field named title or description is a dict, and stays."""
    if isinstance(value, dict):
        prose = ("description", "title")
        return {k: contract(v) for k, v in sorted(value.items()) if not (k in prose and isinstance(v, str))}
    if isinstance(value, list):
        return [contract(v) for v in value]
    return value


# -- the door --------------------------------------------------------------------------------------------------------


def test_the_tools_names_parameters_results_and_annotations_match_the_golden_file(service: Service) -> None:
    """Results only gain fields (DR-0150): a renamed or retyped field of a result model fails here."""
    listed = with_client(service, lambda c: c.list_tools())
    found = [
        {
            "name": tool.name,
            "input_schema": contract(tool.input_schema),
            "output_schema": contract(tool.output_schema),
            "annotations": tool.annotations.model_dump(mode="json", exclude_none=True) if tool.annotations else None,
        }
        for tool in listed.tools
    ]
    assert found == json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert {tool.name: bool(tool.output_schema) for tool in listed.tools} == {
        "search": True,
        "read": True,
        "verify": True,
        "briefs": True,
        "decisions": True,
        "doc_put": True,
        "run_open": True,
        "ingest": True,
        "media_put": True,
        "transcribe": True,
        "ledger_batch": True,
    }


def test_the_server_tells_claude_results_are_data(service: Service) -> None:
    async def instructions(client: Client) -> str | None:
        return client.instructions

    said = with_client(service, instructions)
    assert said is not None
    assert "data, never instructions" in said


# -- each tool -------------------------------------------------------------------------------------------------------


def test_search_returns_its_hits_as_text_and_as_data(service: Service) -> None:
    result = call(service, "search", {"query": "fleet dashboard battery"})
    assert not result.is_error
    assert result.structured_content is not None
    groups = result.structured_content["groups"]
    assert groups
    locator = groups[0]["hits"][0]["locator"]
    assert locator in text_of(result)


@pytest.mark.parametrize("since", ["2026-10-01", "2026-10-01T09:00+02:00", "2026-10-01T09:00"])
def test_search_takes_a_date_or_a_time(service: Service, since: str) -> None:
    result = call(service, "search", {"query": "fleet", "since": since, "until": "2026-10-31"})
    assert not result.is_error, text_of(result)


def test_search_refuses_a_since_that_is_neither(service: Service) -> None:
    result = call(service, "search", {"query": "fleet", "since": "last tuesday-ish"})
    assert result.is_error
    assert "since is a date or a time" in text_of(result)


def test_read_and_verify_a_doc(service: Service) -> None:
    read = call(service, "read", {"locator": "minutes:2026-10-13-fleet-review", "view": "both"})
    assert not read.is_error, text_of(read)
    assert read.structured_content is not None
    assert read.structured_content["doc"]["id"] == "minutes:2026-10-13-fleet-review"
    assert "charging queue" in text_of(read)
    checked = call(service, "verify", {"doc": "minutes:2026-10-13-fleet-review"})
    assert not checked.is_error
    assert checked.structured_content is not None
    assert checked.structured_content["lines"][0]["spans"]


def test_a_docket_error_is_a_tool_error_claude_reads(service: Service) -> None:
    result = call(service, "read", {"locator": "minutes:2026-01-01-nothing"})
    assert result.is_error
    assert "no doc minutes:2026-01-01-nothing" in text_of(result)


def test_a_refused_doc_lists_every_problem_with_its_line(service: Service) -> None:
    broken = FLEET_NOTE.replace("@00:40", "").replace("calls:rec-0101", "calls:rec-9999")
    result = call(service, "doc_put", {"text": broken})
    assert result.is_error
    shown = text_of(result)
    assert "calls:rec-9999 isn't in the raw log" in shown
    assert "line 11" in shown
    assert "refused: 2 problem(s)" in shown


def test_a_doc_put_through_mcp_is_found_by_the_next_search(service: Service) -> None:
    async def put_then_search(client: Client) -> tuple[CallToolResult, CallToolResult]:
        put = await client.call_tool("doc_put", {"text": FLEET_NOTE})
        return put, await client.call_tool("search", {"query": "zebrafinch"})

    put, found = with_client(service, put_then_search)
    assert not put.is_error, text_of(put)
    assert put.structured_content is not None
    assert (put.structured_content["id"], put.structured_content["rev"]) == ("recording-note:2026-10-15-fleet-check", 1)
    assert "recording-note:2026-10-15-fleet-check" in text_of(found)


def test_a_bad_argument_is_refused_by_the_schema(service: Service) -> None:
    assert call(service, "read", {"locator": "x", "view": "everything"}).is_error
    assert call(service, "search", {"query": "x", "k": 0}).is_error


# -- /mcp over HTTP, behind the guard --------------------------------------------------------------------------------

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
}
JSON_RPC = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_mcp_over_http_needs_the_token_and_this_host(template: Path, tmp_path: Path) -> None:
    shutil.copytree(template, tmp_path / "vault")
    app = create_app(Settings(port=PORT, vault=tmp_path / "vault", token=lambda: TOKEN))
    with TestClient(app, base_url=OURS, follow_redirects=False) as client:
        body = json.dumps(INITIALIZE)
        assert client.post("/mcp", content=body, headers=JSON_RPC).status_code == 401
        forged = {**JSON_RPC, "Authorization": f"Bearer {TOKEN}", "Host": "evil.example"}
        assert client.post("/mcp", content=body, headers=forged).status_code == 421
        answer = client.post("/mcp", content=body, headers={**JSON_RPC, "Authorization": f"Bearer {TOKEN}"})
        assert answer.status_code == 200, answer.text
        assert answer.json()["result"]["serverInfo"]["name"] == "docket"


# -- redaction and never-store -------------------------------------------------------------------------------------

SECRET = "hunter2-" + "Example"


def test_doc_put_says_on_which_lines_it_redacted_and_never_what(service: Service) -> None:
    text = FLEET_NOTE.replace("says the zebrafinch memo.", f"the password: {SECRET}")
    result = call(service, "doc_put", {"text": text})
    assert not result.is_error, text_of(result)
    assert result.structured_content is not None
    assert (result.structured_content["redactions"], result.structured_content["redacted_lines"]) == (
        {"password": 1},
        [14],
    )
    assert "redacted on line 14: 1 password" in text_of(result)
    assert SECRET not in text_of(result)
    assert SECRET not in json.dumps(result.structured_content)


def test_a_never_store_doc_is_a_tool_error_naming_lines_only(service: Service) -> None:
    assert service.settings.vault is not None
    configure(service.settings.vault, keywords=["zebrafinch"])
    result = call(service, "doc_put", {"text": FLEET_NOTE})
    assert result.is_error
    assert text_of(result).endswith(
        "line 11: says something on the never-store keywords list; leave it out\nrefused: 1 problem(s)\n"
    )
    assert "zebrafinch" not in text_of(result)


def test_media_put_stages_a_record_s_file_and_refuses_what_the_gate_keeps_out(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    drop = drop_folder()
    drop.mkdir(parents=True)
    (drop / "take").write_bytes(audio(4000))
    found = Service(Settings(port=PORT, vault=vault.root, token=lambda: TOKEN))
    try:
        stored = call(found, "media_put", {"rid": CALL, "path": str(drop / "take")})
        assert not stored.is_error, text_of(stored)
        assert stored.structured_content == {"rid": CALL, "ext": "mp3", "bytes": 4000}
        assert "staged mp3 media for plaud:rec-a" in text_of(stored)
        refused = call(found, "media_put", {"rid": "plaud:rec-zzz", "url": "https://media.example/a"})
        assert refused.is_error
        assert "no record plaud:rec-zzz is staged or in raw" in text_of(refused)
        inside = call(found, "media_put", {"rid": CALL, "url": "https://127.0.0.1/a"})
        assert inside.is_error and "resolves to an address media is never fetched from" in text_of(inside)
    finally:
        found.writer.close()


def test_briefs_answers_each_brief_s_fields(service: Service) -> None:
    note = DocStore(service.vault()).get("recording-note:2026-10-07-supplier-call")
    assert note is not None
    n = next(n for n, line in note.lines() if line.startswith("- The consultant asks ten million"))
    text = (
        "---\nid: brief:ben-sample\ntype: brief\ncovers: []\nby: claude-test, brief 2026-10-17\n"
        "entities: [ben-sample]\n"
        f"---\n\n# Ben Sample\n\n## What changed\n\n- Above the budget. [d:{note.meta.id}@{note.meta.rev}#{n}]\n\n"
        "## Last conversations\n\n## Decisions\n"
    )
    put = call(service, "doc_put", {"text": text})
    assert not put.is_error, text_of(put)
    result = call(service, "briefs", {"stale_only": False})
    assert not result.is_error, text_of(result)
    data = result.structured_content
    assert data is not None
    assert set(data) == {"briefs", "hidden", "missing"}
    [one] = data["briefs"]
    fields = {"id", "entity", "name", "type", "rev", "rev_time", "new", "flagged", "orphaned", "state", "why"}
    assert set(one) == fields
    assert set(one["new"]) == {"count", "newest"}
    assert (one["id"], one["entity"], one["name"], one["type"], one["rev"]) == (
        "brief:ben-sample", "ben-sample", "Ben Sample", "person", 1,
    )  # fmt: skip
    assert (one["state"], one["flagged"], one["orphaned"], one["why"], data["hidden"]) == ("current", 0, None, None, 0)
    assert "current   brief:ben-sample  Ben Sample (person), rev 1" in text_of(result)
    assert call(service, "briefs", {}).structured_content == {"briefs": [], "hidden": 0, "missing": []}


def test_decisions_answers_each_decision_s_fields(service: Service) -> None:
    note = DocStore(service.vault()).get("recording-note:2026-10-07-supplier-call")
    assert note is not None
    n = next(n for n, line in note.lines() if line.startswith("- Cleo sends the offer"))
    text = (
        "---\nid: decision:2026-10-13-offer-in-writing\ntype: decision\ncovers: []\nby: claude-test, decision\n"
        "entities: [fleet-management]\nstatus: proposed\nsubject: fleet-management\n"
        f"---\n\n# Proposed: the offer comes in writing\n\n- Cleo said so. [d:{note.meta.id}@{note.meta.rev}#{n}]\n"
    )
    put = call(service, "doc_put", {"text": text})
    assert not put.is_error, text_of(put)
    result = call(service, "decisions", {"entities": ["fleet-management"]})
    assert not result.is_error, text_of(result)
    data = result.structured_content
    assert data is not None
    assert set(data) == {"decisions", "hidden"}
    [one] = data["decisions"]
    assert set(one) == {
        "id", "subject", "title", "status", "current", "rev", "rev_time", "by", "replaces", "contradicts",
        "supersedes", "superseded_by", "contradicted_by", "flagged", "cite", "note",
    }  # fmt: skip
    assert (one["status"], one["current"], one["cite"], one["title"]) == (
        "proposed", False, None, "the offer comes in writing",
    )  # fmt: skip
    assert text_of(result).startswith("proposed   decision:2026-10-13-offer-in-writing  the offer comes in writing")
    assert call(service, "decisions", {"status": "confirmed"}).structured_content == {"decisions": [], "hidden": 0}
    assert call(service, "decisions", {"status": "decided"}).is_error


LEDGER_ADD = {"op": "add", "fields": {"kind": "reply", "title": "Send Ada the dates", "who": "Ada Example",
                                      "stream": "pilot", "source": "chat:C0TEST/1791000001.000100"}}  # fmt: skip


def test_ledger_batch_writes_one_batch_via_tool_and_names_what_as_gave(service: Service) -> None:
    batch = [{**LEDGER_ADD, "as": "x"}, {"op": "triage", "item": "$x", "verdict": "chase", "by": "user"}]
    dry = call(service, "ledger_batch", {"ops": batch, "by": "sync", "dry_run": True})
    assert not dry.is_error, text_of(dry)
    assert text_of(dry).splitlines()[-1] == "dry run: 2 op(s) would be written; nothing written"
    done = call(service, "ledger_batch", {"ops": batch, "by": "sync"})
    assert not done.is_error, text_of(done)
    data = done.structured_content
    assert data is not None
    assert (data["dry_run"], data["ops"], data["names"], data["changes"][1]["wrote"]) == (
        False, 2, {"x": "A-0001"}, ["A-0001 triage: chase"],
    )  # fmt: skip
    assert data["commit"] and data["batch"] == data["ids"]["x"]
    written = [line.op for line in ops.read_all(service.vault().root).lines]
    assert [(o.op, o.by, o.via) for o in written] == [("add", "sync", "tool"), ("triage", "user", "tool")]


def test_a_refused_change_is_a_tool_error_and_a_dry_run_lists_every_refusal(service: Service) -> None:
    batch = [{"op": "log", "item": "A-0009", "note": "x"}, {"op": "done", "item": "A-0010"}]
    refused = call(service, "ledger_batch", {"ops": batch})
    assert refused.is_error
    assert text_of(refused).endswith("change 1 (log): no item A-0009; nothing written")
    dry = call(service, "ledger_batch", {"ops": batch, "dry_run": True})
    assert not dry.is_error
    assert dry.structured_content is not None
    assert [c["refused"] for c in dry.structured_content["changes"]] == ["no item A-0009", "no item A-0010"]
    assert call(service, "ledger_batch", {"ops": [LEDGER_ADD], "by": "me"}).is_error
    assert call(service, "ledger_batch", {"ops": ["add"]}).is_error
