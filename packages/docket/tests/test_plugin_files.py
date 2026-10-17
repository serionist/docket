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

from docket import entry
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


def test_the_session_start_hook_runs_a_command_docket_has() -> None:
    hooks = read_json("hooks/hooks.json")["hooks"]
    assert list(hooks) == ["SessionStart", "PostToolUse"]
    (group,) = hooks["SessionStart"]
    assert "matcher" not in group
    (hook,) = group["hooks"]
    assert hook == {"type": "command", "command": "docket", "args": ["hook", "session-start"], "timeout": 15}
    command: Any = typer.main.get_command(app)
    for name in hook["args"]:
        command = command.commands[name]
    assert "SessionStart" in command.help


def test_the_capture_hook_runs_for_mcp_tools_only_and_runs_a_command_docket_has() -> None:
    (group,) = read_json("hooks/hooks.json")["hooks"]["PostToolUse"]
    assert group["matcher"] == "mcp__.*"
    (hook,) = group["hooks"]
    # Not async: a bind must land before Claude's next call, and Claude reads the hook's line beside the result.
    assert hook == {"type": "command", "command": "docket", "args": ["hook", "post-tool-use"], "timeout": 30}
    assert tuple(hook["args"]) == entry.HOOK
    command: Any = typer.main.get_command(app)
    for name in hook["args"]:
        command = command.commands[name]
    assert "PostToolUse" in command.help


def test_the_plugin_is_past_its_placeholder_version() -> None:
    manifest = read_json(".claude-plugin/plugin.json")
    assert manifest["name"] == "docket"
    assert tuple(int(n) for n in manifest["version"].split(".")) >= (0, 1, 0)


def frontmatter(skill: str) -> dict[str, str]:
    text = (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    head = text.split("---\n", 2)[1]
    return {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in head.splitlines())}


def test_recall_and_brief_pre_approve_only_docket_s_read_only_tools() -> None:
    golden = json.loads((REPO / "packages/docket/tests/fixtures/mcp-tools.json").read_text(encoding="utf-8"))
    read_only = {t["name"] for t in golden if t["annotations"]["read_only_hint"]}
    assert read_only == {"search", "read", "verify", "briefs"}
    # The plugin's name, then the server's key in .mcp.json: the names Claude Code gives a plugin's tools.
    prefix = "mcp__plugin_docket_docket__"
    for skill, tools in (("recall", {"search", "read", "verify"}), ("brief", {"briefs", "search", "read", "verify"})):
        allowed = frontmatter(skill)["allowed-tools"].split()
        assert all(name.startswith(prefix) for name in allowed), skill
        assert {name.removeprefix(prefix) for name in allowed} == tools, skill
        assert tools <= read_only, skill
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
    commands = (
        ["init"],
        ["source", "new"],
        ["service", "install"],
        ["vault", "status"],
        ["ingest"],
        ["redaction", "set"],
    )
    for command in commands:
        used = re.findall(rf"docket {' '.join(command)}[^`\n]*", text)
        assert used, command
        flags = {flag for line in used for flag in re.findall(r"--[a-z][a-z-]*", line)}
        assert flags <= options(*command), command
    # Every override the skill may pass is a flag init has, and the skill names each one.
    for flag in ("--allow-shared", "--remote-is-private", "--allow-dirty-parent"):
        assert f"`{flag}`" in text
        assert flag in options("init")
    # The redaction answers go to init as its own flags: credentials off only on a no, phone numbers on only on a yes.
    for flag in ("--no-redact-credentials", "--redact-phone"):
        assert f"`{flag}`" in text
        assert flag in options("init")


def test_cuts_pre_approves_only_reading_holds_and_hands_the_release_to_the_owner() -> None:
    allowed = re.findall(r"Bash\(([^)]*)\)", frontmatter("cuts")["allowed-tools"])
    assert allowed == ["docket hold list", "docket hold list *", "docket hold show *"]
    for skill in sorted(p.name for p in (PLUGIN / "skills").iterdir()):
        assert "hold release" not in frontmatter(skill).get("allowed-tools", ""), skill
    text = (PLUGIN / "skills" / "cuts" / "SKILL.md").read_text(encoding="utf-8")
    for command in (["hold", "list"], ["hold", "show"], ["hold", "release"], ["staging", "promote"]):
        used = re.findall(rf"docket {' '.join(command)}[^`\n]*", text)
        assert used, command
        flags = {flag for line in used for flag in re.findall(r"--[a-z][a-z-]*", line)}
        assert flags <= options(*command), command
    # Where hold show's output stays, said before the first one; and the release the skill never runs.
    for words in ("session log", "tool-results/*.txt", "bridged", "cleanupPeriodDays", "delete this session"):
        assert words in text, words
    assert "Never run it yourself" in text
    assert "CLAUDECODE" in text


def test_setup_hands_each_source_to_the_owner_one_at_a_time_and_never_loosens_itself() -> None:
    """Adding a source asks at the owner's console (DR-0517): setup gives the commands one at a time, since a pasted
    block feeds its second line into the first one's question, and runs none of them."""
    setup = (PLUGIN / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
    for words in ("one command at a\ntime", "type back a code", "Never run these yourself", "CLAUDECODE", "winpty"):
        assert words in setup, words
    assert "source new" not in frontmatter("setup").get("allowed-tools", "")
    cuts = (PLUGIN / "skills" / "cuts" / "SKILL.md").read_text(encoding="utf-8")
    assert "never edit or commit it, or run a command, to let more in" in cuts
    plaud = (PLUGIN.parent / "examples" / "sources" / "plaud" / "SKILL.md").read_text(encoding="utf-8")
    assert "Then commit `vault.yaml`" in plaud
