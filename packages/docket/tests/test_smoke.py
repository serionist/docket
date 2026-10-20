"""The smoke set (DR-0754): every surface of Docket once, which Windows and macOS run on every PR beside the test files
that can differ by system. One case per CLI command group, read from the CLI itself, so a new group fails here until it
has one; the service started as a process, every MCP tool called through it, and a sync run whose capture the capture
hook posts as Claude Code runs it; a forget at the faked console; the ledger's import; and a media put promoted into
git-lfs and pushed to a bare remote. The pages in a browser are test_web_browser_smoke.py's.

Each case runs on a copy of one invented project built once per run: the fixture vault's raw, a recording note on its
call, the index, a folder source, and the invented memo source of the live-run tests. No case reaches the real Docket
folders, the keychain, a registered service, the default port or a browser. Invented people and data only."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
import pytest
import typer.main
from docket_testkit import REC, SIDE, AtConsole, copied, git, make_config, make_notes_repo, sh, write_mapper, write_raw
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent
from media_kit import CALL, audio, media_vault
from sync_kit import MAPPER, SKILL, TOOLS, as_structured, memo
from test_ledger_import import Export, day, item_fields

from docket import cli, recall, userconfig
from docket.cli import main
from docket.index import build as index_build
from docket.media import layout
from docket.media.keystore import KeyStore
from docket.media.put import drop_folder
from docket.service import client, install
from docket.service.token import Tokens
from docket.vault import cursors
from docket.vault.config import SourceConfig
from docket.vault.vault import Vault

pytestmark = pytest.mark.smoke

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "mcp-tools.json"
NOTE_ID = "recording-note:2026-10-14-supplier-call"
NOTE = f"""---
id: {NOTE_ID}
type: recording-note
covers:
  - {REC}
by: claude-test, sync 2026-10-14
---

# Supplier call

- The consultant is 10 million forints. [r:{REC}@0:20]
"""
QUESTIONS = f"""schema_version: 1
asked: "2026-10-14T08:00:00+00:00"
questions:
  - q: the consultant's price
    want: ["r:{REC}@00:20"]
    kind: paraphrase
    lang: en
"""
SESSION = "5e55a0e1-0000-4000-8000-000000000266"
needs_lfs = pytest.mark.skipif(shutil.which("git-lfs") is None, reason="needs git-lfs")


def _build(folder: Path) -> Path:
    """A consuming project: the memo source's mapper and skill, and a vault holding the fixture's raw and a note on its
    call, indexed, with a folder source and this machine's cursor on the calls."""
    project = folder / "project"
    project.mkdir()
    sh(project, "git", "init", "-q", "-b", "main")
    write_mapper(project, "memo", MAPPER)
    (project / ".claude" / "skills" / "sources" / "memo" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    config = make_config()
    config.sources["memo"] = SourceConfig(
        sensitivity="mixed", mapper=".claude/skills/sources/memo/mapper.py", tools=TOOLS
    )
    # A folder source, its folder named for each machine in the user's config (DR-0490).
    config.sources["documents"] = SourceConfig(folder="~/Work")
    vault = Vault.init(project / "vault", config, "test-box")
    write_raw(vault.root)
    vault.commit([], "Raw: the fixture")
    # Media is put where its source runs: this machine has ingested the calls.
    cursors.save(vault, "calls", cursors.Cursor())
    recall.doc_put(vault, NOTE)
    index_build.rebuild(vault)
    return project / "vault"


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault.open(copied(tmp_path / "copy", _build, f"{__name__}.project"))


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Nothing:
    """The service's registration, none whatever this machine has registered; a case that would change it fails."""

    def install(self, launch: object) -> str:
        raise AssertionError("a smoke case registered the service")

    def stop(self) -> None:
        raise AssertionError("a smoke case stopped the registered service")

    def uninstall(self) -> None:
        raise AssertionError("a smoke case uninstalled the service")

    def installed(self) -> str | None:
        return None


@pytest.fixture(autouse=True)
def guarded(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """No case reaches this machine's registered service, the default port or a browser: the registration is none,
    the default port one nothing answers on, and a page to open is only kept, here."""
    monkeypatch.setattr(install, "installer_for", lambda system=None, *, stop_pid=None: Nothing())
    monkeypatch.setattr(client, "DEFAULT_PORT", free_port())
    pages: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *_, **__: pages.append(url) or True)
    return pages


# -- one case per command group -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Here:
    vault: Path
    tmp: Path


def _in_vault(*args: str) -> Callable[[Here], list[str]]:
    return lambda here: [*args, "--vault", str(here.vault)]


def _eval(here: Here) -> list[str]:
    path = here.tmp / "questions.yaml"
    path.write_text(QUESTIONS, encoding="utf-8")
    return ["eval", str(path), "--no-models", "--vault", str(here.vault)]


def _import(here: Here) -> list[str]:
    return ["import", "notes", str(make_notes_repo(here.tmp)), "--dry-run", "--vault", str(here.vault)]


def _ingest(here: Here) -> list[str]:
    folder = here.tmp / "backfill"
    folder.mkdir()
    (folder / "b1.json").write_text(json.dumps(memo("b1")), encoding="utf-8")
    return ["ingest", "memo", str(folder), "--vault", str(here.vault)]


def _init(here: Here) -> list[str]:
    flags = ["--owner", "Ada Example", "--email", "ada@example.invalid", "--language", "en", "--time-zone",
             "Europe/Budapest", "--machine", "test-box", "--source", "chat"]  # fmt: skip
    return ["init", "--yes", "--path", str(here.tmp / "new-vault"), "--remote", "none", *flags]


def _scan(here: Here) -> list[str]:
    work = here.tmp / "work"
    work.mkdir()
    (work / "plan.md").write_text("# The pilot's plan\n\nThe venue first.\n", encoding="utf-8")
    userconfig.set_section("folders", {str(here.vault): {"documents": str(work)}})
    return ["scan", "documents", "--vault", str(here.vault)]


# Each group's case: its command line, its exit code and what it says.
CASES: dict[str, tuple[Callable[[Here], list[str]], int, str]] = {
    "brief": (_in_vault("brief", "stale"), 0, "nothing stale: every brief is current"),
    "curate": (_in_vault("curate", "status"), 0, "review   no rounds yet"),
    "decision": (_in_vault("decision", "list"), 0, "no decisions yet"),
    "doc": (_in_vault("doc", "read", NOTE_ID), 0, f"- The consultant is 10 million forints. [r:{REC}@0:20]"),
    "eval": (_eval, 0, ": 1 questions, k 10"),
    "hold": (_in_vault("hold", "list"), 0, "nothing held"),
    "import": (_import, 0, "dry run: nothing written"),
    "index": (_in_vault("index", "rebuild", "--no-models"), 0, "records 8  chunks 14  docs 1"),
    "ingest": (_ingest, 0, "staged     memo: 1 pending, 0 ready"),
    "init": (_init, 0, "Created the vault at"),
    "media": (_in_vault("media", "status"), 0, "waiting  0 file(s) in staging"),
    "models": (_in_vault("models", "status"), 0, "models     off"),
    "reconcile": (_in_vault("reconcile", "list"), 0, "nothing to reconcile"),
    "redaction": (_in_vault("redaction", "set", "phone", "on"), 0, "redact   phone on, committed"),
    "render": (_in_vault("render"), 0, "nothing to render"),
    "scan": (_scan, 0, "staged   documents: 0 pending, 1 ready"),
    "search": (_in_vault("search", "consultant", "--no-models"), 0, f"r:{REC}@00:20-00:35"),
    "source": (_in_vault("source", "new", "zebra-notes"), 0, "source   zebra-notes in vault.yaml"),
    "staging": (_in_vault("staging", "list"), 0, "nothing waits"),
    "sync": (_in_vault("sync", "start", "memo"), 0, "agent memo: "),
    # The one answer every machine gives without Whisper installed: the recording found, then speech asked for.
    "transcribe": (_in_vault("transcribe", REC, "--here"), 2, "speech isn't installed on this machine"),
    "vault": (_in_vault("vault", "check"), 0, "vault ok"),
}
# The groups a case below runs, through the process or the console they need.
SERVICE_CASE = "test_the_service_as_a_process_answers_every_tool_and_the_commands_that_ask_it"
ELSEWHERE = {
    "serve": SERVICE_CASE,
    "service": SERVICE_CASE,
    "open": SERVICE_CASE,
    "hook": SERVICE_CASE,
    "forget": "test_a_forget_at_the_console_takes_a_record_out_for_good",
    "ledger": "test_the_ledger_import_writes_an_export_the_ledger_then_shows",
}


def test_every_command_group_has_its_smoke_case() -> None:
    groups = set(typer.main.get_command(cli.app).commands)  # pyright: ignore[reportAttributeAccessIssue]
    assert groups - set(CASES) - set(ELSEWHERE) == set(), "a command group without a smoke case: give it one here"
    assert set(CASES) | set(ELSEWHERE) == groups
    assert set(ELSEWHERE.values()) <= set(globals())


@pytest.mark.parametrize("group", sorted(CASES))
def test_each_command_group_runs(group: str, vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    argv, code, said = CASES[group]
    args = argv(Here(vault.root, tmp_path))
    found = main(args)
    out, err = capsys.readouterr()
    assert found == code, f"docket {' '.join(args)}\n{out}\n{err}"
    assert said in out + err


# -- the service as a process, every MCP tool, and a sync run -------------------------------------------------------


def _wait_for_port(proc: subprocess.Popen[bytes]) -> int:
    """The port the service process recorded, once it answers; on Windows a venv's python.exe starts the real
    interpreter as a child, so the pid on record isn't the one Popen gives."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert proc.poll() is None, "the service process ended"
        record = client.recorded()
        if record is not None and record.get("pid") != os.getpid():
            answer = client.call("GET", "/api/health", at=record["port"])
            if answer is not None and answer.status == 200:
                return int(record["port"])
        time.sleep(0.1)
    raise AssertionError("the service didn't start")


def text_of(result: CallToolResult) -> str:
    first = result.content[0]
    assert isinstance(first, TextContent)
    return first.text


def _hook(event: dict[str, Any]) -> str:
    """The capture hook as Claude Code runs it: a process of its own, the event on its stdin; its line for Claude."""
    done = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "docket", "hook", "post-tool-use"],
        input=json.dumps(event), capture_output=True, text=True, encoding="utf-8", check=True, timeout=60,
    )  # fmt: skip
    return str(json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"])


def _event(n: int, tool: str, arguments: dict[str, Any], response: str) -> dict[str, Any]:
    """What Claude Code hands the hook after a tool's call, as the probe recorded it, for this session's nth call."""
    data: dict[str, Any] = json.loads((FIXTURES / "post-tool-use" / "structured.json").read_text(encoding="utf-8"))
    changed = {"tool_name": tool, "tool_input": arguments, "tool_response": response, "tool_use_id": f"toolu_smoke_{n}"}
    return {**data, "session_id": SESSION, **changed}


LEDGER_ADD = {"op": "add", "fields": {"kind": "reply", "title": "Send Ada the dates", "who": "Ada Example",
                                      "stream": "pilot", "source": SIDE[0]}}  # fmt: skip
NEXT_REV = NOTE.replace("type: recording-note\n", "type: recording-note\nrev: 1\n").replace(
    "forints.", "forints, for the quarter."
)


async def _every_tool(port: int, token: str, take: Path) -> dict[str, CallToolResult]:
    """Each tool the golden file names, called once over HTTP: run_open and ingest with the capture hook between, as
    a sync's agent runs them."""
    golden = [tool["name"] for tool in json.loads(GOLDEN.read_text(encoding="utf-8"))]
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, trust_env=False)
    done: dict[str, CallToolResult] = {}
    async with http, Client(streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http)) as mcp:
        assert [tool.name for tool in (await mcp.list_tools()).tools] == golden

        async def call(name: str, arguments: dict[str, Any]) -> CallToolResult:
            done[name] = await mcp.call_tool(name, arguments)
            return done[name]

        await call("search", {"query": "consultant"})
        await call("read", {"locator": REC, "view": "both"})
        await call("verify", {"doc": NOTE_ID})
        await call("briefs", {"stale_only": False})
        await call("decisions", {})
        await call("doc_put", {"text": NEXT_REV})
        opened = await call("run_open", {"source": "memo"})
        run = (opened.structured_content or {})["run"]
        bound = _event(
            1, "mcp__plugin_docket_docket__run_open", {"source": "memo"}, json.dumps(opened.structured_content)
        )
        assert await asyncio.to_thread(_hook, bound) == f"docket: run {run} is this session's"
        fetched = _event(2, "mcp__memos__get_memo", {"id": "s1"}, as_structured(memo("s1")))
        assert f"kept capture 1 of run {run}" in await asyncio.to_thread(_hook, fetched)
        await call("ingest", {"run": run, "captures": 1})
        await call("media_put", {"rid": REC, "path": str(take)})
        await call("transcribe", {"rid": REC})
        await call("ledger", {})
        await call("ledger_batch", {"ops": [LEDGER_ADD], "by": "sync"})
    assert sorted(done) == sorted(golden), "an MCP tool without a smoke call: call it here"
    return done


def test_the_service_as_a_process_answers_every_tool_and_the_commands_that_ask_it(
    vault: Vault, guarded: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    token = Tokens().ensure().value
    KeyStore(vault.home).init("file")
    take = drop_folder() / "take"
    take.parent.mkdir(parents=True, exist_ok=True)
    take.write_bytes(audio(4000))
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-m", "docket", "serve", "--port", "0", "--vault", str(vault.root)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        port = _wait_for_port(proc)
        done = asyncio.run(_every_tool(port, token, take))
        errors = {name: text_of(result) for name, result in done.items() if result.is_error}
        # Without Whisper the service names what transcribing needs; every other tool answers.
        assert list(errors) == ["transcribe"], errors
        assert "speech isn't installed on this machine" in errors["transcribe"]
        assert f"r:{REC}@00:20-00:35" in text_of(done["search"])
        assert f"{NOTE_ID} rev 1 line 14:" in text_of(done["read"])
        assert "00:20  Ben Sample: The consultant is 10 million forints." in text_of(done["verify"])
        assert (done["doc_put"].structured_content or {})["rev"] == 2
        assert "staged   1 pending, 0 ready" in text_of(done["ingest"])
        assert (done["media_put"].structured_content or {}) == {"rid": REC, "ext": "mp3", "bytes": 4000}
        assert text_of(done["ledger"]).startswith("0 open, 0 snoozed, 0 overdue")
        assert "A-0001 added: Send Ada the dates" in text_of(done["ledger_batch"])
        capsys.readouterr()
        # The commands that ask the running service, in this process, as a user runs them beside it.
        assert main(["service", "status"]) == 0
        assert f"running    yes, port {port}" in capsys.readouterr().out
        assert main(["open", "/search?query=consultant"]) == 0
        assert f"http://127.0.0.1:{port}/session?code=" in capsys.readouterr().out
        assert len(guarded) == 1 and guarded[0].startswith("file:")
        assert main(["hook", "session-start"]) == 0
        context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
        assert f"docket: running on 127.0.0.1:{port}" in context
    finally:
        proc.terminate()
        proc.wait(30)
    assert client.call("GET", "/api/health", at=port) is None


# -- a forget, the ledger's import, media in git-lfs ----------------------------------------------------------------


def test_a_forget_at_the_console_takes_a_record_out_for_good(
    vault: Vault, owner: Callable[..., AtConsole], capsys: pytest.CaptureFixture[str]
) -> None:
    owner("code")
    assert main(["forget", SIDE[0], "--now", "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "forgot    1 record(s), 0 media file(s), 0 doc(s), 0 ledger op(s)" in out
    assert "index     rebuilt without what was forgotten" in out
    assert main(["doc", "read", SIDE[0], "--vault", str(vault.root)]) == 2
    assert f"{SIDE[0]} was forgotten: nothing of it is kept" in capsys.readouterr().err


def test_the_ledger_import_writes_an_export_the_ledger_then_shows(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export = Export()
    export.stream("pilot", day(9, 1), status="active")
    export.item(1, day(9, 1), **item_fields("reply", "Send Ada the venue dates", "Ada Example", "pilot", "app:tasks"))
    export.on(1, "triage", day(9, 2), "user", verdict="chase")
    path = tmp_path / "export.jsonl"
    path.write_text(export.text(), encoding="utf-8")
    assert main(["ledger", "import", str(path), "--vault", str(vault.root)]) == 0
    assert "written in 1 commit(s)" in capsys.readouterr().out
    assert main(["ledger", "show", "A-0001", "--vault", str(vault.root)]) == 0
    shown = capsys.readouterr().out
    assert "A-0001  Send Ada the venue dates" in shown
    assert "state        open, triage chase" in shown


@needs_lfs
def test_a_media_put_is_promoted_into_git_lfs_and_pushed_to_a_bare_remote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = media_vault(tmp_path)
    take = drop_folder() / "take"
    take.parent.mkdir(parents=True, exist_ok=True)
    take.write_bytes(audio(4000))
    assert main(["media", "put", CALL, str(take), "--vault", str(vault.root)]) == 0
    assert main(["staging", "promote", "--vault", str(vault.root)]) == 0
    assert "media     plaud: 1 file(s) stored in media/" in capsys.readouterr().out
    [stored] = layout.stored_files(vault.root, CALL)
    assert stored.name in git(vault.root, "lfs", "ls-files", "--name-only")
    remote = tmp_path / "remote.git"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", "remote.git")
    vault.git.run("remote", "add", "origin", str(remote))
    vault.git.run("push", "-q", "origin", "main")
    sealed = stored.read_bytes()
    oid = hashlib.sha256(sealed).hexdigest()
    assert (remote / "lfs" / "objects" / oid[:2] / oid[2:4] / oid).read_bytes() == sealed
