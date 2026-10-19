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
from docket_testkit import empty_vault, make_config
from ledger_kit import meeting, op, write_ops

from docket import entry, recall
from docket.cli import app, main
from docket.ledger.store import BATCH
from docket.ledger.views import VIEWS
from docket.service import client, state, writer
from docket.service.token import Tokens
from docket.sources import scaffold
from docket.vault.vault import Vault
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


def test_recall_brief_and_decision_pre_approve_only_docket_s_read_only_tools() -> None:
    golden = json.loads((REPO / "packages/docket/tests/fixtures/mcp-tools.json").read_text(encoding="utf-8"))
    read_only = {t["name"] for t in golden if t["annotations"]["read_only_hint"]}
    # The ledger's reads are read-only too, so the sync and tasks skills can pre-approve them (DR-0637).
    assert read_only == {"search", "read", "verify", "briefs", "decisions", "ledger"}
    # The plugin's name, then the server's key in .mcp.json: the names Claude Code gives a plugin's tools.
    prefix = "mcp__plugin_docket_docket__"
    for skill, tools in (
        ("recall", {"search", "read", "verify"}),
        ("brief", {"briefs", "decisions", "search", "read", "verify"}),
        ("decision", {"decisions", "search", "read", "verify"}),
    ):
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


def test_cuts_pre_approves_only_reading_holds_and_runs_the_release_on_the_owner_s_yes() -> None:
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
    # Where hold show's output stays, said before the first one; and the release run only on the owner's yes, after
    # the cuts were shown (DR-0605).
    for words in ("session log", "tool-results/*.txt", "bridged", "cleanupPeriodDays", "delete this session"):
        assert words in text, words
    assert text.index("Show the owner one table per recording") < text.index("On their yes, and only for what")


def test_setup_adds_each_source_on_the_user_s_yes_and_leaves_the_key_to_them() -> None:
    """Adding a source runs on the user's yes, one at a time (DR-0605); the API key is typed in their own terminal, and
    credentials off stays theirs at their console."""
    setup = (PLUGIN / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
    for words in ("only on the user's yes, one source at a time", "they type in their own terminal", "CLAUDECODE"):
        assert words in setup, words
    assert "source new" not in frontmatter("setup").get("allowed-tools", "")
    decision = (PLUGIN / "skills" / "decision" / "SKILL.md").read_text(encoding="utf-8")
    used = re.findall(r"docket decision set[^`\n]*", decision)
    assert used
    assert {flag for line in used for flag in re.findall(r"--[a-z][a-z-]*", line)} <= options("decision", "set")
    plaud = (PLUGIN.parent / "examples" / "sources" / "plaud" / "SKILL.md").read_text(encoding="utf-8")
    assert "Then commit `vault.yaml`" in plaud


# -- the sync skill (DR-0685 to DR-0690) ------------------------------------------------------------------------------

SYNC = PLUGIN / "skills" / "sync"
# The return block (DR-0687): each label with the fields its line starts with. A door: a line gains fields only at
# its end, and no label is renamed or removed.
BLOCK = {
    "RUN": ["<source>", "<run>", "captures <n>", "ready <n>, pending <n>", "skipped <n>", "mark moved yes/no",
            "<a note>"],
    "OWED": ["<who>", "<locator>", "<asked, a day>", "<the ask, short>", "<why still open>",
             "today / this week / whenever"],
    "INVITES": ["<organiser>", "<the event's or the invite mail's rid>", "<when>", "<clashes>"],
    "WAITING": ["<who>", "<locator>", "<since>", "<what the owner waits for>"],
    "CLOSES": ["<A- ref>", "done / moot", "<locator of the evidence>", "<how, one line>"],
    "MEETINGS": ["<M- ref>", "<field>=<value>; ...", "<locator of the evidence>"],
    "STREAMS": ["<stream>", "<field>=<value>; ...", "<the news, one line>", "<locator>"],
    "NOTES": ["<doc id>", "<recording rid>", "<start>", "<end>", "<M- ref when its content names one>"],
    "MINUTES": ["<M- ref>", "<point id>", "answered / dropped / open", "<the answer, one line>", "<locator>"],
    "OUTCOME": ["<M- ref>", "<2 to 4 short lines: what was decided, who does what>", "<locator>"],
    "LEARNED": ["SKILL.md / sync.md", "<what differs from it, for next time>"],
}  # fmt: skip


def sync_texts() -> dict[str, str]:
    return {name: (SYNC / name).read_text(encoding="utf-8") for name in ("SKILL.md", "fork.md")}


def golden_tools() -> dict[str, Any]:
    golden = json.loads((REPO / "packages/docket/tests/fixtures/mcp-tools.json").read_text(encoding="utf-8"))
    return {t["name"]: t for t in golden}


def test_sync_pre_approves_only_reads() -> None:
    """The sync's own writes, the ledger batch and the agents' runs, classifies and promotions, ask (DR-0605)."""
    tools = golden_tools()
    allowed = frontmatter("sync")["allowed-tools"]
    prefix = "mcp__plugin_docket_docket__"
    named = {name.removeprefix(prefix) for name in re.findall(rf"{prefix}\w+", allowed)}
    assert named == {"ledger", "search", "read", "verify", "briefs", "decisions"}
    assert all(tools[name]["annotations"]["read_only_hint"] for name in named)
    commands = re.findall(r"Bash\(([^)]*)\)", allowed)
    assert commands == [
        "docket ledger day", "docket ledger day *", "docket staging list", "docket staging list *",
        "docket staging show *",
    ]  # fmt: skip
    assert re.sub(rf"{prefix}\w+|Bash\([^)]*\)", "", allowed).split() == []


def used_commands(text: str) -> list[tuple[list[str], set[str]]]:
    """Each `docket ...` a text names in backticks: the command's words, as far as docket has them, and its flags."""
    out: list[tuple[list[str], set[str]]] = []
    for span in re.findall(r"`(docket [^`]+)`", text):
        words = span.split()[1:]
        command: Any = typer.main.get_command(app)
        path: list[str] = []
        for word in words:
            subs = getattr(command, "commands", None)
            if subs is None or word not in subs:
                break
            command = subs[word]
            path.append(word)
        out.append((path, {w.split("=")[0] for w in words if w.startswith("--")}))
    return out


def test_sync_names_commands_options_ops_views_and_tools_docket_has() -> None:
    tools = golden_tools()
    seen: set[tuple[str, ...]] = set()
    for name, text in sync_texts().items():
        for path, flags in used_commands(text):
            command: Any = typer.main.get_command(app)
            for word in path:
                command = command.commands[word]
            assert not hasattr(command, "commands"), (name, path)
            assert flags <= options(*path), (name, path, flags)
            seen.add(tuple(path))
        for view in re.findall(r"`ledger` view `([a-z]+)`", text):
            assert view in VIEWS, (name, view)
        # Any other view named is read's.
        assert set(re.findall(r"view `([a-z]+)`", text)) <= {*VIEWS, *recall.VIEWS}, name
        for tool, params in re.findall(r"\b(run_open|ingest|ledger_batch)\(([^)]*)\)", text):
            given = {p.split("=")[0].strip() for p in params.split(",") if "=" in p}
            assert given <= set(tools[tool]["input_schema"]["properties"]), (name, tool)
    assert seen == {("ledger", "day"), ("staging", "list"), ("staging", "show"), ("staging", "classify"),
                    ("staging", "promote")}  # fmt: skip
    skill = sync_texts()["SKILL.md"]
    table = skill[skill.index("| Order | Ops | Rule |") : skill.index("### A worked batch")]
    for row in table.splitlines()[2:]:
        cells = row.split("|")
        if len(cells) > 3:
            assert set(re.findall(r"`([a-z_]+)`", cells[2])) <= set(BATCH), row
    assert set(re.findall(r'"op": "([a-z_]+)"', skill)) <= set(BATCH)
    fork = sync_texts()["fork.md"]
    named = re.search(r"ToolSearch for Docket's tools you will use \(([^)]*)\)", fork)
    assert named is not None
    assert set(re.findall(r"`([a-z_]+)`", named.group(1))) <= set(tools)
    for key in ("dry_run", "by"):
        assert f"`{key}: " in skill and key in tools["ledger_batch"]["input_schema"]["properties"]


def test_the_sync_s_block_keeps_its_labels_and_each_line_its_fields() -> None:
    fork = sync_texts()["fork.md"]
    block = fork.split("```\nRUN:", 1)[1].split("```", 1)[0]
    found: dict[str, list[str]] = {}
    for line in filter(None, f"RUN:{block}".splitlines()):
        label, _, rest = line.partition(":")
        found[label] = [field.strip() for field in rest.split(" | ")]
    assert list(found) == list(BLOCK)
    for label, fields in BLOCK.items():
        assert found[label][: len(fields)] == fields, label
    # The placeholders the skill fills are the ones the prompt uses.
    filled = set(re.findall(r"\{([A-Z_]+)\}", fork))
    assert filled == set(re.findall(r"`\{([A-Z_]+)\}`", sync_texts()["SKILL.md"])) == {
        "SOURCE", "VAULT", "TODAY", "ZONE", "OWED_SINCE",
    }  # fmt: skip


def test_agents_md_names_the_sync_skill_and_its_doors() -> None:
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    for words in ("`sync` (DR-0685)", "(DR-0687)", "`sync.md` (DR-0688)"):
        assert words in agents, words


POINT = "01M4QJ9FZ000X93GNMW1YNY5N8"


def pilot_vault(tmp_path: Path) -> Vault:
    """The worked batch's ledger: Ben's and Cleo's asks, the pilot stream, and two pilot weeklies, the first with a
    point. Invented people only."""
    vault = empty_vault(tmp_path, make_config())
    weekly = {"subject": "Pilot weekly", "organiser": "Ben Sample"}
    made = [
        op("stream", 1),
        op("add", 2, number=1, fields={"kind": "reply", "title": "Send Ben the pilot dates", "who": "Ben Sample",
                                       "stream": "pilot", "source": "slack:C0PILOT/1791100000.000100"}),
        op("add", 3, number=2, fields={"kind": "waiting", "title": "Cleo's quote for the sensors", "who": "Cleo Test",
                                       "stream": "pilot", "source": "outlook:AAMkADquote1"}),
        meeting(4, "calendar:AAMkADweekly-1019", number=1, start="2026-10-19T10:00+02:00",
                end="2026-10-19T10:30+02:00", **weekly),
        meeting(5, "calendar:AAMkADweekly-1026", number=2, start="2026-10-26T10:00+01:00",
                end="2026-10-26T10:30+01:00", **weekly),
        op("point", 6, 4, id=POINT, fields={"text": "Ben: the pilot's dates"}),
    ]  # fmt: skip
    vault.commit(write_ops(vault.root, made), "The pilot's ledger")
    return vault


def test_the_worked_batch_runs_clean_through_a_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    skill = sync_texts()["SKILL.md"]
    example = skill.split("### A worked batch", 1)[1].split("```json\n", 1)[1].split("```", 1)[0]
    batch = tmp_path / "sync-ops.json"
    batch.write_text(example, encoding="utf-8")
    vault = pilot_vault(tmp_path / "v")
    capsys.readouterr()
    code = main(["ledger", "batch", str(batch), "--by", "sync", "--dry-run", "--vault", str(vault.root)])
    out = capsys.readouterr().out.splitlines()
    assert code == 0, out
    assert out[-1].startswith("dry run: ") and out[-1].endswith(" op(s) would be written; nothing written"), out
    assert not any(": refused, " in line for line in out), out
