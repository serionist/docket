"""What the day pages' JSON routes share (DR-0664, DR-0710, DR-0711): the version the poll compares, the reads beside
the writer, a material's checks and its launch, a write's changes from its body, and the write itself.

Meetings reads the snapshot over its window, and its panel the meeting's show, its suggestions and the briefs its
people have (DR-0768, DR-0769); its buttons write a meeting's points through the same write (DR-0667, DR-0770).
Today reads the day as `docket ledger day` does, the items closed today, the next meeting's show and the live
sources' cursor marks; its version hashes the cursor files and the open runs too (DR-0711, DR-0773).

Reads run beside the writer, as the `ledger` tool's do: `ledger_tables`, then `views.read` and its two siblings,
`views.matching` and `views.batch_lines`, and nothing else of the ledger (DR-0616). Every write goes through
`store.write`, one batch per call, by user, via page, on the service's writer thread (DR-0381). The routes are
`day_api`'s.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from docket.errors import DocketError
from docket.index.build import ledger_tables
from docket.ledger import day as ledger_day
from docket.ledger import ops, store, views
from docket.ledger.day import SyncMark
from docket.ledger.store import RefusedError
from docket.ledger.views import Ask, BatchLine, ItemRow, LedgerRead
from docket.vault import cursors
from docket.vault.vault import Vault
from docket.web import day_view
from docket.web.day_state import MEETING_PANELS, PATHS, DayState, DayStateError, ref_of
from docket.web.meetings_view import folded_name

# What a material may open with its own app (DR-0664): documents, images and media; none that runs or carries macros.
OPENABLE = frozenset(
    {
        *day_view.TEXT_SUFFIXES, ".csv", ".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg", ".gif",
        ".webp", ".bmp", ".tif", ".tiff", ".heic", ".mp3", ".m4a", ".wav", ".ogg", ".flac", ".mp4", ".mov",
        ".webm", ".mkv", ".drawio", ".vsdx",
    }
)  # fmt: skip
# The most of a text material the viewer shows: the old tasks app's scope, capped (DR-0664).
VIEW_BYTES = 256 * 1024
# The calls: they take ticked rows, and the selection moves on after one (DR-0711).
CALLS = ("triage", "snooze", "done", "drop", "reopen")
# A meeting's points, each by its id (DR-0667): added, changed, kept, removed or made an item.
POINT_OPS = ("point", "point_set", "keep", "remove", "point_item")
OPS = (*CALLS, "set", "notes", "log", "material", "add", "undo", *POINT_OPS)
# What a field sets on an item or a meeting, each saved on change (DR-0710), and what a new item takes.
ITEM_FIELDS = ("title", "who", "due", "stream", "kind", "description", "outcome")
MEETING_FIELDS = ("outcome", "notes")
SET_FIELDS = (*ITEM_FIELDS, "notes")
NEW_FIELDS = ("kind", "title", "who", "stream", "due", "description", "source")
# What a point's change takes, and what turning one into an item takes, each left blank for the writer's default.
POINT_FIELDS = ("text", "state", "answer")
POINT_ITEM_FIELDS = ("stream", "kind", "who")
# The days Meetings reads either side of today (DR-0666).
MEETINGS_DAYS = 14
NO_DISPLAY = "The service runs with no display here, so it can't open an app; open {path} yourself."


# -- the version the poll compares --------------------------------------------------------------------------------


def version(root: Path, *, syncs: bool = False, running: Iterable[str] = ()) -> str:
    """The op files under a vault's root as they stand, by name, size and modification time (DR-0711): any write, a
    pull and another clone's sync change it. No lock, and no file's content read. Today's, with `syncs`, takes every
    machine's cursor file the same way and the sources with a run open, so a sync that starts or closes shows."""
    digest = hashlib.sha256()
    try:
        entries = sorted(os.scandir(ops.folder(root)), key=lambda e: e.name)
    except (FileNotFoundError, DocketError):
        # No op folder yet, or one that is a link, which no read folds: nothing to compare.
        entries = []
    if syncs:
        try:
            marks = [e for e in os.scandir(root / cursors.STATE) if cursors.FILE.match(e.name) and e.is_file()]
        except (FileNotFoundError, NotADirectoryError):
            marks = []
        entries += sorted(marks, key=lambda e: e.name)
    for entry in entries:
        stat = entry.stat()
        digest.update(f"{entry.name}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    if syncs:
        digest.update(("running\0" + "\0".join(sorted(set(running)))).encode())
    return digest.hexdigest()[:16]


# -- reads ------------------------------------------------------------------------------------------------------------


@dataclass
class Viewer:
    ref: str
    text: str = ""
    cut: bool = False
    problem: str = ""


@dataclass
class Facts:
    """What a day page's read asks of the ledger, read in one go beside the writer."""

    tz: ZoneInfo
    sources: list[str]
    version: str
    # The clock the read's today came from, which says whether a meeting is over.
    at: datetime
    listing: LedgerRead | None = None
    matched: set[str] | None = None
    shown: LedgerRead | None = None
    meeting: LedgerRead | None = None
    # The meeting `open` names, shown whole: Meetings' panel, and what a meeting's materials are checked against.
    held: LedgerRead | None = None
    suggested: LedgerRead | None = None
    # Each folded name or address a person with a brief goes by, and that person's entity (DR-0769).
    briefs: dict[str, str] = field(default_factory=dict[str, str])
    missing: str = ""
    lines: list[BatchLine] = field(default_factory=list[BatchLine])
    alikes: list[ItemRow] = field(default_factory=list[ItemRow])
    viewer: Viewer | None = None
    # Today's: the items closed today, the next meeting's show, the live sources' marks, and the runs open.
    closed: list[ItemRow] = field(default_factory=list[ItemRow])
    lead: LedgerRead | None = None
    syncs: list[SyncMark] = field(default_factory=list[SyncMark])
    sync_problem: str | None = None
    running: tuple[str, ...] = ()

    def opened(self) -> LedgerRead | None:
        """The item or meeting `open` names, whose materials the viewer and the launcher check against."""
        return self.shown or self.held


def gather(
    vault: Vault,
    state: DayState,
    *,
    whole: bool = True,
    alikes: tuple[str, ...] = (),
    running: tuple[str, ...] = (),
) -> Facts:
    """A read's facts: the list whole with `q`'s matches, the Queue's the queue view, Meetings' the snapshot over its
    window, Today's the day (`running` naming the sources with a run open); the open item with the meeting it hangs
    on, or the open meeting with its suggestions and its people's briefs; the lines of the batch `done` names, the
    items an add looks like, and a material in the viewer. The version is taken first, so a write during the read is
    seen by the next poll rather than never."""
    today_page = state.page == "today"
    found = Facts(
        ZoneInfo(vault.config.owner.time_zone),
        sorted(vault.config.sources),
        version(vault.root, syncs=today_page, running=running),
        views.now(),
        running=running,
    )
    at = found.at
    with ledger_tables(vault) as conn:

        def read(ask: Ask) -> LedgerRead:
            # The pages show no chase counts, the most of a whole list's time (DR-0668).
            return views.read(conn, ask, found.tz, at, chased=False)

        if whole and state.page == "queue":
            # The queue view takes no filter: the Queue narrows its rows itself (DR-0666).
            found.listing = read(Ask(view="queue"))
            found.matched = views.matching(conn, state.q) if state.q else None
        elif whole and state.page == "meetings":
            today = at.astimezone(found.tz).date()
            since, until = today - timedelta(days=MEETINGS_DAYS), today + timedelta(days=MEETINGS_DAYS)
            found.listing = read(Ask(view="snapshot", since=since.isoformat(), until=until.isoformat()))
            found.matched = views.matching(conn, state.q) if state.q else None
        elif whole and today_page:
            _today(read, found)
        elif whole:
            ask = Ask(view="list", state="all", stream=state.stream, kind=state.kind, who=state.who, k=None)
            found.listing = read(ask)
            found.matched = views.matching(conn, state.q) if state.q else None
        found.alikes = [shown.items[0] for ref in alikes if (shown := _shown(read, ref)) is not None]
        if state.done is not None and ops.ULID.fullmatch(state.done):
            found.lines = views.batch_lines(conn, state.done)
        if state.open is not None:
            found.shown, found.meeting, found.held, found.missing = _open(read, state.open)
        if found.held is not None and state.page in MEETING_PANELS:
            if state.suggest:
                found.suggested = read(Ask(view="suggest", ref=found.held.meetings[0].id))
            found.briefs = briefs(conn)
    if whole and today_page:
        found.syncs, found.sync_problem = ledger_day.sync_marks(vault, at, running)
    opened = found.opened()
    if opened is not None and state.text is not None:
        found.viewer = view_text(vault, opened, state.text)
    return found


def _today(read: Callable[[Ask], LedgerRead], found: Facts) -> None:
    """Today's reads: the snapshot `docket ledger day` reads, today to the next working day; the items done or dropped,
    for those closed today; and the next meeting's show, for where it is and who is in it."""
    today = found.at.astimezone(found.tz).date()
    after = ledger_day.next_working_day(today)
    found.listing = read(Ask(view="snapshot", since=today.isoformat(), until=after.isoformat()))
    found.closed = [
        row for state in ("done", "dropped") for row in read(Ask(view="list", state=state, k=None)).items
        if row.closed == today
    ]  # fmt: skip
    day = ledger_day.build(found.listing, found.at)
    if day.meetings:
        found.lead = _shown(read, day.meetings[0].meetings[0].meeting.id)


# A person's brief, by each name and plain alias of theirs and each address the entity names (DR-0769): only a person
# whose brief the index holds.
BRIEF_NAMES = """SELECT a.text, a.entity FROM aliases a JOIN entities e ON e.id = a.entity JOIN docs d
ON d.id = 'brief:' || a.entity WHERE e.type = 'person' AND e.status != 'archived' AND a.kind IN ('name', 'plain')"""
BRIEF_ADDRESSES = """SELECT x.value, x.entity FROM external_ids x JOIN entities e ON e.id = x.entity JOIN docs d
ON d.id = 'brief:' || x.entity WHERE e.type = 'person' AND e.status != 'archived' AND x.key = 'email'"""
BRIEF_TABLES = frozenset({"aliases", "entities", "docs", "external_ids"})


def briefs(conn: sqlite3.Connection) -> dict[str, str]:
    """Each folded name or address a person with a brief goes by, and that person's entity; one two people share
    names neither. The ledger folded in memory, with no index, has no entities and so no briefs."""
    tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not tables >= BRIEF_TABLES:
        return {}
    found: dict[str, set[str]] = {}
    for text, entity in [*conn.execute(BRIEF_NAMES), *conn.execute(BRIEF_ADDRESSES)]:
        found.setdefault(folded_name(text), set()).add(entity)
    return {name: next(iter(held)) for name, held in found.items() if name and len(held) == 1}


@dataclass(frozen=True)
class Person:
    """A name a who field suggests: how many items name it, and its other names, which a typed word finds it by."""

    name: str
    uses: int
    also: tuple[str, ...] = ()


# The vault's people as the index holds their entity files: each name and plain alias, never speech recognition's.
PEOPLE = """SELECT a.entity, a.text, a.kind, a.lang FROM aliases a JOIN entities e ON e.id = a.entity
WHERE e.type = 'person' AND e.status != 'archived' AND a.kind IN ('name', 'plain') ORDER BY a.entity, a.ordinal"""


def people(vault: Vault) -> list[Person]:
    """Who the who fields suggest (DR-0714): every who the ledger's items name, the most items first, then the most
    recent; then the vault's people from the index's entity tables that no item names yet, by name. A person an item
    names takes their entity's other names too. The ledger folded in memory, with no index, has no entities."""
    tz = ZoneInfo(vault.config.owner.time_zone)
    with ledger_tables(vault) as conn:
        found = views.read(conn, Ask(view="list", state="all", k=None), tz, views.now(), chased=False)
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entities'").fetchone()
        rows: list[tuple[str, str, str, str | None]] = conn.execute(PEOPLE).fetchall() if has else []
    uses: dict[str, int] = {}
    latest: dict[str, date] = {}
    for item in found.items:
        if item.who:
            uses[item.who] = uses.get(item.who, 0) + 1
            latest[item.who] = max(latest.get(item.who, item.opened), item.opened)
    named = sorted(uses, key=lambda who: (-uses[who], -latest[who].toordinal(), who.casefold()))
    languages = vault.config.owner.languages
    spelt: dict[str, list[tuple[str, str, str | None]]] = {}
    for entity, text, kind, lang in rows:
        spelt.setdefault(entity, []).append((text, kind, lang))
    folded = {who.casefold(): who for who in named}
    also: dict[str, list[str]] = {who: [] for who in named}
    new: list[Person] = []
    for spellings in spelt.values():
        names = {lang: text for text, kind, lang in spellings if kind == "name" and lang}
        first = next((names[lang] for lang in languages if lang in names), None)
        name = first or (names[min(names)] if names else spellings[0][0])
        others = list(dict.fromkeys(text for text, _, _ in spellings if text != name))
        # An entity any of whose names an item already uses joins that who, so a name is suggested once.
        who = next((folded[t.casefold()] for t in (name, *others) if t.casefold() in folded), None)
        if who is not None:
            also[who] += [t for t in (name, *others) if t != who and t not in also[who]]
        elif name.casefold() not in {p.name.casefold() for p in new}:
            new.append(Person(name, 0, tuple(others)))
    return [Person(who, uses[who], tuple(also[who])) for who in named] + sorted(new, key=lambda p: p.name.casefold())


def _shown(read: Callable[[Ask], LedgerRead], ref: str) -> LedgerRead | None:
    """An object's show, or None once it no longer stands."""
    try:
        return read(Ask(view="show", ref=ref))
    except DocketError:
        return None


Opened = tuple[LedgerRead | None, LedgerRead | None, LedgerRead | None, str]


def _open(read: Callable[[Ask], LedgerRead], ref: str) -> Opened:
    """The open item and the meeting it hangs on, or the open meeting whole; and why no item panel opens, which for a
    meeting says where its panel is."""
    try:
        shown = read(Ask(view="show", ref=ref))
    except DocketError as e:
        return None, None, None, str(e)
    kind = shown.detail.kind if shown.detail is not None else None
    if kind == "meeting":
        return None, None, shown, f"{ref} is a meeting; its panel is on the Meetings page."
    if kind != "item":
        return None, None, None, f"{ref} is a stream; a stream has no panel yet."
    hangs = shown.items[0].meeting
    return shown, _shown(read, hangs) if hangs is not None else None, None, ""


# -- materials: the checks and the launch (DR-0664) -----------------------------------------------------------------


def material(shown: LedgerRead, ref: str) -> str:
    """The ref, when the item or meeting names it as a material; refused otherwise."""
    detail = shown.detail
    if detail is None or ref not in [m.ref for m in detail.materials]:
        named = shown.meetings[0].ref if detail is not None and detail.kind == "meeting" else shown.items[0].ref
        raise DayStateError(f"{named} has no material {ref}")
    return ref


def resolve(top: Path, ref: str, allowed: frozenset[str] | tuple[str, ...]) -> Path:
    """A material's path from the host repo's top, checked as text before any filesystem call, then resolved with links
    and junctions followed: the target inside the top, not in `.git`, a folder or a file whose lowercased suffix is
    allowed."""
    problem = day_view.path_problem(ref)
    if problem is not None:
        raise DayStateError(f"{ref} doesn't open from the page: {problem}")
    root = top.resolve()
    target = (root / ref).resolve()
    if target == root or not target.is_relative_to(root):
        raise DayStateError(f"{ref} doesn't open from the page: it leads outside the repo")
    if any(part.lower() == ".git" for part in target.relative_to(root).parts):
        raise DayStateError(f"{ref} doesn't open from the page: it leads into .git")
    if not target.exists():
        raise DayStateError(f"{ref} isn't there")
    if target.is_file() and target.suffix.lower() not in allowed:
        raise DayStateError(
            f"a {target.suffix.lower() or 'suffixless'} file doesn't open from the page; open it yourself"
        )
    return target


def view_text(vault: Vault, shown: LedgerRead, ref: str) -> Viewer:
    """A text material, at most VIEW_BYTES of it, for the panel's viewer; or why it doesn't show."""
    try:
        path = resolve(vault.home.top, material(shown, ref), day_view.TEXT_SUFFIXES)
        if path.is_dir():
            raise DayStateError(f"{ref} is a folder; show it in its folder instead")
        with path.open("rb") as f:
            data = f.read(VIEW_BYTES + 1)
    except (DocketError, OSError) as e:
        return Viewer(ref, problem=str(e) if isinstance(e, DocketError) else f"{ref} doesn't read: {e.strerror}")
    return Viewer(ref, data[:VIEW_BYTES].decode("utf-8", errors="replace"), len(data) > VIEW_BYTES)


def launch(
    path: Path,
    *,
    reveal: bool,
    system: str = sys.platform,
    environ: Mapping[str, str] = os.environ,
    run: Callable[[list[str]], object] | None = None,
    start: Callable[[str], object] | None = None,
) -> None:
    """Open a file with its app or a folder in the file manager, or show either in its folder, on the owner's desktop:
    os.startfile and explorer on Windows, open on macOS, xdg-open elsewhere, where a service with no display refuses."""
    spawn = run or _spawn
    if system == "win32":
        if reveal:
            spawn(["explorer", f"/select,{path}"] if path.is_file() else ["explorer", str(path)])
        else:
            (start or getattr(os, "startfile"))(str(path))  # noqa: B009 - Windows' own; absent elsewhere
        return
    if system == "darwin":
        spawn(["open", "-R", str(path)] if reveal else ["open", str(path)])
        return
    if not (environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY")):
        raise DayStateError(NO_DISPLAY.format(path=path))
    spawn(["xdg-open", str(path.parent if reveal and path.is_file() else path)])


def _spawn(command: list[str]) -> object:
    # The system's own opener with a path the checks passed, its output dropped and nothing waited for.
    return subprocess.Popen(  # noqa: S603 - a fixed opener and a checked path
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
    )


def open_material(vault: Vault, item: str, ref: str, reveal: bool) -> None:
    """A material of an item or a meeting that stands, checked, opened with its app or shown in its folder."""
    found = gather(vault, DayState(open=item), whole=False)
    opened = found.opened()
    if opened is None:
        raise DayStateError(found.missing)
    path = resolve(vault.home.top, material(opened, ref), OPENABLE)
    try:
        launch(path, reveal=reveal)
    except OSError as e:
        raise DayStateError(f"{ref} didn't open: {e.strerror or e}") from None


# -- a write's body as changes (DR-0710) --------------------------------------------------------------------------


class Body(BaseModel):
    """A write's or a save's JSON body: one op of a batch's (DR-0639), the items it acts on and that op's keys. `items`
    holds one ref, or the ticked rows of a bulk; `bulk` says the act bar sent it, so one ticked row runs as a bulk too.
    `at` names the row the selection goes to after a call, which the answer gives back. A point op names its meeting
    in `items`, its point by id in `point`, and a removal its points in `points` (DR-0667, DR-0770)."""

    model_config = ConfigDict(extra="forbid")

    page: str = "ledger"
    op: str
    items: list[str] = []
    at: str | None = None
    bulk: bool = False
    verdict: str | None = None
    until: str | None = None
    note: str | None = None
    outcome: str | None = None
    fields: dict[str, str] = {}
    notes: str | None = None
    ref: str | None = None
    label: str | None = None
    remove: bool = False
    force: bool = False
    of: str | None = None
    batch: str | None = None
    said: Literal["undo", "redo"] | None = None
    point: str | None = None
    points: list[str] = []


@dataclass
class Posted:
    """A body read: its op, the changes it makes, where the selection goes and how the notice says it."""

    op: str
    changes: list[dict[str, Any]]
    at: str | None
    # What an undo says it is, `undo` or `redo`, for the notice after it.
    said: str | None
    bulk: bool


def page_of(page: str) -> str:
    if page.strip() not in PATHS:
        raise DayStateError(f"page is one of {', '.join(PATHS)}, not {page!r}")
    return page.strip()


def _text(value: str | None) -> str | None:
    return (value or "").strip() or None


def posted(body: Body, now: datetime) -> Posted:
    """The changes a body makes: its op and that op's keys, each item of a bulk of calls one change. `now` is the
    owner's local time, which names an add's source."""
    page_of(body.page)
    name = body.op
    items = [ref_of(v, "item") for v in body.items if v.strip()]
    one = items[0] if len(items) == 1 else None
    # A call on ticked rows, one or many, runs as a bulk: the act bar answers the same for one row as for many.
    bulk = name in CALLS and (len(items) > 1 or body.bulk)
    if name not in OPS:
        raise DayStateError(f"op is one of {', '.join(OPS)}")
    if name in CALLS:
        if not items:
            raise DayStateError("tick a row, or open an item, first")
    elif name in POINT_OPS and one is None:
        raise DayStateError(f"{name} takes one meeting")
    elif name not in ("add", "undo") and one is None:
        raise DayStateError(f"{name} takes one item")
    changes: list[dict[str, Any]]
    if name == "triage":
        changes = [{"op": "triage", "item": i, "verdict": body.verdict or None} for i in items]
    elif name == "snooze":
        changes = [{"op": "snooze", "item": i, "until": _text(body.until) or ""} for i in items]
    elif name in ("done", "drop"):
        keys = {"note": _text(body.note), "outcome": body.outcome or None}
        changes = [{"op": name, "item": i, **{k: v for k, v in keys.items() if v}} for i in items]
    elif name == "reopen":
        changes = [{"op": "reopen", "item": i} for i in items]
    elif name == "set":
        fields = {k: body.fields[k] for k in SET_FIELDS if k in body.fields}
        if not fields:
            raise DayStateError("set takes a field: " + ", ".join(SET_FIELDS))
        changes = [{"op": "set", "item": one, "fields": fields}]
    elif name == "notes":
        changes = [{"op": "notes", "item": one, "set": body.notes or None}]
    elif name == "log":
        changes = [{"op": "log", "item": one, "note": _text(body.note)}]
    elif name == "material":
        keys = {"ref": _text(body.ref), "label": _text(body.label), "remove": body.remove}
        changes = [{"op": "material", "item": one, **keys}]
    elif name == "add":
        fields = {k: v for k in NEW_FIELDS if (v := _text(body.fields.get(k)))}
        fields.setdefault("source", f"page:{now:%Y-%m-%dT%H:%M}")
        force = {"force": True} if body.force else {}
        changes = [{"op": "add", "fields": fields, **force}]
    elif name in POINT_OPS:
        changes = _point_changes(name, str(one), body)
    else:
        of, batch = _text(body.of), _text(body.batch)
        changes = [{"op": "undo", "of": of} if of else {"op": "undo", "batch": batch}]
    said = body.said if name == "undo" else None
    at = _text(body.at)
    return Posted(name, changes, ref_of(at, "at") if at else None, said, bulk)


def point_of(given: str | None) -> str:
    """A point as a page names it: by its id, never its n, which moves as points come and go (DR-0667)."""
    text = (given or "").strip()
    if not ops.ULID.fullmatch(text):
        raise DayStateError(f"point is a point's id, not {given!r}")
    return text


def _point_changes(name: str, meeting: str, body: Body) -> list[dict[str, Any]]:
    """A point button's changes on its meeting (DR-0667): an add, with text or a suggested item; a change of text,
    state or answer, "" clearing an answer; a keep of one proposed point or of them all; a removal, which `prepared`
    makes an undo of each point's own op or a drop of a carried one; and an item made from a point, its stream, kind
    and who blank for the writer's default."""
    if name == "point":
        fields: dict[str, Any] = {}
        if text := _text(body.fields.get("text")):
            fields["text"] = text
        if item := _text(body.fields.get("item")):
            fields["item"] = ref_of(item, "item")
        if not fields:
            raise DayStateError("a point needs text, or an item to raise")
        return [{"op": "point", "item": meeting, "fields": fields}]
    if name == "point_set":
        fields = {k: body.fields[k].strip() for k in POINT_FIELDS if k in body.fields}
        if not fields:
            raise DayStateError("point_set takes a field: " + ", ".join(POINT_FIELDS))
        return [{"op": "point_set", "item": meeting, "point": point_of(body.point), "fields": fields}]
    if name == "keep":
        if body.point is None:
            return [{"op": "keep", "item": meeting}]
        return [{"op": "point_set", "item": meeting, "point": point_of(body.point), "fields": {"proposed": None}}]
    if name == "remove":
        points = list(dict.fromkeys(point_of(p) for p in body.points))
        if not points:
            raise DayStateError("remove names the points to remove")
        return [{"op": "undo", "of": p, "meeting": meeting} for p in points]
    fields = {k: v for k in POINT_ITEM_FIELDS if (v := _text(body.fields.get(k)))}
    return [{"op": "point_item", "item": meeting, "point": point_of(body.point), "fields": fields}]


def prepared(vault: Vault, post: Posted) -> Posted:
    """A point op made ready with what only the ledger knows, read beside the writer: a suggestion raised with no text
    takes its item's whole title, and a removal names only points its meeting holds, so it never undoes another op.
    A point a roll carried in is dropped, its proposal with it, rather than undone: undoing a copy undoes its roll
    too (DR-0624), which would open the point again on a meeting that is over, and the next rollover would carry it
    back. A rolled point is refused, as the writer refuses any change to one (DR-0770)."""
    first = post.changes[0] if post.changes else {}
    raising = post.op == "point" and "text" not in first["fields"]
    if not raising and post.op != "remove":
        return post
    tz = ZoneInfo(vault.config.owner.time_zone)
    with ledger_tables(vault) as conn:

        def read(ask: Ask) -> LedgerRead:
            return views.read(conn, ask, tz, views.now(), chased=False)

        if raising:
            item = _shown(read, str(first["fields"]["item"]))
            if item is None or item.detail is None or item.detail.kind != "item":
                raise DayStateError(f"{first['fields']['item']} isn't an item to raise")
            first["fields"]["text"] = item.items[0].title
            return post
        meeting = str(first["meeting"])
        try:
            shown = read(Ask(view="show", ref=meeting))
        except DocketError as e:
            raise DayStateError(str(e)) from None
        if shown.detail is None or shown.detail.kind != "meeting":
            raise DayStateError(f"{meeting} isn't a meeting")
        held = {p.id: p for p in shown.points}
    label = shown.meetings[0].ref
    stray = [str(c["of"]) for c in post.changes if c["of"] not in held]
    if stray:
        raise DayStateError(f"{label} has no point {stray[0]}")
    changes: list[dict[str, Any]] = []
    for change in post.changes:
        point = held[str(change["of"])]
        if point.state == "rolled":
            raise DayStateError(f"{point.ref} rolled on; change it on the meeting it rolled to")
        if point.from_ is None:
            changes.append({"op": "undo", "of": point.id})
        else:
            changes.append({"op": "point_set", "item": meeting, "point": point.id,
                            "fields": {"state": "dropped", "proposed": None}})  # fmt: skip
    post.changes = changes
    return post


@dataclass(frozen=True)
class Wrote:
    written: store.Written
    skipped: tuple[str, ...]
    # The versions just before and just after the write, under its lock: a page whose read had `before` saw every
    # write but this one.
    before: str
    version: str


def write(vault: Vault, changes: list[dict[str, Any]], *, bulk: bool) -> Wrote:
    """The changes as one batch, by user, via page. A bulk of calls gives each op only the rows it takes: a dry run
    names those the writer refuses as they stand, and the rest are written, unless it refuses them all (DR-0710). The
    versions before and after are taken under the same lock as the write, so a page's own write brings no refetch from
    its poll, and a write that landed since the page's read still does."""
    with vault.lock:
        before = version(vault.root)
        skipped: tuple[str, ...] = ()
        if bulk:
            dry = store.write(vault, changes, by="user", via="page", dry_run=True)
            refused = {r.n: r for r in dry.refused}
            if len(refused) == len(changes):
                first = dry.refused[0]
                raise RefusedError(first.n, first.op, first.why)
            skipped = tuple(str(changes[n - 1]["item"]) for n in sorted(refused))
            changes = [c for n, c in enumerate(changes, start=1) if n not in refused]
        written = store.write(vault, changes, by="user", via="page")
        return Wrote(written, skipped, before, version(vault.root))


def local_now(vault: Vault) -> datetime:
    """The owner's local time now, in vault.yaml's zone."""
    return datetime.now(UTC).astimezone(ZoneInfo(vault.config.owner.time_zone))
