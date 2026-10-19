"""The ledger's ops a forget masks (DR-0694): each row of the rule, judged by the plan against invented vaults quoting
a Plaud-shaped recording, chat messages and calendar records; the shells it leaves, by number on both screens; the
`from` chain, followed only from a point masked for its own words; a weekly 1:1's point rolled across ten meetings,
one meeting's calendar record forgotten; a version forget; the writer and the rollover on a shell, and a locator on
what a forget took (DR-0696); a stream's STATUS.md rendered again once its ops are masked; and the rule's own parts on
ops made by hand. Invented data only."""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import (
    PLAUD,
    POLISHED,
    AtConsole,
    empty_vault,
    folder_vault,
    git,
    make_config,
    make_record,
    plaud_records,
)  # fmt: skip
from ledger_kit import ADD_FIELDS, NOW, lines, meeting, op, point, point_set, roll, ulid

from docket import recall
from docket.forget import apply as forget_apply
from docket.forget import ledger as forget_ledger
from docket.forget import now as forget_now
from docket.forget import plan as planning
from docket.ledger import fold as folding
from docket.ledger import imports, ops, store
from docket.ledger import load as ledger_load
from docket.ledger.ops import MARK
from docket.ledger.store import RefusedError
from docket.media.keystore import KeyStore
from docket.vault import forgotten, holds
from docket.vault.anchors import DocRef
from docket.vault.config import CONFIG_FILE, RenderedFile, VaultConfig
from docket.vault.docstore import DocStore
from docket.vault.forgotten import Forgotten
from docket.vault.records import RawRecord
from docket.vault.staging import rid_hash
from docket.vault.vault import Vault

AT = datetime(2026, 10, 14, 10, tzinfo=UTC)
CHAT = make_record(1).rid
OTHER = make_record(2).rid
NOTE = "recording-note:2026-10-14-supplier-sync"
NOTE_PATH = "processed/recording-note/2026/10/2026-10-14-supplier-sync.md"
TEN = f"- Ten million, signed by Friday. [r:{POLISHED}@10:42]"
KEPT = f"- Cleo opened the channel. [r:{CHAT}]"
SAID = "Ada said Tuesday at the venue"


def event(n: int) -> str:
    return f"calendar:evt-{n:02d}"


def calendar(n: int) -> RawRecord:
    return make_record(rid=event(n), source="calendar", kind="event", container=None, title="Weekly 1:1", text="")


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    """A Plaud-shaped recording with its transcript, two chat messages and ten weekly calendar records, all in raw."""
    found = empty_vault(tmp_path, make_config())
    found.write_run("plaud", plaud_records("transaction_polish"), AT)
    found.write_run("chat", [make_record(1), make_record(2)], AT)
    found.write_run("calendar", [calendar(n) for n in range(1, 11)], AT)
    return found


def write(vault: Vault, *changes: dict[str, Any], now: datetime = NOW) -> store.Written:
    return store.write(vault, list(changes), now=now)


def add(vault: Vault, **fields: Any) -> str:
    """An item, its fields over the kit's; its number."""
    written = write(vault, {"op": "add", "fields": {**ADD_FIELDS, **fields}, "force": True})
    return written.labels[written.ops[0].id]


def weekly(vault: Vault, n: int, **fields: Any) -> str:
    """The nth weekly 1:1, from its calendar record, on a Monday from 3 August; its number."""
    start = datetime(2026, 8, 3, 9, tzinfo=UTC) + timedelta(weeks=n - 1)
    keyed = {"subject": "Weekly 1:1", "start": f"{start:%Y-%m-%dT%H:%M}+00:00", "event": event(n)}
    written = write(vault, {"op": "meeting", "fields": {**keyed, "end": f"{start:%Y-%m-%dT}09:30+00:00", **fields}})
    return written.labels[written.ops[0].id]


def plan_of(vault: Vault, *rids: str) -> planning.Plan:
    return planning.make(vault, "rids", list(rids))


def found(vault: Vault, *rids: str) -> forget_ledger.Found:
    return plan_of(vault, *rids).ledger


def raw(vault: Vault) -> dict[str, str]:
    """Each op's line as the files hold it."""
    return {line.op.id: line.text for line in ops.read_all(vault.root, masked=frozenset()).lines}


def ids_of(vault: Vault, *words: str) -> set[str]:
    """The ids of the ops whose lines say every one of these words."""
    return {op_id for op_id, text in raw(vault).items() if all(word in text for word in words)}


def forget_now_at(vault: Vault, owner: Callable[..., AtConsole], *rids: str) -> forget_now.Forgot:
    owner("code")
    done = forget_now.forget(vault, "rids", list(rids))
    assert done.applied is not None
    return done


def chain(vault: Vault, meetings: int = 10) -> list[str]:
    """The weekly 1:1s with one point raised on the first and rolled on to the last, where it is open."""
    numbers = [weekly(vault, n) for n in range(1, meetings + 1)]
    write(vault, {"op": "point", "item": numbers[0], "fields": {"text": "Ada: dates for the venue"}})
    write(vault, {"op": "rollover"})
    return numbers


def points_of(vault: Vault, number: str) -> list[tuple[str, str]]:
    folded = ledger_load(vault.root)
    (found_one,) = [m for m in folded.meetings.values() if f"M-{m.number:04d}" == number]
    return [(p.text, p.state) for p in found_one.points]


# -- what an op says itself --------------------------------------------------------------------------------------------


def test_an_item_whose_source_is_forgotten_is_a_shell_named_by_number_on_both_screens(vault: Vault) -> None:
    number = add(vault, source=CHAT)
    write(vault, {"op": "log", "item": number, "note": SAID})
    kept = add(vault, source=OTHER, title="Book the venue")
    plan = plan_of(vault, CHAT)
    assert plan.ledger.shells == (number,) and plan.ledger.open == (number,)
    assert len(plan.ledger.new) == 2
    assert plan.line("test-box", AT).ops == sorted(plan.ledger.new)
    for screen in (False, True):
        said = planning.lines(plan, screen=screen)
        assert f"ledger    2 ops masked; {number} is a shell now" in said
        assert f"drop      {number} is a shell left open, to drop" in said
        assert not any(SAID in line or "Send Ada" in line for line in said)
        assert any("ledger text that names it in plain words" in line for line in said)
    assert kept not in "".join(planning.lines(plan, screen=True))


def test_a_source_a_set_gave_makes_the_item_a_shell(vault: Vault) -> None:
    number = add(vault, source=OTHER)
    write(vault, {"op": "set", "item": number, "fields": {"source": CHAT}})
    assert found(vault, CHAT).shells == (number,)
    assert found(vault, OTHER).shells == (number,)


def test_an_anchor_on_the_family_masks_its_op_judged_where_it_reads(vault: Vault) -> None:
    number = add(vault, source=OTHER)
    write(
        vault,
        {"op": "log", "item": number, "note": f"{SAID} [r:{PLAUD}@10:42]"},
        {"op": "log", "item": number, "note": f"The channel opened [r:{CHAT}]"},
        {"op": "log", "item": number, "note": "An unread one [r:nocolon] and a ref that isn't [d:bad]"},
    )
    # The recording has no segments of its own: a time on it reads on its transcript, which the forget takes.
    plan = plan_of(vault, POLISHED)
    assert plan.ledger.new == frozenset(ids_of(vault, SAID))
    assert plan.ledger.shells == () and plan.ledger.kept == (f"{number}: 1 op masked; the rest kept",)
    assert found(vault, CHAT).new == frozenset(ids_of(vault, "The channel opened"))


def test_a_ref_to_a_doc_line_the_rule_drops_masks_its_op_and_one_to_a_kept_line_doesn_t(vault: Vault) -> None:
    covers = "".join(f"  - {rid}\n" for rid in (CHAT, POLISHED))
    recall.doc_put(
        vault,
        f"---\nid: {NOTE}\ntype: recording-note\ncovers:\n{covers}by: claude-test, sync 2026-10-14\n---\n\n"
        f"# Supplier sync\n\n{TEN}\n{KEPT}\n",
    )
    note = DocStore(vault).get(NOTE)
    assert note is not None
    lines_of = {text: n for n, text in note.lines()}
    number = add(vault, source=OTHER)
    write(
        vault,
        {"op": "log", "item": number, "note": f"The deal [d:{NOTE}@1#{lines_of[TEN]}]"},
        {"op": "log", "item": number, "note": f"The channel [d:{NOTE}@1#{lines_of[KEPT]}]"},
    )
    assert found(vault, POLISHED).new == frozenset(ids_of(vault, "The deal"))


def test_a_material_on_the_family_goes_as_its_link_and_its_meeting_stays_named(vault: Vault) -> None:
    number = weekly(vault, 1)
    write(
        vault,
        {"op": "material", "item": number, "ref": PLAUD, "label": "the recording"},
        {"op": "material", "item": number, "ref": "https://example.invalid/agenda", "label": "the agenda"},
        {"op": "point", "item": number, "fields": {"text": "The venue"}},
    )
    plan = plan_of(vault, PLAUD)
    assert plan.ledger.new == frozenset(ids_of(vault, "the recording"))
    assert plan.ledger.shells == ()
    assert plan.ledger.kept == (f"{number}: 1 material link removed; the rest kept",)
    assert f"kept      {number}: 1 material link removed; the rest kept" in planning.lines(plan, screen=False)


def test_a_material_naming_a_doc_the_forget_removes_by_id_or_path_goes(vault: Vault) -> None:
    recall.doc_put(
        vault,
        f"---\nid: {NOTE}\ntype: recording-note\ncovers:\n  - {POLISHED}\nby: claude-test, sync 2026-10-14\n---\n\n"
        f"# Supplier sync\n\n{TEN}\n",
    )
    number = add(vault, source=OTHER)
    write(
        vault,
        {"op": "material", "item": number, "ref": NOTE, "label": "by id"},
        {"op": "material", "item": number, "ref": f"notes/{NOTE_PATH}", "label": "by path"},
        {"op": "material", "item": number, "ref": "processed/recording-note/2026-10-14-supplier-sync.md",
         "label": "not its path"},
        {"op": "material", "item": number, "ref": "notes/elsewhere.md", "label": "a note"},
    )  # fmt: skip
    plan = plan_of(vault, POLISHED)
    assert any(c.id == NOTE and c.removed for c in plan.docs)
    assert plan.ledger.new == frozenset(ids_of(vault, "by id") | ids_of(vault, "by path"))
    assert plan.ledger.kept == (f"{number}: 2 material links removed; the rest kept",)


def test_a_doc_an_earlier_forget_s_commit_removed_reads_as_removed_until_the_tree_holds_it_again(
    vault: Vault, owner: Callable[..., AtConsole]
) -> None:
    recall.doc_put(
        vault,
        f"---\nid: {NOTE}\ntype: recording-note\ncovers:\n  - {POLISHED}\nby: claude-test, sync 2026-10-14\n---\n\n"
        f"# Supplier sync\n\n{TEN}\n",
    )
    number = add(vault, source=OTHER)
    forget_now_at(vault, owner, POLISHED)
    assert forget_apply.removed_before(vault) == {NOTE}
    # A link written after, as another clone's from before its pull would be: the next forget's plan names it.
    write(vault, {"op": "material", "item": number, "ref": NOTE, "label": "linked after"})
    assert found(vault, CHAT).new == frozenset(ids_of(vault, "linked after"))
    (vault.root / NOTE_PATH).write_text("---\nid: x\n---\n", encoding="utf-8")
    assert forget_apply.removed_before(vault) == frozenset()


# -- shells ------------------------------------------------------------------------------------------------------------


def test_a_meeting_whose_calendar_record_is_forgotten_is_a_shell_with_its_points(vault: Vault) -> None:
    number = weekly(vault, 1)
    write(
        vault,
        {"op": "point", "item": number, "fields": {"text": "The venue"}},
        {"op": "set", "item": number, "fields": {"notes": "Agreed the dates"}},
    )
    other = weekly(vault, 2)
    plan = plan_of(vault, event(1))
    # Its open point would keep it in the day's lists: named to drop on both screens.
    assert plan.ledger.shells == (number,) and plan.ledger.open == (number,)
    for screen in (False, True):
        assert f"drop      {number} is a shell left open, to drop" in planning.lines(plan, screen=screen)
    assert plan.ledger.new == frozenset(ids_of(vault, "Weekly 1:1", event(1)) | ids_of(vault, "The venue")
                                        | ids_of(vault, "Agreed the dates"))  # fmt: skip
    assert other not in plan.ledger.shells


def test_a_meeting_the_calendar_step_brings_back_after_an_anchor_made_a_shell_keeps_its_points(
    vault: Vault, owner: Callable[..., AtConsole]
) -> None:
    shell = weekly(vault, 1, notes=f"Agreed [r:{CHAT}]")
    forget_now_at(vault, owner, CHAT)
    # The record is kept: the calendar step reads it again and, the shell's event reading masked, makes a new meeting.
    start = datetime(2026, 8, 3, 9, tzinfo=UTC)
    fields = {"subject": "Weekly 1:1", "start": f"{start:%Y-%m-%dT%H:%M}+00:00", "end": "2026-08-03T09:30+00:00",
              "event": event(1)}  # fmt: skip
    (outcome,) = store.write_calendar(vault, [store.Event(event(1), fields)], via="cli", now=NOW).outcomes
    assert outcome.what == "added" and outcome.meeting is not None and outcome.meeting != shell
    write(vault, {"op": "point", "item": outcome.meeting, "fields": {"text": "The venue, again"}})
    kept = ids_of(vault, "The venue, again") | ids_of(vault, '"op":"meeting"', event(1))
    assert len(kept) == 2
    # A later forget's plan, and its apply's match again, leave the new meeting and the owner's point alone.
    later = found(vault, OTHER)
    assert later.new == frozenset() and later.shells == ()
    forget_now_at(vault, owner, OTHER)
    assert points_of(vault, outcome.meeting) == [("The venue, again", "open")]
    assert not kept & forgotten.load(vault.root, vault.home).ops


def test_a_version_forget_takes_what_its_versions_alone_held(vault: Vault) -> None:
    holds.add(vault, "chat", [CHAT.partition(":")[2]])
    number = add(vault, source=CHAT)
    plan = plan_of(vault, CHAT)
    assert plan.taken == [planning.Taken(CHAT, "held")]
    assert plan.ledger.shells == (number,)


# -- the from chain ----------------------------------------------------------------------------------------------------


def test_a_weekly_point_loses_only_the_copy_on_the_meeting_whose_record_was_forgotten(vault: Vault) -> None:
    numbers = chain(vault)
    assert points_of(vault, numbers[9]) == [("Ada: dates for the venue", "open")]
    middle = found(vault, event(5))
    # The copy rolled into M-0005 goes as that shell's op; the copies beyond keep their words.
    assert middle.shells == (numbers[4],) and middle.heads == ()
    copies = ids_of(vault, "Ada: dates for the venue")
    assert len(copies) == 10 and len(middle.new & copies) == 1
    head = found(vault, event(10))
    assert head.heads == (f"{numbers[9]}#1, rolled in from {numbers[8]}, open",)
    assert len(head.new & copies) == 1
    lines_said = planning.lines(plan_of(vault, event(10)), screen=False)
    assert f"raise     {numbers[9]}#1, rolled in from {numbers[8]}, open: raise it again" in lines_said


def test_a_point_raised_on_a_shell_takes_its_copies_and_the_item_made_from_one(vault: Vault) -> None:
    numbers = chain(vault, 3)
    write(vault, {"op": "point_item", "item": numbers[2], "point": 1, "fields": {"stream": "pilot"}})
    made = ids_of(vault, '"op":"add"', "Ada: dates for the venue")
    # The first meeting's record forgotten: the point was raised there, so its words go wherever they rolled.
    gone = found(vault, event(1))
    assert ids_of(vault, "Ada: dates for the venue") <= gone.new
    assert made <= gone.new and len(gone.shells) == 2 and gone.heads == ()


def test_a_copy_whose_text_was_set_on_a_shell_takes_its_copies_beyond(vault: Vault) -> None:
    numbers = [weekly(vault, n) for n in range(1, 4)]
    write(vault, {"op": "point", "item": numbers[0], "fields": {"text": "The venue"}})
    write(vault, {"op": "rollover"}, now=datetime(2026, 8, 10, 8, tzinfo=UTC))
    write(vault, {"op": "point_set", "item": numbers[1], "point": 1, "fields": {"text": "The venue, Tuesday"}})
    write(vault, {"op": "rollover"})
    assert points_of(vault, numbers[2]) == [("The venue, Tuesday", "open")]
    gone = found(vault, event(2))
    assert ids_of(vault, "The venue, Tuesday") <= gone.new
    # The point raised on the first meeting, a live one, keeps its words.
    assert not ids_of(vault, '"text":"The venue"}') & gone.new


def test_an_item_from_a_copy_rolled_into_a_shell_keeps_its_words_and_is_named(vault: Vault) -> None:
    numbers = chain(vault, 3)
    item = store.write(
        vault, [{"op": "point_item", "item": numbers[2], "point": 1, "fields": {"stream": "pilot"}}], now=NOW
    )
    number = item.labels[next(o.id for o in item.ops if o.op == "add")]
    gone = found(vault, event(3))
    assert gone.shells == (numbers[2],)
    assert gone.kept == (f"{number}: from {numbers[2]}, a shell; the rest kept",)
    assert gone.heads == (f"{numbers[2]}#1, rolled in from {numbers[1]}, open",)


# -- the forget, the writer and the rollover on a shell ----------------------------------------------------------------


def test_the_forget_masks_the_ops_and_the_writer_refuses_text_on_its_shells(
    vault: Vault, owner: Callable[..., AtConsole]
) -> None:
    number = add(vault, source=CHAT)
    write(vault, {"op": "log", "item": number, "note": SAID})
    done = forget_now_at(vault, owner, CHAT)
    assert done.applied is not None and done.applied.ops == 2 and done.applied.listed_ops == 0
    subject = git(vault.root, "log", "-1", "--format=%s").strip()
    assert subject == "Forget: 1 record(s), 0 media file(s), 0 doc(s), 2 op(s) from test-box"
    assert not any(SAID in text or CHAT in text for text in raw(vault).values())
    item = next(iter(ledger_load(vault.root).items.values()))
    assert (item.title, item.who) == (MARK, MARK) and item.source.startswith("[forgotten:")
    refused = {
        "log": {"op": "log", "item": number, "note": "Ada again"},
        "notes": {"op": "set", "item": number, "fields": {"notes": "Again"}},
        "ref": {"op": "material", "item": number, "ref": "https://example.invalid/x"},
    }
    for name, change in refused.items():
        with pytest.raises(RefusedError, match=f"{number} is a shell a forget left; it takes no {name}"):
            write(vault, change)
    write(vault, {"op": "set", "item": number, "fields": {"due": "2026-10-30"}})
    dropped = write(vault, {"op": "drop", "item": number, "note": "Forgotten"})
    assert [o.note for o in dropped.ops] == [MARK]
    archived = write(vault, {"op": "archive", "note": "Archived by the sync"}, now=NOW + timedelta(days=15))
    assert [(o.op, o.note) for o in archived.ops] == [("archive", MARK)]
    assert vault.check() == []


def test_a_batch_closing_a_shell_with_an_outcome_beside_ten_other_items_goes_its_outcome_the_marker(
    vault: Vault, owner: Callable[..., AtConsole]
) -> None:
    shell = add(vault, source=CHAT)
    others = [add(vault, source=OTHER, title=f"Book the venue, round {n}") for n in range(10)]
    forget_now_at(vault, owner, CHAT)
    closes = [{"op": "done", "item": number, "outcome": f"Booked, round {n}", "note": "Closed by the sync"}
              for n, number in enumerate([*others[:5], shell, *others[5:]])]  # fmt: skip
    written = write(vault, *closes)
    items = ledger_load(vault.root).items
    assert {item.state for item in items.values()} == {"done"}
    (shell_id,) = [item_id for item_id, item in items.items() if folding.number_text(item.number or 0) == shell]
    said = [(o.op, o.note, (o.fields or {}).get("outcome")) for o in written.ops if o.item == shell_id]
    assert said == [("set", None, MARK), ("done", MARK, None)]
    kept = [(o.fields or {}).get("outcome") for o in written.ops if o.op == "set" and o.item != shell_id]
    assert kept == [f"Booked, round {n}" for n in range(11) if n != 5]
    assert vault.check() == []


def test_the_writer_refuses_a_locator_or_an_anchor_on_what_a_forget_took(
    vault: Vault, owner: Callable[..., AtConsole]
) -> None:
    number = add(vault, source=OTHER)
    forget_now_at(vault, owner, CHAT)
    refused = [
        ({"op": "add", "fields": {**ADD_FIELDS, "source": CHAT}}, "source names what a forget took"),
        ({"op": "add", "fields": {**ADD_FIELDS, "source": f"[r:{CHAT}]"}}, "source names what a forget took"),
        ({"op": "material", "item": number, "ref": CHAT}, "ref names what a forget took"),
        ({"op": "log", "item": number, "note": f"As said [r:{CHAT}]"}, "note anchors on what a forget took"),
    ]
    for change, why in refused:
        with pytest.raises(RefusedError, match=why):
            write(vault, change)
    write(vault, {"op": "log", "item": number, "note": "As said [r:nocolon] and [r:other:one]"})
    write(vault, {"op": "material", "item": number, "ref": "NoRid"})


def test_the_writer_refuses_a_listed_legacy_note_s_old_path(vault: Vault) -> None:
    number = add(vault, source=OTHER)
    forgotten.append(vault, forgotten.Forget(old_paths=[forgotten.old_path_hash("notes/old.md")], at=AT,
                                             machine="test-box"))  # fmt: skip
    with pytest.raises(RefusedError, match="ref names what a forget took"):
        write(vault, {"op": "material", "item": number, "ref": "notes/old.md"})
    write(vault, {"op": "material", "item": number, "ref": "notes/new.md"})


def test_a_meeting_shell_takes_no_point_answer_item_or_join_and_its_points_never_roll(
    vault: Vault, owner: Callable[..., AtConsole]
) -> None:
    first, second = weekly(vault, 1), weekly(vault, 2)
    write(vault, {"op": "point", "item": first, "fields": {"text": "The venue"}})
    forget_now_at(vault, owner, event(1))
    refused = [
        ({"op": "point", "item": first, "fields": {"text": "Again"}}, "it takes no text"),
        ({"op": "point_set", "item": first, "point": 1, "fields": {"answer": "Tuesday"}}, "it takes no answer"),
        ({"op": "point_item", "item": first, "point": 1}, "its points make no item"),
    ]
    for change, why in refused:
        with pytest.raises(RefusedError, match=f"{first} is a shell a forget left; {why}"):
            write(vault, change)
    (shell_add,) = [o for o in ops.read_all(vault.root).lines if o.op.op == "meeting" and o.op.number == 1]
    with pytest.raises(RefusedError, match="no meeting continues it"):
        write(vault, {"op": "meeting", "fields": {"subject": "Weekly 1:1", "start": "2026-08-03T09:00+00:00",
                                                  "end": "2026-08-03T09:30+00:00", "event": "calendar:new",
                                                  "continues": shell_add.op.id}})  # fmt: skip
    rolled = write(vault, {"op": "rollover"})
    assert rolled.ops == () and rolled.remarks == ()
    assert points_of(vault, second) == []
    write(vault, {"op": "point_set", "item": first, "point": 1, "fields": {"state": "dropped"}})


def test_a_stream_whose_ops_are_masked_has_its_status_file_rendered_again(
    tmp_path: Path, owner: Callable[..., AtConsole]
) -> None:
    made = folder_vault(tmp_path)
    path = made.root / CONFIG_FILE
    files = [RenderedFile(kind="status", stream="pilot", path="projects/pilot/STATUS.md")]
    config = VaultConfig.load(path).model_copy(update={"rendered": files})
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    git(made.home.top, "commit", "-qm", "Render the streams", "--", f"{made.home.prefix}{CONFIG_FILE}")
    vault = Vault.open(made.root)
    (vault.home.top / "projects" / "pilot").mkdir(parents=True)
    vault.write_run("chat", [make_record(1)], AT)
    write(vault, {"op": "stream", "fields": {"name": "pilot", "status": "active", "where": f"Agreed [r:{CHAT}]"}})
    status_file = vault.home.top / "projects" / "pilot" / "STATUS.md"
    assert "Agreed" in status_file.read_text(encoding="utf-8")
    plan = plan_of(vault, CHAT)
    assert plan.ledger.streams == 1
    assert "streams   1 stream op masked, the streams kept" in planning.lines(plan, screen=False)
    done = forget_now_at(vault, owner, CHAT)
    assert done.applied is not None and done.applied.streams == ["pilot"]
    assert done.applied.rendered == ("rendered   projects/pilot/STATUS.md",)
    text = status_file.read_text(encoding="utf-8")
    assert "Agreed" not in text and 'where: "[forgotten]"' in text


# -- the rule's parts, on ops made by hand -----------------------------------------------------------------------------


class Raw:
    """Raw as a forget leaves it: the rids in `gone` and nothing else."""

    def __init__(self, gone: Collection[str] = ()) -> None:
        self.taken = set(gone)

    def gone(self, rid: str) -> bool:
        return rid in self.taken

    def timed(self, rid: str) -> bool:
        return False

    def lands(self, rid: str, start: int, last: int) -> bool:
        return False

    def media(self, rid: str) -> bool:
        return False


class Docs:
    """The docs as a forget leaves them: those `removed` names gone, and refs to `dropped` dropped."""

    def __init__(self, removed: Collection[str] = (), dropped: Collection[str] = ()) -> None:
        self.gone = set(removed)
        self.dropped = set(dropped)

    def removed(self, doc_id: str) -> bool:
        return doc_id in self.gone

    def cited(self, refs: Collection[DocRef]) -> set[DocRef]:
        return {ref for ref in refs if ref.doc in self.dropped}


def matched(
    *made: ops.Op, gone: Collection[str] = (), listed: Collection[str] = (), **more: Any
) -> forget_ledger.Found:
    return forget_ledger.match(lines(*made), Raw(gone), Forgotten(ops=frozenset(listed), **more), Docs())


def read_as(*made: ops.Op, listed: Collection[str]) -> list[ops.Line]:
    """The lines as every reader reads them, each listed op's as its mask."""
    text = "\n".join(o.text() for o in made)
    return list(ops.parse(text, ops.rel_of(made[0]), frozenset(listed)).lines)


def test_an_unlisted_add_of_a_shell_s_event_is_masked_only_when_the_event_reads_on_the_family() -> None:
    shell, twin, other = meeting(10, event(1)), meeting(20, event(1), notes="From the desk"), meeting(30, event(2))
    read = read_as(shell, twin, other, listed=[ulid(10)])
    listed = Forgotten(ops=frozenset({ulid(10)}))
    # Another clone's add of a forgotten event reads on the family itself.
    assert forget_ledger.match(read, Raw([event(1)]), listed, Docs()).new == frozenset({ulid(20)})
    # One of a kept event is that record's own meeting, brought back: never joined to the shell by its event.
    assert forget_ledger.match(read, Raw(), listed, Docs()).new == frozenset()


def test_a_legacy_note_s_listed_old_path_and_a_marker_read_as_they_should() -> None:
    item = op("add", 1, fields={**ADD_FIELDS, "source": "notes/old.md"})
    gone = matched(item, old_paths=frozenset({forgotten.old_path_hash("notes/old.md")}))
    assert gone.shells == ("A-0001",)
    marked = op("add", 2, fields={**ADD_FIELDS, "source": "[forgotten:" + "0" * 32 + "]"})
    assert matched(marked).new == frozenset()


def test_a_hidden_shell_is_named_by_its_last_claim_and_a_hidden_meeting_by_its_first_add() -> None:
    item = op("add", 1, fields={**ADD_FIELDS, "source": CHAT})
    undone = op("undo", 2, 1, of=ulid(1))
    gone = matched(item, undone, gone=[CHAT])
    assert gone.shells == ("A-0001",) and gone.open == ()
    keyed = meeting(10, event(1))
    undo_meeting = op("undo", 11, 10, of=ulid(10))
    assert matched(keyed, undo_meeting, gone=[event(1)]).shells == ("M-0010",)


def test_a_meeting_shell_is_named_to_drop_only_while_it_holds_an_open_point() -> None:
    held, answered = meeting(10, event(1)), meeting(20, event(2))
    made = (held, point(11, 10), answered, point(21, 20), point_set(22, 20, 21, state="answered"))
    got = matched(*made, gone=[event(1), event(2)])
    assert got.shells == ("M-0010", "M-0020") and got.open == ("M-0010",)


def test_heads_walk_back_through_shells_to_the_live_meeting_a_point_came_from() -> None:
    first, second, third = meeting(10, event(1)), meeting(20, event(2)), meeting(30, event(3))
    raised = point(11, 10, "Ada: dates for the venue")
    copy_one, rolled_one = roll(21, 10, 11, 20)
    copy_two, rolled_two = roll(31, 20, 21, 30)
    made = (first, second, third, raised, copy_one, rolled_one, copy_two, rolled_two)
    assert matched(*made, gone=[event(2), event(3)]).heads == ("M-0030#1, rolled in from M-0010, open",)
    assert matched(*made, gone=[event(1), event(2), event(3)]).heads == ()


def test_an_op_on_an_object_this_docket_doesn_t_know_is_masked_only_for_its_words() -> None:
    later = op("gadget", 1, fields={"note": f"[r:{CHAT}]"})
    on_it = op("log", 2, 1, note=f"About it [r:{CHAT}]")
    assert matched(later, on_it, gone=[CHAT]).new == frozenset({ulid(1), ulid(2)})


def test_a_listed_op_is_never_new_and_a_set_of_only_kept_fields_on_a_shell_isn_t_named() -> None:
    item = op("add", 1, fields={**ADD_FIELDS, "source": CHAT})
    due = op("set", 2, 1, fields={"due": "2026-10-30"})
    gone = matched(item, due, gone=[CHAT])
    assert gone.new == frozenset({ulid(1)})
    assert matched(item, due, listed=[ulid(1)]).new == frozenset()


def test_a_stream_op_on_the_family_is_counted_never_named() -> None:
    stream = op("stream", 1, fields={"name": "pilot", "status": "active", "where": f"Agreed [r:{CHAT}]"})
    gone = matched(stream, gone=[CHAT])
    assert gone.streams == 1 and gone.kept == () and gone.shells == ()
    assert forget_ledger.streams(lines(stream, op("set", 2, 1, fields={"next": "x"})), {ulid(2)}) == ["pilot"]


def test_a_ref_the_docs_drop_masks_its_op() -> None:
    item = op("add", 1, fields={**ADD_FIELDS, "notes": f"[d:{NOTE}@1#3]"})
    got = forget_ledger.match(lines(item), Raw(), Forgotten(), Docs(dropped=[NOTE]))
    assert got.shells == ("A-0001",)


def test_an_anchor_in_a_list_and_a_locator_that_reads_as_nothing() -> None:
    tagged = op("add", 1, fields={**ADD_FIELDS, "tags": ["kept", f"[r:{CHAT}]"]})
    odd = op("material", 3, 2, ref="NoRid")
    item = op("add", 2, fields={**ADD_FIELDS, "source": OTHER})
    assert matched(tagged, item, odd, gone=[CHAT]).new == frozenset({ulid(1)})


def test_a_point_an_anchor_matched_or_whose_text_one_set_takes_its_copies() -> None:
    first, second, third = meeting(10, event(1)), meeting(20, event(2)), meeting(30, event(3))
    anchored = point(11, 10, f"Ada said so [r:{CHAT}]")
    copy, rolled = roll(21, 10, 11, 20)
    plain = point(12, 10, "The venue")
    reworded = point_set(13, 10, 12, text=f"The venue, as said [r:{CHAT}]")
    copy_two, rolled_two = roll(31, 10, 12, 30)
    got = matched(first, second, third, anchored, copy, rolled, plain, reworded, copy_two, rolled_two, gone=[CHAT])
    assert got.new == frozenset({ulid(11), ulid(21), ulid(12), ulid(13), ulid(31)})
    assert got.shells == ()
    counts = ((10, "3 ops"), (20, "1 op"), (30, "1 op"))
    assert got.kept == tuple(f"M-{n:04d}: {k} masked; the rest kept" for n, k in counts)


def test_a_point_linked_to_a_shell_item_isn_t_suspected_without_rids() -> None:
    assert not forget_ledger.suspect(point(3, 2, item=ulid(1)).text(), Forgotten(ops=frozenset({ulid(1)})))


def test_the_import_names_a_line_with_text_on_a_shell_meeting(vault: Vault) -> None:
    shell, said = meeting(10, event(1)), point(11, 10, "The venue")
    hidden, undone, on_hidden = meeting(20, event(2)), op("undo", 21, 20, of=ulid(20)), op("log", 22, 20, note="x")
    folded = folding.fold(lines(shell, said, hidden, undone, on_hidden))
    fresh = lines(said, on_hidden)
    refused = imports._on_shells(folded, fresh, frozenset({ulid(10), ulid(20)}))
    assert [r.split(": ", 1)[1] for r in refused] == [
        "holds text on M-0010, a shell a forget left; leave it out of the file",
        "holds text on M-0020, a shell a forget left; leave it out of the file",
    ]


def test_a_keys_only_list_reads_no_op_file(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    add(vault, source=CHAT)
    forgotten.append(vault, forgotten.Forget(keys=["ab" * 32], at=AT, machine="test-box"))
    read: list[str] = []
    real = Path.read_bytes

    def watched(path: Path) -> bytes:
        if "ledger" in path.parts:
            read.append(path.name)
        return real(path)

    monkeypatch.setattr(Path, "read_bytes", watched)
    done = forget_apply.apply(vault, KeyStore(vault.home))
    assert read == [] and done.ops == 0


SUSPECTS = [
    (op("log", 3, 1, note="plain").text(), True),
    (op("log", 3, 2, note="plain").text(), False),
    (point(3, 2, **{"from": ulid(1)}).text(), True),
    (point_set(3, 2, 1, state="answered", answer="Tuesday").text(), True),
    # A mask leaves these as they are, so the rule never lists them: a shell's drop, its triage, a point's state.
    (point_set(3, 2, 1, state="answered").text(), False),
    (op("drop", 3, 1, note=MARK).text(), False),
    (op("triage", 3, 1, verdict="chase").text(), False),
    (op("log", 3, 2, note=f"Said [r:{CHAT}@0:01]").text(), True),
    (op("log", 3, 2, note=f"Said [r:{OTHER}] [d:{NOTE}@1#2]").text(), False),
    (op("log", 1, 1, note="listed").text(), False),
    (op("material", 3, 2, ref=CHAT).text(), True),
    (op("material", 3, 2, ref="notes/old.md").text(), True),
    (op("material", 3, 2, ref="notes/new.md").text(), False),
    (op("material", 3, 2, ref="NoRid").text(), False),
    (op("add", 3, fields={**ADD_FIELDS, "source": OTHER}).text(), False),
    ('{"item":"' + ulid(1) + '"', False),
    ('[{"source":"' + CHAT + '"}]', False),
    ("", False),
]


@pytest.mark.parametrize(("line", "suspect"), SUSPECTS, ids=[str(n) for n in range(len(SUSPECTS))])
def test_a_line_an_op_file_gained_is_suspected_from_the_list_alone(line: str, suspect: bool) -> None:
    listed = Forgotten(
        rids=frozenset({rid_hash(CHAT)}), old_paths=frozenset({forgotten.old_path_hash("notes/old.md")}),
        ops=frozenset({ulid(1)}),
    )  # fmt: skip
    assert forget_ledger.suspect(line, listed) is suspect


def test_a_list_of_op_ids_alone_never_reads_a_locator() -> None:
    listed = Forgotten(ops=frozenset({ulid(1)}))
    assert not forget_ledger.suspect(op("material", 3, 2, ref=CHAT).text(), listed)
    assert forget_ledger.suspect(op("log", 3, 1, note="plain").text(), listed)


def test_the_screens_count_past_twenty_and_say_two_shells_as_shells(tmp_path: Path) -> None:
    many = tuple(f"A-{n:04d}" for n in range(1, 23))
    plan = planning.Plan(
        "rids", (CHAT,), None, tmp_path, tmp_path, taken=[planning.Taken(CHAT, "whole")],
        ledger=forget_ledger.Found(
            new=frozenset({ulid(1)}), shells=("A-0001", "M-0001"), open=("A-0001", "A-0002"),
            kept=tuple(f"{n}: 1 op masked; the rest kept" for n in many),
            heads=tuple(f"M-{n:04d}#1, rolled in from M-0001, open" for n in range(2, 24)),
        ),
    )  # fmt: skip
    said = planning.lines(plan, screen=False)
    assert "ledger    1 op masked; A-0001, M-0001 are shells now" in said
    assert "drop      A-0001, A-0002 are shells left open, to drop" in said
    assert sum(line.startswith("kept      A-") for line in said) == 20 and "kept      and 2 more" in said
    assert sum(line.startswith("raise     M-") for line in said) == 20 and "raise     and 2 more" in said


def test_only_an_item_point_item_made_goes_with_its_point() -> None:
    shell = meeting(10, event(1))
    raised = point(11, 10, "Ada: dates for the venue")
    made = op("add", 12, fields={**ADD_FIELDS, "source": f"meeting:{ulid(10)}"}, batch=ulid(12))
    link = op("point_set", 13, 10, point=ulid(11), fields={"item": ulid(12)}, batch=ulid(12))
    own = op("add", 14, fields={**ADD_FIELDS, "source": OTHER}, batch=ulid(14))
    linked = op("point_set", 15, 10, point=ulid(11), fields={"item": ulid(14)}, batch=ulid(14))
    got = matched(shell, raised, made, link, own, linked, gone=[event(1)])
    assert ulid(12) in got.new and ulid(14) not in got.new
    assert got.shells == ("A-0012", "M-0010")
