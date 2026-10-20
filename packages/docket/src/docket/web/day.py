"""What the day pages' JSON routes share (DR-0664, DR-0710, DR-0711): the version the poll compares, the reads beside
the writer, a material's checks and its launch, a write's changes from its body, and the write itself.

Reads run beside the writer, as the `ledger` tool's do: `ledger_tables`, then `views.read` and its two siblings,
`views.matching` and `views.batch_lines`, and nothing else of the ledger (DR-0616). Every write goes through
`store.write`, one batch per call, by user, via page, on the service's writer thread (DR-0381). The routes are
`day_api`'s.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from docket.errors import DocketError
from docket.index.build import ledger_tables
from docket.ledger import ops, store, views
from docket.ledger.store import RefusedError
from docket.ledger.views import Ask, BatchLine, ItemRow, LedgerRead
from docket.vault.vault import Vault
from docket.web import day_view
from docket.web.day_state import PATHS, DayState, DayStateError, is_meeting, ref_of

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
OPS = (*CALLS, "set", "notes", "log", "material", "add", "undo")
# What a field sets on an item, each saved on change (DR-0710), and what a new item takes.
ITEM_FIELDS = ("title", "who", "due", "stream", "kind", "description", "outcome")
NEW_FIELDS = ("kind", "title", "who", "stream", "due", "description", "source")
NO_DISPLAY = "The service runs with no display here, so it can't open an app; open {path} yourself."


# -- the version the poll compares --------------------------------------------------------------------------------


def version(root: Path) -> str:
    """The op files under a vault's root as they stand, by name, size and modification time (DR-0711): any write, a
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
    """What a day page's read asks of the ledger, read in one go beside the writer."""

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
    """A read's facts: the list whole with `q`'s matches, the open item with the meeting it hangs on, the lines of the
    batch `done` names, the items an add looks like, and a material in the viewer. The version is taken first, so a
    write during the read is seen by the next poll rather than never."""
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


def open_material(vault: Vault, item: str, ref: str, reveal: bool) -> None:
    """A material of an item that stands, checked, opened with its app or shown in its folder."""
    found = gather(vault, DayState(open=item), whole=False)
    if found.shown is None:
        raise DayStateError(found.missing)
    path = resolve(vault.home.top, material(found.shown, ref), OPENABLE)
    try:
        launch(path, reveal=reveal)
    except OSError as e:
        raise DayStateError(f"{ref} didn't open: {e.strerror or e}") from None


# -- a write's body as changes (DR-0710) --------------------------------------------------------------------------


class Body(BaseModel):
    """A write's or a save's JSON body: one op of a batch's (DR-0639), the items it acts on and that op's keys. `items`
    holds one ref, or the ticked rows of a bulk; `bulk` says the act bar sent it, so one ticked row runs as a bulk too.
    `at` names the row the selection goes to after a call, which the answer gives back."""

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
        fields = {k: body.fields[k] for k in ITEM_FIELDS if k in body.fields}
        if not fields:
            raise DayStateError("set takes a field: " + ", ".join(ITEM_FIELDS))
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
    else:
        of, batch = _text(body.of), _text(body.batch)
        changes = [{"op": "undo", "of": of} if of else {"op": "undo", "batch": batch}]
    said = body.said if name == "undo" else None
    at = _text(body.at)
    return Posted(name, changes, ref_of(at, "at") if at else None, said, bulk)


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
