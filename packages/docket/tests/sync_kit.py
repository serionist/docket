"""What the live-run tests share: a consuming project with an invented `memo` source, its mapper and skill, and a
service over it on a test port. Invented memos only: Ada Example and Ben Sample."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import copy_project, digest, make_config, sh, shared, write_mapper
from starlette.testclient import TestClient

from docket.service.app import ServiceApp, create_app
from docket.service.state import Settings
from docket.service.sync import MARKER_LINE
from docket.vault.config import SourceConfig
from docket.vault.vault import Vault

TOKEN = "t0ken-for-tests-only"  # noqa: S105 - an invented token
PORT = 8750
OURS = f"http://127.0.0.1:{PORT}"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
TOOLS = ["get_memo", "list_memos"]
TITLE = "Dentist with Cleo"

# Maps get_memo's memos to records. A memo it only listed, inside the run's window, waits to be fetched; `wait` makes a
# fetched memo wait for something more, `partial` stages it and waits too, `sleep` and `crash` misbehave.
MAPPER = """\
import json
import time


def items(result):
    if isinstance(result, str):
        result = json.loads(result)
    if isinstance(result, list) and result and isinstance(result[0], dict) and result[0].get("type") == "text":
        result = json.loads(result[0]["text"])
    return result if isinstance(result, list) else [result]


def map_run(run):
    held = set(run["holds"])
    window = run["manifest"].get("window") or {}
    out = {"contract": 1, "records": [], "set_aside": [], "waiting": [], "held": []}
    fetched, listed = {}, {}
    for capture in run["captures"]:
        for item in items(capture["result"]):
            memo = item["id"]
            if memo in held:
                out["held"].append(memo)
                continue
            if capture["tool"] == "get_memo" or capture["file"]:
                fetched[memo] = (item, capture)
            else:
                listed[memo] = item
    for memo, (item, capture) in sorted(fetched.items()):
        if item.get("sleep"):
            time.sleep(item["sleep"])
        if item.get("crash"):
            raise ValueError(item["crash"])
        if item.get("wait"):
            out["waiting"].append({"id": memo, "for": item["wait"]})
            if not item.get("partial"):
                continue
        out["records"].append(
            {
                "rid": "memo:" + memo + ("/part" if item.get("partial") else ""),
                "source": "memo",
                "kind": "memo",
                "container": "memo:" + memo,
                "ts": item["ts"],
                "title": item.get("title"),
                "text": item.get("text", ""),
                "fetched": capture["captured"],
                "via": "memo-tool",
            }
        )
    since, until = window.get("since"), window.get("until")
    for memo, item in sorted(listed.items()):
        if memo not in fetched and since and until and since <= item["ts"] <= until:
            out["waiting"].append({"id": memo, "for": "fetch"})
    return out
"""
SKILL = f"""\
---
name: memo
description: Harvest invented memos for Docket's tests.
---

{MARKER_LINE}

# memo

List with list_memos newest first; fetch each memo with get_memo.
"""


def when(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def memo(memo_id: str, days_ago: float = 1, **fields: Any) -> dict[str, Any]:
    return {"id": memo_id, "ts": when(days_ago), "title": TITLE, "text": f"memo {memo_id} with Ada Example", **fields}


def as_text(payload: Any) -> list[dict[str, str]]:
    """A result as Claude Code hands a text-only MCP result to a hook: its content blocks."""
    return [{"type": "text", "text": json.dumps(payload)}]


def as_structured(payload: Any) -> str:
    """A result as Claude Code hands a result with structured content to a hook: that content as JSON text."""
    return json.dumps(payload)


def _build(folder: Path) -> Path:
    project = folder / "project"
    project.mkdir()
    sh(project, "git", "init", "-q", "-b", "main")
    write_mapper(project, "memo", MAPPER)
    (project / ".claude" / "skills" / "sources" / "memo" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    config = make_config()
    config.sources["memo"] = SourceConfig(
        sensitivity="mixed", mapper=".claude/skills/sources/memo/mapper.py", tools=TOOLS
    )
    Vault.init(project / "vault", config, "test-box")
    return project


def sync_template(factory: pytest.TempPathFactory) -> Path:
    return shared(_build, "sync-project-" + digest(MAPPER, SKILL), factory)


def sync_vault(template: Path, tmp_path: Path) -> Vault:
    return copy_project(template, tmp_path)


@contextmanager
def service_client(vault: Vault) -> Iterator[TestClient]:
    app = create_app(Settings(port=PORT, vault=vault.root, token=lambda: TOKEN, store="a test file"))
    with TestClient(app, base_url=OURS) as client:
        yield client


def app_of(client: TestClient) -> ServiceApp:
    app = client.app
    assert isinstance(app, ServiceApp)
    return app


class Session:
    """One Claude Code session's run, driven over the REST API as the hook drives it."""

    def __init__(self, client: TestClient, session: str = "s1", agent: str | None = None) -> None:
        self.client = client
        self.session = session
        self.agent = agent
        self.run = ""
        self.calls = 0

    def open(self, source: str = "memo", **body: Any) -> dict[str, Any]:
        answer = self.client.post("/api/runs", json={"source": source, **body}, headers=BEARER)
        assert answer.status_code == 200, answer.text
        opened: dict[str, Any] = answer.json()
        self.run = opened["run"]
        bound = self.client.post(
            f"/api/runs/{self.run}/session", json={"session": self.session, "agent": self.agent}, headers=BEARER
        )
        assert bound.status_code == 200, bound.text
        return opened

    def capture(self, tool: str, result: Any, *, arguments: dict[str, Any] | None = None) -> Any:
        self.calls += 1
        return self.client.post(
            f"/api/runs/{self.run}/captures",
            json={
                "session": self.session,
                "agent": self.agent,
                "tool_name": f"mcp__memos__{tool}",
                "tool_use_id": f"toolu_{self.run[-6:]}_{self.calls}",
                "arguments": arguments or {},
                "result": result,
                "captured": datetime.now(UTC).isoformat(),
            },
            headers=BEARER,
        )

    def listing(self, *memos: dict[str, Any]) -> None:
        assert self.capture("list_memos", as_text(list(memos))).status_code == 200

    def fetch(self, item: dict[str, Any]) -> None:
        answer = self.capture("get_memo", as_structured(item), arguments={"id": item["id"]})
        assert answer.status_code == 200, answer.text

    def close(self, captures: int | None = None, **body: Any) -> Any:
        count = self.calls if captures is None else captures
        return self.client.post(f"/api/runs/{self.run}/close", json={"captures": count, **body}, headers=BEARER)
