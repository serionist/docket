"""`docket sync settle` (#272): the draft's unsure lines settled in one call by their numbers, each kept, swapped for an
alternative, both, or dropped, what goes with an op following it, and nothing written when a choice doesn't apply. The
reconcile's own draft, settled and written, is in test_sync_reconcile.py. Invented people and data only."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, make_config

from docket.cli import main
from docket.errors import DocketError
from docket.sync import folder, settle
from docket.sync.reconcile import GOES_WITH
from docket.vault.vault import Vault

NOW = datetime(2026, 10, 20, 7, 15, tzinfo=UTC)
SENT = "slack:C0PILOT/1791100500.000200"
QUOTE_AGAIN = "outlook:AAMkADquote2"
TWO_ASKS = "slack:C0PILOT/1791101500.000100"
UNTITLED = "calendar:untitled-1"
WEEKLY = "plaud:rec-0101"


def j(op: dict[str, Any]) -> str:
    return json.dumps(op, ensure_ascii=False)


LOG = {"op": "log", "item": "A-0002", "note": f"Cleo: the quote by Thursday: {QUOTE_AGAIN}"}
CLEO = {"kind": "waiting", "title": "the quote by Thursday", "who": "Cleo Test", "stream": "pilot"}
ADD = {"op": "add", "fields": CLEO | {"source": QUOTE_AGAIN, "opened": "2026-10-19", "due": "2026-10-22"}}
DUE = {"op": "set", "item": "A-0002", "fields": {"due": "2026-10-22"}}
HAL = {"kind": "reply", "who": "Hal Example", "stream": "pilot", "source": TWO_ASKS, "opened": "2026-10-19"}
DATES = {"op": "add", "fields": HAL | {"title": "Send Hal the pilot dates"}}
BUDGET = {"op": "add", "fields": HAL | {"title": "Send Hal the pilot budget"}, "force": True}
INVITE = {"op": "add", "fields": {"kind": "invite", "who": "Ivy Example", "stream": "pilot", "source": UNTITLED}}
OUTCOME = {
    "op": "set",
    "item": "M-0003",
    "fields": {"outcome": "Gail draws it"},
    "note": f"r:{WEEKLY}/transcript@14:00",
}
JOINED = OUTCOME | {"fields": {"outcome": "Gail owns the design.\nGail draws it"}}


def draft(sync: str) -> list[str]:
    """A draft as the reconcile writes it: a sure close, then four unsure ops, the first with a set of its due that
    goes with it, the merge of Hal's two asks, an untitled invite, an outcome on a meeting that has one."""
    return [
        f"# docket sync reconcile {sync}: 8 ops, 5 unsure, rev 1",
        "# closes",
        j({"op": "done", "item": "A-0001", "note": SENT, "outcome": "Sent Ben the dates"}),
        "# asks",
        "# ? blocks/outlook.txt:2 WAITING: Cleo Test | outlook:AAMkADquote2 | 2026-10-19 09:00 | the quote by Thursday",
        "# ? A-0002 | waiting | Cleo Test | Cleo's quote for the sensors | outlook:AAMkADquote1 | due 10-21",
        j(LOG | {"unsure": "it continues A-0002, but its title doesn't look like that item's: keep it, or the add"}),
        f"# {j(ADD)}",
        j(DUE | {"unsure": GOES_WITH}),
        "# ? blocks/slack.txt:3 OWED: Hal Example | slack:C0PILOT/1791101500.000100 | 2026-10-19 | Send Hal the dates",
        "# ? blocks/slack.txt:4 OWED: Hal Example | slack:C0PILOT/1791101500.000100 | 2026-10-19 | Send Hal the budget",
        j(DATES | {"unsure": "2 lines with the same who and a look-alike title, drafted as one ask"}),
        f"# {j(BUDGET)}",
        "# ? blocks/calendar.txt:2 INVITES: Ivy Example | calendar:untitled-1 | 2026-11-03 09:00 | new series | pilot",
        j(INVITE | {"unsure": "no title: neither a meeting nor the record gives one, so write one"}),
        "# meetings, links, minutes",
        "# ? blocks/plaud/rec-0101.txt:4 OUTCOME: M-0003 | Gail draws it | r:plaud:rec-0101/transcript@14:00",
        "# ? M-0003 outcome: Gail owns the design.",
        j(OUTCOME | {"unsure": "M-0003 has an outcome already: keep one, or take the alternative, the two joined"}),
        f"# {j(JOINED)}",
        j({"op": "rollover"}),
        "# prep: the next five working days: M-0002",
        "#   M-0002 | 10-26 10:00-10:30 | Pilot weekly | Ben Sample",
        j({"op": "archive"}),
    ]  # fmt: skip


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return empty_vault(tmp_path, make_config())


def started(vault: Vault) -> tuple[str, Path]:
    """A sync folder of the vault holding the draft above, as a start and a reconcile leave it."""
    sync = folder.new_id(NOW)
    path = folder.make(sync)
    info = folder.Info(sync=sync, vault=str(vault.root), made=NOW, today=NOW.date(), zone="Europe/Budapest",
                       owed_since="the run's window", sources=[], docket="test")  # fmt: skip
    (path / folder.INFO).write_text(info.model_dump_json(), encoding="utf-8")
    (path / "draft.jsonl").write_text("\n".join(draft(sync)) + "\n", encoding="utf-8")
    return sync, path / "draft.jsonl"


def test_each_choice_settles_its_line_and_what_goes_with_it_follows_a_keep(vault: Vault) -> None:
    """The log kept with the set of its due, the merge kept and its other ask added too, the invite dropped, the
    outcomes joined: each op's quotes and alternatives go, the head counts what is left, and the file is the ops."""
    sync, file = started(vault)
    done = settle.settle(vault, sync, ["7=keep", "12=keep+alt", "15=drop", "19=alt"], rev=1)
    assert (done.kept, done.swapped, done.dropped, done.unsure) == (1, 2, 1, [])
    assert file.read_text(encoding="utf-8").splitlines() == [
        f"# docket sync reconcile {sync}: 8 ops, 0 unsure, rev 2",
        "# closes",
        j({"op": "done", "item": "A-0001", "note": SENT, "outcome": "Sent Ben the dates"}),
        "# asks",
        j(LOG),
        j(DUE),
        j(DATES),
        j(BUDGET),
        "# meetings, links, minutes",
        j(JOINED),
        j({"op": "rollover"}),
        "# prep: the next five working days: M-0002",
        "#   M-0002 | 10-26 10:00-10:30 | Pilot weekly | Ben Sample",
        j({"op": "archive"}),
    ]
    assert done.prep == 12 and done.ops == 8


def test_an_alternative_in_its_place_takes_the_lines_that_go_with_the_op_away(vault: Vault) -> None:
    """The add in place of the log carries its own due, so the set of the log's item goes with the log."""
    sync, file = started(vault)
    done = settle.settle(vault, sync, ["7=alt"], rev=1)
    lines = file.read_text(encoding="utf-8").splitlines()
    assert lines[4] == j(ADD) and j(DUE) not in lines and j(DUE | {"unsure": GOES_WITH}) not in lines
    # The rest stays as it was, by its new numbers: the merge, the invite, the outcome.
    assert done.unsure == [8, 11, 15] and lines[0] == f"# docket sync reconcile {sync}: 7 ops, 3 unsure, rev 2"
    assert [json.loads(lines[n - 1])["op"] for n in done.unsure] == ["add", "add", "set"]
    assert lines[5].startswith("# ? blocks/slack.txt:3") and done.prep == 18


def test_a_line_that_goes_with_an_op_is_kept_or_dropped_alone_when_named(vault: Vault) -> None:
    sync, file = started(vault)
    settle.settle(vault, sync, ["9=keep"], rev=1)
    lines = file.read_text(encoding="utf-8").splitlines()
    # Its op still waits, with its quotes and its alternative; the set is no longer unsure.
    assert lines[4:9] == [*draft(sync)[4:8], j(DUE)]
    sync, file = started(vault)
    settle.settle(vault, sync, ["7=keep", "9=drop"], rev=1)
    lines = file.read_text(encoding="utf-8").splitlines()
    assert lines[4] == j(LOG) and lines[5].startswith("# ? blocks/slack.txt:3")


@pytest.mark.parametrize(
    ("choices", "said"),
    [
        (["12=keep", "3=keep"], "line 3 isn't an unsure op: name the line of an op that carries unsure"),
        (["12=keep", "8=keep"], "line 8 isn't an unsure op"),
        (["12=keep", "7=alt2"], "line 7: alt2 names an alternative the line doesn't have: it has 1 alternative"),
        (["12=keep", "15=alt"], "line 15: alt names an alternative the line doesn't have: it has no alternative"),
        (["12=keep", "7=kept"], "line 7: kept isn't keep, alt, alt2 and so on, or drop"),
        (["12=keep", "7=keep+keep"], "line 7: keep+keep names a part twice"),
        (["12=keep", "9=alt"], "line 9 goes with the op on line 7: keep it or drop it"),
        (["7=keep", "7=drop"], "line 7 is named twice"),
        (["12=keep", "seven=keep"], "'seven=keep' isn't <line>=<choice>, such as 12=keep"),
        ([], "name the lines to settle, such as 12=keep 16=alt 18=drop"),
    ],
)
def test_a_choice_that_doesn_t_apply_refuses_the_call_and_changes_nothing(
    vault: Vault, choices: list[str], said: str
) -> None:
    sync, file = started(vault)
    before = file.read_bytes()
    with pytest.raises(DocketError, match=r"^nothing settled: ") as refused:
        settle.settle(vault, sync, choices, rev=1)
    assert said in str(refused.value)
    assert file.read_bytes() == before


def test_the_command_prints_what_it_settled_and_refuses_another_vault_s_sync(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sync, file = started(vault)
    assert main(["sync", "settle", sync, "12=keep", "15=drop", "--rev", "1", "--vault", str(vault.root)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "settled 2 line(s): 1 kept, 0 alternative(s) taken, 1 dropped",
        f"draft: {file} rev 2: 7 ops, 3 unsure, lines 7, 9, 14",
        "prep: line 17",
    ]
    other = empty_vault(tmp_path / "other", make_config())
    assert main(["sync", "settle", sync, "7=keep", "--rev", "1", "--vault", str(other.root)]) == 2
    assert f"is a sync of {vault.root}" in capsys.readouterr().err
    file.unlink()
    with pytest.raises(DocketError, match="no draft in"):
        settle.settle(vault, sync, ["7=keep"], rev=1)


def test_a_line_an_edit_left_unread_stays_as_it_is_and_can_t_be_named(vault: Vault) -> None:
    """A line Claude's own edit broke is no op to settle: it stays for the dry run to name, and the rest settles."""
    sync, file = started(vault)
    lines = file.read_text(encoding="utf-8").splitlines()
    lines[14] = '{"op": "add", "fields": {"kind": "invite"'
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(DocketError, match="line 15 isn't an unsure op"):
        settle.settle(vault, sync, ["15=drop"], rev=1)
    settle.settle(vault, sync, ["12=keep"], rev=1)
    assert '{"op": "add", "fields": {"kind": "invite"' in file.read_text(encoding="utf-8").splitlines()


def test_a_settle_at_a_rev_the_draft_has_left_changes_nothing(vault: Vault) -> None:
    """A second settle with the numbers read before the first would act on lines that moved: the invite meant by 15 is
    line 11 now, and 15 the outcome. It names the rev it read at, and the draft at another refuses it whole."""
    sync, file = started(vault)
    first = settle.settle(vault, sync, ["7=alt"], rev=1)
    assert first.rev == 2 and settle.lines(first)[1].startswith(f"draft: {file} rev 2: ")
    before = file.read_bytes()
    with pytest.raises(DocketError, match="the draft is at rev 2, not 1, so its lines moved since they were read"):
        settle.settle(vault, sync, ["15=drop"], rev=1)
    assert file.read_bytes() == before
    # Read again at rev 2, the invite is line 11.
    lines = file.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[10])["fields"]["kind"] == "invite"
    assert settle.settle(vault, sync, ["11=drop"], rev=2).rev == 3
    after = file.read_text(encoding="utf-8")
    assert '"kind": "invite"' not in after and '"item": "M-0003"' in after


def test_a_draft_with_no_head_line_is_refused(vault: Vault) -> None:
    sync, file = started(vault)
    file.write_text("\n".join(draft(sync)[1:]) + "\n", encoding="utf-8")
    with pytest.raises(DocketError, match="the draft's head line, with its rev, isn't its first line"):
        settle.settle(vault, sync, ["6=keep"], rev=1)


POINT = {"op": "point", "item": "M-0002", "fields": {"text": "Ben: the dates, where do they stand?", "proposed": True,
                                                   "item": "A-0001"}}  # fmt: skip


def with_candidates(file: Path) -> list[str]:
    """The draft with two prep candidates under its prep meeting, as the reconcile writes them (#305)."""
    lines = file.read_text(encoding="utf-8").splitlines()
    at = lines.index("#   M-0002 | 10-26 10:00-10:30 | Pilot weekly | Ben Sample") + 1
    lines[at:at] = ["#     candidate, on Ben Sample", f"# + {j(POINT)}", f"# + {j(POINT | {'item': 'M-0003'})}"]
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def test_a_range_settles_each_line_its_choice_fits_and_passes_over_the_rest(vault: Vault) -> None:
    sync, file = started(vault)
    with_candidates(file)
    done = settle.settle(vault, sync, ["1-15=drop", "24-26=take"], rev=1)
    assert (done.kept, done.swapped, done.dropped, done.taken, done.unsure) == (0, 0, 3, 2, [8])
    lines = file.read_text(encoding="utf-8").splitlines()
    assert lines[-4:-1] == ["#     candidate, on Ben Sample", j(POINT), j(POINT | {"item": "M-0003"})]
    assert lines[0] == f"# docket sync reconcile {sync}: 6 ops, 1 unsure, rev 2"


def test_a_prep_candidate_is_taken_or_dropped_and_a_range_keeps_only_unsure_ops(vault: Vault) -> None:
    sync, file = started(vault)
    with_candidates(file)
    done = settle.settle(vault, sync, ["25=drop", "7-26=keep"], rev=1)
    assert (done.kept, done.taken) == (4, 0)
    lines = file.read_text(encoding="utf-8").splitlines()
    assert f"# + {j(POINT)}" not in lines and f"# + {j(POINT | {'item': 'M-0003'})}" in lines


@pytest.mark.parametrize(
    ("choices", "said"),
    [
        (["25=keep"], "line 25 is a prep candidate: take it or drop it"),
        (["7=take"], "line 7: take isn't keep, alt, alt2 and so on, or drop"),
        (["7-12=alt"], "'7-12=alt': a range runs up, such as 31-38, and takes keep, take or drop"),
        (["12-7=keep"], "'12-7=keep': a range runs up"),
        (["7-12=keep", "12=drop"], "line 12 is named twice"),
    ],
)
def test_a_take_or_a_range_that_doesn_t_apply_changes_nothing(vault: Vault, choices: list[str], said: str) -> None:
    sync, file = started(vault)
    with_candidates(file)
    before = file.read_bytes()
    with pytest.raises(DocketError, match=r"^nothing settled: ") as refused:
        settle.settle(vault, sync, choices, rev=1)
    assert said in str(refused.value)
    assert file.read_bytes() == before
