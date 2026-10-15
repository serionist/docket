from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from docket_testkit import configure, index_template
from starlette.testclient import TestClient

from docket import index
from docket.errors import DocketError
from docket.service import calls, logs
from docket.service.api import MAX_BODY
from docket.service.app import create_app
from docket.service.state import Settings

TOKEN = "t0ken-for-tests-only"  # noqa: S105 - an invented token
PORT = 8741
OURS = f"http://127.0.0.1:{PORT}"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
# An invented name that must never reach the log.
NAME = "Kovács Ödön"
NOTE = """\
---
id: recording-note:2026-10-15-fleet-check
type: recording-note
covers:
  - calls:rec-0101
by: claude-test, sync 2026-10-15T09:00
---

# Fleet check for Ödön

- The charging queue comes before the pilot. @00:40
"""


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    shutil.copytree(template, tmp_path / "vault")
    app = create_app(Settings(port=PORT, vault=tmp_path / "vault", token=lambda: TOKEN, store="the system keychain"))
    with TestClient(app, base_url=OURS) as found:
        yield found


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    """The service's real logging setup on a temp file, taken down after the test."""
    root = logging.getLogger()
    levels = {name: logging.getLogger(name).level for name in ("", "docket", *logs.LIBRARIES)}
    path = tmp_path / "log" / "service.log"
    handler = logs.configure(path)
    yield path
    root.removeHandler(handler)
    handler.close()
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


# -- the routes ------------------------------------------------------------------------------------------------------


def test_search_takes_lists_as_repeated_parameters(client: TestClient) -> None:
    answer = client.get(
        "/api/search", params={"query": "fleet", "sources": ["calls", "chat"], "people": ["cleo-test"]}, headers=BEARER
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["groups"]
    assert {group["source"] for group in body["groups"]} <= {"calls", "chat", None}
    assert body["query"]["words"] == ["fleet"]


def test_read_and_verify(client: TestClient) -> None:
    read = client.get(
        "/api/read", params={"locator": "minutes:2026-10-13-fleet-review", "view": "both"}, headers=BEARER
    )
    assert read.status_code == 200
    assert read.json()["doc"]["id"] == "minutes:2026-10-13-fleet-review"
    checked = client.get(
        "/api/verify", params={"doc": "minutes:2026-10-13-fleet-review", "lines": [12]}, headers=BEARER
    )
    assert checked.status_code == 200
    assert [line["n"] for line in checked.json()["lines"]] == [12]


def test_a_doc_with_non_ascii_text_and_a_bom_is_put(client: TestClient) -> None:
    body = ("﻿" + NOTE).encode("utf-8")
    answer = client.post("/api/docs", content=body, headers={**BEARER, "Content-Type": "text/markdown"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["id"] == "recording-note:2026-10-15-fleet-check"


def test_a_refused_doc_is_422_with_every_problem(client: TestClient) -> None:
    broken = NOTE.replace("@00:40", "").replace("calls:rec-0101", "calls:rec-9999").encode("utf-8")
    answer = client.post("/api/docs", content=broken, headers=BEARER)
    assert answer.status_code == 422
    problems = answer.json()["problems"]
    assert len(problems) == 2
    assert {p["line"] for p in problems} >= {11}


def test_a_put_says_what_it_redacted_and_a_never_store_doc_is_422_without_excerpts(
    client: TestClient, tmp_path: Path
) -> None:
    secret = "hunter2-" + "Example"
    stored = client.post("/api/docs", content=NOTE.replace("pilot.", f"pilot, password: {secret}"), headers=BEARER)
    assert stored.status_code == 200, stored.text
    assert (stored.json()["redactions"], stored.json()["redacted_lines"]) == ({"password": 1}, [14])
    assert secret not in stored.text
    configure(tmp_path / "vault", keywords=["charging queue"])
    refused = client.post("/api/docs", content=NOTE.replace("fleet-check", "fleet-check-2"), headers=BEARER)
    assert refused.status_code == 422
    assert refused.json()["problems"] == [
        {"line": 11, "message": "says something on the never-store keywords list; leave it out", "excerpt": ""}
    ]


def test_a_docket_error_is_400_and_text_that_isnt_utf8_too(client: TestClient) -> None:
    missing = client.get("/api/read", params={"locator": "minutes:2026-01-01-nothing"}, headers=BEARER)
    assert missing.status_code == 400
    assert missing.json() == {"error": "no doc minutes:2026-01-01-nothing"}
    latin = client.post("/api/docs", content="caf\xe9".encode("latin-1"), headers=BEARER)
    assert (latin.status_code, latin.json()) == (400, {"error": "a doc is UTF-8 text"})


def test_a_body_over_the_cap_is_refused(client: TestClient) -> None:
    big = b"x" * (MAX_BODY + 1)
    assert client.post("/api/docs", content=big, headers=BEARER).status_code == 400

    def chunks() -> Iterator[bytes]:
        yield big

    streamed = client.post("/api/docs", content=chunks(), headers=BEARER)
    assert streamed.status_code == 400


def test_the_session_link_needs_the_token(client: TestClient) -> None:
    link = client.post("/api/session", headers=BEARER).json()
    assert link["url"].startswith(f"{OURS}/session?code=")
    assert "expires_at" in link


def test_status_names_the_vault_its_index_and_the_token_store(client: TestClient) -> None:
    body = client.get("/api/status", headers=BEARER).json()
    assert body["vault"].endswith("vault")
    assert body["machine"] == "test-box"
    assert body["store"] == "the system keychain"
    assert body["index"]["languages"]


# -- the log keeps no content ----------------------------------------------------------------------------------------


def test_errors_over_rest_and_mcp_leave_no_name_in_the_log(
    client: TestClient, log_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A refused doc over MCP: the SDK logs a tool error's text at INFO.
    refused = NOTE.replace("calls:rec-0101", f"calls:{NAME.replace(' ', '-')}").replace("@00:40", f"@00:40 {NAME}")
    rpc = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "doc_put", "arguments": {"text": refused}},
    }
    headers = {**BEARER, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    answer = client.post("/mcp", content=json.dumps(rpc), headers=headers)
    assert answer.status_code == 200
    assert answer.json()["result"]["isError"]
    # A DocketError over REST: its message goes to the caller only.
    assert client.get("/api/read", params={"locator": f"minutes:{NAME}"}, headers=BEARER).status_code == 400

    # A broken doc the core skips with a warning that names its path and quotes its frontmatter.
    broken_doc = tmp_path / "vault" / "processed" / "recording-note" / "2026" / "10" / "2026-10-15-Ödön.md"
    broken_doc.parent.mkdir(parents=True, exist_ok=True)
    broken_doc.write_text(
        f"---\nid: recording-note:2026-10-15-odon\ntype: recording-note\ncovers: [{NAME}]\n---\n\n- {NAME}\n",
        encoding="utf-8",
    )
    assert client.get("/api/read", params={"locator": "r:calls:rec-0101"}, headers=BEARER).status_code == 200

    # A 500 over REST, from an exception whose message, and whose cause's, hold the name.
    async def broken(*_args: object, **_kwargs: object) -> object:
        try:
            raise ValueError(f"{NAME} said the door code")
        except ValueError as e:
            raise RuntimeError(f"reading {NAME}'s note") from e

    monkeypatch.setattr(calls, "read", broken)
    failed = client.get("/api/read", params={"locator": "x"}, headers=BEARER)
    assert (failed.status_code, failed.json()) == (500, {"error": "internal error; see the service log"})
    for handler in logging.getLogger().handlers:
        handler.flush()
    written = log_file.read_text(encoding="utf-8")
    assert NAME not in written
    assert "Ödön" not in written
    assert "builtins.RuntimeError" in written
    assert "builtins.ValueError" in written
    assert "in broken" in written
    assert "GET /api/read 500" in written
    assert "POST /mcp 200" in written
    # The core's warning is there, as its template alone.
    assert "docket.vault.docstore: skipping %s: %s" in written


def test_the_libraries_log_at_warning(log_file: Path) -> None:
    for name in ("mcp", "uvicorn", "uvicorn.error", "uvicorn.access", "starlette"):
        assert logging.getLogger(name).level == logging.WARNING
    assert logging.getLogger("docket").level == logging.INFO
    assert log_file.parent.is_dir()


def test_an_exception_before_any_answer_is_a_500_that_isnt_raised_again(log_file: Path) -> None:
    async def crash(scope: object, receive: object, send: object) -> None:
        raise OSError(f"{NAME} is unreachable")

    guarded = logs.Requests(crash)  # pyright: ignore[reportArgumentType]
    answer = TestClient(guarded, base_url=OURS).get("/api/anything")
    assert answer.status_code == 500
    assert answer.json() == logs.INTERNAL
    for handler in logging.getLogger().handlers:
        handler.flush()
    written = log_file.read_text(encoding="utf-8")
    assert "builtins.OSError" in written
    assert NAME not in written
    assert "GET <other> 500" in written


def test_status_without_a_readable_index_still_names_the_vault(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreadable(_vault: object) -> object:
        raise DocketError("SQLite 3.27 or later is needed")

    monkeypatch.setattr(index, "status", unreadable)
    body = client.get("/api/status", headers=BEARER).json()
    assert (body["index"], body["vault_problem"], body["machine"]) == (None, None, "test-box")
