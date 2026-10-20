"""`docket sync summary` (#272): the sync's summary groups from the ledger after its batch, a line per item, read only.
Invented people and data only: the pilot's ledger of `test_sync_folder.py`, a sync of it and its batch."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from docket_testkit import copy_vault, empty_vault, git, make_config, template_with
from test_sync_folder import NOTED, NOW, WEEKLY, build_pilot

from docket.cli import main
from docket.errors import DocketError
from docket.index import build as index_build
from docket.ledger import store, views
from docket.sync import folder, start, summary
from docket.vault.vault import Vault

AT = NOW + timedelta(minutes=10)
SENT = "slack:C0PILOT/1791100500.000200"
DAN = "slack:C0PILOT/1791101200.000100"
RUN = "RUN:      {} | run-1 | captures 2 | ready 2, pending 0 | skipped 0 | mark moved {} | {}"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_with(tmp_path_factory, build_pilot)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    v = copy_vault(template, tmp_path)
    index_build.rebuild(v)
    return v


def batch(vault: Vault) -> str:
    """The sync's batch, as the skill writes it from the draft: Ben's ask closed, Dan's room asked again after the
    weekly, Cleo's quote logged and late, the weekly's outcome, a proposed point for next week's and one for a weekly
    past the prep week, an invite and a call due today. Its id, for the undo."""
    written = store.write(vault, [
        {"op": "done", "item": "A-0001", "note": SENT},
        {"op": "add", "fields": {"kind": "reply", "title": "Send Dan the floor plan",
                                 "who": "Dan Example", "stream": "pilot", "source": DAN, "due": "2026-10-23"}},
        {"op": "log", "item": "A-0002", "note": "Cleo: the quote by Monday"},
        {"op": "set", "item": "A-0002", "fields": {"due": "2026-10-19"}},
        {"op": "set", "item": "M-0001", "fields": {"outcome": "The pilot starts on the 28th.\nDan books the room."}},
        {"op": "point", "item": "M-0002", "fields": {"text": "Dan: is the room booked for the 28th?",
                                                     "item": "A-0003", "proposed": True}},
        {"op": "point", "item": "M-0003", "fields": {"text": "The badges for the site"}},
        {"op": "add", "fields": {"kind": "invite", "title": "Supplier day", "who": "Ivy Example", "stream": "pilot",
                                 "source": "calendar:supplier-1103"}},
        {"op": "add", "fields": {"kind": "decision", "title": "Pick the pilot's caterer", "who": "Gail Example",
                                 "stream": "pilot", "source": DAN, "due": "2026-10-20"}},
    ], by="sync", now=AT)  # fmt: skip
    assert written.ops
    return str(written.ops[0].batch)


def given(vault: Vault, path: Path) -> None:
    """The plaud source's two recordings given agents in this sync: the kickoff's note is in the index, the weekly's
    isn't."""
    made = [
        folder.Recording(rid=rid, key=rid.partition(":")[2], title=title, start=NOW, prompt="-", block="-", files=[])
        for rid, title in ((WEEKLY, "Pilot weekly"), (NOTED, "Pilot kickoff"))
    ]
    listing = folder.Recordings(source="plaud", recordings=made)
    folder.recordings_file(path, "plaud").write_text(listing.model_dump_json(), encoding="utf-8")


def test_the_summary_says_each_group_from_the_ledger_after_the_batch(vault: Vault) -> None:
    """Every group, an item once among them; a batch before the sync's start or by the user left out of the batch's
    lines, though the user's item is listed where it is due; the read writes nothing."""
    earlier = [{"op": "log", "item": "A-0003", "note": "Dan: before the sync"}]
    store.write(vault, earlier, by="sync", now=NOW - timedelta(hours=1))
    started = start.start(vault, [], NOW)
    sync, path = started.info.sync, started.folder
    (path / "blocks" / "slack.txt").write_text(RUN.format("slack", "yes", "-") + "\n", encoding="utf-8")
    (path / "blocks" / "plaud.txt").write_text(RUN.format("plaud", "no", "the budget ran out") + "\n", encoding="utf-8")
    given(vault, path)
    id_ = batch(vault)
    store.write(vault, [{"op": "add", "fields": {"kind": "task", "title": "Order the badges", "who": "Hal Example",
                                                 "stream": "pilot", "source": DAN}}], by="user", now=AT)  # fmt: skip
    before = git(vault.root, "rev-parse", "HEAD"), git(vault.root, "status", "--porcelain")
    found = summary.summary(vault, sync, AT + timedelta(minutes=1))
    assert (git(vault.root, "rev-parse", "HEAD"), git(vault.root, "status", "--porcelain")) == before
    assert summary.lines(found) == [
        f"summary {sync}  Tue 2026-10-20 09:26 Europe/Budapest: 6 open, 1 snoozed, 1 overdue",
        f'batch {id_}: 9 lines; undo it whole with {{"op": "undo", "batch": "{id_}"}}',
        "closed (1)",
        f"  A-0001 done: {SENT}",
        "added (3)",
        "  A-0006 | reply | Dan Example | Send Dan the floor plan | 0d, due 10-23",
        "  A-0007 | invite | Ivy Example | Supplier day | 0d",
        "  A-0008 | decision | Gail Example | Pick the pilot's caterer | 0d, due 10-20",
        "logged 1 on 1 item(s); 2 set, 2 point",
        "outcomes written (1)",
        "  M-0001 outcome: The pilot starts on the 28th. Dan books the room.",
        "meetings and prep (1)",
        "  M-0002 | 10-26 10:00-10:30 | Pilot weekly | Ben Sample",
        "    #1 open | Dan: is the room booked for the 28th? -> A-0003 (proposed)",
        "    A-0003 | task | Dan Example | Book the room for the pilot | 2d @M-0002",
        "today and overdue (1)",
        "  A-0008 | decision | Gail Example | Pick the pilot's caterer | 0d, due 10-20",
        "this week (1)",
        "  A-0006 | reply | Dan Example | Send Dan the floor plan | 0d, due 10-23",
        "whenever (2)",
        "  A-0003 | task | Dan Example | Book the room for the pilot | 2d @M-0002",
        "  A-0009 | task | Hal Example | Order the badges | 0d",
        "invites (1)",
        "  A-0007 | invite | Ivy Example | Supplier day | 0d",
        "waiting (1)",
        "  A-0002 | waiting | Cleo Test | Cleo's quote / the sensors | 2d, due 10-19, 1d late",
        "left over (1)",
        "  M-0001 | 10-19 10:00-10:30 | Pilot weekly | Ben Sample | 1 open",
        "runs short (1)",
        "  plaud: mark moved no: the budget ran out",
        "notes (1 of 2 put)",
        f"  {WEEKLY} | Pilot weekly | no note in the index",
        f"  {NOTED} | Pilot kickoff | recording-note:2026-10-18-pilot-kickoff",
        "staging: 0 pending, 0 ready",
    ]


def test_a_sync_with_nothing_written_yet_says_so_and_each_run_that_stopped_short(vault: Vault) -> None:
    """No batch by sync since the start; a run that didn't open, a block with no RUN line, a source with no block."""
    started = start.start(vault, [], NOW)
    (started.folder / "blocks" / "plaud.txt").write_text("RUN: plaud | none | the recorder's tools aren't there\n",
                                                         encoding="utf-8")  # fmt: skip
    lines = summary.lines(summary.summary(vault, started.info.sync, AT))
    assert lines[1] == "batch: none by sync since the sync started, so nothing is written yet"
    at = lines.index("runs short (2)")
    assert lines[at + 1 : at + 3] == [
        "  plaud: no run: the recorder's tools aren't there",
        "  slack: no block, so its agent's answer says why",
    ]
    (started.folder / "blocks" / "slack.txt").write_text("OWED:     Ben Sample | x | y | z | w | today\n",
                                                         encoding="utf-8")  # fmt: skip
    lines = summary.lines(summary.summary(vault, started.info.sync, AT))
    assert "  slack: its block has no RUN line" in lines
    assert not any(line.startswith("notes") for line in lines)


def test_the_summary_keeps_within_the_room_cutting_whenever_first(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = start.start(vault, [], NOW)
    store.write(vault, [
        {"op": "add", "fields": {"kind": "task", "title": f"Order part{n:03d} from vendor{n:03d}", "who": "Hal Example",
                                 "stream": "pilot", "source": f"slack:C0PILOT/17911{n:05d}.000100"}}
        for n in range(30)
    ], by="sync", now=AT)  # fmt: skip
    whole = summary.lines(summary.summary(vault, started.info.sync, AT))
    monkeypatch.setattr(summary, "ROOM", sum(len(line) + 1 for line in whole) - 500)
    cut = summary.lines(summary.summary(vault, started.info.sync, AT))
    assert sum(len(line) + 1 for line in cut) <= summary.ROOM
    at = cut.index("whenever (32)")
    end = next(k for k in range(at + 1, len(cut)) if not cut[k].startswith("  A-"))
    assert cut[end] == f"  and {32 - (end - at - 1)} more: docket ledger list gives them" and 0 < end - at - 1 < 32
    # The groups after it are whole: whenever gave enough.
    assert cut[end + 1 :] == whole[whole.index("whenever (32)") + 33 :]
    # Whenever all gone, left over gives its rows next, then what this batch added, the counts kept.
    monkeypatch.setattr(summary, "ROOM", 1_500)
    cut = summary.lines(summary.summary(vault, started.info.sync, AT))
    assert sum(len(line) + 1 for line in cut) <= 1_500
    assert cut[cut.index("whenever (32)") + 1] == "  and 32 more: docket ledger list gives them"
    assert cut[cut.index("left over (1)") + 1] == "  and 1 more: docket ledger list gives them"
    at = cut.index("added (30)")
    end = next(k for k in range(at + 1, len(cut)) if not cut[k].startswith("  A-"))
    assert 0 < end - at - 1 < 30 and cut[end] == f"  and {30 - (end - at - 1)} more: docket ledger list gives them"
    assert "waiting (1)" in cut and cut[cut.index("waiting (1)") + 1].startswith("  A-0002 | waiting")


def test_the_command_prints_the_summary_and_refuses_another_vault_s_sync(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The command reads the clock: held at the fixtures' day.
    monkeypatch.setattr(views, "now", lambda: AT)
    started = start.start(vault, [], NOW)
    assert main(["sync", "summary", started.info.sync, "--vault", str(vault.root)]) == 0
    assert capsys.readouterr().out.startswith(f"summary {started.info.sync}  ")
    other = empty_vault(tmp_path / "other", make_config())
    assert main(["sync", "summary", started.info.sync, "--vault", str(other.root)]) == 2
    assert f"is a sync of {vault.root}" in capsys.readouterr().err


def test_the_notes_say_when_the_index_can_t_answer(vault: Vault) -> None:
    started = start.start(vault, [], NOW)
    given(vault, started.folder)
    index_build.db_path(vault).unlink()
    with pytest.raises(DocketError, match="no index"):
        index_build.noted(vault, [WEEKLY])
    lines = summary.lines(summary.summary(vault, started.info.sync, AT))
    assert "notes: the index can't say which of 2 recording(s) have one: there is no index yet" in " ".join(lines)


def test_only_the_newest_batches_since_the_start_are_read(vault: Vault) -> None:
    """A sync writes one batch; a ledger holding more by sync since its start names the newest three and counts the
    rest."""
    started = start.start(vault, [], NOW)
    ids = [
        str(store.write(vault, [{"op": "log", "item": "A-0003", "note": f"Dan: try {n}"}], by="sync",
                        now=AT + timedelta(minutes=n)).ops[0].batch)
        for n in range(4)
    ]  # fmt: skip
    lines = summary.lines(summary.summary(vault, started.info.sync, AT + timedelta(minutes=5)))
    assert lines[1:5] == [
        "1 older batch(es) by sync since the sync started, not listed",
        *(f'batch {id_}: 1 lines; undo it whole with {{"op": "undo", "batch": "{id_}"}}' for id_ in ids[1:]),
    ]
    assert "logged 3 on 1 item(s)" in lines
