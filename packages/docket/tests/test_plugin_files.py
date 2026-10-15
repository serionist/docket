"""The Claude Code plugin's files, read from the repo, against the package they call: a change to either fails here
before it reaches a user. Claude Code validates the files themselves in the full gate."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import typer.main

from docket.cli import app, main
from docket.service import client, state, writer
from docket.service.token import Tokens
from docket.sources import scaffold
from docket.vault.wizard import KNOWN_SOURCES

REPO = Path(__file__).resolve().parents[3]
PLUGIN = REPO / "plugin"


def read_json(rel: str) -> Any:
    return json.loads((PLUGIN / rel).read_text(encoding="utf-8"))


def test_the_mcp_entry_is_the_service_on_its_default_port() -> None:
    servers = read_json(".mcp.json")["mcpServers"]
    assert list(servers) == ["docket"]
    entry = servers["docket"]
    assert set(entry) == {"type", "url", "headersHelper", "timeout"}
    assert entry["type"] == "http"
    assert entry["url"] == f"http://127.0.0.1:${{DOCKET_PORT:-{client.DEFAULT_PORT}}}/mcp"
    assert urlsplit(entry["url"].replace("${DOCKET_PORT:-8741}", "8741")).path == "/mcp"
    # Above the service's own waits, so a slow call ends with Docket's message rather than the client's.
    waits = max(state.LOCK_SECONDS, writer.UPDATE_WAIT_SECONDS)
    assert entry["timeout"] > waits * 1000


def test_the_header_helper_prints_the_header_claude_code_reads(capsys: pytest.CaptureFixture[str]) -> None:
    command = shlex.split(read_json(".mcp.json")["mcpServers"]["docket"]["headersHelper"])
    assert command[0] == "docket"
    token = Tokens().rotate().value
    capsys.readouterr()
    assert main(command[1:]) == 0
    assert json.loads(capsys.readouterr().out) == {"Authorization": f"Bearer {token}"}


def test_the_session_start_hook_runs_a_command_docket_has(capsys: pytest.CaptureFixture[str]) -> None:
    hooks = read_json("hooks/hooks.json")["hooks"]
    assert list(hooks) == ["SessionStart"]
    (group,) = hooks["SessionStart"]
    assert "matcher" not in group
    (hook,) = group["hooks"]
    assert hook == {"type": "command", "command": "docket", "args": ["hook", "session-start"], "timeout": 15}
    assert main([*hook["args"], "--help"]) == 0
    assert "SessionStart" in capsys.readouterr().out


def test_the_plugin_is_past_its_placeholder_version() -> None:
    manifest = read_json(".claude-plugin/plugin.json")
    assert manifest["name"] == "docket"
    assert tuple(int(n) for n in manifest["version"].split(".")) >= (0, 1, 0)


def frontmatter(skill: str) -> dict[str, str]:
    text = (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    head = text.split("---\n", 2)[1]
    return {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in head.splitlines())}


def test_recall_pre_approves_only_docket_s_read_only_tools() -> None:
    golden = json.loads((REPO / "packages/docket/tests/fixtures/mcp-tools.json").read_text(encoding="utf-8"))
    read_only = {t["name"] for t in golden if t["annotations"]["read_only_hint"]}
    allowed = frontmatter("recall")["allowed-tools"].split()
    # The plugin's name, then the server's key in .mcp.json: the names Claude Code gives a plugin's tools.
    prefix = "mcp__plugin_docket_docket__"
    assert all(name.startswith(prefix) for name in allowed)
    assert {name.removeprefix(prefix) for name in allowed} == read_only == {"search", "read", "verify"}
    assert list(read_json(".mcp.json")["mcpServers"]) == ["docket"]


def options(*path: str) -> set[str]:
    """A docket command's options, read from the command itself: its help text cuts long names to the terminal."""
    command: Any = typer.main.get_command(app)
    for name in path:
        command = command.commands[name]
    return {opt for param in command.params for opt in (*param.opts, *param.secondary_opts)}


def test_recall_names_command_line_options_docket_has() -> None:
    text = (PLUGIN / "skills" / "recall" / "SKILL.md").read_text(encoding="utf-8")
    for command, wanted in (
        (["search"], {"--person", "--entity", "--since", "--until", "-k", "--vault"}),
        (["doc", "read"], {"--view", "--around", "--vault"}),
        (["doc", "verify"], {"--line", "--vault"}),
    ):
        assert f"docket {' '.join(command)}" in text
        assert wanted <= options(*command), command


def test_setup_knows_the_sources_the_wizard_knows_and_their_references() -> None:
    text = (PLUGIN / "skills" / "setup" / "sources.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| `([a-z0-9._-]+)` \| (work|mixed|private) \| .+ \| (yes|no) \|$", text, re.MULTILINE)
    assert {name: level for name, level, _ in rows} == KNOWN_SOURCES
    assert {name for name, _, ref in rows if ref == "yes"} == set(scaffold.REFERENCES)
    for name in scaffold.REFERENCES:
        assert scaffold.template_files(name)


def test_setup_runs_commands_with_flags_docket_has() -> None:
    text = (PLUGIN / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
    for command in (["init"], ["source", "new"], ["service", "install"], ["vault", "status"], ["ingest"]):
        used = re.findall(rf"docket {' '.join(command)}[^`\n]*", text)
        assert used, command
        flags = {flag for line in used for flag in re.findall(r"--[a-z][a-z-]*", line)}
        assert flags <= options(*command), command
    # Every override the skill may pass is a flag init has, and the skill names each one.
    for flag in ("--allow-shared", "--remote-is-private", "--allow-dirty-parent"):
        assert f"`{flag}`" in text
        assert flag in options("init")
