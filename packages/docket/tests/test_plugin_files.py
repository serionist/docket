"""The Claude Code plugin's files, read from the repo, against the package they call: a change to either fails here
before it reaches a user. Claude Code validates the files themselves in the full gate."""

from __future__ import annotations

import json
import re
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import typer.main
from docket_testkit import empty_vault, make_config
from ledger_kit import meeting, op, ulid, write_ops

from docket import entry, recall
from docket.cli import app, main
from docket.ledger.ops import VERDICTS
from docket.ledger.store import BATCH
from docket.ledger.views import VIEWS
from docket.service import client, sessions, state, writer
from docket.service.token import Tokens
from docket.sources import scaffold
from docket.sync import block as sync_block
from docket.sync import folder as sync_folder
from docket.vault.vault import Vault
from docket.vault.wizard import KNOWN_SOURCES
from docket.web.pages import NAV

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


# -- the sync skill (DR-0685, DR-0690, DR-0722, DR-0746 to DR-0750, DR-0771, DR-0772) ----------------------------------

SYNC = PLUGIN / "skills" / "sync"
PROMPTS = REPO / "packages" / "docket" / "src" / "docket" / "sync"
# The block before it went to a file: each label with the fields its line started with. A door: a line gains fields
# only at its end, and no label is renamed or removed (DR-0722).
BLOCK = {
    "RUN": ["<source>", "<run>", "captures <n>", "ready <n>, pending <n>", "skipped <n>", "mark moved yes/no",
            "<a note>"],
    "OWED": ["<who>", "<locator>", "<asked, a day>", "<the ask, short>", "<why still open>",
             "today / this week / whenever"],
    "INVITES": ["<organiser>", "<the event's or the invite mail's rid>", "<when>",
                "<why it matters: the clash, new series or customer>"],
    "WAITING": ["<who>", "<locator>", "<since>", "<what the owner waits for>"],
    "CLOSES": ["<A- ref>", "done / moot", "<locator of the evidence>", "<how, one line>"],
    "MEETINGS": ["<M- ref>", "<field>=<value>; ...", "<locator of the evidence>"],
    "STREAMS": ["<stream>", "<field>=<value>; ...", "<the news, one line>", "<locator>"],
    "NOTES": ["<doc id>", "<recording rid>", "<start>", "<end>", "<M- ref when its content names one>"],
    "MINUTES": ["<M- ref>", "<point id>", "answered / dropped / open", "<the answer, one line>", "<locator>"],
    "OUTCOME": ["<M- ref>", "<2 to 4 short lines: what was decided, who does what>", "<locator>"],
    "LEARNED": ["SKILL.md / sync.md", "<what differs from it, for next time>"],
}  # fmt: skip
# The fields DR-0722 adds at the ends, which the reconcile drafts from.
GAINED = {
    "OWED": ["reply / decision / task", "<stream>", "<the A- ref it continues, else ->", "<due: YYYY-MM-DD, else ->"],
    "INVITES": ["<stream>"],
    "WAITING": ["<stream>", "<the A- ref it continues, else ->", "<due: YYYY-MM-DD, else ->"],
}


def sync_texts() -> dict[str, str]:
    """The skill, and the prompts Docket ships beside the block's parser."""
    texts = {"SKILL.md": (SYNC / "SKILL.md").read_text(encoding="utf-8")}
    return texts | {name: (PROMPTS / name).read_text(encoding="utf-8") for name in ("agent.md", "recording.md")}


def golden_tools() -> dict[str, Any]:
    golden = json.loads((REPO / "packages/docket/tests/fixtures/mcp-tools.json").read_text(encoding="utf-8"))
    return {t["name"]: t for t in golden}


def test_sync_pre_approves_only_reads_and_the_sync_folder_s_commands() -> None:
    """The sync's own writes, the ledger batch and the agents' runs, classifies and promotions, ask (DR-0605); the
    sync folder's commands write only scratch outside the vault (DR-0746, DR-0760)."""
    tools = golden_tools()
    allowed = frontmatter("sync")["allowed-tools"]
    prefix = "mcp__plugin_docket_docket__"
    named = {name.removeprefix(prefix) for name in re.findall(rf"{prefix}\w+", allowed)}
    assert named == {"ledger", "search", "read", "verify", "briefs", "decisions"}
    assert all(tools[name]["annotations"]["read_only_hint"] for name in named)
    commands = re.findall(r"Bash\(([^)]*)\)", allowed)
    assert commands == [
        "docket ledger day", "docket ledger day *", "docket staging list", "docket staging list *",
        "docket staging show *", "docket sync start", "docket sync start *", "docket sync recordings *",
        "docket sync reconcile *", "docket sync settle *", "docket sync summary *",
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
        for tool, params in re.findall(rf"\b({'|'.join(sorted(tools, key=len, reverse=True))})\(([^)]*)\)", text):
            given = {p.split("=")[0].strip() for p in params.split(",") if "=" in p}
            assert given <= set(tools[tool]["input_schema"]["properties"]), (name, tool, given)
    assert seen == {("ledger", "day"), ("staging", "list"), ("staging", "show"), ("staging", "classify"),
                    ("staging", "promote"), ("sync", "start"), ("sync", "recordings"), ("sync", "reconcile"),
                    ("sync", "settle"), ("sync", "summary"), ("ledger", "batch")}  # fmt: skip
    skill = sync_texts()["SKILL.md"]
    table = skill[skill.index("| Order | Ops | Drafted from |") : skill.index("### A worked draft")]
    for row in table.splitlines()[2:]:
        cells = row.split("|")
        if len(cells) > 3:
            assert set(re.findall(r"`([a-z_]+)`", cells[2])) <= set(BATCH), row
    assert set(re.findall(r'"op": "([a-z_]+)"', skill)) <= set(BATCH)
    for name in ("agent.md", "recording.md"):
        named = re.search(r"ToolSearch for Docket's tools you will use[ :(]+([^)\n]*)", sync_texts()[name])
        assert named is not None, name
        assert set(re.findall(r"`([a-z_]+)`", named.group(1))) <= set(tools), name
    # The batch is written from the draft's file, its dry run first (DR-0724).
    calls = re.findall(r"\bledger_batch\(([^)]*)\)", skill)
    assert [c.split(", ")[:2] for c in calls] == [["ops=[]", 'file="<the draft\'s path>"']] * 2
    assert calls[0].endswith("dry_run=true") and "dry_run" not in calls[1]


def test_the_agents_check_in_one_read_and_the_prep_asks_one_suggest() -> None:
    """An agent checks every locator of its block in one read with more, a recording at its moment and a doc by its
    summary, and the prep asks suggest once for all its meetings with refs; each asks again from what `later` lists
    until it is empty, never from the locator it already read (DR-0725)."""
    texts = {name: " ".join(text.split()) for name, text in sync_texts().items()}
    check = texts["agent.md"].split("## 7. Check", 1)[1].split("## 8.", 1)[0]
    assert re.findall(r"\bread\(([^)]*)\)", check) == [
        'locator="<the first>", more=["<the second>", "<the third>"], view="raw"'
    ]
    assert "in one call" in check and "past the room" in check
    assert "A doc id is checked in a read of its own, with the default summary view" in check
    again = "its first as `locator` and the rest in `more`, until `later` is empty"
    assert again in check and again in " ".join(tasks_text().split())
    prep = texts["SKILL.md"].split("2. Prep, at the `# prep` line", 1)[1].split("3. Settle", 1)[0]
    assert re.findall(r"\bledger\(([^)]*)\)", prep) == ['view="suggest", refs=["M-0002", "M-0003"]']
    assert "one `ledger` view `suggest` for all of them" in prep and "`suggested_for`" in prep
    assert "While `later` lists meetings past the room, ask again with those as `refs`, until `later` is empty" in prep


def test_the_settle_is_one_pass_and_the_summary_one_command() -> None:
    """The draft quotes what settling needs, so `show` only where a quoted line can't answer; one `docket sync settle`
    settles every line a choice settles, an Edit only for new words, the draft never written out again; the summary is
    one command read once, never pulled from a snapshot by shell (#272)."""
    skill = " ".join(sync_texts()["SKILL.md"].split())
    steps = skill.split("Then, in this thread, in one pass:", 1)[1].split("### A worked draft", 1)[0]
    assert "`ledger` view `show` only when a candidate's quoted line can't answer the question" in steps
    assert (
        "Settle every unsure line a choice settles in one call" in steps and "Never write the draft out again" in steps
    )
    assert "An Edit only for a line that needs new words" in steps and steps.count("`docket sync settle <sync>") == 1
    assert "one `ledger` view `suggest` for all of them" in steps
    summary = skill.split("## The summary", 1)[1].split("## Rules", 1)[0]
    assert "`docket sync summary <sync> --vault <vault>` prints the list from the ledger" in summary
    assert (
        "Read it once" in summary
        and "never pull the list out of a snapshot, a block or the draft with the shell" in summary
    )
    assert "docket staging list" not in summary


def block_of(prompt: str) -> dict[str, list[str]]:
    """A prompt's block: its fenced lines that start with a label, each label's fields."""
    found: dict[str, list[str]] = {}
    for fenced in prompt.split("```\n")[1::2]:
        for line in fenced.splitlines():
            label, colon, rest = line.partition(":")
            if colon and label in sync_block.FIELDS:
                found[label] = [field.strip() for field in rest.split(" | ")]
    return found


def test_the_sync_s_block_keeps_its_labels_and_each_line_its_fields() -> None:
    """The block is the old one with fields gained only at the ends, its labels split between the source agent's
    prompt and the recording agent's, a label both write the same in each, and the parser takes both shapes."""
    texts = sync_texts()
    agent, recording = block_of(texts["agent.md"]), block_of(texts["recording.md"])
    assert list(agent) == ["RUN", "OWED", "INVITES", "WAITING", "CLOSES", "MEETINGS", "STREAMS", "LEARNED"]
    assert list(recording) == ["OWED", "WAITING", "CLOSES", "STREAMS", "NOTES", "MINUTES", "OUTCOME", "LEARNED"]
    assert all(agent[label] == recording[label] for label in agent.keys() & recording.keys())
    found = agent | recording
    assert sorted(found, key=list(BLOCK).index) == list(BLOCK) == list(sync_block.FIELDS)
    for label, fields in BLOCK.items():
        assert found[label] == fields + GAINED.get(label, []), label
        # RUN's fewest is its `none` form; NOTES' M- ref comes only when its content names one.
        least = {"RUN": 3, "NOTES": 4}.get(label, len(fields))
        assert sync_block.FIELDS[label] == (least, len(found[label])), label


def test_the_prompts_placeholders_are_the_ones_docket_fills_and_the_skill_launches_by_path() -> None:
    texts = sync_texts()
    assert set(re.findall(r"\{([A-Z_]+)\}", texts["agent.md"])) == {
        "SOURCE", "VAULT", "TODAY", "ZONE", "OWED_SINCE", "CONTEXT", "BLOCK", "OWNER",
    }  # fmt: skip
    assert set(re.findall(r"\{([A-Z_]+)\}", texts["recording.md"])) == {
        "SOURCE", "VAULT", "TODAY", "ZONE", "OWED_SINCE", "CONTEXT", "BLOCK", "RECORDING", "TITLE", "START", "DAY",
        "FILES", "MEETINGS", "LAYOUT", "SYNC_AT", "OWNER",
    }  # fmt: skip
    # The skill writes no prompt out: each Agent call's prompt is the one line naming its file.
    skill = " ".join(texts["SKILL.md"].split())
    assert skill.count("`Read and follow <the prompt's path>`") == 2
    # Every agent starts fresh: no fork carries this session's context into it (#272), and every agent, a recording's
    # too, keeps the session's model (DR-0771).
    assert "`subagent_type: fork`" not in skill and skill.count("`subagent_type: general-purpose`") == 2
    assert skill.count("`subagent_type: general-purpose` and no `model`") == 2
    assert "`model:" not in skill and "sonnet" not in skill.lower()
    assert "This prompt and the files it names are all you need" in " ".join(texts["agent.md"].split())
    assert "{SOURCE}" not in skill and "fork.md" not in skill
    # The agent writes its block to its file and answers with the RUN line alone.
    agent = " ".join(texts["agent.md"].split())
    assert "Write the block to {BLOCK} with the Write tool" in agent
    assert "answer with the RUN line alone" in agent


def test_each_recording_is_fetched_once_and_read_once_by_an_agent_of_its_own() -> None:
    """The source's agent classifies from its fetch and writes no line from a recording; each recording's agent, on
    the session's model, reads its files once, gives an outcome's who-does-what on the owner its own line, and puts its
    note (#267, DR-0771)."""
    texts = {name: " ".join(text.split()) for name, text in sync_texts().items()}
    agent, recording, skill = texts["agent.md"], texts["recording.md"], texts["SKILL.md"]
    assert "Judge each record your run fetched from what it fetched, already in your context" in agent
    assert "`docket staging show <rid> --vault {VAULT}` only for a record your run didn't fetch" in agent
    assert "A recording source writes no line from a recording; its recordings' agents do." in agent
    assert "never read its transcript again with `read`" in recording
    assert (
        "A who-does-what in OUTCOME that falls on the owner, or that the owner waits on, also gets its own OWED or "
        "WAITING line" in recording
    )
    assert "By #211's method" in recording and "add `-2` to it, in the block's NOTES line too" in recording
    launch = skill.split("## The recordings", 1)[1].split("## The continuing-work rule", 1)[0]
    assert "`subagent_type: general-purpose` and no `model`, `description` `recording <key>`" in launch
    assert "Every agent runs on this session's model, the recording agents too, so no `model` is ever set" in launch
    assert "a legacy note covers it" in launch
    assert "The reconcile waits for every one" in launch
    assert "its route lines go into `sync.md` too, under a part `Route`" in skill
    plaud = " ".join(
        (PLUGIN.parent / "examples" / "sources" / "plaud" / "SKILL.md").read_text(encoding="utf-8").split()
    )
    assert "one message holds the next page of each, at most four at once" in plaud
    assert "under `Route`, which the copy leaves alone" in plaud


def test_every_agent_goes_through_the_open_items_its_people_are_in_for_evidence_before_its_block() -> None:
    """The fourth rehearsal's agents wrote no CLOSES line where the old sync closed five items on evidence: each agent,
    a source's and a recording's, now goes through the open items whose people are in what it read, and only those,
    for evidence they were done, before it writes its block; the reconcile says a sync with no close (#286)."""
    texts = {name: " ".join(text.split()) for name, text in sync_texts().items()}
    for name, closes, block, people in (
        ("agent.md", "## 6. Closes", "## 8. Write the block", "whose people are in what your run read, and only those"),
        ("recording.md", "## 3. Closes", "## 4. Write the block", "whose people are in the call, and only those"),
    ):
        prompt = texts[name]
        assert prompt.index("## 2.") < prompt.index(closes) < prompt.index(block), name
        step = prompt.split(closes, 1)[1].split("## ", 1)[0]
        assert f"Before the block, go through the open and snoozed items in {{CONTEXT}} {people}" in step, name
        assert "for an item on a meeting (`@M-0003`)" in step and "is left alone" in step, name
        assert "a message sent, a call held, a file or a link shared, an answer given" in step, name
        assert "plan or a promise to do it isn't evidence" in step, name
        assert "Evidence found: a CLOSES line, `done`, or `moot`" in step and "None found: nothing" in step, name
        assert "| CLOSES | the open or snoozed items the closes pass, step" in prompt, name
    assert "This call is itself the evidence for an item asking for it to be held" in texts["recording.md"]
    skill = texts["SKILL.md"]
    assert skill.count("for evidence they were done") == 2
    assert "`no source reported a close` when no block holds a CLOSES line" in skill
    assert "or that no source reported a close" in skill


def test_agents_md_names_the_sync_and_tasks_skills_and_their_doors() -> None:
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    for words in ("`sync` and `tasks` (DR-0685)", "(DR-0722)", "(DR-0748)", "`sync.md` (DR-0750)"):
        assert words in agents, words


def test_the_two_name_doors_hold_the_skill_s_name_and_sync_md_beside_skill_md() -> None:
    """Users invoke /docket:sync by name (DR-0685), and an owner's lore lives in sync.md beside the source's SKILL.md,
    where the agent looks for it (DR-0750)."""
    assert frontmatter("sync")["name"] == "sync"
    assert "names the source's `SKILL.md`: read it, then `sync.md` beside it" in sync_texts()["agent.md"]


def test_the_readme_allows_the_sync_folder_by_read_and_edit_rules() -> None:
    """Claude Code checks a file's write against Edit rules and never Write ones, so a Write rule would leave every
    block's write asking (DR-0746): a Read and an Edit rule per system, each on the folder of sync folders."""
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## The sync") : readme.index("## The tasks")]
    assert "Write(" not in section
    for tool in ("Read", "Edit"):
        found = re.findall(rf"`{tool}\((~/[^)]*)\)`", section)
        assert len(found) == 3 and all(path.endswith("/docket/sync/**") for path in found), tool


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


def test_the_worked_draft_is_refused_unsettled_and_runs_clean_settled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The skill's draft as the reconcile writes it, its unsure line refused; settled as the skill says, its line 7
    taking its alternative by `docket sync settle` and the prep point added at the `# prep` line it names, it dry-runs
    clean (DR-0760)."""
    worked = sync_texts()["SKILL.md"].split("### A worked draft", 1)[1]
    draft = worked.split("```jsonl\n", 1)[1].split("```", 1)[0].splitlines()
    prep = worked.split("```json\n", 1)[1].split("```", 1)[0].strip()
    assert "`docket sync settle <sync> 7=alt --rev 1 --vault <vault>`" in " ".join(worked.split())
    assert '"unsure": ' in draft[6] and draft[7].startswith("# {")
    assert draft[0].endswith(f": {sum(not line.startswith('#') for line in draft)} ops, 1 unsure, rev 1")
    vault = pilot_vault(tmp_path / "v")
    made = datetime(2026, 10, 20, 7, 15, tzinfo=UTC)
    sync = sync_folder.new_id(made)
    folder = sync_folder.make(sync)
    info = sync_folder.Info(sync=sync, vault=str(vault.root), made=made, today=made.date(), zone="Europe/Budapest",
                            owed_since="the run's window", sources=[], docket="test")  # fmt: skip
    (folder / sync_folder.INFO).write_text(info.model_dump_json(), encoding="utf-8")
    batch = folder / "draft.jsonl"
    command = ["ledger", "batch", str(batch), "--by", "sync", "--dry-run", "--vault", str(vault.root)]
    batch.write_text("\n".join(draft) + "\n", encoding="utf-8")
    capsys.readouterr()
    assert main(command) == 1
    refused = "2. add: refused, add takes only fields, note, as, force, by, not unsure"
    assert refused in capsys.readouterr().out.splitlines()
    assert main(["sync", "settle", sync, "7=alt", "--rev", "1", "--vault", str(vault.root)]) == 0
    said = capsys.readouterr().out.splitlines()
    settled = batch.read_text(encoding="utf-8").splitlines()
    assert settled == [
        draft[0].replace(", 1 unsure, rev 1", ", 0 unsure, rev 2"),
        *draft[1:4],
        draft[7][2:],
        *draft[8:],
    ]
    at = int(said[-1].removeprefix("prep: line "))
    assert settled[at - 1] == "# prep: the next five working days: M-0002"
    batch.write_text("\n".join([*settled[:at], prep, *settled[at:]]) + "\n", encoding="utf-8")
    code = main(command)
    out = capsys.readouterr().out.splitlines()
    assert code == 0, out
    assert out[-1].startswith("dry run: ") and out[-1].endswith(" op(s) would be written; nothing written"), out
    assert not any(": refused, " in line for line in out), out


# -- the tasks skill (DR-0685, DR-0691) -----------------------------------------------------------------------------


def tasks_text() -> str:
    return (PLUGIN / "skills" / "tasks" / "SKILL.md").read_text(encoding="utf-8")


def test_tasks_pre_approves_only_reads() -> None:
    """Each item's batch and every draft ask (DR-0605); opening the page and the ledger's reads don't."""
    tools = golden_tools()
    allowed = frontmatter("tasks")["allowed-tools"]
    prefix = "mcp__plugin_docket_docket__"
    named = {name.removeprefix(prefix) for name in re.findall(rf"{prefix}\w+", allowed)}
    assert named == {"ledger", "search", "read", "verify"}
    assert all(tools[name]["annotations"]["read_only_hint"] for name in named)
    commands = re.findall(r"Bash\(([^)]*)\)", allowed)
    assert commands == [
        "docket open *", "MSYS_NO_PATHCONV=1 docket open *", "docket ledger day", "docket ledger day *",
        "docket ledger queue", "docket ledger queue *", "docket ledger show *", "docket ledger suggest *",
    ]  # fmt: skip
    assert re.sub(rf"{prefix}\w+|Bash\([^)]*\)", "", allowed).split() == []


def test_tasks_names_commands_options_views_verdicts_ops_and_parameters_docket_has() -> None:
    text = tasks_text()
    tools = golden_tools()
    seen: set[tuple[str, ...]] = set()
    for path, flags in used_commands(text):
        command: Any = typer.main.get_command(app)
        for word in path:
            command = command.commands[word]
        assert not hasattr(command, "commands"), path
        assert flags <= options(*path), (path, flags)
        seen.add(tuple(path))
    assert seen == {("open",), ("ledger", "day"), ("ledger", "queue"), ("ledger", "show"), ("ledger", "suggest"),
                    ("ledger", "batch")}  # fmt: skip
    for view in re.findall(r"`ledger` view `([a-z]+)`", text):
        assert view in VIEWS, view
    assert set(re.findall(r"view `([a-z]+)`", text)) <= {*VIEWS, *recall.VIEWS}
    # The verdicts are the ledger's four in its order, and every one but me is worked.
    for head, verdicts in (
        ("| Verdict | What it means | What you do |", VERDICTS),
        ("| Verdict | Do |", tuple(v for v in VERDICTS if v != "me")),
    ):
        table = text[text.index(head) :].split("\n\n", 1)[0]
        assert tuple(re.findall(r"^\| `([a-z]+)` \|", table, re.MULTILINE)) == verdicts, head
    table = text[text.index("| Op | For |") :].split("\n\n", 1)[0]
    for row in table.splitlines()[2:]:
        assert set(re.findall(r"`([a-z_]+)`", row.split("|")[1])) <= set(BATCH), row
    assert set(re.findall(r'"op": "([a-z_]+)"', text)) <= set(BATCH)
    for tool, params in re.findall(r"\b(ledger|ledger_batch|read|search|verify)\(([^)]*)\)", text):
        given = {p.split("=")[0].strip() for p in params.split(",") if "=" in p}
        assert given <= set(tools[tool]["input_schema"]["properties"]), (tool, given)
    for tool, param in (("ledger", "ref"), ("ledger_batch", "by")):
        assert f"`{param}`" in text and param in tools[tool]["input_schema"]["properties"], (tool, param)
    # An item's locators in one read, its source first (DR-0725).
    assert 'read(locator="<its source>", more=["<a log\'s locator>"])' in text


def test_tasks_opens_a_page_of_the_web_app_and_its_name_door_holds() -> None:
    """Users invoke /docket:tasks by name (DR-0685). It opens the Queue page, and Git Bash's form opens the same
    page."""
    assert frontmatter("tasks")["name"] == "tasks"
    text = tasks_text()
    paths = re.findall(r"`(?:MSYS_NO_PATHCONV=1 )?docket open (\S+)`", text)
    assert len(paths) >= 2 and "`MSYS_NO_PATHCONV=1 docket open " in text
    (path,) = set(paths)
    assert path == "/queue"
    assert sessions.safe_next(path) == path
    assert path in {page.path for group in NAV for page in group.pages}


def queue_vault(tmp_path: Path) -> Vault:
    """The worked batches' ledger: the pilot's, with Ben's ask triaged claude, Cleo's chase, a prep task on the next
    weekly and a point the sync proposed there. Invented people only."""
    vault = pilot_vault(tmp_path)
    prep = {"kind": "task", "title": "Prep: the pilot weekly", "who": "Ben Sample", "stream": "pilot",
            "source": "calendar:AAMkADweekly-1026", "meeting": ulid(5), "due": "2026-10-23"}  # fmt: skip
    made = [
        op("triage", 7, 2, verdict="claude", by="user", via="page"),
        op("triage", 8, 3, verdict="chase", by="user", via="page"),
        op("add", 9, number=3, fields=prep),
        op("point", 10, 5, fields={"text": "Ben: is the room booked for the 28th?", "proposed": True}),
    ]
    vault.commit(write_ops(vault.root, made), "The pilot's queue")
    return vault


def test_the_tasks_worked_batches_run_clean_through_a_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocks = re.findall(r"```json\n(.*?)```", tasks_text(), re.DOTALL)
    assert len(blocks) == 3
    vault = queue_vault(tmp_path / "v")
    for k, example in enumerate(blocks):
        batch = tmp_path / f"tasks-{k}.json"
        batch.write_text(example, encoding="utf-8")
        capsys.readouterr()
        code = main(["ledger", "batch", str(batch), "--dry-run", "--vault", str(vault.root)])
        out = capsys.readouterr().out.splitlines()
        assert code == 0, out
        assert out[-1].startswith("dry run: ") and out[-1].endswith(" op(s) would be written; nothing written"), out
        assert not any(": refused, " in line for line in out), out
    # A chase's log says where its draft is, as a claude draft's material does.
    assert '"op": "material"' not in blocks[1] and "chase drafted in " in blocks[1]


def test_a_second_draft_in_the_same_thread_leaves_out_the_material_the_item_has(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The skill's rule for a material the item has already: written again, it refuses the whole batch."""
    assert "one the item has already is left out, so a second draft in the same thread isn't refused" in tasks_text()
    first = re.findall(r"```json\n(.*?)```", tasks_text(), re.DOTALL)[0]
    vault = queue_vault(tmp_path / "v")
    batch = tmp_path / "draft.json"
    batch.write_text(first, encoding="utf-8")
    assert main(["ledger", "batch", str(batch), "--vault", str(vault.root)]) == 0
    capsys.readouterr()
    assert main(["ledger", "batch", str(batch), "--dry-run", "--vault", str(vault.root)]) == 1
    assert "it has that material already" in capsys.readouterr().out
    again = [change for change in json.loads(first) if change["op"] != "material"]
    batch.write_text(json.dumps(again), encoding="utf-8")
    assert main(["ledger", "batch", str(batch), "--dry-run", "--vault", str(vault.root)]) == 0
