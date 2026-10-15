"""A live run through the MCP tools: `run_open` and `ingest`, with the bind and the captures made as the capture hook
makes them."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent
from sync_kit import PORT, TOKEN, as_structured, memo, sync_template, sync_vault

from docket.service.state import Service, Settings
from docket.service.tools import create_mcp
from docket.vault.vault import Vault


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return sync_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return sync_vault(template, tmp_path)


@pytest.fixture
def service(vault: Vault) -> Service:
    return Service(Settings(port=PORT, vault=vault.root, token=lambda: TOKEN))


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


def test_a_run_through_the_tools_opens_takes_its_captures_and_closes(service: Service) -> None:
    opened = call(service, "run_open", {"source": "memo"})
    assert not opened.is_error, text_of(opened)
    structured = opened.structured_content
    assert structured is not None
    run = structured["run"]
    text = text_of(opened)
    assert text.startswith(f"run      {run}  memo\nwindow   ")
    assert "tools    get_memo, list_memos" in text
    assert "budget   400 KB of results" in text
    assert "How this run goes:" in text
    # The capture hook binds the run and posts the session's calls; here the registry takes them directly.
    service.runs.bind(run, "s1", None)
    item = memo("t1")
    service.runs.capture(
        run,
        session="s1",
        agent=None,
        tool="get_memo",
        tool_use_id="toolu_1",
        arguments={"id": "t1"},
        result=as_structured(item),
        captured=datetime.now(UTC),
        size=len(json.dumps(item)),
    )
    closed = call(service, "ingest", {"run": run, "captures": 1})
    assert not closed.is_error, text_of(closed)
    assert closed.structured_content is not None
    assert closed.structured_content["pending"] == 1
    assert "staged   1 pending, 0 ready" in text_of(closed)
    assert "mark     moved to " in text_of(closed)


def test_a_refused_open_and_a_wrong_count_are_tool_errors_naming_why(service: Service) -> None:
    refused = call(service, "run_open", {"source": "nope"})
    assert refused.is_error
    assert "no source 'nope' in vault.yaml" in text_of(refused)
    opened = call(service, "run_open", {"source": "memo"})
    run = (opened.structured_content or {})["run"]
    service.runs.bind(run, "s1", None)
    short = call(service, "ingest", {"run": run, "captures": 3, "skipped": [{"id": "x", "reason": "later"}]})
    assert short.is_error
    assert "counts 3 capture(s) and 0 arrived" in text_of(short)


def test_the_tools_text_for_a_final_run_says_it_has_no_window(service: Service, vault: Vault) -> None:
    from docket.vault import cursors

    old = datetime.now(UTC).date().replace(year=2025)
    path = cursors.file_path(vault.root, "test-box")
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        cursors.dump({"memo": cursors.Cursor(waiting={"w1": cursors.Waiting("w1", "summary", old)})}), encoding="utf-8"
    )
    opened = call(service, "run_open", {"source": "memo", "final": True})
    text = text_of(opened)
    assert "window   none: a final run" in text
    assert "waiting  1: w1 (summary, since 2025-" in text
    assert "How this final run goes:" in text
