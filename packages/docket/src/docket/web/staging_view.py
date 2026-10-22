"""The Staging page's parts (DR-0380, DR-0382, DR-0383, DR-0784): what waits at the staging gate by source and age,
an open record as promotion would write it, the cuts chosen on it, and the held recordings with the commands that
release them, run by the owner or by Claude on their yes (DR-0600).

`StagingState` is the URL. `build_list`, `build_panel` and `build_held` take what the gate and the vault said and do
no I/O, so every rule here is tested on invented records. Their models are what `GET /api/web/staging` and its panel
answer (DR-0709): values ready to show, times in the owner's zone, links as the server's URLs. Nothing here releases a
hold or shows a held recording's text (DR-0382): a held id is shown by its id alone, and a staged record a hold covers
by its key, rid, kind and time.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from starlette.datastructures import QueryParams

from docket.copied import filled
from docket.errors import DocketError
from docket.vault import cuts as cutting
from docket.vault.anchors import format_time
from docket.vault.cuts import Cut
from docket.vault.gate import Listing, Shown, Why
from docket.vault.holds import Holds
from docket.vault.records import RawRecord, source_name_problem
from docket.vault.redact import Redactor
from docket.vault.releaselog import Releases
from docket.vault.segments import segments
from docket.vault.staging import Entry, Staged, State
from docket.web import urls
from docket.web.display import at

PATH = "/staging"
DEFAULT_K = 50
MORE = 50
MAX_K = 1000
TITLE_CHARS = 80
# The most numbers a selection of utterances or lines may hold, so a crafted URL can't make the page count to a billion.
MAX_PICK = 100_000
# The most typed cuts, and the longest one, a URL may hold.
MAX_TYPED = 200
MAX_SPEC = 40

UTC_ZONE = ZoneInfo("UTC")

KEY = re.compile(r"^[0-9a-f]{32}$")
DONE = re.compile(
    r"^(?:(?P<label>work|private)-(?P<key>[0-9a-f]{12})"
    r"|promoted-(?P<written>\d{1,7})-(?P<held>\d{1,7})(?P<stopped>-stopped)?)$"
)
PICK = re.compile(r"^(\d{1,6})(?:-(\d{1,6}))?$")

KIND_ICON = {
    "transcript": "recording",
    "recording": "recording",
    "message": "thread",
    "thread": "thread",
    "event": "event",
    "doc": "doc",
}

# The line each reason shows beside a record (DR-0380). `{source}` is the record's source.
WHY_LINE: dict[str, str] = {
    "on-hold": "on hold: it stops every ingest of {source}; delete its file, or release the hold",
    "held-back": "held back at the last promotion: {problem}",
    "released": "released with its cuts: classify what is left",
    "by-hand": "an earlier version was decided by hand: work or private?",
    "mixed": "a mixed source: work or private?",
    "work": "a work source: it goes at the next promotion",
    "classified": "classified work by {by}{cuts}: it goes at the next promotion",
}


class StagingStateError(DocketError):
    """A parameter of the Staging page's URL, or of a write it posts, that can't be read; the message names it."""


Pick = tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class StagingState:
    """The Staging page's URL (DR-0784), in one canonical form, defaults left out."""

    source: str | None = None
    state: State | None = None
    k: int = DEFAULT_K
    open: str | None = None
    u: Pick = ()
    line: Pick = ()
    cut: tuple[str, ...] = ()
    confirm: bool = False
    done: str | None = None
    # The selection is the user's, even an empty one. Without it, a record opened with nothing selected starts from
    # the cuts its staged file holds, so Work never drops a cut made earlier (by Claude, the CLI or another tab).
    picked: bool = False

    @staticmethod
    def read(params: QueryParams | Mapping[str, str]) -> StagingState:
        def first(name: str) -> str | None:
            return (params.get(name) or "").strip() or None

        def every(name: str) -> list[str]:
            got = params.getlist(name) if isinstance(params, QueryParams) else [params.get(name) or ""]
            return [v.strip() for v in got if v.strip()]

        source = first("source")
        if source is not None and source_name_problem(source) is not None:
            raise StagingStateError(f"source is a source's name, such as calls, not {source!r}")
        state = first("state")
        if state is not None and state not in ("pending", "ready"):
            raise StagingStateError(f"state is pending or ready, not {state!r}")
        given = first("k") or ""
        # isdigit alone takes a superscript two, which int() refuses.
        if given and (not (given.isascii() and given.isdigit()) or not 1 <= int(given) <= MAX_K):
            raise StagingStateError(f"k is a whole number from 1 to {MAX_K}, not {given!r}")
        key = first("open")
        if key is not None and not KEY.match(key):
            raise StagingStateError(f"open is a staged record's key, 32 hex characters, not {key!r}")
        confirm = first("confirm")
        if confirm is not None and confirm != "private":
            raise StagingStateError(f"confirm is private, not {confirm!r}")
        done = first("done")
        if done is not None and not DONE.match(done):
            raise StagingStateError(f"done is what the last change did, such as work-3f9a1c2e4b5d, not {done!r}")
        # A typed cut that doesn't read stays in the state, so the panel names it beside what was checked: the record
        # and the checks aren't lost to one slip in the field. Only its size is held here.
        specs = [s for v in every("cut") for s in re.split(r"[,\s]+", v) if s]
        if len(specs) > MAX_TYPED or any(len(s) > MAX_SPEC for s in specs):
            raise StagingStateError(f"cut is at most {MAX_TYPED} ranges such as 12:30-14:05, each a short one")
        picked = first("picked")
        if picked is not None and picked != "1":
            raise StagingStateError(f"picked is 1, not {picked!r}")
        return StagingState(
            source=source,
            state=state,
            k=int(given) if given else DEFAULT_K,
            open=key,
            u=_picks(every("u"), "u"),
            line=_picks(every("line"), "line"),
            cut=tuple(sorted(set(specs))),
            confirm=confirm is not None,
            done=done,
            picked=picked is not None,
        ).canonical()

    def canonical(self) -> StagingState:
        """Selections and the drop's question only with an open record; `picked` only where it says something, with
        nothing selected."""
        if self.open is None:
            return replace(self, u=(), line=(), cut=(), confirm=False, picked=False)
        return replace(self, picked=self.picked and not self.selected())

    def pairs(self, *, done: bool = False) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        out += [("source", self.source)] if self.source else []
        out += [("state", self.state)] if self.state else []
        out += [("k", str(self.k))] if self.k != DEFAULT_K else []
        out += [("open", self.open)] if self.open else []
        out += [("u", _pick(p)) for p in self.u]
        out += [("line", _pick(p)) for p in self.line]
        out += [("cut", c) for c in self.cut]
        out += [("picked", "1")] if self.picked else []
        out += [("confirm", "private")] if self.confirm else []
        out += [("done", self.done)] if done and self.done else []
        return out

    def url(self, path: str = PATH, *, done: bool = False) -> str:
        return urls.link(path, self.pairs(done=done))

    def listing(self) -> StagingState:
        """The list as this state narrows it, nothing open."""
        return StagingState(source=self.source, state=self.state, k=self.k)

    def opened(self, key: str) -> StagingState:
        return replace(self.listing(), open=key)

    def selected(self) -> bool:
        return bool(self.u or self.line or self.cut)

    def chosen(self) -> StagingState:
        """This selection marked as the user's: what a preview's URL holds, even when nothing is checked."""
        return replace(self, picked=not self.selected())


def _picks(values: Sequence[str], name: str) -> Pick:
    out: set[tuple[int, int]] = set()
    for value in values:
        m = PICK.match(value)
        first, last = (int(m.group(1)), int(m.group(2) or m.group(1))) if m else (0, 0)
        if not m or first < 1 or last < first or last - first >= MAX_PICK:
            raise StagingStateError(f"{name} is a number from 1, or a range such as 4-9, not {value!r}")
        out.add((first, last))
    return tuple(sorted(out))


def _pick(p: tuple[int, int]) -> str:
    return str(p[0]) if p[0] == p[1] else f"{p[0]}-{p[1]}"


def numbers(picks: Pick) -> list[int]:
    return sorted({n for first, last in picks for n in range(first, last + 1)})


# -- the record as the page shows it ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """A staged record and what promotion would write of it with no cut: the view, redacted by the current rules."""

    record: RawRecord
    view: RawRecord
    # The staged text's lines, each its first character and where it ends, a line break's carriage return left out.
    spans: list[tuple[int, int]]
    # The view's lines, one for each staged line; None when the rules join lines, so numbers wouldn't match.
    lines: list[str] | None
    # Whether the view's text has the staged text's length, so a character range read off it lands where it reads.
    same_length: bool

    @property
    def changed(self) -> bool:
        return self.view.model_dump(exclude={"v"}) != self.record.model_dump(exclude={"v"})


def read_record(record: RawRecord, redactor: Redactor) -> Reading:
    view, _ = redactor.record(record)
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in record.text.split("\n"):
        spans.append((offset, offset + len(line.removesuffix("\r"))))
        offset += len(line) + 1
    shown = view.text.split("\n")
    lines = [line.removesuffix("\r") for line in shown] if len(shown) == len(spans) else None
    return Reading(record, view, spans, lines, len(view.text) == len(record.text))


def chosen_cuts(reading: Reading, state: StagingState) -> list[Cut]:
    """The cuts the checked utterances and lines and the typed ranges make, exactly where the staged record has them.
    A selection that doesn't fit the record is refused, naming what doesn't."""
    record = reading.record
    said = segments(record)
    out: list[Cut] = []
    picked = numbers(state.u)
    if picked and not said:
        raise StagingStateError("u picks utterances, and this record has no transcript; check its lines")
    for n in picked:
        if n > len(said):
            raise StagingStateError(f"u names utterance {n}, and this transcript has {len(said)}")
        s = said[n - 1]
        # An utterance with no length still sits at a moment, as cuts reads it: the cut takes that millisecond.
        out.append(Cut(start=s.start_ms, end=max(s.end_ms, s.start_ms + 1), unit="ms"))
    lines = numbers(state.line)
    if lines and said:
        raise StagingStateError("line picks lines of a text, and this record is a transcript; check its utterances")
    if lines and reading.lines is None:
        raise StagingStateError(_JOINED)
    for n in lines:
        if n > len(reading.spans):
            raise StagingStateError(f"line names line {n}, and this text has {len(reading.spans)}")
    for run in _runs(lines):
        start, end = reading.spans[run[0] - 1][0], reading.spans[run[-1] - 1][1]
        if end > start:
            out.append(Cut(start=start, end=end, unit="char"))
    for spec in state.cut:
        try:
            cut = cutting.parse(spec)
        except DocketError as e:
            raise StagingStateError(f"cut {spec}: {e}") from None
        if cut.unit == "char" and not reading.same_length:
            raise StagingStateError(
                f"cut {spec} counts characters, and the redaction rules changed this text's length since it was "
                "staged, so a range read off the page would land elsewhere: check its lines, or cut it with docket "
                "staging show and docket staging classify --cut in a terminal"
            )
        out.append(cut)
    return cutting.merge(out)


_JOINED = (
    "the redaction rules join lines of this text since it was staged, so its line numbers aren't the staged text's: "
    "cut it with docket staging show and docket staging classify --cut in a terminal, or classify it whole"
)


def stored(reading: Reading, cuts: Sequence[Cut]) -> tuple[Pick, Pick, tuple[str, ...]]:
    """The checks and typed ranges that make the cuts a staged file holds: each utterance or line a cut covers whole is
    checked, and what of the cut they leave is typed exactly, so a panel opened on a cut record keeps its cuts."""
    said = segments(reading.record)
    utterances: set[int] = set()
    lines: set[int] = set()
    typed: list[Cut] = []
    for cut in cuts:
        if cut.unit == "ms" and said:
            covered = [
                n
                for n, s in enumerate(said, start=1)
                if s.text != cutting.CUT_MARKER and cut.start <= s.start_ms and max(s.end_ms, s.start_ms + 1) <= cut.end
            ]
            utterances |= set(covered)
            blocks = [(said[n - 1].start_ms, max(said[n - 1].end_ms, said[n - 1].start_ms + 1)) for n in covered]
        elif cut.unit == "char" and not said and reading.lines is not None:
            covered = [n for n, (a, b) in enumerate(reading.spans, start=1) if cut.start <= a and b <= cut.end]
            lines |= set(covered)
            # Lines next to each other make one cut with the break between, as `chosen_cuts` makes them.
            blocks = [(reading.spans[run[0] - 1][0], reading.spans[run[-1] - 1][1]) for run in _runs(covered)]
        else:
            typed.append(cut)
            continue
        typed += [Cut(start=a, end=b, unit=cut.unit) for a, b in _gaps(cut, blocks)]
    return _as_picks(utterances), _as_picks(lines), tuple(sorted({cutting.spec(c) for c in typed}))


def _gaps(cut: Cut, blocks: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """What of a cut the blocks inside it leave."""
    out: list[tuple[int, int]] = []
    at = cut.start
    for a, b in sorted(blocks):
        if a > at:
            out.append((at, a))
        at = max(at, b)
    if at < cut.end:
        out.append((at, cut.end))
    return out


def _as_picks(found: set[int]) -> Pick:
    return tuple((run[0], run[-1]) for run in _runs(sorted(found)))


def _runs(numbers_: list[int]) -> list[list[int]]:
    """Numbers next to each other, together: lines next to each other make one cut, with the break between."""
    runs: list[list[int]] = []
    for n in numbers_:
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    return runs


class Seen(BaseModel):
    """What a panel showed of a staged record: its state, who classified it and its cuts, posted back with a
    classification so a change since is caught (DR-0785)."""

    state: str
    by: str
    cuts: str


def seen(staged: Staged) -> Seen:
    return Seen(
        state=staged.state,
        by=staged.classified_by or "",
        cuts=",".join(f"{c.unit}:{c.start}-{c.end}" for c in staged.cuts),
    )


# -- the list --------------------------------------------------------------------------------------------------------


class StageChip(BaseModel):
    label: str
    url: str
    on: bool


class StagedRow(BaseModel):
    key: str
    short: str
    rid: str
    source: str
    kind: str
    icon: str
    title: str
    # The record's own time, in the owner's zone.
    when: str
    waited: str
    state: str
    why: Why
    line: str
    on_hold: bool
    # The URL that opens it beside the list, written by the server (DR-0792).
    url: str


class StagedGroup(BaseModel):
    source: str
    rows: list[StagedRow]
    counts: str


class ReadyCount(BaseModel):
    """A source with records ready to promote, and how many."""

    source: str
    n: int


class Unreadable(BaseModel):
    path: str
    message: str


class StagingList(BaseModel):
    lead: str
    source_chips: list[StageChip]
    state_chips: list[StageChip]
    ready: list[ReadyCount]
    groups: list[StagedGroup]
    more: str | None
    unreadable: list[Unreadable]
    narrowed: bool


def build_list(
    listing: Listing,
    redactor: Redactor,
    state: StagingState,
    now: datetime,
    tz: ZoneInfo = UTC_ZONE,
    *,
    held_ids: int = 0,
    held_sources: Sequence[str] = (),
) -> StagingList:
    entries = listing.entries
    why = listing.why
    pending = sum(1 for e in entries if e.staged.state == "pending" and why.get(e.key) != "on-hold")
    ready_entries = [e for e in entries if e.staged.state == "ready" and why.get(e.key) != "on-hold"]
    on_hold = sum(1 for e in entries if why.get(e.key) == "on-hold")
    shown = [e for e in entries if _keeps(e, state)]
    rows = [_row(e, why.get(e.key, "mixed"), redactor, state, now=now, tz=tz) for e in shown[: state.k]]
    groups: dict[str, list[StagedRow]] = {}
    for row in sorted(rows, key=lambda r: r.source):
        groups.setdefault(row.source, []).append(row)
    sources = sorted({e.staged.record.source for e in entries} | set(held_sources))
    ready = Counter(e.staged.record.source for e in ready_entries if state.source in (None, e.staged.record.source))
    return StagingList(
        lead=_lead(pending, len(ready_entries), on_hold, held_ids),
        source_chips=[
            StageChip(label="All sources", url=replace(state.listing(), source=None).url(), on=state.source is None),
            *(StageChip(label=s, url=replace(state.listing(), source=s).url(), on=state.source == s) for s in sources),
        ],
        state_chips=[
            StageChip(label=label, url=replace(state.listing(), state=value).url(), on=state.state == value)
            for label, value in (("Pending and ready", None), ("Pending", "pending"), ("Ready", "ready"))
        ],
        ready=[ReadyCount(source=s, n=n) for s, n in sorted(ready.items())],
        groups=[StagedGroup(source=s, rows=rs, counts=_group_counts(rs)) for s, rs in groups.items()],
        more=replace(state, k=min(MAX_K, state.k + MORE)).url() if len(shown) > state.k else None,
        unreadable=[Unreadable(path=str(path), message=message) for path, message in listing.unreadable],
        narrowed=state.source is not None or state.state is not None,
    )


def _keeps(entry: Entry, state: StagingState) -> bool:
    record = entry.staged.record
    return state.source in (None, record.source) and state.state in (None, entry.staged.state)


def _lead(pending: int, ready: int, on_hold: int, held: int) -> str:
    parts = [f"{pending} for you to classify", f"{ready} ready to promote"]
    if on_hold:
        parts.append(f"{on_hold} on hold")
    waiting = pending + ready + on_hold
    head = (
        f"{waiting} record{_s(waiting)} wait at the gate: " + ", ".join(parts)
        if waiting
        else "Nothing waits at the gate"
    )
    return f"{head}; {held} recording{_s(held)} held" if held else head


def _group_counts(rows: list[StagedRow]) -> str:
    counts = Counter(r.state for r in rows)
    return ", ".join(f"{counts[s]} {s}" for s in ("pending", "ready") if counts[s])


def _row(entry: Entry, why: Why, redactor: Redactor, state: StagingState, *, now: datetime, tz: ZoneInfo) -> StagedRow:
    staged = entry.staged
    record = staged.record
    held = why == "on-hold"
    return StagedRow(
        key=entry.key,
        short=entry.key[:12],
        rid=record.rid,
        source=record.source,
        kind=record.kind,
        icon=KIND_ICON.get(record.kind, "record"),
        # Nothing that reports a hold names a title (DR-0121); the rest as promotion would write it, one line.
        title="" if held else _one_line(redactor.text(record.title or "")),
        when=at(record.ts, tz),
        waited=waited(now - staged.staged_at),
        state=staged.state,
        why=why,
        line=why_line(why, staged),
        on_hold=held,
        url=state.opened(entry.key).url(),
    )


def _one_line(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= TITLE_CHARS else flat[: TITLE_CHARS - 1].rstrip() + "\N{HORIZONTAL ELLIPSIS}"


def why_line(why: Why, staged: Staged) -> str:
    by = {"user": "you", "claude": "Claude"}.get(staged.classified_by or "", "you")
    n = len(staged.cuts)
    cuts = f", with {n} cut{_s(n)}" if n else ""
    return WHY_LINE[why].format(source=staged.record.source, problem=staged.problem or "", by=by, cuts=cuts)


def waited(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h"
    days = hours // 24
    return f"{days} days"


def done_line(done: str | None) -> str | None:
    m = DONE.match(done or "")
    if m is None:
        return None
    if m.group("label") == "work":
        return f"{m.group('key')} classified work: it goes to raw at the next promotion."
    if m.group("label") == "private":
        return f"{m.group('key')} dropped as private: a later pull of the same version is skipped."
    written, held = int(m.group("written")), int(m.group("held"))
    line = f"Promoted: {written} record{_s(written)} written to raw and committed"
    line += f"; {held} held back, each with its problem in the list." if held else "."
    if m.group("stopped"):
        line += " Meetings not made: the calendar step stopped, and its records wait for the next promotion."
    return line


def _s(n: int) -> str:
    return "" if n == 1 else "s"


# -- the panel -------------------------------------------------------------------------------------------------------


class Utterance(BaseModel):
    n: int
    span: str
    speaker: str
    text: str
    checked: bool
    going: bool
    was_cut: bool
    # Its recording's release cuts it at classification and promotion, whatever is checked (DR-0201).
    released: bool = False


class TextLine(BaseModel):
    n: int
    offset: int
    text: str
    checked: bool


class CutPreview(BaseModel):
    summary: str
    text: str | None


class Fact(BaseModel):
    name: str
    value: str


class CutBefore(BaseModel):
    """The version in raw that was cut before: its run and its text."""

    run: str
    text: str


class StagingPanel(BaseModel):
    key: str
    rid: str
    kind: str
    title: str
    source: str
    # The record's own time and when it was staged, in the owner's zone.
    when: str
    staged_at: str
    state: str
    by: str
    cuts: list[str]
    why_line: str
    on_hold: bool
    utterances: list[Utterance] | None
    lines: list[TextLine] | None
    text: str
    facts: list[Fact]
    native: str
    cut_before: CutBefore | None
    notes: list[str]
    problem: str | None
    preview: CutPreview | None
    typed: str
    work_label: str
    can_work: bool
    confirm: bool
    # What a classification carries back, so a change since is caught (DR-0785).
    seen: Seen
    # The list with nothing open, the drop's question, and the record without it: URLs the server wrote.
    close: str
    confirm_url: str
    back: str


class Missing(BaseModel):
    """An open key nothing is staged under any more."""

    key: str
    note: str
    close: str


def missing(state: StagingState) -> Missing:
    key = state.open or ""
    note = f"Nothing is staged as {key[:12]} any more: it was promoted, dropped, or swept by a never-store list."
    return Missing(key=key, note=note, close=state.listing().url())


def build_panel(shown: Shown, redactor: Redactor, state: StagingState, tz: ZoneInfo = UTC_ZONE) -> StagingPanel:
    if shown.why == "on-hold":
        return _held_panel(shown, state, tz)
    staged = shown.entry.staged
    record = staged.record
    key = shown.entry.key
    reading = read_record(record, redactor)
    view = reading.view
    if staged.cuts and not state.selected() and not state.picked:
        # Opened with nothing chosen: start from the cuts its file holds, so Work keeps them unless they are unchecked.
        u, line, cut = stored(reading, staged.cuts)
        state = replace(state, u=u, line=line, cut=cut)
    said = segments(record)
    released = {id(s) for c in shown.release_cuts for s in cutting.removes(said, c)}
    notes: list[str] = []
    if reading.changed:
        notes.append(
            "The redaction rules changed since this was staged: it shows as promotion will write it, and the staged "
            "file keeps what it had until then."
        )
    if released:
        n = len(released)
        notes.append(
            f"Its recording's release cuts {n} utterance{_s(n)}, marked below: they go whatever is checked, as you "
            "confirmed them for the release."
        )
    problem: str | None = None
    chosen: list[Cut] = []
    try:
        chosen = chosen_cuts(reading, state)
    except DocketError as e:
        problem = str(e)
    preview: CutPreview | None = None
    going: set[int] = set()
    if chosen and problem is None:
        try:
            preview, going = _preview(reading, chosen, redactor, shown.release_cuts)
        except DocketError as e:
            problem = str(e)
    view_said = segments(view)
    picked = set(numbers(state.u))
    utterances = (
        [
            Utterance(
                n=n,
                span=_range(s.start_ms, s.end_ms),
                # What the release cuts shows no words, and is never sent to show (DR-0383).
                speaker="" if id(s) in released else v.speaker or "",
                text="" if id(s) in released else " ".join(v.text.split()),
                checked=n in picked,
                going=n in going or id(s) in released,
                was_cut=s.text == cutting.CUT_MARKER,
                released=id(s) in released,
            )
            for n, (s, v) in enumerate(zip(said, view_said, strict=True), start=1)
        ]
        if said
        else None
    )
    checked_lines = set(numbers(state.line))
    lines = (
        [
            TextLine(n=n, offset=span[0], text=text, checked=n in checked_lines)
            for n, (span, text) in enumerate(zip(reading.spans, reading.lines, strict=True), start=1)
        ]
        if not said and reading.lines is not None
        else None
    )
    if not said and reading.lines is None:
        notes.append(_JOINED[0].upper() + _JOINED[1:] + ".")
    n = len(chosen) if problem is None else 0
    return StagingPanel(
        key=key,
        rid=record.rid,
        kind=record.kind,
        title=" ".join((view.title or "").split()),
        source=record.source,
        when=at(record.ts, tz),
        staged_at=at(staged.staged_at, tz),
        state=staged.state,
        by={"user": "you", "claude": "Claude"}.get(staged.classified_by or "", "nobody yet"),
        cuts=[_cut_text(c) for c in staged.cuts],
        why_line=why_line(shown.why, staged),
        on_hold=False,
        utterances=utterances,
        lines=lines,
        text="" if said else view.text,
        facts=[Fact(name=n, value=v) for n, v in _facts(view)],
        native="" if not view.native else json.dumps(view.native, indent=2, ensure_ascii=False, default=str),
        cut_before=None
        if shown.cut_before is None
        else CutBefore(run=shown.cut_before.run, text=shown.cut_before.text),
        notes=notes,
        problem=problem,
        preview=preview,
        typed=", ".join(state.cut),
        work_label=f"Work, with {n} cut{_s(n)}" if n else "Work, whole",
        can_work=problem is None,
        confirm=state.confirm,
        seen=seen(staged),
        close=state.listing().url(),
        confirm_url=replace(state.opened(key), confirm=True).url(),
        back=state.opened(key).url(),
    )


def _held_panel(shown: Shown, state: StagingState, tz: ZoneInfo) -> StagingPanel:
    """A record a hold covers: its key, rid, kind and times, and why it waits, and nothing worked out from its body, not
    a selection's cuts, their preview or the problem a selection has with it (DR-0380, DR-0382)."""
    staged = shown.entry.staged
    record = staged.record
    key = shown.entry.key
    return StagingPanel(
        key=key,
        rid=record.rid,
        kind=record.kind,
        title="",
        source=record.source,
        when=at(record.ts, tz),
        staged_at=at(staged.staged_at, tz),
        state=staged.state,
        by={"user": "you", "claude": "Claude"}.get(staged.classified_by or "", "nobody yet"),
        cuts=[_cut_text(c) for c in staged.cuts],
        why_line=why_line(shown.why, staged),
        on_hold=True,
        utterances=None,
        lines=None,
        text="",
        facts=[],
        native="",
        cut_before=None,
        notes=[],
        problem=None,
        preview=None,
        typed="",
        work_label="Work, whole",
        can_work=False,
        confirm=False,
        seen=seen(staged),
        close=state.listing().url(),
        confirm_url=replace(state.opened(key), confirm=True).url(),
        back=state.opened(key).url(),
    )


def _preview(
    reading: Reading, chosen: list[Cut], redactor: Redactor, release: Sequence[Cut] = ()
) -> tuple[CutPreview, set[int]]:
    """Promotion's order: cut, the release's cuts with the chosen ones as classification and promotion add them, then
    redact. A transcript names the utterances the chosen cuts take, those an overlapping one takes along included; a
    text shows what stays."""
    after, _ = redactor.record(cutting.apply(reading.record, [*chosen, *release]))
    said = segments(reading.record)
    if said:
        gone = {id(s) for cut in chosen for s in cutting.removes(said, cut)}
        going = {n for n, s in enumerate(said, start=1) if id(s) in gone}
        spans = [_range(c.start, c.end) for c in chosen]
        summary = f"Removes {len(going)} utterance{_s(len(going))}: {', '.join(spans)}."
        return CutPreview(summary=summary, text=None), going
    removed = sum(c.end - c.start for c in chosen)
    summary = f"Cuts {removed} character{_s(removed)} in {len(chosen)} stretch{'es' if len(chosen) != 1 else ''}."
    return CutPreview(summary=summary, text=after.text), set()


def _facts(view: RawRecord) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = []
    facts += [("author", view.author)] if view.author else []
    # A transcript's people are who spoke, as a machine transcript carries its recording's (DR-0570).
    facts += [("speakers" if view.segments else "to", ", ".join(view.to))] if view.to else []
    facts += [("container", view.container)] if view.container else []
    facts += [("thread", view.thread)] if view.thread else []
    facts += [("attachments", ", ".join(a.name for a in view.attachments))] if view.attachments else []
    return facts


def _range(start_ms: int, end_ms: int) -> str:
    """A stretch rounded outward to whole seconds, so it covers what it names."""
    return f"{format_time(start_ms // 1000)}-{format_time(-(-end_ms // 1000))}"


def _cut_text(cut: Cut) -> str:
    return _range(cut.start, cut.end) if cut.unit == "ms" else f"characters {cut.start}-{cut.end}"


# -- held recordings -------------------------------------------------------------------------------------------------


class HeldCommand(BaseModel):
    label: str
    text: str


class HeldId(BaseModel):
    id: str
    again: str | None
    commands: list[HeldCommand]


class Unfilled(BaseModel):
    """A placeholder a command keeps, and the value it stands for: one a shell would act on."""

    name: str
    value: str


class HeldSource(BaseModel):
    source: str
    ids: list[HeldId]
    on_hold: int
    unfilled: list[Unfilled] = []
    paste: str = ""


def build_held(
    holds: Holds,
    releases: Releases,
    state: StagingState,
    *,
    folders: Mapping[str, list[str]],
    root: str,
    on_hold: Mapping[str, int],
) -> list[HeldSource]:
    """Each source's held ids and the commands that read and release one (DR-0382, DR-0600): ids only, never anything
    from an import folder."""
    out: list[HeldSource] = []
    for source in sorted(holds.ids):
        if state.source not in (None, source) or not holds.of(source):
            continue
        unfilled: list[tuple[str, str]] = []
        known = folders.get(source, [])
        folder = filled(known[0], "folder", unfilled) if len(known) == 1 else "<folder>"
        if len(known) != 1:
            unfilled += [("folder", f) for f in known]
        vault = filled(root, "vault", unfilled)
        granted = releases.of(source)
        ids: list[HeldId] = []
        for native in sorted(holds.of(source)):
            name = filled(native, "id", unfilled)
            where = f'--from "{folder}" --vault "{vault}"'
            again = granted.get(native)
            if again is not None:
                first = again.first
                note = f"released {first.at:%Y-%m-%d %H:%M} UTC on {first.machine}, and held again after a merge"
                release = f'docket hold release {source} "{name}" {where}'
                commands = [HeldCommand(label="take the hold off", text=release)]
            else:
                note = None
                commands = [
                    HeldCommand(label="read it", text=f'docket hold show {source} "{name}" {where}'),
                    HeldCommand(
                        label="release it", text=f'docket hold release {source} "{name}" {where} --cut <mm:ss-mm:ss>'
                    ),
                ]
            ids.append(HeldId(id=native, again=note, commands=commands))
        out.append(
            HeldSource(
                source=source,
                ids=ids,
                on_hold=on_hold.get(source, 0),
                unfilled=[Unfilled(name=n, value=v) for n, v in sorted(set(unfilled))],
                paste=f"Go through my held {source} recordings with /docket:cuts.",
            )
        )
    return out
