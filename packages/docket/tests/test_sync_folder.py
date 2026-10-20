"""The sync folder (DR-0720, DR-0721): `docket sync start` writes `context.md` and an agent prompt per live source into
Docket's state folder, refusing before any file; `docket sync notes` a prompt per note the blocks' NOTES lines name;
old folders go at a start. Invented people and data only: the pilot's ledger, two recorded weeklies and a third with
its note."""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from docket_testkit import copy_vault, empty_vault, git, make_config, make_record, template_with
from ledger_kit import meeting, op, ulid, write_ops
from recording_kit import call, doc
from sync_kit import Session, app_of, service_client, sync_template, sync_vault

from docket import recall
from docket.cli import main
from docket.errors import DocketError
from docket.index import build as index_build
from docket.sync import block, folder, start
from docket.vault import cursors
from docket.vault.config import SourceConfig
from docket.vault.vault import Vault

# Tuesday 20 October 2026, 09:15 in Budapest: the prep week runs to Tuesday the 27th.
NOW = datetime(2026, 10, 20, 7, 15, 30, 123456, tzinfo=UTC)
POINT = "01M4QJ9FZ000X93GNMW1YNY5N8"
WEEKLY = "plaud:rec-0101"
NOTED = "plaud:rec-0102"
OLD = "plaud:rec-0090"
SAID = [("Ben Sample", "The pilot starts on the 28th."), ("Dan Example", "I book the room.")]
PLACEHOLDER = re.compile(r"\{[A-Z_]+\}")


def build_pilot(root: Path) -> Vault:
    """The pilot's vault: chat and the recorder live, mail private and calls with no tools; Ben's, Cleo's, Dan's and
    Eve's asks and one done; three weeklies and a cancelled vendor call; three recordings, one with its note."""
    config = make_config()
    config.sources = {
        "slack": SourceConfig(tools=["read_channel"]),
        "plaud": SourceConfig(sensitivity="mixed", tools=["list_files"]),
        "mail": SourceConfig(sensitivity="private", tools=["read_mail"]),
        "calls": SourceConfig(sensitivity="mixed"),
    }
    vault = Vault.init(root / "vault", config, "test-box")
    weekly = {"subject": "Pilot weekly", "organiser": "Ben Sample"}
    made = [
        op("stream", 1, fields={"name": "pilot", "status": "active", "where": "The pilot starts on the 28th",
                                "next": "Book the room", "deadline": "2026-10-28"}),
        op("add", 2, number=1, fields={"kind": "reply", "title": "Send Ben the pilot dates", "who": "Ben Sample",
                                       "stream": "pilot", "source": "slack:C0PILOT/1791100000.000100"}),
        op("add", 3, number=2, fields={"kind": "waiting", "title": "Cleo's quote | the sensors", "who": "Cleo Test",
                                       "stream": "pilot", "source": "outlook:AAMkADquote1"}),
        meeting(4, "calendar:weekly-1019", number=1, start="2026-10-19T10:00+02:00", end="2026-10-19T10:30+02:00",
                **weekly),
        meeting(5, "calendar:weekly-1026", number=2, start="2026-10-26T10:00+01:00", end="2026-10-26T10:30+01:00",
                **weekly),
        op("point", 6, 4, id=POINT, fields={"text": "Ben: the pilot's dates"}),
        op("add", 7, number=3, fields={"kind": "task", "title": "Book the room for the pilot", "who": "Dan Example",
                                       "stream": "pilot", "source": "slack:C0PILOT/1791100800.000300",
                                       "meeting": ulid(5)}),
        op("add", 8, number=4, fields={"kind": "task", "title": "Draft the pilot plan", "who": "Eve Example",
                                       "stream": "pilot", "source": "slack:C0PILOT/1791100900.000100"}),
        op("snooze", 9, 8, until=date(2026, 10, 25)),
        op("add", 10, number=5, fields={"kind": "reply", "title": "An old ask", "who": "Finn Example",
                                        "stream": "pilot", "source": "slack:C0PILOT/1791000000.000100"}),
        op("done", 11, 10, note="answered"),
        meeting(12, "calendar:weekly-1116", number=3, start="2026-11-16T10:00+01:00", end="2026-11-16T10:30+01:00",
                **weekly),
        meeting(13, "calendar:vendor-1022", number=4, start="2026-10-22T23:30+02:00", end="2026-10-23T00:30+02:00",
                subject="Vendor call", stream="pilot", response="cancelled"),
    ]  # fmt: skip
    vault.commit(write_ops(vault.root, made), "The pilot's ledger")
    vault.write_run(
        "plaud",
        [
            *call(WEEKLY, "Pilot weekly", SAID, 19, "transaction_polish"),
            *call(NOTED, "Pilot kickoff", SAID, 18, "transaction_polish"),
            *call(OLD, "Pilot idea", SAID, 2, "transaction_polish"),
        ],
    )
    covered = f"{NOTED}/transaction_polish"
    recall.doc_put(vault, doc("recording-note:2026-10-18-pilot-kickoff", [covered], ["- Ben opens it. @00:00"]))
    return vault


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_with(tmp_path_factory, build_pilot)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    v = copy_vault(template, tmp_path)
    index_build.rebuild(v)
    # Slack synced yesterday morning with a day's lookback; the recorder never, so its window is the last 7 days.
    cursors.save(v, "slack", cursors.Cursor(mark=datetime(2026, 10, 19, 6, 2, tzinfo=UTC), lookback_days=1))
    return v


def tree(vault: Vault) -> tuple[str, str]:
    return git(vault.root, "rev-parse", "HEAD"), git(vault.root, "status", "--porcelain", "--ignored")


# -- start -------------------------------------------------------------------------------------------------------------


def test_start_writes_the_context_and_a_prompt_per_live_source_and_nothing_else(vault: Vault) -> None:
    before = tree(vault)
    started = start.start(vault, [], NOW)
    assert tree(vault) == before
    path = started.folder
    assert path.parent == folder.root() and folder.ID.match(path.name)
    assert sorted(p.relative_to(path).as_posix() for p in path.rglob("*")) == [
        "agents", "agents/plaud.md", "agents/slack.md", "blocks", "context.md", "notes", "sync.json",
    ]  # fmt: skip
    info = json.loads((path / "sync.json").read_text(encoding="utf-8"))
    assert info["vault"] == str(vault.root) and info["today"] == "2026-10-20" and info["zone"] == "Europe/Budapest"
    assert info["owed_since"] == "the run's window" and info["made"].startswith("2026-10-20T07:15:30")
    assert [s["source"] for s in info["sources"]] == ["plaud", "slack"]
    assert start.lines(started) == [
        f"sync folder: {path}",
        f"agent plaud: {path / 'agents' / 'plaud.md'}",
        f"agent slack: {path / 'agents' / 'slack.md'}",
    ]
    for source in ("plaud", "slack"):
        prompt = (path / "agents" / f"{source}.md").read_text(encoding="utf-8")
        assert not PLACEHOLDER.search(prompt), source
        assert prompt.startswith(f"You are the {source} agent of a Docket sync.")
        assert f"| the ledger | {path / 'context.md'} |" in prompt
        assert f"| your block | {path / 'blocks' / f'{source}.txt'} |" in prompt
        assert f'`run_open(source="{source}")`' in prompt and f"--vault {vault.root}" in prompt
        assert "| owed since | the run's window |" in prompt and "local in Europe/Budapest" in prompt


def test_the_context_lists_the_items_meetings_streams_windows_and_the_recordings_with_no_note(vault: Vault) -> None:
    started = start.start(vault, [], NOW)
    context = (started.folder / "context.md").read_text(encoding="utf-8").splitlines()
    assert context == [
        f"sync {started.info.sync}  Tue 2026-10-20 09:15 Europe/Budapest  owed since: the run's window",
        "sources",
        "plaud | from 2026-10-13 09:15 | agents/plaud.md | blocks/plaud.txt",
        "slack | from 2026-10-18 08:02 | agents/slack.md | blocks/slack.txt",
        "recordings with no note (1)",
        f"{WEEKLY} | 10-19 10:30 | Pilot weekly",
        "open items (3)",
        "A-0001 | reply | Ben Sample | Send Ben the pilot dates | slack:C0PILOT/1791100000.000100",
        "A-0002 | waiting | Cleo Test | Cleo's quote / the sensors | outlook:AAMkADquote1",
        "A-0003 | task | Dan Example | Book the room for the pilot | slack:C0PILOT/1791100800.000300 @M-0002",
        "snoozed (1)",
        "A-0004 | task | Eve Example | Draft the pilot plan | slack:C0PILOT/1791100900.000100 | until 10-25",
        "meetings 10-13 to 10-27, or with an open point (3)",
        "M-0001 | 10-19 10:00-10:30 | Pilot weekly | Ben Sample",
        f"  #1 open | Ben: the pilot's dates | {POINT}",
        "M-0004 | 10-22 23:30-10-23 00:30 | Vendor call | - | stream pilot | cancelled",
        "M-0002 | 10-26 10:00-10:30 | Pilot weekly | Ben Sample",
        "streams (1)",
        "pilot | active | The pilot starts on the 28th | Book the room | deadline 10-28",
    ]


def test_each_window_is_the_one_run_open_opens_with_the_same_clock(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path
) -> None:
    vault = sync_vault(sync_template(tmp_path_factory), tmp_path)
    cursors.save(vault, "memo", cursors.Cursor(mark=NOW - timedelta(hours=5), lookback_days=2))
    with service_client(vault) as client:
        app_of(client).service.runs.clock = lambda: NOW
        opened = Session(client).open("memo")
    (source,) = start.start(vault, [], NOW).info.sources
    assert source.since == datetime.fromisoformat(opened["since"])
    assert source.until == datetime.fromisoformat(opened["until"])


def test_only_the_sources_named_get_an_agent_and_a_name_given_twice_once(vault: Vault) -> None:
    started = start.start(vault, ["slack", "slack"], NOW)
    assert [s.source for s in started.info.sources] == ["slack"]
    context = (started.folder / "context.md").read_text(encoding="utf-8")
    # The meetings start on the day slack's window does.
    assert "meetings 10-18 to 10-27, or with an open point (3)" in context
    assert "recordings with no note (0)" in context


@pytest.mark.parametrize(
    ("named", "said"),
    [
        (["teams"], "not a live source: teams; the live ones: plaud, slack"),
        (["mail", "calls"], "not a live source: mail, calls; the live ones: plaud, slack"),
    ],
)
def test_a_name_that_isn_t_a_live_source_is_refused_before_any_file(vault: Vault, named: list[str], said: str) -> None:
    with pytest.raises(DocketError, match=re.escape(said)):
        start.start(vault, named, NOW)
    assert not folder.root().exists()


def test_a_cursor_file_that_doesn_t_read_is_said_and_nothing_is_written(vault: Vault) -> None:
    (vault.root / "state" / "cursors-test-box.yaml").write_text("schema_version: [\n", encoding="utf-8")
    with pytest.raises(DocketError, match=r"^no sync: state/cursors-test-box.yaml: line \d+ isn't valid YAML"):
        start.start(vault, [], NOW)
    assert not folder.root().exists()


def test_no_live_source_is_refused(tmp_path: Path) -> None:
    vault = empty_vault(tmp_path, make_config())
    with pytest.raises(DocketError, match="no source syncs live"):
        start.start(vault, [], NOW)


def test_owed_since_goes_40_working_days_back_while_the_ledger_holds_no_item(tmp_path: Path) -> None:
    config = make_config()
    config.sources = {"chat": SourceConfig(tools=["read_chat"])}
    vault = empty_vault(tmp_path, config)
    started = start.start(vault, [], NOW)
    # Eight weeks back is 40 working days: Tuesday 25 August.
    assert started.info.owed_since == "2026-08-25"
    prompt = (started.folder / "agents" / "chat.md").read_text(encoding="utf-8")
    assert "| owed since | 2026-08-25 |" in prompt
    context = (started.folder / "context.md").read_text(encoding="utf-8").splitlines()
    assert context[0].endswith("owed since: 2026-08-25")
    # No index yet: the agent checks each recording itself.
    assert context[3].startswith("recordings with no note: the index can't say (there is no index yet")
    assert context[4:] == ["open items (0)", "snoozed (0)", "meetings 10-13 to 10-27, or with an open point (0)",
                           "streams (0)"]  # fmt: skip


def test_a_start_clears_sync_folders_over_7_days_old_and_only_those(vault: Vault) -> None:
    top = folder.root()
    old = top / "sync-20261012T071500-0a0b0c0d"
    young = top / "sync-20261014T071500-0a0b0c0d"
    other = top / "notes-of-mine"
    not_a_time = top / "sync-20261399T071500-0a0b0c0d"
    for path in (old, young, other, not_a_time):
        (path / "blocks").mkdir(parents=True)
    (top / "sync-20261001T000000-0a0b0c0d").write_text("a file, not a folder", encoding="utf-8")
    started = start.start(vault, [], NOW)
    assert started.cleared == 1 and not old.exists()
    assert young.is_dir() and other.is_dir() and not_a_time.is_dir()
    assert start.lines(started)[-1] == "cleared 1 sync folder(s) over 7 days old"


def test_a_folder_a_file_in_use_keeps_stays_for_the_next_try(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    old = folder.root() / "sync-20261001T000000-0a0b0c0d"
    old.mkdir(parents=True)

    def held(path: Path) -> None:
        raise PermissionError(f"{path} is open in another program")

    monkeypatch.setattr(shutil, "rmtree", held)
    assert folder.clear(NOW) == 0 and old.is_dir()


def test_nothing_is_written_through_a_linked_folder_of_sync_folders(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(folder, "_linked", lambda path: path == folder.root())
    with pytest.raises(DocketError, match="is a link; Docket writes sync folders only into a plain folder"):
        start.start(vault, [], NOW)
    assert folder.clear(NOW, every=True) == 0


# -- finding a sync ----------------------------------------------------------------------------------------------------


def test_a_sync_is_found_by_its_id_or_its_folder_and_refused_otherwise(vault: Vault, tmp_path: Path) -> None:
    started = start.start(vault, [], NOW)
    sync = started.info.sync
    assert folder.find(sync, vault.root)[1] == started.info
    assert folder.find(str(started.folder), vault.root)[0] == started.folder
    refused = {
        "a-sync": "isn't a sync: give its id",
        str(tmp_path / sync): "isn't where this machine keeps its sync folders",
        "sync-20261020T000000-00000000": "no sync sync-20261020T000000-00000000 on this machine",
    }
    for given, said in refused.items():
        with pytest.raises(DocketError, match=re.escape(said)):
            folder.find(given, vault.root)
    with pytest.raises(DocketError, match=re.escape(f"{sync} is a sync of {vault.root}, not of {tmp_path}")):
        folder.find(sync, tmp_path)
    (started.folder / "sync.json").write_text("{", encoding="utf-8")
    with pytest.raises(DocketError, match=re.escape(f"{sync}'s sync.json doesn't read; start another sync")):
        folder.find(sync, vault.root)


# -- notes -------------------------------------------------------------------------------------------------------------


def blocks(started: start.Started, **texts: str) -> None:
    for source, text in texts.items():
        (started.folder / "blocks" / f"{source}.txt").write_text(text, encoding="utf-8")


def test_notes_writes_a_prompt_per_doc_id_and_one_over_two_recordings(vault: Vault) -> None:
    started = start.start(vault, [], NOW)
    blocks(
        started,
        plaud=(
            "RUN:      plaud | run-1 | captures 2 | ready 1, pending 0 | skipped 0 | mark moved yes | -\n"
            "NOTES:    recording-note:2026-10-19-pilot-weekly | plaud:rec-0101 | 10:30 | 10:50 | M-0001\n"
            "NOTES:    recording-note:2026-10-19-pilot-weekly | plaud:rec-0090 | 10:52 | 11:10 | -\n"
            "NOTES:    recording-note:2026-10-18-pilot-kickoff | plaud:rec-0102 | 10:30 | 10:50\n"
        ),
        slack="RUN:      slack | run-2 | captures 3 | ready 3, pending 0 | skipped 0 | mark moved yes | -\n",
    )
    noted = start.notes(vault, started.info.sync)
    assert [n.doc for n in noted.notes] == ["recording-note:2026-10-19-pilot-weekly",
                                            "recording-note:2026-10-18-pilot-kickoff"]  # fmt: skip
    assert noted.said == []
    weekly, kickoff = (n.prompt.read_text(encoding="utf-8") for n in noted.notes)
    assert noted.notes[0].prompt == started.folder / "notes" / "2026-10-19-pilot-weekly.md"
    assert not PLACEHOLDER.search(weekly) and not PLACEHOLDER.search(kickoff)
    assert "| its recordings | plaud:rec-0101, 10:30 to 10:50; plaud:rec-0090, 10:52 to 11:10 |" in weekly
    assert "| its meeting | M-0001, which its content names |" in weekly
    assert "| its meeting | none named: the one 10 minutes either side of its start" in kickoff
    assert "`by: claude, sync 2026-10-20T09:15`" in weekly and f"--vault {vault.root}" in weekly
    # A vault of its own repo has no project, so no sync.md: Docket's layout.
    assert f"| the layout | {start.LAYOUT} |" in weekly
    assert "One line and nothing else: `put recording-note:2026-10-19-pilot-weekly rev <n>`" in weekly
    assert start.note_lines(noted) == [f"note {n.doc}: {n.prompt}" for n in noted.notes]


def test_notes_says_each_line_that_gets_no_prompt_and_why(vault: Vault) -> None:
    started = start.start(vault, [], NOW)
    blocks(
        started,
        plaud=(
            "NOTES:    recording-note:2026-10-19-pilot-weekly | plaud:rec-9999 | 10:30 | 10:50\n"
            "NOTES:    minutes:2026-10-19-pilot-weekly | plaud:rec-0101 | 10:30 | 10:50\n"
            "NOTES:    recording-note:Pilot Weekly | plaud:rec-0101 | 10:30 | 10:50\n"
            "NOTES:    recording-note:2026-10-19-pilot | plaud:rec-0101\n"
            "Some words the agent wrote.\n"
        ),
    )
    noted = start.notes(vault, started.info.sync)
    assert noted.notes == []
    assert noted.said == [
        "blocks/plaud.txt:2: minutes:2026-10-19-pilot-weekly isn't a recording-note",
        "blocks/plaud.txt:3: the slug 'Pilot Weekly' is lowercase letters, digits and inner hyphens",
        "blocks/plaud.txt:4: NOTES takes 4 to 5 fields, not 2",
        "blocks/slack.txt isn't there: no block from slack",
        "blocks/plaud.txt:1: plaud:rec-9999 doesn't open, so it gets no note",
    ]
    assert start.note_lines(noted) == noted.said


def test_notes_with_no_notes_line_says_so(vault: Vault) -> None:
    started = start.start(vault, ["slack"], NOW)
    blocks(started, slack="RUN:      slack | none | the service didn't answer\n")
    noted = start.notes(vault, started.info.sync)
    assert start.note_lines(noted) == ["no note to write: the blocks name none"]


def test_a_note_s_layout_is_the_one_its_source_s_sync_md_gives(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path
) -> None:
    vault = sync_vault(sync_template(tmp_path_factory), tmp_path)
    vault.write_run("memo", [make_record(rid="memo:m-1", source="memo", kind="memo", container="memo:m-1")])
    started = start.start(vault, [], NOW)
    blocks(started, memo="NOTES:    recording-note:2026-10-19-a-memo | memo:m-1 | 10:30 | 10:50\n")
    sync_md = vault.root.parent / ".claude" / "skills" / "sources" / "memo" / "sync.md"
    sync_md.write_text("# memo\n\nA note's layout: the decisions first.\n", encoding="utf-8")
    (note,) = start.notes(vault, started.info.sync).notes
    assert f"| the layout | the one {sync_md.resolve()} gives for a recording note, else " in note.prompt.read_text(
        encoding="utf-8"
    )


# -- the block ---------------------------------------------------------------------------------------------------------


def test_the_block_reads_each_label_s_fields_and_says_a_shape_it_doesn_t_take() -> None:
    lines = block.parse(
        "```\n"
        "RUN:      chat | none | no skill\n"
        "OWED:     Ben Sample | slack:C0PILOT/1 | 2026-10-19 11:05 | the dates | not sent | today\n"
        "CLOSES:   A-0001 | done | slack:C0PILOT/2 | sent | twice\n"
        "WHAT:     not a label\n"
        "LEARNED:  sync.md | the channel moved\n"
        "```\n"
    )
    assert [(ln.label, ln.n, ln.problem) for ln in lines] == [
        ("RUN", 2, None),
        ("OWED", 3, None),
        ("CLOSES", 4, "CLOSES takes 4 fields, not 5"),
        ("LEARNED", 6, None),
    ]
    assert lines[1].fields == ["Ben Sample", "slack:C0PILOT/1", "2026-10-19 11:05", "the dates", "not sent", "today"]


def test_a_prompt_is_filled_in_one_pass_so_a_path_holding_a_placeholder_s_name_stays() -> None:
    filled = start.fill("vault {VAULT}, block {BLOCK}, {OTHER}", {"VAULT": "/v/{BLOCK}", "BLOCK": "/b.txt"})
    assert filled == "vault /v/{BLOCK}, block /b.txt, {OTHER}"


# -- the commands ------------------------------------------------------------------------------------------------------


def test_the_commands_print_the_folder_the_agents_and_the_notes(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["sync", "start", "slack", "--vault", str(vault.root)]) == 0
    first = capsys.readouterr().out.splitlines()
    assert first[0].startswith("sync folder: ") and first[1].startswith("agent slack: ") and len(first) == 2
    # The group's --vault names the vault for its subcommand.
    assert main(["sync", "--vault", str(vault.root), "start", "plaud"]) == 0
    second = capsys.readouterr().out.splitlines()
    assert second[1].startswith("agent plaud: ")
    sync = Path(second[0].removeprefix("sync folder: "))
    (sync / "blocks" / "plaud.txt").write_text(
        "NOTES:    recording-note:2026-10-19-pilot-weekly | plaud:rec-0101 | 10:30 | 10:50\n", encoding="utf-8"
    )
    assert main(["sync", "--vault", str(vault.root), "notes", str(sync)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"note recording-note:2026-10-19-pilot-weekly: {sync / 'notes' / '2026-10-19-pilot-weekly.md'}"
    ]
    assert main(["sync", "notes", "sync-1", "--vault", str(vault.root)]) == 2
    assert "isn't a sync" in capsys.readouterr().err
