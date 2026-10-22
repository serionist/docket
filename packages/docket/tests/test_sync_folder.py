"""The sync folder (DR-0746, DR-0772): `docket sync start` writes `context.md` and an agent prompt per live source into
Docket's state folder, refusing before any file; old folders go at a start. `test_sync_recordings.py` holds `docket
sync recordings`. Invented people and data only: the pilot's ledger, two recorded weeklies and a third with its
note."""

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
from docket.ledger import store, views
from docket.sync import block, folder, slices, start
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
        "agents", "agents/plaud.md", "agents/slack.md", "blocks", "context", "context.md", "context/plaud.md",
        "context/slack.md", "recordings", "sync.json",
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
        assert f"| the ledger | {path / 'context' / f'{source}.md'}: your slice of it |" in prompt
        assert f"| your block | {path / 'blocks' / f'{source}.txt'} |" in prompt
        assert f'`run_open(source="{source}")`' in prompt and f"--vault {vault.root}" in prompt
        assert "| owed since | the run's window |" in prompt and "local in Europe/Budapest" in prompt
        # A fresh agent has none of the session's context: the prompt names whose asks it finds (#272).
        assert "| the owner | Ada Example (ada@example.invalid): whose asks and waits you find |" in prompt


def test_the_context_lists_the_windows_items_meetings_and_streams(vault: Vault) -> None:
    started = start.start(vault, [], NOW)
    context = (started.folder / "context.md").read_text(encoding="utf-8").splitlines()
    assert context == [
        f"sync {started.info.sync}  Tue 2026-10-20 09:15 Europe/Budapest  owed since: the run's window",
        "sources",
        "plaud | from 2026-10-13 09:15 | agents/plaud.md | blocks/plaud.txt",
        "slack | from 2026-10-18 08:02 | agents/slack.md | blocks/slack.txt",
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
    assert context[3:] == ["open items (0)", "snoozed (0)", "meetings 10-13 to 10-27, or with an open point (0)",
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
    assert folder.clear_before(NOW) == 0


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


def test_a_block_field_whose_bar_moved_a_field_along_is_said() -> None:
    """An ask that ended or began with `|`: the line splits into a field starting with it, and its count still fits."""
    owed, waiting = block.parse(
        "OWED:     Ben Sample | slack:C0PILOT/1 | 2026-10-19 | pick A | | not yet | today\n"
        "WAITING:  Cleo Test | outlook:AAMkADquote1 | 2026-10-19 | the quote | pilot | - |\n"
    )
    assert owed.fields[4] == "| not yet"
    assert owed.problem == "OWED's field 5 starts or ends with |: a field holds no bar"
    assert waiting.problem == "WAITING's field 6 starts or ends with |: a field holds no bar"


def test_a_prompt_is_filled_in_one_pass_so_a_path_holding_a_placeholder_s_name_stays() -> None:
    filled = start.fill("vault {VAULT}, block {BLOCK}, {OTHER}", {"VAULT": "/v/{BLOCK}", "BLOCK": "/b.txt"})
    assert filled == "vault /v/{BLOCK}, block /b.txt, {OTHER}"


def test_a_short_form_field_holds_no_bar_inside_or_at_an_end() -> None:
    assert views.as_field("the quote | | the\n sensors") == "the quote / / the sensors"
    title = views.as_field("Send Ben the pilot dates |")
    assert title == "Send Ben the pilot dates /"
    fields = f"A-0001 | reply | Ben Sample | {title} | slack:C0PILOT/1".split(" | ")
    assert fields[3:] == ["Send Ben the pilot dates /", "slack:C0PILOT/1"]


# -- the commands ------------------------------------------------------------------------------------------------------


def test_the_command_prints_the_folder_and_the_agents(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The command reads the clock: held at the fixtures' day, as the cursors and the meetings are.
    monkeypatch.setattr(views, "now", lambda: NOW)
    assert main(["sync", "start", "slack", "--vault", str(vault.root)]) == 0
    first = capsys.readouterr().out.splitlines()
    assert first[0].startswith("sync folder: ") and first[1].startswith("agent slack: ") and len(first) == 2
    # The group's --vault names the vault for its subcommand.
    assert main(["sync", "--vault", str(vault.root), "start", "plaud"]) == 0
    second = capsys.readouterr().out.splitlines()
    assert second[1].startswith("agent plaud: ")


def test_an_owner_with_no_address_is_named_alone(tmp_path: Path) -> None:
    assert start.owner(empty_vault(tmp_path, make_config(emails=[]))) == "Ada Example"


# -- each agent's slice (#305) ---------------------------------------------------------------------------------------


def test_each_source_agent_reads_its_own_slice_items_by_source_people_meeting_and_due(vault: Vault) -> None:
    """Slack's slice: its own items, Cleo's because she wrote in Slack lately, Gail's on a meeting Cleo is in, and one
    due this week; not Finn's mail, and no points. The weekly's open point stays in context.md."""
    vault.write_run("slack", [make_record(rid="slack:C0PILOT/1791100950.000100", source="slack",
                                          container="slack:C0PILOT", author="Cleo Test <cleo@example.invalid>",
                                          ts=datetime(2026, 10, 19, 8, tzinfo=UTC))])  # fmt: skip
    mail = {"kind": "reply", "stream": "pilot", "opened": "2026-10-19"}
    store.write(vault, [
        {"op": "meeting", "as": "sensors", "fields": {"subject": "Sensor review", "start": "2026-10-21T10:00+02:00",
                                                     "end": "2026-10-21T11:00+02:00", "organiser": "Hugo Example",
                                                     "attendees": ["Cleo Test", "Hugo Example"]}},
        {"op": "add", "fields": mail | {"title": "Send Gail the sensor list", "who": "Gail Example",
                                        "source": "outlook:AAMkADgail", "meeting": "$sensors"}},
        {"op": "add", "fields": mail | {"title": "Send Finn the crane report", "who": "Finn Example",
                                        "source": "outlook:AAMkADfinn"}},
        {"op": "add", "fields": mail | {"title": "Sign the venue form", "who": "Ivy Example",
                                        "source": "outlook:AAMkADivy", "due": "2026-10-22"}},
    ], by="claude", now=NOW)  # fmt: skip
    index_build.update(vault)
    started = start.start(vault, [], NOW)
    sliced = (started.folder / "context" / "slack.md").read_text(encoding="utf-8").splitlines()
    assert sliced[0].endswith("owed since: the run's window  the ledger for slack")
    assert sliced[1:] == [
        "open items (5)",
        "A-0001 | reply | Ben Sample | Send Ben the pilot dates | slack:C0PILOT/1791100000.000100",
        "A-0002 | waiting | Cleo Test | Cleo's quote / the sensors | outlook:AAMkADquote1",
        "A-0003 | task | Dan Example | Book the room for the pilot | slack:C0PILOT/1791100800.000300 @M-0002",
        "A-0006 | reply | Gail Example | Send Gail the sensor list | outlook:AAMkADgail @M-0005",
        "A-0008 | reply | Ivy Example | Sign the venue form | outlook:AAMkADivy",
        "snoozed (1)",
        "A-0004 | task | Eve Example | Draft the pilot plan | slack:C0PILOT/1791100900.000100 | until 10-25",
        "meetings 10-13 to 10-27, or with an open point (1)",
        "M-0005 | 10-21 10:00-11:00 | Sensor review | Hugo Example",
        "streams (1)",
        "pilot | active | The pilot starts on the 28th | Book the room | deadline 10-28",
    ]
    whole = (started.folder / "context.md").read_text(encoding="utf-8")
    assert "Send Finn the crane report" in whole and f"  #1 open | Ben: the pilot's dates | {POINT}" in whole


def test_a_prompt_keeps_only_the_parts_its_source_uses(vault: Vault) -> None:
    started = start.start(vault, [], NOW)
    slack = (started.folder / "agents" / "slack.md").read_text(encoding="utf-8")
    plaud = (started.folder / "agents" / "plaud.md").read_text(encoding="utf-8")
    # Slack is a work source with no recordings: nothing to classify, no recordings part.
    assert "## Promote" in slack and "## Classify and promote" not in slack and "## Recordings" not in slack
    assert "## Classify and promote" in plaud and "## Recordings" in plaud and "## Promote\n" not in plaud
    assert "[[" not in slack + plaud
    assert len(slack) < len(plaud)


def test_without_the_index_s_records_a_slice_holds_every_item(vault: Vault) -> None:
    index_build.db_path(vault).unlink()
    started = start.start(vault, [], NOW)
    sliced = (started.folder / "context" / "slack.md").read_text(encoding="utf-8")
    assert "open items (3)" in sliced and "snoozed (1)" in sliced and "M-0001 | 10-19" in sliced


def test_fill_keeps_a_part_by_its_flag_or_its_absence() -> None:
    text = "a\n[[if CLASSIFY]]\nb {X}\n[[end]]\n[[if !CLASSIFY]]\nc\n[[end]]\nd\n"
    assert start.fill(text, {"X": "{Y}"}, frozenset({"CLASSIFY"})) == "a\nb {Y}\nd\n"
    assert start.fill(text, {}) == "a\nc\nd\n"


def test_a_slice_past_the_read_tool_s_room_is_written_in_parts(tmp_path: Path) -> None:
    long = ["x" * 999] * 100
    files = slices.write(tmp_path, "slack", "the ledger for slack", long)
    assert [f.name for f in files] == ["slack.1.md", "slack.2.md", "slack.3.md"]
    texts = [f.read_text(encoding="utf-8") for f in files]
    assert texts[0].startswith("the ledger for slack | part 1 of 3\n") and all(len(t.encode()) <= 40_000 for t in texts)
    assert sum(t.count("x" * 999) for t in texts) == 100
    assert slices.listed(files) == ", ".join(str(f) for f in files)
