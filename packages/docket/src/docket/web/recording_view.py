"""What the Recording page shows, worked out from what the raw log and the docs hold (DR-0545): the recording's summary
beside its transcript, each summary line's anchors linked to the transcript lines they point at, and why there is no
audio to play.

| The rid | Shows |
|---|---|
| a recording, or a transcript | its summary beside one transcript: the rid's, else the summary's, else the first |
| a held recording | that it is held and nothing of it is stored, with where its hold is listed |
| a record that isn't a recording | that it isn't, with a link to it on the Search page |
| nothing the raw log holds | that there is no such recording |

`build` takes the facts and does no I/O, so every rule here is tested on what the facts hold. Text from records and
docs is data: the templates escape it, and no link is made from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from zoneinfo import ZoneInfo

from docket.recall import DocView
from docket.vault.anchors import Anchor, format_anchor, format_time
from docket.vault.records import RawRecord
from docket.vault.segments import Segment, segments
from docket.vault.spans import problem
from docket.web.panel import Style, anchored_rid, frontmatter_end, line_parts
from docket.web.recording_state import RecordingState, time_at
from docket.web.state import SearchState
from docket.web.templating import at as local_time

Mode = Literal["start", "recording", "held", "missing", "other"]

# Why there is no audio to play, each with the sentence the page says: the media reader's states (DR-0551), and
# `stored` while the page has no player. A closed list, so every reason has its words and its test.
NoAudio = Literal[
    "not-fetched", "staged", "held", "withheld", "recut", "cut-by-characters", "not-downloaded", "no-key", "stored"
]
NO_AUDIO: dict[NoAudio, str] = {
    "not-fetched": "No audio stored yet: none has been put for this recording. A cut recording's audio is put "
    "again, cut, at its source's next run.",
    "staged": "The audio waits at the gate with its record, and is stored at the next promotion.",
    "held": "The audio waits with the recording's hold: once it is released, the next run puts it, cut.",
    "withheld": "The audio isn't played: a record of this recording was dropped for a never-store value or as "
    "private, or its source's settings keep it out.",
    "recut": "The audio was cut before this recording's latest cuts, so it isn't played; the next run puts it again, "
    "cut.",
    "cut-by-characters": "A record of it was cut by characters, so its audio can't be cut to match; it stays unplayed.",
    "not-downloaded": "The audio is stored but not downloaded to this machine: git lfs pull fetches it.",
    "no-key": "The audio is stored, but this machine has no key to open it.",
    "stored": "The audio is stored here; playing it from this page comes next.",
}


@dataclass(frozen=True)
class Facts:
    """What the page shows, read from the vault. `transcripts` are the recording's own segments first, then the
    transcripts under it by rid; `docs` are the docs with lines on it, the one search attaches first."""

    rid: str
    tz: ZoneInfo
    record: RawRecord | None = None
    held: bool = False
    recording: RawRecord | None = None
    transcripts: tuple[RawRecord, ...] = ()
    attached: str | None = None
    docs: tuple[str, ...] = ()
    legacy: tuple[str, ...] = ()
    summary: DocView | None = None
    problems: tuple[str, ...] = ()
    audio: NoAudio = "not-fetched"


@dataclass(frozen=True)
class Audio:
    why: NoAudio

    @property
    def message(self) -> str:
        return NO_AUDIO[self.why]


@dataclass(frozen=True)
class AnchorView:
    """An anchor as a link: on the transcript shown, its time alone, since the rid is the page's; else as written."""

    text: str
    url: str | None
    title: str = ""


@dataclass(frozen=True)
class SummaryLine:
    n: int
    body: str
    style: Style
    anchors: list[AnchorView]
    cited: bool


@dataclass(frozen=True)
class SummaryView:
    id: str
    type: str
    rev: int
    lines: list[SummaryLine]


@dataclass(frozen=True)
class DocChoice:
    id: str
    url: str
    on: bool


@dataclass(frozen=True)
class SegmentView:
    at: str
    url: str
    speaker: str
    text: str
    mark: bool
    # The first line `at` marks carries the id every link setting `at` names.
    target: bool


@dataclass(frozen=True)
class TranscriptView:
    rid: str
    meta: str
    segments: list[SegmentView]
    problem: str | None


@dataclass(frozen=True)
class OtherTranscript:
    rid: str
    url: str


@dataclass
class RecordingView:
    mode: Mode
    rid: str = ""
    title: str = ""
    meta: str = ""
    locator: str = ""
    message: str = ""
    link: str | None = None
    link_label: str = ""
    audio: Audio | None = None
    summary: SummaryView | None = None
    no_summary: str = ""
    note: str = ""
    docs: list[DocChoice] = field(default_factory=list[DocChoice])
    transcripts: list[TranscriptView] = field(default_factory=list[TranscriptView])
    others: list[OtherTranscript] = field(default_factory=list[OtherTranscript])
    no_transcript: str = ""
    problems: list[str] = field(default_factory=list[str])


def is_recording(record: RawRecord, transcripts: tuple[RawRecord, ...]) -> bool:
    """Whether the page shows a record: one with segments, a recording, or a record with transcripts under it."""
    return bool(segments(record)) or record.kind == "recording" or bool(transcripts)


def _marks(segs: list[Segment], at: Anchor) -> set[int]:
    """The lines a time marks: those overlapping it, a point being its whole second."""
    lo, hi = at.start * 1000, (at.last + 1) * 1000
    return {i for i, s in enumerate(segs) if s.start_ms < hi and max(s.end_ms, s.start_ms + 1) > lo}


def _locator(rid: str, at: Anchor | None) -> str:
    kind = "record" if at is None else "time"
    return format_anchor(rid, kind, 0 if at is None else at.start, None if at is None else at.end)[1:-1]


@dataclass(frozen=True)
class _Shown:
    """What every part of a recording's view reads: the facts, the URL, the transcript shown, its lines in time order,
    and those `at` marks."""

    facts: Facts
    state: RecordingState
    transcript: RawRecord | None
    segs: list[Segment]
    marked: set[int]


def build(facts: Facts, state: RecordingState) -> RecordingView:
    record = facts.record
    if record is None:
        return _absent(facts)
    if not is_recording(record, facts.transcripts):
        return RecordingView(
            mode="other",
            rid=facts.rid,
            title=facts.rid,
            message=f"{facts.rid} is a {record.kind}, not a recording or a transcript.",
            link=SearchState(hit=_locator(facts.rid, None)).url(),
            link_label="Open it on the Search page",
        )
    shown = _shown(facts)
    segs = segments(shown) if shown is not None else []
    page = _Shown(facts, state, shown, segs, _marks(segs, state.at) if state.at is not None else set[int]())
    head = facts.recording or record
    view = RecordingView(
        mode="recording",
        rid=facts.rid,
        title=head.title or (shown.title if shown is not None else None) or facts.rid,
        locator=_locator(facts.rid, state.at),
        audio=Audio(facts.audio),
        problems=list(facts.problems),
    )
    parts = [head.source, local_time(head.ts, facts.tz)]
    if segs:
        parts.append(f"{format_time(-(-max(s.end_ms for s in segs) // 1000))} long")
    view.meta = " \N{MIDDLE DOT} ".join(parts)
    if shown is None:
        view.no_transcript = "No transcript of this recording is in the raw log yet."
    else:
        view.transcripts = [_transcript(page, shown)]
        view.others = [
            OtherTranscript(t.rid, state.but(rid=t.rid).link()) for t in facts.transcripts if t.rid != shown.rid
        ]
    _summary(page, view)
    return view


def _shown(facts: Facts) -> RawRecord | None:
    """The transcript the page shows: the one the rid names, else the first the summary covers, which its anchors
    point into, else the first of the recording's."""
    by_rid = {t.rid: t for t in facts.transcripts}
    covered = facts.summary.covers if facts.summary is not None else []
    order = [facts.rid, *covered, *by_rid]
    return next((by_rid[rid] for rid in order if rid in by_rid), None)


def _absent(facts: Facts) -> RecordingView:
    """A rid the raw log doesn't hold: held back, or unknown."""
    if facts.held:
        source = facts.rid.partition(":")[0]
        return RecordingView(
            mode="held",
            rid=facts.rid,
            title=facts.rid,
            message=(
                f"{facts.rid} is held: it has stretches you left out, so nothing of it is stored yet. /docket:cuts "
                "goes through its cuts with you, and you release it in your own terminal."
            ),
            link=f"/staging?source={source}",
            link_label=f"Held {source} recordings on the Staging page",
        )
    return RecordingView(
        mode="missing",
        rid=facts.rid,
        title=facts.rid,
        message=f"No recording {facts.rid} in the raw log.",
        link="/search",
        link_label="Find it on the Search page",
    )


def _transcript(page: _Shown, shown: RawRecord) -> TranscriptView:
    state, segs, marked = page.state, page.segs, page.marked
    first = min(marked, default=None)
    lines = [
        SegmentView(
            at=format_time(s.start_ms // 1000),
            url=state.but(at=time_at(s.start_ms // 1000)).link(),
            speaker=s.speaker or "?",
            text=s.text,
            mark=i in marked,
            target=i == first,
        )
        for i, s in enumerate(segs)
    ]
    said = "1 line" if len(segs) == 1 else f"{len(segs)} lines"
    meta = ", ".join(p for p in (shown.rid, shown.engine, said) if p)
    outside = None
    if state.at is not None and not marked:
        outside = problem(Anchor(shown.rid, "time", state.at.start, state.at.end), shown) or (
            f"no line of {shown.rid} is at {state.at_text}"
        )
    return TranscriptView(shown.rid, meta, lines, outside)


def _summary(page: _Shown, view: RecordingView) -> None:
    facts, state = page.facts, page.state
    chosen = facts.summary
    default = facts.attached or (facts.legacy[0] if facts.legacy else None)
    for doc in (*facts.docs, *facts.legacy):
        on = chosen is not None and doc == chosen.id
        view.docs.append(DocChoice(doc, state.but(doc=None if doc == default else doc).link(), on))
    if chosen is None:
        if not view.problems:
            view.no_summary = "No summary covers this recording yet."
        return
    start = frontmatter_end(chosen)
    lines: list[SummaryLine] = []
    for line in chosen.lines:
        if line.n <= start:
            continue
        style, body, anchors = line_parts(line.text)
        links: list[AnchorView] = []
        cited = False
        for anchor in anchors:
            link, on = _anchor(page, anchor, chosen.covers)
            links.append(link)
            cited = cited or on
        lines.append(SummaryLine(line.n, body, style, links, cited))
    view.summary = SummaryView(chosen.id, chosen.type, chosen.rev, lines)
    if chosen.type == "legacy-note":
        view.note = "A legacy note has no anchors, so its lines don't point into the transcript."


def _anchor(page: _Shown, anchor: Anchor, covers: list[str]) -> tuple[AnchorView, bool]:
    """Where a summary line's anchor links, and whether it points at what `at` marks. A time on this recording opens
    here; anything else opens on the Search page."""
    rid = anchored_rid(anchor, covers)
    if rid is None:
        return AnchorView(anchor.token, None), False
    facts, shown = page.facts, page.transcript
    target = next((t for t in facts.transcripts if t.rid == rid), None)
    if target is None and facts.recording is not None and rid == facts.recording.rid:
        # A time on a recording without segments of its own is placed on its transcript (DR-0370).
        target = shown
    if target is None or anchor.kind == "part":
        hit = format_anchor(rid, anchor.kind, anchor.start, anchor.end)[1:-1]
        return AnchorView(anchor.token, SearchState(hit=hit).url(), f"Open {hit} on the Search page"), False
    at = time_at(anchor.start, anchor.end) if anchor.kind == "time" else None
    here = shown is not None and target.rid == shown.rid
    url = page.state.but(rid=page.state.rid if here else target.rid, at=at).link()
    text = format_anchor(None, "time", at.start, at.end) if here and at is not None else anchor.token
    title = f"{anchor.token}: the transcript" + (" at this time" if at is not None else "")
    return AnchorView(text, url, title), here and at is not None and bool(_marks(page.segs, at) & page.marked)
