"""Each agent's own slice of the ledger (#305): the open items and meetings it can act on, not the whole ledger, in
`context/<source>.md`, or `context/<source>/<key>.md` for a recording's agent. `context.md` stays whole, for the main
thread and as every agent's fallback.

- A source's agent: the open and snoozed items whose source is one of its records, whose who wrote one of its records
  from 30 days before its window on, or that hang on a meeting such a person organises or attends; every meeting for
  a source with events or mail, with the items on them, else those such a person is in; no points.
- A recording's agent: the items whose who its transcript or notes name, or that hang on its meetings; those meetings
  with their points.
- Every agent: the items due by the prep week's end, every stream, and whole people: every open item of each person
  the slice holds an item of, so an agent that finds someone in it has all their items.

A person is matched by the words of their name, three letters or more, as the ledger's `name_tokens` reads them: Ben
Sample is in `Ben Sample <ben@example.invalid>` and in `ben.sample@example.invalid`. The owner is no one's person: they
write in every source and sit in every meeting. A slice is written in parts of at most 40,000 bytes, under the Read
tool's limit, so an agent reads it once, whole.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from docket.index.ledger import name_tokens
from docket.ledger import views

CONTEXTS = "context"
# The days before a source's window whose records name the people the source covers.
PEOPLE_DAYS = 30
PART_BYTES = 40_000
# The kinds of record whose source sees the owner's meetings whole: a calendar's events, a mailbox's invites.
SEES_MEETINGS = ("event", "mail")
_WORDS = re.compile(r"[^\w]+")


@dataclass(frozen=True)
class People:
    """The people an agent covers, each by the words of their name or address."""

    words: list[set[str]]
    # The text's own words, for a recording: whoever it names is in it.
    text: frozenset[str] = frozenset()
    # Without the index's records nobody can be told apart, so everyone is in.
    everyone: bool = False

    def has(self, person: str | None) -> bool:
        if self.everyone:
            return True
        wanted = name_tokens(person)
        if not wanted:
            return False
        return wanted <= self.text or any(wanted <= words for words in self.words)


def people(conn: sqlite3.Connection, source: str, since: datetime, owner: Callable[[str], bool]) -> People:
    """Everyone but the owner who wrote a record of the source from `PEOPLE_DAYS` before `since` on: the owner is in
    every meeting and writes in every source, so they would bring in everything. Everyone, when the index holds no
    records to ask, as a ledger read with the index out of reach has none."""
    if not has_records(conn):
        return People([], everyone=True)
    start = (since - timedelta(days=PEOPLE_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT DISTINCT author FROM records WHERE source = ? AND ts >= ? AND author IS NOT NULL", (source, start)
    ).fetchall()
    return People([words for (author,) in rows if not owner(author) and (words := name_tokens(author))])


def has_records(conn: sqlite3.Connection) -> bool:
    """Whether the connection holds the index's records: an in-memory ledger read has only the ledger's tables."""
    found = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'records'").fetchone()
    return found is not None


def sees_meetings(conn: sqlite3.Connection, source: str) -> bool:
    """Whether the source has events or mail, so sees the owner's meetings whole; with no records to ask, it may."""
    if not has_records(conn):
        return True
    marks = ", ".join("?" * len(SEES_MEETINGS))
    query = f"SELECT 1 FROM records WHERE source = ? AND kind IN ({marks}) LIMIT 1"  # noqa: S608 - marks only
    return conn.execute(query, (source, *SEES_MEETINGS)).fetchone() is not None


def text_people(texts: Iterable[str]) -> People:
    """The people a recording's text names: every word of it, so a who all of whose words it holds is in it."""
    words: set[str] = set()
    for text in texts:
        words |= {w for w in _WORDS.split(text.lower()) if len(w) >= 3}
    return People([], frozenset(words))


def _source_of(locator: str) -> str:
    return locator.removeprefix("[").removeprefix("r:").partition(":")[0]


def for_source(
    snapshot: views.LedgerRead,
    source: str,
    covered: People,
    attendees: Mapping[str, Sequence[str]],
    *,
    until: date,
    every_meeting: bool,
) -> views.LedgerRead:
    """A source agent's slice of the snapshot: its items, its meetings with no points, every stream. `attendees` are
    each meeting's, by its id."""
    met = {m.ref for m in snapshot.meetings if _in_meeting(m, attendees.get(m.id, ()), covered)}
    meetings = [m for m in snapshot.meetings if every_meeting or m.ref in met]
    # A source that sees every meeting sees what hangs on each.
    listed = {m.ref for m in meetings} if every_meeting else met
    items = [
        i for i in snapshot.items
        if _source_of(i.source) == source or covered.has(i.who) or (i.meeting in listed) or _due_by(i, until)
    ]  # fmt: skip
    return snapshot.model_copy(update={"items": whole(snapshot.items, items), "meetings": meetings, "points": []})


def whole(every: Sequence[views.ItemRow], taken: Sequence[views.ItemRow]) -> list[views.ItemRow]:
    """The items taken, and every other item of each person they hold, in the snapshot's order: an agent that finds
    a person in its slice takes their items there as all of them, so none may be left out."""
    people = [words for i in taken if (words := name_tokens(i.who))]
    ids = {i.id for i in taken}
    return [i for i in every if i.id in ids or _one_of(name_tokens(i.who), people)]


def _one_of(words: set[str], people: Sequence[set[str]]) -> bool:
    """The same person as one of them, by name in full or in part, as Ben in Ben Sample."""
    return bool(words) and any(words <= other or other <= words for other in people)


def for_recording(
    snapshot: views.LedgerRead, named: People, meetings: Sequence[views.MeetingFacts], *, until: date
) -> views.LedgerRead:
    """A recording agent's slice: the items its text names or on its meetings, and those meetings with their
    points."""
    refs = {m.row.ref for m in meetings}
    items = whole(
        snapshot.items, [i for i in snapshot.items if named.has(i.who) or i.meeting in refs or _due_by(i, until)]
    )
    rows = [m.row for m in meetings]
    points = [p for p in snapshot.points if p.meeting in refs]
    return snapshot.model_copy(update={"items": items, "meetings": rows, "points": points})


def _in_meeting(m: views.MeetingRow, attendees: Sequence[str], covered: People) -> bool:
    return any(covered.has(person) for person in [m.organiser, *attendees])


def _due_by(item: views.ItemRow, until: date) -> bool:
    return item.due is not None and item.due <= until


def parts(head: str, lines: Sequence[str]) -> list[str]:
    """The slice's lines in parts of at most 40,000 bytes of UTF-8, split between lines, each opening with its head
    line and `part n of m`."""
    room = PART_BYTES - len(f"{head} | part 999 of 999\n".encode())
    chunks: list[list[str]] = [[]]
    used = 0
    for line in lines:
        size = len(line.encode("utf-8")) + 1
        if chunks[-1] and used + size > room:
            chunks.append([])
            used = 0
        chunks[-1].append(line)
        used += size
    total = len(chunks)
    if total == 1:
        return ["\n".join([head, *chunks[0]]) + "\n"]
    return ["\n".join([f"{head} | part {n} of {total}", *chunk]) + "\n" for n, chunk in enumerate(chunks, start=1)]


def write(where: Path, name: str, head: str, lines: Sequence[str]) -> list[Path]:
    """The slice under `where` as `<name>.md`, or `<name>.<n>.md` in parts; the files written."""
    texts = parts(head, lines)
    where.mkdir(mode=0o700, parents=True, exist_ok=True)
    files = [where / f"{name}.md"] if len(texts) == 1 else [where / f"{name}.{n}.md" for n in range(1, len(texts) + 1)]
    for file, text in zip(files, texts, strict=True):
        file.write_text(text, encoding="utf-8", newline="\n")
    return files


def listed(files: Sequence[Path]) -> str:
    """The slice's files as a prompt names them: one path, or each part's."""
    return str(files[0]) if len(files) == 1 else ", ".join(str(f) for f in files)
