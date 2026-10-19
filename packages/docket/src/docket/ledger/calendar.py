"""Meetings from calendar records (DR-0646 to DR-0652): the core turns each record of kind `event` into the ledger's
meeting op keyed by its rid, in one write by sync that never refuses.

What the core reads from an event record, a one-way door every calendar mapper writes to (DR-0646):

| Meeting field | From the record |
|---|---|
| event | the rid, one per occurrence, which a move or a rename keeps |
| series | the thread, when there is one |
| start, end | ts and native.end, in vault.yaml's zone with that moment's offset (DR-0648) |
| subject | the title, a `Canceled: ` start dropped from a cancelled one; `(no subject)` for none |
| organiser | the author's name without its `<address>`, or the address alone |
| where | the first `Where: ` line of the text's header, its lines before the first blank one |
| response | `cancelled` when the header has a `Cancelled` line, else its first `Response: ` line |
| attendees | the names in `to` |

Left out, named with why: an all-day record (`native.all_day` true), a derived one, one with no end or an end before
its start, one media's screen or a hold keeps out now (DR-0650), and in the writer a cancelled one no meeting holds or
joins. A field the record doesn't carry is never cleared.

A promotion's records drive sets; the catch-up's own records, read from raw, only add and join (DR-0647). The rids a
promotion notes as pending, in this clone's private folder, are replayed first by the next step, each at raw's latest
version, sets included, and cleared once the step's write commits or has nothing to write.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from docket.index import build
from docket.ledger import ops, store
from docket.ledger.ops import Op
from docket.ledger.store import CalendarWritten, Event, Outcome
from docket.media.screen import refusal
from docket.media.staged import read_withheld
from docket.vault import forgotten, holds
from docket.vault.records import RawRecord
from docket.vault.spans import RawLookup
from docket.vault.staging import Staging
from docket.vault.vault import Vault

KIND = "event"
# The catch-up's own pass reaches back this far by default: a meeting older than that is the archive's (DR-0641).
SINCE = timedelta(days=14)
CANCELLED = re.compile(r"^(?:Canceled|Cancelled): ?", re.IGNORECASE)
ADDRESS = re.compile(r"<([^<>]*)>")
NO_SUBJECT = "(no subject)"
WHERE = "Where: "
RESPONSE = "Response: "
CANCELLED_LINE = "Cancelled"
# The responses a header's line may give; a cancellation comes from its own line.
RESPONSES = tuple(r for r in ops.RESPONSES if r != "cancelled")


@dataclass(frozen=True)
class Stepped:
    """What a calendar step did: an outcome per record, in the order written, then the records left out before the
    write; its ops and commit; and the pending rids it replayed."""

    outcomes: tuple[Outcome, ...]
    ops: tuple[Op, ...] = ()
    commit: str | None = None
    replayed: tuple[str, ...] = ()
    dry_run: bool = False
    counts: dict[str, int] = field(default_factory=dict[str, int])


# -- the record ---------------------------------------------------------------------------------------------------


def header(text: str) -> tuple[str | None, str | None, bool]:
    """The place, the response and whether it is cancelled, from the text's header: its lines before the first blank
    one, the first `Where: ` and `Response: ` lines and a `Cancelled` line (DR-0646). A body goes after it, never
    read."""
    where: str | None = None
    response: str | None = None
    seen_where = seen_response = cancelled = False
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if not line.strip():
            break
        if line.startswith(WHERE) and not seen_where:
            seen_where, where = True, line[len(WHERE) :].strip() or None
        elif line.startswith(RESPONSE) and not seen_response:
            seen_response, response = True, line[len(RESPONSE) :].strip() or None
        elif line.strip() == CANCELLED_LINE:
            cancelled = True
    return where, response, cancelled


def person(value: str | None) -> str | None:
    """A name from `Name <address>`: the name, or the address when there is none."""
    if not value or not value.strip():
        return None
    name = ADDRESS.sub(" ", value).strip()
    if name:
        return " ".join(name.split())
    found = ADDRESS.search(value)
    return found.group(1).strip() if found is not None and found.group(1).strip() else None


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def local(moment: datetime, zone: ZoneInfo) -> str:
    """A moment as a meeting's time: the owner's local time with that moment's offset (DR-0622, DR-0648)."""
    return moment.astimezone(zone).isoformat(timespec="minutes")


def meeting_fields(record: RawRecord, zone: ZoneInfo) -> dict[str, Any] | str:
    """The meeting fields a record of kind event gives (DR-0646, DR-0648), or why it gives none."""
    if record.fidelity == "derived":
        return "derived: its rid is built from its content"
    if record.native.get("all_day") is True:
        return "an all-day event"
    end = _moment(record.native.get("end"))
    if end is None:
        return "no end"
    if end < record.ts:
        return "end is before start"
    where, response, cancelled = header(record.text)
    subject = " ".join((record.title or "").split())
    if cancelled:
        subject = CANCELLED.sub("", subject).strip()
    fields: dict[str, Any] = {
        "event": record.rid, "subject": subject or NO_SUBJECT, "start": local(record.ts, zone), "end": local(end, zone),
    }  # fmt: skip
    if record.thread:
        fields["series"] = record.thread
    organiser = person(record.author)
    if organiser:
        fields["organiser"] = organiser
    if where:
        fields["where"] = where
    if cancelled:
        fields["response"] = "cancelled"
    elif response in RESPONSES:
        fields["response"] = response
    names = list(dict.fromkeys(name for name in (person(to) for to in record.to) if name))
    if names:
        fields["attendees"] = names
    return fields


def latest(records: Sequence[RawRecord]) -> list[RawRecord]:
    """One version per rid, the last given, as raw's line order has them (DR-0647); records of other kinds dropped."""
    out: dict[str, RawRecord] = {}
    for record in records:
        if record.kind == KIND:
            out.pop(record.rid, None)
            out[record.rid] = record
    return list(out.values())


class _Screen:
    """What keeps a record out now (DR-0650): media's screen, a family read as the record alone, and the holds."""

    def __init__(self, vault: Vault) -> None:
        self.config = vault.config
        self.holds = holds.read_all(vault.root)
        self.decided = Staging(vault.root, vault.home).decided()
        self.withheld = read_withheld(vault.root)
        self.listed = forgotten.load(vault.root, vault.home)

    def why(self, record: RawRecord) -> str | None:
        if self.holds.covers(record):
            return "kept out: held"
        found = refusal(record.rid, self.config, [record], self.decided, self.withheld, listed=self.listed)
        # A source only off still counts, as media's screen lets it play (DR-0570).
        return f"kept out: {found[0]}" if found is not None and found[0] != "off" else None


# -- pending rids: the store writes the file, as it writes every file in the ledger's package (DR-0618) -----------

pending_path = store.calendar_pending_path
pending = store.calendar_pending
note_pending = store.note_calendar_pending
clear_pending = store.clear_calendar_pending


# -- a step ---------------------------------------------------------------------------------------------------------


def step(
    vault: Vault,
    records: Sequence[RawRecord] = (),
    *,
    via: ops.Via,
    catch_up: Sequence[RawRecord] = (),
    dry_run: bool = False,
    now: datetime | None = None,
) -> Stepped:
    """One calendar step (DR-0647): the pending rids at raw's latest version and `records`, a promotion's, with sets;
    then `catch_up`, the catch-up's own records, adding and joining only; one version per rid, the adds in start order
    then rid, in one write by sync. The pending rids it replayed are cleared once that write commits or has nothing to
    write; a dry run writes nothing and clears nothing."""
    zone = ZoneInfo(vault.config.owner.time_zone)
    replayed = pending(vault)
    lookup = RawLookup(vault.root)
    given = {r.rid: r for r in latest(records)}
    driven = [*(r for rid in replayed if rid not in given and (r := lookup.latest(rid)) is not None), *given.values()]
    driving = {r.rid: r for r in latest(driven)}
    adding = {r.rid: r for r in latest(catch_up) if r.rid not in driving}
    screen = _Screen(vault)
    events: list[tuple[datetime, str, Event]] = []
    left: list[Outcome] = []
    for record, adds_only in [*((r, False) for r in driving.values()), *((r, True) for r in adding.values())]:
        why = screen.why(record)
        made = meeting_fields(record, zone) if why is None else why
        if isinstance(made, str):
            left.append(Outcome(record.rid, store.LEFT_OUT, why=made))
            continue
        events.append((record.ts.astimezone(UTC), record.rid, Event(record.rid, made, adds_only)))
    events.sort(key=lambda e: (e[0], e[1]))
    written = (
        store.write_calendar(vault, [e for _, _, e in events], via=via, dry_run=dry_run, now=now)
        if events
        else CalendarWritten((), None, ())
    )
    if not dry_run and replayed:
        clear_pending(vault, replayed)
    outcomes = (*written.outcomes, *sorted(left, key=lambda o: o.rid))
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.what] = counts.get(outcome.what, 0) + 1
    return Stepped(outcomes, written.ops, written.commit, tuple(replayed), dry_run, counts)


def since_default(vault: Vault, now: datetime | None = None) -> date:
    """The catch-up's first day by default: 14 days before today, in the owner's zone."""
    moment = now or datetime.now(UTC)
    return moment.astimezone(ZoneInfo(vault.config.owner.time_zone)).date() - SINCE


def catch_up(vault: Vault, since: date | None = None, *, dry_run: bool = False, now: datetime | None = None) -> Stepped:
    """`docket ledger calendar` (DR-0647): what is pending, then the latest version of each event record in raw that
    starts on `since` or later, in the owner's zone, adding and joining only. The index is brought up to date first,
    to find the event records."""
    first = since or since_default(vault, now)
    zone = ZoneInfo(vault.config.owner.time_zone)
    start = datetime(first.year, first.month, first.day, tzinfo=zone)
    build.update(vault)
    with closing(build.open_index(vault)) as conn:
        rids = [str(rid) for (rid,) in conn.execute("SELECT rid FROM records WHERE kind = ? ORDER BY rid", (KIND,))]
    lookup = RawLookup(vault.root)
    found = [r for r in (lookup.latest(rid) for rid in rids) if r is not None and r.ts >= start]
    return step(vault, via="cli", catch_up=found, dry_run=dry_run, now=now)


# -- what it says -------------------------------------------------------------------------------------------------

ORDER = ("added", "joined", "set", "kept", "idle", "removed", store.LEFT_OUT)


def lines(stepped: Stepped) -> list[str]:
    """The step as `docket ledger calendar` prints it: a line per meeting it would change, each near miss, each record
    left out by its rid and why, never its text, then the counts and the commit."""
    out: list[str] = []
    for o in stepped.outcomes:
        when = (o.start or "").replace("T", " ")[:16]
        if o.what in ("added", "joined", "set"):
            out.append(f"{o.what:<8} {o.meeting}  {when}  {o.subject}")
            out += [f"near     {o.meeting} has {other} close by: join it by hand if it is the same" for other in o.near]
        elif o.what == "removed":
            out.append(f"removed  {when}  {o.subject}: the owner removed its meeting, so it stays out")
        elif o.what == store.LEFT_OUT:
            out.append(f"left out {o.rid}: {o.why}")
    said = ", ".join(f"{stepped.counts[what]} {what}" for what in ORDER if stepped.counts.get(what))
    out.append(said or "no event records")
    if stepped.replayed:
        out.append(f"replayed {len(stepped.replayed)} pending record(s)")
    if stepped.dry_run:
        out.append(f"dry run: {len(stepped.ops)} op(s) would be written; nothing written")
    elif stepped.commit:
        out.append(f"{len(stepped.ops)} op(s) written, commit {stepped.commit[:7]}")
    return out
