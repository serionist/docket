"""The day pages' JSON routes under /api/web (DR-0709 to DR-0711): each page's list and the open item's panel, then
what the pages share under `/ledger`: a text material in the viewer, the poll's version check, the names the who
fields suggest, and the writes, each a JSON body in and a JSON answer out: a button's write, a field's save and a
material opened on the owner's desktop.

| Route | What |
|---|---|
| `GET /ledger`, `/queue`, `/meetings`, `/today` | the list, read by its query string as it is, and its canonical URL |
| `GET /ledger/panel`, `GET /queue/panel` | the item `open` names, or why no panel opens |
| `GET /meetings/panel` | the meeting `open` names, its suggestions with `suggest=1`, or why no panel opens |
| `GET /today/panel` | the item or the meeting `open` names, as the Ledger's or Meetings' panel, or why none opens |
| `GET /ledger/text` | the text material `text` names, at most its first 256 KB |
| `GET /ledger/changed` | whether the op files moved since `v`; with `page=today`, the cursor files and runs too |
| `GET /ledger/who` | the names the who fields suggest, the most used first (DR-0714) |
| `POST /ledger/write` | one batch, by user, via page: a call, a bulk through a dry run, a point; the notice and Undo |
| `POST /ledger/save` | a field saved on change: the row, or on Meetings the meeting's line, the panel, the versions |
| `POST /ledger/open` | a material opened with its app, or shown in its folder |

A value the URL or the body can't hold, and the writer's refusal, answer 400 with their words as `error`, an add
refused as a look-alike with the items it looks like; anything else is logged as its type and frames and answers 500
with no detail. A write's body is JSON of at most 256 KB, which `webapi.WriteBodies` checks before any of it is read.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.responses import Response

from docket.errors import DocketError
from docket.ledger import ops, views
from docket.ledger.store import RefusedError
from docket.ledger.views import LedgerRead
from docket.service.state import Service
from docket.vault.vault import Vault
from docket.web import day, day_view, meetings_view, today_view
from docket.web.day_state import DayState, DayStateError, is_meeting, ref_of
from docket.web.day_view import ItemPanel, LedgerList, Notice, Row
from docket.web.display import FAILED
from docket.web.meetings_view import MeetingLine, MeetingPanel, MeetingsList
from docket.web.today_view import TodayList

log = logging.getLogger("docket.service.web")


class Refused(BaseModel):
    """Why a read or a write was refused, in the writer's or the reader's own words; an add refused as a look-alike
    names the open items it looks like, so the page can offer Add anyway (DR-0710)."""

    error: str
    alikes: list[Row] = []


class Filters(BaseModel):
    """The list's own filters as the canonical URL holds them, for the page's filter bar."""

    q: str
    stream: str
    kind: str
    who: str
    k: int
    state: str
    group: str
    archived: bool


class Ledger(BaseModel):
    """The Ledger's list (DR-0666), as the page shows it, with what its bars need."""

    # The URL as the server writes it, which the app puts in the address bar as it is (DR-0704).
    canonical: str
    # The op files' version, taken before the read: what the poll compares (DR-0711).
    version: str
    today: str
    list: LedgerList
    filters: Filters
    kinds: list[str]
    # The act bar's verdicts as no row holds them, and its snooze choices.
    verdicts: list[day_view.Button]
    snooze: list[day_view.Snooze]
    tomorrow: str
    # What the write `done` names did, for a URL that still carries it.
    notice: Notice | None


class Queue(Ledger):
    """The Queue's list (DR-0666), in the Ledger's shape: its groups by call, no state chips, and the Ledger's own
    filters, state, group and archived, at their defaults."""


class Meetings(BaseModel):
    """Meetings' list (DR-0666, DR-0768): the meetings by day, with what its bars need."""

    canonical: str
    version: str
    today: str
    list: MeetingsList
    # The shared filters as the canonical URL holds them; Meetings takes q and stream.
    filters: Filters
    # The kinds an item made from a point may take.
    kinds: list[str]
    notice: Notice | None


class Today(BaseModel):
    """Today's list (DR-0773): the next meeting with its prep, the items due, the rest of the day's meetings, the
    items closed today and the last sync per source, with what the act bar and the panels need."""

    canonical: str
    # The op files', the cursor files' and the open runs' version (DR-0711).
    version: str
    today: str
    list: TodayList
    # The kinds an item made from a point, or a new item, may take.
    kinds: list[str]
    verdicts: list[day_view.Button]
    snooze: list[day_view.Snooze]
    tomorrow: str
    notice: Notice | None


class Person(BaseModel):
    name: str
    # How many items name it; 0 for a person of the vault's entities no item names yet.
    uses: int
    # Its other names and aliases, which a typed word finds it by too.
    also: list[str]


class People(BaseModel):
    people: list[Person]


class Panel(BaseModel):
    canonical: str
    panel: ItemPanel | None
    # Why no panel opens, such as an item that doesn't stand or a meeting's number.
    missing: str


class MeetingRead(BaseModel):
    canonical: str
    panel: MeetingPanel | None
    # Why no panel opens, such as a meeting that doesn't stand or an item's number.
    missing: str


class TodayPanel(BaseModel):
    """Today's panel: the item's, as the Ledger's, or the meeting's, as Meetings', whichever `open` names."""

    canonical: str
    item: ItemPanel | None
    meeting: MeetingPanel | None
    # Why no panel opens, such as a ref that doesn't stand or a stream's.
    missing: str


class Viewer(BaseModel):
    ref: str
    text: str
    # Whether the text is only its first 256 KB.
    cut: bool


class Changed(BaseModel):
    changed: bool
    version: str


class Wrote(BaseModel):
    """What a write did: its notice, with Undo or Redo by its batch, the rows a bulk skipped, where the selection goes,
    an add's new item, and the version under the write's own lock, so the page's poll doesn't refetch for it."""

    notice: Notice
    done: str | None
    skipped: list[str]
    at: str | None
    added: str | None
    version: str


class Saved(BaseModel):
    """A field saved: the item's row and panel after it, and the versions before and after its write (DR-0710). A
    page whose read had `before` refetches nothing else; one whose read is older missed a write from the CLI, Claude
    or a sync while the poll held for the field, and refetches its reads."""

    row: Row
    panel: ItemPanel
    # The version just before the save's own write, under its lock.
    before: str
    version: str


class SavedMeeting(BaseModel):
    """A meeting's outcome or notes saved: its line and panel after it, and the versions before and after its write,
    as `Saved` answers them for an item (DR-0710, DR-0770)."""

    line: MeetingLine
    panel: MeetingPanel
    before: str
    version: str


class Opened(BaseModel):
    notice: Notice


class Open(BaseModel):
    page: str = "ledger"
    item: str
    ref: str
    reveal: bool = False


def refused(words: str, alikes: list[Row] | None = None) -> JSONResponse:
    return JSONResponse(Refused(error=words, alikes=alikes or []).model_dump(mode="json"), status_code=400)


async def safely(request: Request, answer: Callable[[], Awaitable[Response | BaseModel]]) -> Response | BaseModel:
    """A route's answer; a refusal its words with 400, and on an unexpected error 500 with no detail, the log getting
    the type and frames only, since a message can quote an item."""
    try:
        return await answer()
    except DocketError as e:
        return refused(e.why if isinstance(e, RefusedError) else str(e))
    except Exception as e:
        log.error("%s %s failed", request.method, request.url.path, exc_info=e)
        return JSONResponse({"error": FAILED}, status_code=500)


def router(service: Service) -> APIRouter:
    api = APIRouter()

    def vault() -> Vault:
        return service.vault()

    def running() -> tuple[str, ...]:
        return tuple(sorted({run.source for run in service.runs.snapshot()}))

    def page_write(changes: list[dict[str, Any]], *, bulk: bool, page: str) -> day.Wrote:
        """The changes written on the writer thread, answering the versions the page's own read takes: Today's with
        the cursor files and the open runs (DR-0711)."""
        today = page == "today"
        return day.write(vault(), changes, bulk=bulk, syncs=today, running=running() if today else ())

    async def gather(state: DayState, *, whole: bool = True, alikes: tuple[str, ...] = ()) -> day.Facts:
        busy = running() if state.page == "today" else ()
        return await asyncio.to_thread(lambda: day.gather(vault(), state, whole=whole, alikes=alikes, running=busy))

    async def listed(request: Request, page: str) -> Ledger:
        state = DayState.read(request.query_params, page)
        found = await gather(state)
        # A whole read always lists.
        listing = cast(LedgerRead, found.listing)
        today = listing.today
        build = day_view.build_queue if page == "queue" else day_view.build_list
        model = Queue if page == "queue" else Ledger
        return model(
            canonical=state.url(),
            version=found.version,
            today=today.isoformat(),
            list=build(listing, state, found.matched),
            filters=filters_of(state),
            kinds=list(ops.KINDS),
            verdicts=day_view.verdict_buttons([]),
            snooze=day_view.snooze_choices(today),
            tomorrow=(today + timedelta(days=1)).isoformat(),
            notice=day_view.build_notice(state, found.lines),
        )

    def filters_of(state: DayState) -> Filters:
        return Filters(
            q=state.q or "",
            stream=state.stream or "",
            kind=state.kind or "",
            who=state.who or "",
            k=state.k,
            state=state.state,
            group=state.group,
            archived=state.archived,
        )

    async def meetings_listed(request: Request) -> Meetings:
        state = DayState.read(request.query_params, "meetings")
        found = await gather(state)
        # A whole read always lists.
        listing = cast(LedgerRead, found.listing)
        return Meetings(
            canonical=state.url(),
            version=found.version,
            today=listing.today.isoformat(),
            list=meetings_view.build_meetings(listing, state, found.matched, found.at, found.tz),
            filters=filters_of(state),
            kinds=list(ops.KINDS),
            notice=day_view.build_notice(state, found.lines),
        )

    async def today_listed(request: Request) -> Today:
        state = DayState.read(request.query_params, "today")
        found = await gather(state)
        # A whole read always lists.
        listing = cast(LedgerRead, found.listing)
        today = listing.today
        return Today(
            canonical=state.url(),
            version=found.version,
            today=today.isoformat(),
            list=today_view.build_today(
                listing,
                found.closed,
                found.lead,
                at=found.at,
                tz=found.tz,
                syncs=found.syncs,
                sync_problem=found.sync_problem,
                running=found.running,
            ),
            kinds=list(ops.KINDS),
            verdicts=day_view.verdict_buttons([]),
            snooze=day_view.snooze_choices(today),
            tomorrow=(today + timedelta(days=1)).isoformat(),
            notice=day_view.build_notice(state, found.lines),
        )

    async def today_paneled(request: Request) -> TodayPanel:
        state = DayState.read(request.query_params, "today")
        found = await gather(state, whole=False)
        item = None
        if found.shown is not None:
            item = day_view.build_panel(found.shown, found.meeting, found.sources, found.tz)
        meeting = meeting_panel(found) if found.held is not None else None
        missing = "" if item is not None or meeting is not None else found.missing
        return TodayPanel(canonical=state.url(), item=item, meeting=meeting, missing=missing)

    def meeting_panel(found: day.Facts) -> MeetingPanel:
        held = cast(LedgerRead, found.held)
        return meetings_view.build_meeting_panel(
            held, found.suggested, briefs=found.briefs, sources=found.sources, tz=found.tz, at=found.at
        )

    async def meeting_paneled(request: Request) -> MeetingRead:
        state = DayState.read(request.query_params, "meetings")
        found = await gather(state, whole=False)
        if found.held is not None:
            return MeetingRead(canonical=state.url(), panel=meeting_panel(found), missing="")
        missing = found.missing
        if found.shown is not None:
            missing = f"{state.open} is an item; its panel is on the Ledger."
        return MeetingRead(canonical=state.url(), panel=None, missing=missing)

    async def paneled(request: Request, page: str) -> Panel:
        state = DayState.read(request.query_params, page)
        found = await gather(state, whole=False)
        panel = None
        if found.shown is not None:
            panel = day_view.build_panel(found.shown, found.meeting, found.sources, found.tz)
        return Panel(canonical=state.url(), panel=panel, missing=found.missing)

    @api.get("/ledger", response_model=Ledger, responses={400: {"model": Refused}})
    async def ledger(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: listed(request, "ledger"))

    @api.get("/queue", response_model=Queue, responses={400: {"model": Refused}})
    async def queue(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: listed(request, "queue"))

    @api.get("/meetings", response_model=Meetings, responses={400: {"model": Refused}})
    async def meetings(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: meetings_listed(request))

    @api.get("/today", response_model=Today, responses={400: {"model": Refused}})
    async def today(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: today_listed(request))

    @api.get("/today/panel", response_model=TodayPanel, responses={400: {"model": Refused}})
    async def today_panel(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: today_paneled(request))

    @api.get("/meetings/panel", response_model=MeetingRead, responses={400: {"model": Refused}})
    async def meetings_panel(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: meeting_paneled(request))

    @api.get("/ledger/panel", response_model=Panel, responses={400: {"model": Refused}})
    async def ledger_panel(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: paneled(request, "ledger"))

    @api.get("/queue/panel", response_model=Panel, responses={400: {"model": Refused}})
    async def queue_panel(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: paneled(request, "queue"))

    @api.get("/ledger/who", response_model=People, responses={400: {"model": Refused}})
    async def ledger_who(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> People:
            found = await asyncio.to_thread(lambda: day.people(vault()))
            return People(people=[Person(name=p.name, uses=p.uses, also=list(p.also)) for p in found])

        return await safely(request, answer)

    @api.get("/ledger/text", response_model=Viewer, responses={400: {"model": Refused}})
    async def ledger_text(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response | Viewer:
            # An item's material or a meeting's: Meetings' state keeps text with either open.
            state = DayState.read(request.query_params, "meetings")
            if state.text is None:
                raise DayStateError("text names the material to show, with open naming its item")
            found = await gather(state, whole=False)
            viewer = found.viewer or day.Viewer(state.text, problem=found.missing)
            if viewer.problem:
                return refused(viewer.problem)
            return Viewer(ref=viewer.ref, text=viewer.text, cut=viewer.cut)

        return await safely(request, answer)

    @api.get("/ledger/changed", response_model=Changed)
    async def ledger_changed(request: Request, v: str = "", page: str = "") -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        given = v.strip()
        # Today's version takes the cursor files and the runs open too (DR-0711).
        syncs = page.strip() == "today"

        async def answer() -> Changed:
            root = service.settings.vault
            busy = running() if syncs else ()
            if root is None:
                return Changed(changed=False, version=given)
            now = await asyncio.to_thread(lambda: day.version(root, syncs=syncs, running=busy))
            return Changed(changed=now != given, version=now)

        try:
            return await answer()
        except Exception as e:
            # A poll that fails says nothing changed: the next one asks again.
            log.error("%s %s failed", request.method, request.url.path, exc_info=e)
            return Changed(changed=False, version=given)

    @api.post(
        "/ledger/write",
        response_model=Wrote,
        responses={400: {"model": Refused}},
    )
    async def ledger_write(request: Request, body: day.Body) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response | Wrote:
            given = day.posted(body, day.local_now(vault()))
            post = await asyncio.to_thread(lambda: day.prepared(vault(), given))
            try:
                page = day.page_of(body.page)
                wrote = await service.writer.run(lambda: page_write(post.changes, bulk=post.bulk, page=page))
            except RefusedError as e:
                if not e.refs:
                    raise
                found = await gather(DayState(), whole=False, alikes=e.refs)
                today = day.local_now(vault()).date()
                return refused(e.why, [day_view.row_of(r, today) for r in found.alikes])
            written = wrote.written
            batch = written.ops[0].batch if written.ops else None
            lines = (await gather(DayState(done=batch), whole=False)).lines if batch else []
            notice = day_view.notice_of(batch or "nothing", post.said, wrote.skipped, lines)
            added = None
            if post.op == "add" and written.ops:
                new = written.ops[-1].item
                added = written.labels.get(new, new)
            return Wrote(
                notice=notice,
                done=batch,
                skipped=list(wrote.skipped),
                at=post.at if post.op in day.CALLS else None,
                added=added,
                version=wrote.version,
            )

        return await safely(request, answer)

    async def meeting_saved(post: day.Posted, page: str) -> Response | SavedMeeting:
        """A meeting's outcome or notes, saved on change from Meetings or Today: checked to be a meeting's before it
        writes."""
        change = post.changes[0]
        if post.op != "set" or not set(change["fields"]) <= set(day.MEETING_FIELDS):
            raise DayStateError("a save on a meeting sets its outcome or notes")
        meeting = str(change["item"])
        if not is_meeting(meeting) and (await gather(DayState(open=meeting), whole=False)).held is None:
            raise DayStateError(f"{meeting} isn't a meeting")
        wrote = await service.writer.run(lambda: page_write(post.changes, bulk=False, page=page))
        found = await gather(DayState(page="meetings", open=meeting), whole=False)
        if found.held is None:
            return refused(found.missing)
        held = found.held
        row = held.meetings[0]
        # The list cuts a subject as the views cut it; the panel shows it whole.
        listed = row.model_copy(update={"subject": views.cut(row.subject)})
        return SavedMeeting(
            line=meetings_view.line_of(listed, held.points, held.today, found.at),
            panel=meeting_panel(found),
            before=wrote.before,
            version=wrote.version,
        )

    async def names_meeting(post: day.Posted) -> bool:
        """Whether a save from Today is a meeting's: an M- number says so, and an id is read; Today's panel saves an
        item's fields or a meeting's outcome and notes (DR-0773)."""
        ref = str(post.changes[0].get("item", "")) if post.changes else ""
        if is_meeting(ref):
            return True
        if not ops.ULID.fullmatch(ref):
            return False
        return (await gather(DayState(open=ref), whole=False)).held is not None

    @api.post(
        "/ledger/save",
        response_model=Saved | SavedMeeting,
        responses={400: {"model": Refused}},
    )
    async def ledger_save(request: Request, body: day.Body) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response | Saved | SavedMeeting:
            post = day.posted(body, day.local_now(vault()))
            page = day.page_of(body.page)
            if page == "meetings" or (page == "today" and await names_meeting(post)):
                return await meeting_saved(post, page)
            if post.op not in ("set", "notes"):
                raise DayStateError("a save sets a field of an item")
            if is_meeting(str(post.changes[0]["item"])):
                raise DayStateError("a save here sets an item's field; a meeting's are saved on Meetings")
            wrote = await service.writer.run(lambda: page_write(post.changes, bulk=False, page=page))
            item = str(post.changes[0]["item"])
            found = await gather(DayState(open=item), whole=False)
            if found.shown is None:
                return refused(found.missing)
            row = found.shown.items[0]
            # The list cuts a title as the views cut it; the panel shows it whole.
            shown = row.model_copy(update={"title": views.cut(row.title)})
            return Saved(
                row=day_view.row_of(shown, found.shown.today),
                panel=day_view.build_panel(found.shown, found.meeting, found.sources, found.tz),
                before=wrote.before,
                version=wrote.version,
            )

        return await safely(request, answer)

    @api.post(
        "/ledger/open",
        response_model=Opened,
        responses={400: {"model": Refused}},
    )
    async def ledger_open(request: Request, body: Open) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Opened:
            day.page_of(body.page)
            item = ref_of(body.item, "item")
            ref = body.ref.strip()
            await asyncio.to_thread(lambda: day.open_material(vault(), item, ref, body.reveal))
            return Opened(notice=day_view.notice_of("shown" if body.reveal else "opened", None, (), []))

        return await safely(request, answer)

    return api
