"""The day pages' shared parts and the Ledger page (DR-0661 to DR-0668): `/ledger` is always a whole page,
`/ledger/panel` the open item and `/ledger/text` a material in its viewer; `/ledger/write` is every button's post,
`/ledger/save` a text field's save on change, `/ledger/changed` the poll, and `/ledger/open` a material opened on the
owner's desktop.

Reads run beside the writer, as the `ledger` tool's do: `ledger_tables`, then `views.read` and its two siblings,
`views.matching` and `views.batch_lines`, and nothing else of the ledger (DR-0616). Every write goes through
`store.write`, one batch per post, by user, via page, on the service's writer thread (DR-0381); a post answers a 303
back to its page, and a refusal answers the page with the writer's words, 400, the text as typed and no poll. No
route reads `HX-Request` (DR-0170): a fragment route's answer is the same for htmx and for a link.

A value the URL or a form can't hold, or a `DocketError`, is the page's own error state; anything else is logged as its
type and frames and answered with the error page (DR-0170).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from starlette.datastructures import QueryParams
from starlette.responses import RedirectResponse, Response

from docket.errors import DocketError
from docket.index.build import ledger_tables
from docket.ledger import ops, store, views
from docket.ledger.store import RefusedError
from docket.ledger.views import Ask, BatchLine, ItemRow, LedgerRead
from docket.service.state import Service
from docket.vault.vault import Vault
from docket.web import day_view, templating
from docket.web.day_state import (
    CHANGED,
    OPEN,
    PANEL,
    PATHS,
    SAVE,
    TEXT,
    WRITE,
    DayState,
    DayStateError,
    canonical,
    is_meeting,
    ref_of,
)
from docket.web.shell import shell

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
# The calls: their buttons take ticked rows, and the selection moves on after one (DR-0662).
CALLS = ("triage", "snooze", "done", "drop", "reopen")
# What a field form sets on an item, each saved on change (DR-0661), and what a new item takes. A form posts them as
# `fields.<name>`, the batch's own key, so an item's who, stream or kind never reads as the page's filter of that name.
ITEM_FIELDS = ("title", "who", "due", "stream", "kind", "description", "outcome")
NEW_FIELDS = ("kind", "title", "who", "stream", "due", "description", "source")
# A page's text fields kept as typed on a refusal, by name.
TYPED = (
    "item",
    "notes",
    "note",
    "outcome",
    "label",
    "ref",
    "until",
    *(f"fields.{k}" for k in (*ITEM_FIELDS, "source")),
)
NO_DISPLAY = "The service runs with no display here, so it can't open an app; open {path} yourself."


# -- the version the poll compares --------------------------------------------------------------------------------


def version(root: Path) -> str:
    """The op files under a vault's root as they stand, by name, size and modification time (DR-0663): any write, a
    pull and another clone's sync change it. No lock, and no file's content read."""
    digest = hashlib.sha256()
    try:
        entries = sorted(os.scandir(ops.folder(root)), key=lambda e: e.name)
    except (FileNotFoundError, DocketError):
        # No op folder yet, or one that is a link, which no read folds: nothing to compare.
        entries = []
    for entry in entries:
        stat = entry.stat()
        digest.update(f"{entry.name}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
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
    """What a day page shows, read in one go beside the writer."""

    tz: ZoneInfo
    sources: list[str]
    version: str
    listing: LedgerRead | None = None
    matched: set[str] | None = None
    shown: LedgerRead | None = None
    meeting: LedgerRead | None = None
    missing: str = ""
    lines: list[BatchLine] = field(default_factory=list[BatchLine])
    alikes: list[ItemRow] = field(default_factory=list[ItemRow])
    viewer: Viewer | None = None


def gather(vault: Vault, state: DayState, *, whole: bool = True, alikes: tuple[str, ...] = ()) -> Facts:
    """The page's reads: its list whole, `q`'s matches, the open item with the meeting it hangs on, the notice's
    lines, the items an add looks like, and a material in the viewer. The version is taken first, so a write during
    the read reloads the page once more rather than never."""
    found = Facts(ZoneInfo(vault.config.owner.time_zone), sorted(vault.config.sources), version(vault.root))
    at = views.now()
    with ledger_tables(vault) as conn:

        def read(ask: Ask) -> LedgerRead:
            # The pages show no chase counts, the most of a whole list's time (DR-0668).
            return views.read(conn, ask, found.tz, at, chased=False)

        if whole:
            ask = Ask(view="list", state="all", stream=state.stream, kind=state.kind, who=state.who, k=None)
            found.listing = read(ask)
            found.matched = views.matching(conn, state.q) if state.q else None
            found.alikes = [shown.items[0] for ref in alikes if (shown := _shown(read, ref)) is not None]
        if state.done is not None and ops.ULID.fullmatch(state.done):
            found.lines = views.batch_lines(conn, state.done)
        if state.open is not None:
            found.shown, found.meeting, found.missing = _open(read, state.open)
    if found.shown is not None and state.text is not None:
        found.viewer = view_text(vault, found.shown, state.text)
    return found


def _shown(read: Callable[[Ask], LedgerRead], ref: str) -> LedgerRead | None:
    """An object's show, or None once it no longer stands."""
    try:
        return read(Ask(view="show", ref=ref))
    except DocketError:
        return None


def _open(read: Callable[[Ask], LedgerRead], ref: str) -> tuple[LedgerRead | None, LedgerRead | None, str]:
    """The open item and the meeting it hangs on; or why no item panel opens."""
    if is_meeting(ref):
        return None, None, f"{ref} is a meeting; its panel comes with the Meetings page."
    try:
        shown = read(Ask(view="show", ref=ref))
    except DocketError as e:
        return None, None, str(e)
    if shown.detail is None or shown.detail.kind != "item":
        return None, None, f"{ref} is a meeting or a stream; its panel comes with the Meetings page."
    hangs = shown.items[0].meeting
    return shown, _shown(read, hangs) if hangs is not None else None, ""


# -- materials: the checks and the launch (DR-0664) -----------------------------------------------------------------


def material(shown: LedgerRead, ref: str) -> str:
    """The ref, when the item names it as a material; refused otherwise."""
    detail = shown.detail
    if detail is None or ref not in [m.ref for m in detail.materials]:
        raise DayStateError(f"{shown.items[0].ref} has no material {ref}")
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


# -- the posts: a form as changes (DR-0661) ------------------------------------------------------------------------


@dataclass
class Posted:
    """A form post read: the page it goes back to, its op, the changes it makes and how the page says them."""

    state: DayState
    op: str
    changes: list[dict[str, Any]]
    at: str | None
    # What an undo's post says it is, `undo` or `redo`, for the notice after it.
    said: str | None
    typed: dict[str, str]
    bulk: bool


def page_of(params: QueryParams) -> str:
    page = (params.get("page") or "ledger").strip()
    if page not in PATHS:
        raise DayStateError(f"page is one of {', '.join(PATHS)}, not {page!r}")
    return page


def posted(form: QueryParams, now: datetime) -> Posted:
    """The changes a form posts: its op (or a button's `op:value`) and that op's keys; `item` repeats for a bulk of
    calls, each value one change. `now` is the owner's local time, which names an add's source."""
    state = DayState.read(form, page_of(form)).quiet()
    name, colon, value = (form.get("op") or "").partition(":")
    items = [ref_of(v, "item") for v in form.getlist("item") if v.strip()]
    typed = {k: form.get(k) or "" for k in TYPED if k in form}
    at = (form.get("at") or "").strip()
    one = items[0] if len(items) == 1 else None
    # A call on ticked rows, one or many, runs as a bulk: the act bar answers the same for one row as for many.
    bulk = name in CALLS and (len(items) > 1 or form.get("form") == "act")
    if name in CALLS:
        if not items:
            raise DayStateError("tick a row, or open an item, first")
    elif name not in ("add", "undo") and one is None:
        raise DayStateError(f"{name or 'a post'} takes one item")

    def text(key: str) -> str | None:
        return (form.get(key) or "").strip() or None

    changes: list[dict[str, Any]]
    if name == "triage":
        verdict = value if colon else form.get("verdict") or ""
        changes = [{"op": "triage", "item": i, "verdict": verdict or None} for i in items]
    elif name == "snooze":
        until = value if colon else text("until") or ""
        changes = [{"op": "snooze", "item": i, "until": until} for i in items]
    elif name in ("done", "drop"):
        keys = {"note": text("note"), "outcome": form.get("outcome") or None}
        changes = [{"op": name, "item": i, **{k: v for k, v in keys.items() if v}} for i in items]
    elif name == "reopen":
        changes = [{"op": "reopen", "item": i} for i in items]
    elif name == "set":
        fields = {k: form.get(f"fields.{k}") or "" for k in ITEM_FIELDS if f"fields.{k}" in form}
        if not fields:
            raise DayStateError("set takes a field: " + ", ".join(ITEM_FIELDS))
        changes = [{"op": "set", "item": one, "fields": fields}]
    elif name == "notes":
        changes = [{"op": "notes", "item": one, "set": form.get("notes") or None}]
    elif name == "log":
        changes = [{"op": "log", "item": one, "note": text("note")}]
    elif name == "material":
        keys = {"ref": text("ref"), "label": text("label"), "remove": form.get("remove") == "1"}
        changes = [{"op": "material", "item": one, **keys}]
    elif name == "add":
        fields = {k: v for k in NEW_FIELDS if (v := text(f"fields.{k}"))}
        fields.setdefault("source", f"page:{now:%Y-%m-%dT%H:%M}")
        force = {"force": True} if form.get("force") == "true" else {}
        changes = [{"op": "add", "fields": fields, **force}]
    elif name == "undo":
        of, batch = value or text("of"), text("batch")
        changes = [{"op": "undo", "of": of} if of else {"op": "undo", "batch": batch}]
    else:
        raise DayStateError("op is one of triage, snooze, done, drop, reopen, set, notes, log, material, add, undo")
    said = ((form.get("said") or "").strip() or None) if name == "undo" else None
    return Posted(state, name, changes, ref_of(at, "at") if at else None, said, typed, bulk)


@dataclass(frozen=True)
class Wrote:
    written: store.Written
    skipped: tuple[str, ...]
    version: str


def write(vault: Vault, changes: list[dict[str, Any]], *, bulk: bool) -> Wrote:
    """The changes as one batch, by user, via page. A bulk of calls gives each op only the rows it takes: a dry run
    names those the writer refuses as they stand, and the rest are written, unless it refuses them all (DR-0661). The
    version is taken under the same lock as the write, so a save's answer brings no reload of its own."""
    with vault.lock:
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
        return Wrote(written, skipped, version(vault.root))


def after(post: Posted, wrote: Wrote) -> DayState:
    """Where a post goes back to: its page, the selection moved on after a call, an add's new item opened, with what
    it wrote, nothing or the rows it skipped."""
    back = post.state
    if post.op in CALLS and post.at is not None:
        back = back.but(open=post.at) if back.open is not None else back.but(at=post.at)
    written = wrote.written
    if post.op == "add" and written.ops:
        added = written.ops[-1].item
        back = back.opened(written.labels.get(added, added))
    if not written.ops:
        return back.but(done="nothing", skipped=wrote.skipped)
    return back.but(done=written.ops[0].batch, said=post.said, skipped=wrote.skipped)


# -- the routes -----------------------------------------------------------------------------------------------------


def router(service: Service) -> APIRouter:
    web = APIRouter()

    async def facts(state: DayState, **keys: Any) -> Facts:
        return await asyncio.to_thread(lambda: gather(service.vault(), state, **keys))

    async def whole(
        request: Request,
        state: DayState,
        *,
        status: int = 200,
        problem: str = "",
        typed: Mapping[str, str] | None = None,
        alikes: tuple[str, ...] = (),
        form: str = "",
    ) -> Response:
        """The Ledger page for a state; a refusal's carries its words, the text as typed and no poll."""
        context = shell(service, request, "Ledger", current="ledger")
        try:
            found = await facts(state, alikes=alikes)
        except DocketError as e:
            return templating.page(
                "day/ledger.html", context | _empty(state, str(e)), status=400, referrer=templating.POSTS_FORMS
            )
        page = context | _context(found, state) | {"problem": problem, "typed": dict(typed or {}), "form": form}
        if status != 200:
            page["version"] = None
        return templating.page("day/ledger.html", page, status=status, referrer=templating.POSTS_FORMS)

    def failed_page(request: Request) -> Response:
        return templating.page("error.html", shell(None, request, "Something failed", current="ledger"), status=500)

    @web.get(PATHS["ledger"], include_in_schema=False)
    async def ledger_page(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            try:
                state = DayState.read(request.query_params)
            except DocketError as e:
                context = shell(service, request, "Ledger", current="ledger") | _empty(DayState(), str(e))
                return templating.page("day/ledger.html", context, status=400, referrer=templating.POSTS_FORMS)
            return await whole(request, state)

        return await templating.safely(request, PATHS["ledger"], answer, lambda: failed_page(request))

    @web.get(PANEL, include_in_schema=False)
    async def ledger_panel(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            try:
                params = request.query_params
                state = DayState.read(params, page_of(params))
                found = await facts(state, whole=False)
            except DocketError as e:
                return _panel_error(str(e), 400)
            context = _panel(found, state, "")
            headers = templating.history(request, state.url(), canonical)
            return templating.page("day/_item.html", context, headers=headers, referrer=templating.POSTS_FORMS)

        return await templating.safely(request, PANEL, answer, lambda: _panel_error(templating.FAILED, 500))

    @web.get(TEXT, include_in_schema=False)
    async def ledger_text(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            try:
                params = request.query_params
                state = DayState.read(params, page_of(params))
                if state.text is None:
                    raise DayStateError("text names the material to show, with open naming its item")
                found = await facts(state, whole=False)
            except DocketError as e:
                return _viewer(Viewer("", problem=str(e)), 400)
            viewer = found.viewer or Viewer(state.text, problem=found.missing)
            headers = templating.history(request, state.url(), canonical)
            return _viewer(viewer, 400 if viewer.problem else 200, headers)

        return await templating.safely(
            request, TEXT, answer, lambda: _viewer(Viewer("", problem=templating.FAILED), 500)
        )

    @web.get(CHANGED, include_in_schema=False)
    async def ledger_changed(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        quiet = Response(status_code=204, headers={"Cache-Control": "no-store"})

        async def answer() -> Response:
            given = (request.query_params.get("v") or "").strip()
            root = service.settings.vault
            now = await asyncio.to_thread(lambda: version(root)) if root is not None else given
            if now == given:
                return quiet
            return Response(status_code=200, headers={"Cache-Control": "no-store", "HX-Refresh": "true"})

        # A poll that fails says nothing changed: the next one asks again.
        return await templating.safely(request, CHANGED, answer, lambda: quiet)

    @web.post(WRITE, include_in_schema=False)
    async def ledger_write(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            try:
                post = posted(form, _now(service))
            except DocketError as e:
                back = _back(form)
                return await whole(request, back, status=400, problem=str(e), typed=_typed(form), form=_form(form))
            try:
                wrote = await service.writer.run(lambda: write(service.vault(), post.changes, bulk=post.bulk))
            except RefusedError as e:
                return await whole(
                    request, post.state, status=400, problem=e.why, typed=post.typed, alikes=e.refs, form=_form(form)
                )
            except DocketError as e:
                return await whole(request, post.state, status=400, problem=str(e), typed=post.typed, form=_form(form))
            return RedirectResponse(after(post, wrote).url(notice=True), status_code=303)

        return await templating.safely(request, WRITE, answer, lambda: failed_page(request))

    @web.post(SAVE, include_in_schema=False)
    async def ledger_save(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            try:
                post = posted(form, _now(service))
                if post.op not in ("set", "notes"):
                    raise DayStateError("a save sets a field of an item")
                wrote = await service.writer.run(lambda: write(service.vault(), post.changes, bulk=False))
            except RefusedError as e:
                return _refused_field(form, e.why)
            except DocketError as e:
                return _refused_field(form, str(e))
            item = str(post.changes[0]["item"])
            found = await facts(post.state.opened(item), whole=False)
            if found.shown is None:
                return _refused_field(form, found.missing)
            context: dict[str, Any] = {"state": post.state.opened(item), "version": wrote.version, "tz": found.tz}
            context["row"] = _saved_row(found.shown, post.state)
            context["panel"] = day_view.build_panel(found.shown, found.meeting, post.state, found.sources, "")
            return templating.page("day/_saved.html", context, referrer=templating.POSTS_FORMS)

        return await templating.safely(
            request, SAVE, answer, lambda: _refused_field(QueryParams(""), templating.FAILED, 500)
        )

    @web.post(OPEN, include_in_schema=False)
    async def ledger_open(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            back = _back(form)
            try:
                item = ref_of(form.get("item") or "", "item")
                ref = (form.get("ref") or "").strip()
                reveal = form.get("reveal") == "1"
                await asyncio.to_thread(lambda: _open_material(service.vault(), back, item, ref, reveal))
            except DocketError as e:
                return await whole(request, back, status=400, problem=str(e))
            return RedirectResponse(back.but(done="shown" if reveal else "opened").url(notice=True), status_code=303)

        return await templating.safely(request, OPEN, answer, lambda: failed_page(request))

    return web


def _now(service: Service) -> datetime:
    """The owner's local time now, in vault.yaml's zone."""
    return datetime.now(UTC).astimezone(ZoneInfo(service.vault().config.owner.time_zone))


def _open_material(vault: Vault, state: DayState, item: str, ref: str, reveal: bool) -> None:
    found = gather(vault, state.opened(item), whole=False)
    if found.shown is None:
        raise DayStateError(found.missing)
    path = resolve(vault.home.top, material(found.shown, ref), OPENABLE)
    try:
        launch(path, reveal=reveal)
    except OSError as e:
        raise DayStateError(f"{ref} didn't open: {e.strerror or e}") from None


def _back(form: QueryParams) -> DayState:
    """The page a form names, or the Ledger as it opens when the form's state doesn't read."""
    try:
        return DayState.read(form, page_of(form)).quiet()
    except DocketError:
        return DayState()


def _typed(form: QueryParams) -> dict[str, str]:
    return {k: form.get(k) or "" for k in TYPED if k in form}


def _form(form: QueryParams) -> str:
    return (form.get("form") or "").strip()


# -- contexts -------------------------------------------------------------------------------------------------------


def _empty(state: DayState, problem: str) -> dict[str, Any]:
    return {
        "state": state,
        "view": None,
        "panel": None,
        "missing": "",
        "notice": None,
        "problem": problem,
        "typed": {},
        "form": "",
        "alikes": [],
        "version": None,
        "tz": ZoneInfo("UTC"),
        "today": None,
        "act": day_view.verdict_buttons([]),
        "act_snooze": [],
        "tomorrow": "",
        "kinds": ops.KINDS,
        "viewer": None,
    }


def _context(found: Facts, state: DayState) -> dict[str, Any]:
    # A whole page's read always lists.
    listing = cast(LedgerRead, found.listing)
    view = day_view.build_list(listing, state, found.matched)
    next_ref = view.rows[state.open].next if state.open in view.rows else ""
    return {
        "state": state,
        "view": view,
        "notice": day_view.build_notice(state, found.lines),
        "version": found.version,
        "today": listing.today,
        "act": day_view.verdict_buttons([]),
        "act_snooze": day_view.snooze_choices(listing.today),
        "tomorrow": (listing.today + timedelta(days=1)).isoformat(),
        "kinds": ops.KINDS,
        "alikes": [day_view.row_of(r, state, listing.today) for r in found.alikes],
    } | _panel(found, state, next_ref)


def _panel(found: Facts, state: DayState, next_ref: str) -> dict[str, Any]:
    panel = None
    if found.shown is not None:
        panel = day_view.build_panel(found.shown, found.meeting, state, found.sources, next_ref)
    return {
        "state": state,
        "panel": panel,
        "missing": found.missing,
        "tz": found.tz,
        "viewer": found.viewer,
        "form": "",
        "typed": {},
    }


def _saved_row(shown: LedgerRead, state: DayState) -> day_view.Row:
    row = shown.items[0]
    return day_view.row_of(row.model_copy(update={"title": views.cut(row.title)}), state.opened(row.ref), shown.today)


def _panel_error(message: str, status: int) -> Response:
    context = {"state": DayState(), "panel": None, "missing": message, "tz": ZoneInfo("UTC"), "viewer": None}
    context |= {"form": "", "typed": {}}
    return templating.page("day/_item.html", context, status=status, referrer=templating.POSTS_FORMS)


def _viewer(viewer: Viewer, status: int, headers: Mapping[str, str] | None = None) -> Response:
    return templating.page("day/_viewer.html", {"viewer": viewer}, status=status, headers=headers)


def _key(name: str) -> str:
    """A field's name in a form: an item's fields under `fields.`, the notes on their own."""
    return name if name == "notes" else f"fields.{name}"


def _refused_field(form: QueryParams, problem: str, status: int = 400) -> Response:
    """A save the writer refused: its field's form again, the text as typed, marked unsaved, with the writer's words;
    no version, so nothing reloads it away (DR-0661)."""
    state = _back(form)
    name = next((k for k in (*ITEM_FIELDS, "notes") if _key(k) in form), "")
    context = {
        "state": state,
        "item": (form.get("item") or "").strip(),
        "name": name,
        "value": form.get(_key(name)) or "",
        "problem": problem,
    }
    return templating.page("day/_field.html", context, status=status, referrer=templating.POSTS_FORMS)
