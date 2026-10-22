"""The invented worlds `uv run devkit ui` serves for the screenshot loop (DR-0712), built with the test kits' own
records, people and ledger: run as a script, `python ui_world.py <folder> <world>`, in the environment devkit ui gives
it (its own config and state folders, the failing keyring), which is where the file token it makes lands.

| World | What |
|---|---|
| `empty` | a new vault: no items, nothing staged, a live source never synced |
| `week` | a working week: day_kit's ledger and more items beside it, one whose snooze ended yesterday, the files |
| | its materials name, staging_kit's three records waiting, a live source due a sync; meetings around today, |
| | one under way, a recording, a point rolled on, and Ben's entity and brief |
| `meetings` | the week with its index built, so a meeting's people link to their briefs from the first read |
| `today` | the meetings world with a second live source, mail, both synced this morning, a follow-up later today |
| | and an item on the meeting under way, so Today's next meeting has prep of both kinds |
| `stale` | today's world with chat synced three days ago and mail never, so both are due a sync |
| `search` | the index fixture: calls, chat, mail and the calendar with their minutes, notes and entities, indexed |
| `recording` | recording_kit's calls, one held, the yard tour's audio stored with Whisper's transcript beside the |
| | recorder's and a stretch cut, a correction on it, indexed |
| `briefs` | the index fixture with briefs_kit's four briefs, stale, current and orphaned, and decisions_kit's on |
| | fleet management: two waiting, one current over the one it superseded, one rejected; indexed |
| `staging` | staging_kit's sources with more waiting: the supplier call to cut, a mail redacted by a rule turned on |
| | since, one Claude cut, one held back at the last promotion, a call on hold, memos ready, and the held |
| | recordings with their import folder, one held again after its release |
| `curate` | curate_kit's recordings, mail and notes with Northwind a customer, indexed, and a round started on |
| | this machine: one card accepted, one rejected, the rest open |
| `sync` | a vault in a consuming project: calls synced an hour ago, memo whole yesterday then dropped twice and |
| | owing ids, another machine's mark, chat refused for want of its skill, a folder source |
| `settings` | settings_kit's sources, lists and pattern, memos by Ada stored and indexed, two waiting at the gate |
| `edited` | the settings world with vault.yaml edited by hand and not committed: credentials off |

Invented people only: Ada Example, Ben Sample and Cleo Example.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from briefs_kit import brief, fill_briefs
from curate_kit import NORTHWIND, fill_curate
from day_kit import ZONE, add, day, seed
from decisions_kit import fill_decisions
from docket_testkit import fill_index_vault, fixture_records, index_config, make_config, make_record, sh, write_mapper
from ledger_kit import write_ops
from recording_kit import TOUR_POLISHED, doc, fill_recording_vault, recording_sources, with_audio
from staging_kit import PHONE, TALK, mail, memo, transcript
from staging_kit import config as staging_config

from docket import recall
from docket.curate import batch
from docket.index import build as index_build
from docket.ledger import ops, store
from docket.service.history import RunHistory, RunLine
from docket.service.sync import MARKER_LINE
from docket.service.token import Tokens
from docket.sources import local
from docket.vault import cursors, holds, releaselog
from docket.vault.config import NeverStore, Redaction, RedactionPattern, SourceConfig, VaultConfig
from docket.vault.cuts import Cut
from docket.vault.docstore import DocStore
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.releaselog import LoggedCut, Release
from docket.vault.staging import Staging
from docket.vault.vault import Vault

WORLDS = (
    "empty", "week", "meetings", "today", "stale", "search", "recording", "briefs", "staging", "curate", "sync",
    "settings", "edited",
)  # fmt: skip
# The worlds whose vault sits in a consuming project, beside its sources' skills and mappers.
PROJECTS = ("sync", "settings", "edited")
# The worlds Today is shot on: the meetings world, a second live source and the day's syncs.
DAYS = ("today", "stale")
MACHINE = "ui-box"
PLAN_TEXT = """# The plan

Book the room first, then the caterer.

- Room: the long hall, 40 seats, the 12th or the 19th
- Caterer: two menus, the second one vegetarian
- Badges: printed the day before, collected at the door
"""


def config(world: str = "week") -> VaultConfig:
    if world == "sync":
        return sync_config()
    if world in ("settings", "edited"):
        return settings_config()
    if world == "staging":
        found = staging_config()
        found.primary_machine = MACHINE
        return found
    if world in ("search", "briefs"):
        found = index_config()
        found.primary_machine = MACHINE
        return found
    made = make_config()
    if world == "recording":
        made.primary_machine = MACHINE
        made.sources = recording_sources()
        return made
    made.primary_machine = MACHINE
    # A live source: one the capture hook posts for, so the Sync page and the sidebar mark it due until it syncs.
    made.sources["chat"] = SourceConfig(tools=["read_channel"])
    made.sources["memo"] = SourceConfig()
    if world in DAYS:
        # Today's bar names each live source's last sync: two of them read as a working vault's.
        made.sources["mail"] = SourceConfig(tools=["read_mail"])
    return made


def busier() -> list[dict[str, Any]]:
    """More of the week beside day_kit's seven, so a page shows a list as full as a working day's: calls of every
    kind, dues in every group, one snoozed and one closed."""
    ops: list[dict[str, Any]] = [
        add("brief", "reply", "Answer Ben about the pilot budget", "Ben Sample", "pilot", due=day(-4)),
        {"op": "triage", "item": "$brief", "verdict": "me"},
        add("deck", "task", "Send the deck to the venue", "Ada Example", "ops", due=day(-1)),
        {"op": "triage", "item": "$deck", "verdict": "claude"},
        add("badge", "task", "Order badges for the visit", "Cleo Example", "ops", due=day(0)),
        {"op": "triage", "item": "$badge", "verdict": "claude"},
        add("caterer", "decision", "Choose the caterer's second menu", "Ben Sample", "ops", due=day(1)),
        {"op": "triage", "item": "$caterer", "verdict": "discuss"},
        add("slides", "task", "Draft the pilot's review slides", "Ada Example", "pilot", due=day(2)),
        add("parking", "waiting", "Parking passes from the venue", "Cleo Example", "ops", due=day(2)),
        {"op": "triage", "item": "$parking", "verdict": "chase"},
        add("visit", "invite", "The supplier's site visit, Thursday", "Ben Sample", "pilot", due=day(3)),
        add("minutes", "task", "Write up the steering minutes", "Ada Example", "pilot", due=day(4)),
        {"op": "triage", "item": "$minutes", "verdict": "claude"},
        add("budget", "decision", "Sign off the spring budget", "Cleo Example", "ops", due=day(12)),
        {"op": "triage", "item": "$budget", "verdict": "me"},
        add("survey", "waiting", "Survey results from the pilot group", "Ben Sample", "pilot", due=day(20)),
        add("contract", "reply", "Reply on the contract's notice period", "Ada Example", "ops"),
        {"op": "triage", "item": "$contract", "verdict": "discuss"},
        add("photos", "task", "Collect the event photos", "Cleo Example", "ops"),
        add("printer", "task", "Fix the printer settings", "Ben Sample", "ops"),
        {"op": "snooze", "item": "$printer", "until": day(9)},
        add("invoice", "task", "Pay the venue's deposit invoice", "Ada Example", "ops"),
        {"op": "done", "item": "$invoice", "note": "paid by card"},
    ]
    return ops


def woke(vault: Vault) -> None:
    """An item snoozed to yesterday, so it is open again with its snooze ended: the writer snoozes only to a day after
    today, so its snooze op is written as the writer would have written it yesterday."""
    written = store.write(
        vault, [add("permit", "task", "Renew the parking permit", "Cleo Example", "ops")], by="claude", via="cli"
    )
    added = written.ops[-1]
    today = datetime.now(UTC).astimezone(ZONE).date()
    (minted,) = ops.mint(added.id, datetime.now(UTC), 1)
    snooze = ops.Op(
        schema_version=1,
        id=minted,
        item=added.item,
        op="snooze",
        day=today,
        machine=MACHINE,
        by="claude",
        via="cli",
        until=today - timedelta(days=1),
    )
    vault.commit(write_ops(vault.root, [snooze]), "A snooze that ended")


BEN = """id: ben-sample
type: person
name: {en: Ben Sample}
aliases:
  - {text: Ben}
ids: {email: ben@example.invalid}
"""


def meeting(name: str, subject: str, start: datetime, minutes: int, **fields: Any) -> dict[str, Any]:
    span = {"subject": subject, "start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat()}
    return {"op": "meeting", "as": name, "fields": span | fields}


def point(on: str, text: str, **fields: Any) -> dict[str, Any]:
    return {"op": "point", "item": f"${on}", "fields": {"text": text, **fields}}


def meetings(vault: Vault) -> None:
    """The weeks around today on Meetings, beside day_kit's Pilot sync tomorrow: a stand-up done this morning with
    nothing on it, a budget review under way now, yesterday's supplier call with its recording, its outcome and its
    open point rolled on to next week's, a cancelled and a declined one ahead, and a retro twelve days back whose
    point is still open. Ben has an entity and a brief, so his name links to it."""
    now = datetime.now(UTC).astimezone(ZONE).replace(minute=0, second=0, microsecond=0)
    today = now.replace(hour=0)

    def on(days: int, hour: int) -> datetime:
        return today + timedelta(days=days, hours=hour)

    people = {"attendees": ["Ben Sample", "Ada Example", "Cleo Example"], "organiser": "Ben Sample"}
    changes: list[dict[str, Any]] = [
        meeting("standup", "Daily stand-up", now - timedelta(hours=3), 15, event="cal:standup"),
        meeting(
            "budget",
            "Budget review with Ben",
            now - timedelta(hours=1),
            120,
            event="cal:budget",
            stream="ops",
            where="Room 2, second floor",
            response="tentative",
            **people,
        ),
        point("budget", "Ben: the spring numbers"),
        point("budget", "Who signs off the deposit"),
        meeting(
            "supplier",
            "Supplier call",
            on(-1, 15),
            60,
            event="cal:supplier-1",
            stream="pilot",
            outcome="Cleo sends the offer in writing by Friday.",
            attendees=["Cleo Example", "Ada Example"],
        ),
        point("supplier", "The offer in writing"),
        {"op": "material", "item": "$supplier", "ref": "calls:rec-0100", "label": "recording"},
        {"op": "material", "item": "$supplier", "ref": "notes/plan.md", "label": "The plan"},
        meeting("next", "Supplier call", on(6, 15), 60, event="cal:supplier-2", stream="pilot"),
        meeting("demo", "Vendor demo", on(2, 11), 45, event="cal:demo", response="cancelled"),
        meeting("walk", "Venue walk", on(3, 14), 60, event="cal:walk", response="declined", where="The long hall"),
        meeting("retro", "Design retro", on(-12, 10), 60, event="cal:retro"),
        point("retro", "What the pilot taught us"),
    ]
    store.write(vault, changes, by="claude", via="cli")
    proposed = [
        {"op": "point", "item": "M-0003", "fields": {"text": "Ask about the printer budget", "proposed": True}},
        {"op": "point_set", "item": "M-0003", "point": "2", "fields": {"answer": "Cleo, once the quote is in"}},
        {"op": "rollover"},
    ]
    store.write(vault, proposed, by="sync", via="tool")
    vault.write_run("calls", [r for r in fixture_records("calls") if r.rid == "calls:rec-0100"])
    said = make_record(7, text="Ben Sample sends the spring numbers on Friday.")
    vault.write_run("chat", [said])
    entity = vault.root / "entities" / "person" / "ben-sample.yaml"
    entity.parent.mkdir(parents=True, exist_ok=True)
    entity.write_text(BEN, encoding="utf-8", newline="\n")
    vault.commit(["entities/person/ben-sample.yaml"], "Ben's entity")
    lines = [[f"- Ben sends the spring numbers on Friday. [r:{said.rid}]"], [], []]
    recall.doc_put(vault, brief("ben-sample", "Ben Sample", [said.rid], lines))


def the_day(vault: Vault, world: str) -> None:
    """Today's worlds: a follow-up later today with a point Claude proposed, an item on the budget review under way,
    and each live source's last sync, this morning's for `today`, three days back and never for `stale`."""
    now = datetime.now(UTC).astimezone(ZONE).replace(minute=0, second=0, microsecond=0)
    changes = [
        meeting("follow", "Supplier follow-up", now + timedelta(hours=3), 30, event="cal:follow", stream="pilot"),
        point("follow", "The written offer", proposed=True),
        add("numbers", "task", "Bring the spring numbers", "Ben Sample", "ops", due=day(0), meeting="M-0003"),
    ]
    store.write(vault, changes, by="claude", via="cli")
    clock = datetime.now(UTC)
    marks = {"chat": clock - timedelta(minutes=40), "mail": clock - timedelta(hours=3)}
    if world == "stale":
        marks = {"chat": clock - timedelta(days=3)}
    for source, mark in marks.items():
        cursors.save(vault, source, cursors.Cursor(mark=mark))


TOUR_NOTE = "recording-note:2026-10-14-yard-tour"
CORRECTION = "correction:2026-10-14-yard-tour"
THURSDAY = f"- The new gate opens on Thursday. [r:{TOUR_POLISHED}@00:20]"


def recordings(vault: Vault) -> None:
    """recording_kit's calls with the tour's audio stored and Whisper's transcript beside the recorder's, its note
    and a correction of the note's line on the gate, so the Recording page shows a doc of each kind it lists."""
    fill_recording_vault(vault)
    with_audio(vault)
    note = [
        "# Yard tour",
        "",
        "- Ada welcomed everyone to the yard tour. @00:00",
        THURSDAY,
        f"- The fence is done by the end of the month. [r:{TOUR_POLISHED}@01:00]",
    ]
    recall.doc_put(vault, doc(TOUR_NOTE, [TOUR_POLISHED], note))
    stored = DocStore(vault).get(TOUR_NOTE)
    assert stored is not None
    n = next(n for n, line in stored.lines() if line == THURSDAY)
    fix = [
        "# Yard tour: a correction",
        "",
        f"- The gate opens on Friday, as Ben said. [r:{TOUR_POLISHED}@00:20] [d:{TOUR_NOTE}@{stored.meta.rev}#{n}]",
    ]
    recall.doc_put(vault, doc(CORRECTION, [TOUR_POLISHED], fix))


def staged(vault: Vault, exports: Path) -> None:
    """What waits at the gate on a working day: a call to cut, mail, a mail a rule turned on since redacts, one Claude
    cut, one held back at the last promotion, a call on hold, two memos ready; and the held recordings, read from an
    import folder, one released and held again after a merge."""
    gate = Gate(vault)
    ring = mail("mail-2", title=f"Ring Ben on {PHONE}", text=f"Ben,\nRing me on {PHONE} after ten.\nAda")
    gate.stage([
        transcript(),
        mail(),
        ring,
        mail("mail-3", title="Badges for the visit", text="Forty badges, printed the day before.\nCleo"),
        transcript("rec-2", title="Pilot steering call", segments=TALK[:3]),
        transcript("rec-7", title="A call on hold"),
        memo(),
        memo("m-2", title="Venue rates", text="The long hall is 400 a day."),
        memo("m-3", title="Caterer's menus", text="Two menus, the second vegetarian."),
    ])  # fmt: skip
    keys = {e.staged.record.rid: e.key for e in gate.list().entries}
    gate.classify(keys["calls:rec-2/transcript"], "work", [Cut(start=12_000, end=30_400, unit="ms")], by="claude")
    shown = gate.show(keys["memo:m-3"])
    Staging(vault.root, vault.home).write(shown.entry.staged.model_copy(update={"problem": "a cut didn't fit"}))
    # A rule turned on since the mail was staged: the page shows it as promotion will write it.
    found = VaultConfig.load(vault.root / "vault.yaml")
    found.redaction.phone = True
    (vault.root / "vault.yaml").write_text(found.dump(), encoding="utf-8", newline="\n")
    vault.commit(["vault.yaml"], "Config: phone redaction on")
    folder = exports / "calls"
    folder.mkdir(parents=True)
    local.record(vault, "calls", folder)
    line = Release.model_validate(
        {"id": "rec-cut-03", "cuts": [LoggedCut(start=1000, end=2000)], "keep": [], "at": "2026-10-14T09:00:00Z",
         "machine": MACHINE}
    )  # fmt: skip
    releaselog.add(vault, "calls", line)
    # A merge brought rec-cut-03's hold back; an add would leave a released id out, so the list is written by hand.
    (vault.root / holds.HOLDS).mkdir(parents=True, exist_ok=True)
    (vault.root / holds.HOLDS / "calls.txt").write_text("rec-7\nrec-cut-01\nrec-cut-03\n", encoding="utf-8")


NORTHWIND_ENTITY = "id: northwind\ntype: customer\nname: {en: Northwind}\n"


def curated(vault: Vault) -> None:
    """curate_kit's vault with Northwind a customer, so its misheard spelling is an alias card, and a round started on
    this machine: its first new card accepted and its last rejected, so the log undoes and the stoplist lifts."""
    fill_curate(vault)
    path = vault.root / NORTHWIND
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NORTHWIND_ENTITY, encoding="utf-8", newline="\n")
    vault.commit([NORTHWIND], "An entity: Northwind")
    found = batch.start(vault)
    new = [c for c in found.cards if c.kind == "new"]
    batch.accept(vault, found, new[:1])
    batch.reject(vault, found, new[-1:])


# The runs the sync world's history holds, by fixed ids so a state can open one.
CALLS_RUN = "run-20261020T081500-a1b2c3d4e5f60718"
MAPPER_RUN = "run-20261020T063000-0f1e2d3c4b5a6978"
COUNT_RUN = "run-20261019T171000-9a8b7c6d5e4f3021"
MEMO_RUN = "run-20261019T071000-1122334455667788"
SKILL = f"""---
name: {{name}}
description: Harvest invented {{name}} records for Docket's screenshots.
---

{MARKER_LINE}

# {{name}}

List newest first, then fetch each one.
"""
SYNC_MAPPER = "def map_run(run):\n    return {'contract': 1, 'records': []}\n"


def project_of(folder: Path) -> Path:
    """The consuming project a world's vault sits in: the throwaway folder devkit ui made, as a repo, so a source's
    skill and mapper sit outside the vault, where run_open and the runner look for them."""
    project = folder.parent
    sh(project, "git", "init", "-q", "-b", "main")
    return project


def sync_config() -> VaultConfig:
    """memo and calls with their skills and mappers, chat with its tools and no skill, a folder source."""
    made = make_config()
    made.primary_machine = MACHINE
    made.sources["memo"] = SourceConfig(
        sensitivity="mixed", mapper=".claude/skills/sources/memo/mapper.py", tools=["list_memos", "get_memo"]
    )
    made.sources["calls"] = SourceConfig(mapper=".claude/skills/sources/calls/mapper.py", tools=["list_calls"])
    made.sources["chat"] = SourceConfig(mapper=".claude/skills/sources/chat/mapper.py", tools=["read_channel"])
    made.sources["documents"] = SourceConfig(folder="docs")
    return made


def run_line(run: str, source: str, ended: datetime, minutes: int, **fields: Any) -> RunLine:
    opened = ended - timedelta(minutes=minutes)
    return RunLine.model_validate({"run": run, "source": source, "opened": opened, "ended": ended, **fields})


def synced(vault: Vault, project: Path) -> None:
    """A working week's syncs: calls synced an hour ago with two ids expired at its close; memo whole yesterday
    morning, then dropped twice since, its mapper failing and a count short, owing two ids to retry and one waiting
    for a final run; another machine's mark of memo two days back; chat refused before any work, for want of its
    skill."""
    for source in ("memo", "calls"):
        write_mapper(project, source, SYNC_MAPPER)
        skill = project / ".claude" / "skills" / "sources" / source / "SKILL.md"
        skill.write_text(SKILL.format(name=source), encoding="utf-8", newline="\n")
    write_mapper(project, "chat", SYNC_MAPPER)
    now = datetime.now(UTC).replace(microsecond=0)
    today = now.date()
    cursors.save(vault, "calls", cursors.Cursor(mark=now - timedelta(hours=1)))
    cursors.save(
        vault,
        "memo",
        cursors.Cursor(
            mark=now - timedelta(hours=26),
            retry={
                "m-017": cursors.Retry("m-017", "rate-limited", today - timedelta(days=3)),
                "m-009": cursors.Retry("m-009", "gone", today - timedelta(days=10)),
            },
            waiting={"m-021": cursors.Waiting("m-021", "summary", today - timedelta(days=8))},
        ),
    )
    other = cursors.file_path(vault.root, "desk-2")
    other.write_text(cursors.dump({"memo": cursors.Cursor(mark=now - timedelta(days=2))}), encoding="utf-8")
    vault.commit([f"{cursors.STATE}/{other.name}"], "Sync: desk-2's cursors")
    history = RunHistory(lambda: vault.home)
    history.add(
        run_line(
            MEMO_RUN, "memo", now - timedelta(hours=26), 6, outcome="closed", kind="closed", captures=14,
            bytes=61_000, staged={"pending": 5, "ready": 0, "skipped": 2}, mark={"moved": True,
            "after": now - timedelta(hours=26)}, owed={"retry": 2, "waiting": 1}, waiting=["m-021"], waiting_count=1,
        )
    )  # fmt: skip
    history.add(
        run_line(
            COUNT_RUN, "memo", now - timedelta(hours=14), 3, outcome="dropped", kind="count", captures=3,
            bytes=9_000, why="the manifest counts 4 capture(s) and 3 arrived",
        )
    )  # fmt: skip
    history.add(
        run_line(
            MAPPER_RUN, "memo", now - timedelta(hours=3), 2, outcome="dropped", kind="mapper", captures=6,
            bytes=22_000, why="the mapper failed: KeyError in map_run, line 14",
        )
    )  # fmt: skip
    history.add(
        run_line(
            CALLS_RUN, "calls", now - timedelta(hours=1), 4, outcome="closed", kind="closed", captures=9,
            bytes=37_000, staged={"pending": 0, "ready": 3, "skipped": 1}, mark={"moved": True,
            "after": now - timedelta(hours=1)}, owed={"retry": 0, "waiting": 0}, expired=["c-301", "c-302"],
            expired_count=2,
        )
    )  # fmt: skip


SETTINGS_MAPPER = "def map_run(run):\n    return {'contract': 1, 'records': []}\n"


def settings_config() -> VaultConfig:
    """settings_kit's: a mixed calls with its mapper, a work memo with none, a folder source, invented never-store
    values and a pattern of the owner's."""
    made = make_config()
    made.primary_machine = MACHINE
    made.sources["calls"] = SourceConfig(
        sensitivity="mixed", mapper=".claude/skills/sources/calls/mapper.py", tools=["list_calls", "get_call"]
    )
    made.sources["memo"] = SourceConfig(tools=["get_memo"])
    made.sources["documents"] = SourceConfig(folder="docs", exclude=["archive/*"])
    made.never_store = NeverStore(
        people=["Cleo Test", "Dora #2"], containers=["chat:garden"], keywords=["Project: Phoenix"]
    )
    made.redaction = Redaction(patterns=[RedactionPattern(name="ticket-id", pattern=r"TCK-\d{6}")])
    return made


def settled(vault: Vault, project: Path, world: str) -> None:
    """The Settings worlds: calls' mapper in place; memos by Ada stored and indexed, so an add's question counts what
    already holds her, and two waiting at the gate; for `edited`, vault.yaml edited by hand and not committed,
    credentials off, which isn't in force until committed."""
    write_mapper(project, "calls", SETTINGS_MAPPER)
    run = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    vault.write_run("memo", [settings_memo(1, "Ada Example", "Lunch with the supplier on Friday."),
                             settings_memo(2, "Ben Sample", "The day rates, attached.")], run)  # fmt: skip
    Gate(vault).stage([settings_memo(3, "Ada Example", "Dinner plans."), settings_memo(4, "Ben Sample", "Badges.")])
    if world == "edited":
        found = VaultConfig.load(vault.root / "vault.yaml")
        found.redaction.credentials = False
        (vault.root / "vault.yaml").write_text(found.dump(), encoding="utf-8", newline="\n")


def settings_memo(n: int, author: str, text: str) -> RawRecord:
    return make_record(
        n, rid=f"memo:m-{n}", source="memo", kind="memo", container=f"memo:m-{n}", author=author, text=text
    )


def build(folder: Path, world: str) -> Vault:
    if world not in WORLDS:
        raise ValueError(f"no world {world!r}; the worlds are {', '.join(WORLDS)}")
    project = project_of(folder) if world in PROJECTS else None
    vault = Vault.init(folder, config(world), MACHINE)
    if project is not None and world == "sync":
        synced(vault, project)
    if project is not None and world != "sync":
        settled(vault, project, world)
    if world in ("week", "meetings", *DAYS):
        Gate(vault).stage([transcript(), mail(), memo()])
        store.write(vault, seed(), by="claude", via="cli")
        store.write(vault, busier(), by="claude", via="cli")
        woke(vault)
        notes = vault.home.top / "notes"
        notes.mkdir(exist_ok=True)
        (notes / "plan.md").write_text(PLAN_TEXT, encoding="utf-8", newline="\n")
        (notes / "costs.xlsx").write_bytes(b"PK\x03\x04 invented")
        meetings(vault)
    if world in DAYS:
        the_day(vault, world)
    if world in ("search", "briefs"):
        fill_index_vault(vault)
    if world == "briefs":
        fill_briefs(vault)
        fill_decisions(vault)
    if world == "recording":
        recordings(vault)
    if world == "staging":
        staged(vault, folder.parent / "exports")
    if world == "curate":
        curated(vault)
    if world in ("meetings", *DAYS):
        # The op files an hour old, as files no write just wrote are, so the index takes them as settled and a read
        # finds nothing to catch up.
        hour_ago = time.time_ns() - 3600 * 10**9
        for path in (vault.root / ops.OPS_DIR).iterdir():
            os.utime(path, ns=(hour_ago, hour_ago))
    if world in ("meetings", "search", "recording", "briefs", "settings", "edited", *DAYS):
        index_build.rebuild(vault)
    return vault


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        print("usage: ui_world.py <folder> <world>", file=sys.stderr)
        return 2
    build(Path(argv[0]), argv[1])
    # The service reads its token from the store install recorded; with the failing keyring that is a file.
    Tokens().ensure()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
