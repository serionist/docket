"""What the Recording page shows, worked out from what the raw log and the docs hold (DR-0545): the recording's summary
beside its transcript, each summary line's anchors linked to the transcript lines they point at, and why there is no
audio to play, or the player, starting at the line the URL marks. Each part is a model the app's JSON read answers
(DR-0709).

| The rid | Shows |
|---|---|
| a recording, or a transcript | its summary beside one transcript: the rid's, else the summary's, else the first |
| a recording Whisper transcribed | the same, with Whisper's transcript beside it, aligned by time, once raw holds it |
| a held recording | that it is held and nothing of it is stored, with where its hold is listed |
| a record that isn't a recording | that it isn't, with a link to it on the Search page |
| nothing the raw log holds | that there is no such recording |

`build` takes the facts and does no I/O, so every rule here is tested on what the facts hold. Text from records and
docs is data: the app shows it as text, and no link is made from it.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from docket.recall import DocView
from docket.vault.anchors import Anchor, format_anchor, format_time
from docket.vault.cuts import CUT_MARKER
from docket.vault.records import TRANSCRIBED_VIA, RawRecord
from docket.vault.segments import Segment, segments
from docket.vault.spans import problem
from docket.web import urls
from docket.web.display import at as local_time
from docket.web.panel import AnchorLink, SummaryDoc, SummaryLine, anchored_rid, frontmatter_end, line_parts, title_of
from docket.web.recording_state import RecordingState, time_at
from docket.web.state import SearchState

Mode = Literal["start", "recording", "held", "missing", "other"]
# The player's source: the stored audio, by byte range, for the browser's session only (DR-0777, DR-0709).
AUDIO_PATH = "/api/web/recording/audio"

# Why there is no audio to play, each with the sentence the page says: the media reader's states (DR-0551), and
# `stored` while the page has no player. A closed list, so every reason has its words and its test.
NoAudio = Literal[
    "forgotten",
    "not-fetched",
    "staged",
    "held",
    "withheld",
    "recut",
    "cut-by-characters",
    "not-downloaded",
    "no-key",
    "stored",
]
NO_AUDIO: dict[NoAudio, str] = {
    "forgotten": "This recording was forgotten: its audio is gone, and none is stored for it again.",
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
    "stored": "A line's time plays the audio from there.",
}


@dataclass(frozen=True)
class Facts:
    """What the page shows, read from the vault. `transcripts` are the recording's own segments first, then the
    transcripts under it by rid; `docs` are the docs with lines on it, the one search attaches first."""

    rid: str
    tz: ZoneInfo
    record: RawRecord | None = None
    held: bool = False
    # A rid the raw log doesn't hold because a forget took it (DR-0579).
    forgotten: bool = False
    recording: RawRecord | None = None
    transcripts: tuple[RawRecord, ...] = ()
    attached: str | None = None
    docs: tuple[str, ...] = ()
    legacy: tuple[str, ...] = ()
    summary: DocView | None = None
    problems: tuple[str, ...] = ()
    audio: NoAudio = "not-fetched"
    # The service's Whisper job for the recording, in its words, when it ran or queued one (DR-0571).
    speech: str | None = None


class Audio(BaseModel):
    why: NoAudio
    message: str
    speech: str | None = None


class DocChoice(BaseModel):
    id: str
    url: str
    on: bool


class TranscriptLine(BaseModel):
    at: str
    url: str
    speaker: str
    text: str
    mark: bool
    # The first line `at` marks: the page scrolls to it, and every link setting `at` names it (#at).
    target: bool = False
    # Where the player starts for this line, in seconds; none for a cut stretch, which is silent.
    start: float | None = None
    cut: bool = False


class TranscriptView(BaseModel):
    rid: str
    meta: str
    lines: list[TranscriptLine]
    problem: str | None


class PairRow(BaseModel):
    """One stretch of time: the recorder's lines in it, and Whisper's whose middle falls in them."""

    left: list[TranscriptLine]
    right: list[TranscriptLine]


class TranscriptPair(BaseModel):
    """The recorder's transcript and Whisper's side by side, aligned by time (DR-0570)."""

    left: TranscriptView
    right: TranscriptView
    rows: list[PairRow]


class OtherTranscript(BaseModel):
    rid: str
    url: str


class RecordingView(BaseModel):
    mode: Mode
    rid: str = ""
    title: str = ""
    # The source, when and how long, each a part of the line under the title.
    meta: list[str] = []
    locator: str = ""
    message: str = ""
    link: str | None = None
    link_label: str = ""
    audio: Audio | None = None
    summary: SummaryDoc | None = None
    no_summary: str = ""
    note: str = ""
    docs: list[DocChoice] = []
    transcripts: list[TranscriptView] = []
    others: list[OtherTranscript] = []
    no_transcript: str = ""
    problems: list[str] = []
    # The player's source, with the line `at` marks as its start (`#t=`), when the audio reads ok.
    player: str | None = None
    pair: TranscriptPair | None = None


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
        audio=Audio(why=facts.audio, message=NO_AUDIO[facts.audio], speech=facts.speech),
        problems=list(facts.problems),
    )
    parts = [head.source, local_time(head.ts, facts.tz)]
    if segs:
        parts.append(f"{format_time(-(-max(s.end_ms for s in segs) // 1000))} long")
    view.meta = parts
    if shown is None:
        view.no_transcript = "No transcript of this recording is in the raw log yet."
    else:
        partner = _partner(facts, shown)
        if partner is None:
            view.transcripts = [_transcript(page, shown)]
        else:
            view.pair = _pair(page, shown, partner)
        paired = {shown.rid, *([partner.rid] if partner is not None else [])}
        view.others = [
            OtherTranscript(rid=t.rid, url=state.but(rid=t.rid).link())
            for t in facts.transcripts
            if t.rid not in paired
        ]
    if facts.audio == "stored" and facts.recording is not None:
        view.player = _player(facts.recording.rid, segs, page.marked)
    _summary(page, view)
    return view


def is_whisper(record: RawRecord) -> bool:
    """A transcript Docket made with Whisper (DR-0570)."""
    return record.via.startswith(TRANSCRIBED_VIA)


def _partner(facts: Facts, shown: RawRecord) -> RawRecord | None:
    """The transcript shown beside the one the page shows: Whisper's beside the recorder's, or the recorder's the
    summary covers beside Whisper's. Only what raw holds: a Whisper transcript still staged isn't shown."""
    if is_whisper(shown):
        covered = facts.summary.covers if facts.summary is not None else []
        recorders = [t for t in facts.transcripts if not is_whisper(t)]
        return next((t for t in recorders if t.rid in covered), recorders[0] if recorders else None)
    return next((t for t in facts.transcripts if is_whisper(t)), None)


def _player(rid: str, segs: list[Segment], marked: set[int]) -> str:
    """The audio's URL, starting at the first line `at` marks (`#t=`), so the player starts there."""
    url = urls.link(AUDIO_PATH, [("rid", rid)])
    first = min(marked, default=None)
    return url if first is None else f"{url}#t={_seconds(segs[first].start_ms)}"


def _seconds(ms: int) -> str:
    return f"{ms // 1000}.{ms % 1000:03d}"


def _shown(facts: Facts) -> RawRecord | None:
    """The transcript the page shows: the one the rid names, else the first the summary covers, which its anchors
    point into, else the first of the recording's."""
    by_rid = {t.rid: t for t in facts.transcripts}
    covered = facts.summary.covers if facts.summary is not None else []
    order = [facts.rid, *covered, *by_rid]
    return next((by_rid[rid] for rid in order if rid in by_rid), None)


def _absent(facts: Facts) -> RecordingView:
    """A rid the raw log doesn't hold: forgotten, held back, or unknown."""
    if facts.forgotten:
        return RecordingView(
            mode="missing",
            rid=facts.rid,
            title=facts.rid,
            message="This recording was forgotten: nothing of it is kept in the vault.",
            link="/search",
            link_label="Search what is kept",
        )
    if facts.held:
        source = facts.rid.partition(":")[0]
        return RecordingView(
            mode="held",
            rid=facts.rid,
            title=facts.rid,
            message=(
                f"{facts.rid} is held: it has stretches you left out, so nothing of it is stored yet. /docket:cuts "
                "goes through its cuts with you and releases it on your yes."
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


def _line(state: RecordingState, s: Segment, *, mark: bool, target: bool, named: bool = True) -> TranscriptLine:
    """A transcript line; `named` false for Whisper's, which names no speaker (there is no diarization)."""
    cut = s.text == CUT_MARKER
    return TranscriptLine(
        at=format_time(s.start_ms // 1000),
        url=state.but(at=time_at(s.start_ms // 1000)).link(),
        speaker="" if cut or not named else s.speaker or "?",
        text=s.text,
        mark=mark,
        target=target,
        start=None if cut else s.start_ms / 1000,
        cut=cut,
    )


def _transcript(page: _Shown, shown: RawRecord) -> TranscriptView:
    state, segs, marked = page.state, page.segs, page.marked
    first = min(marked, default=None)
    lines = [_line(state, s, mark=i in marked, target=i == first) for i, s in enumerate(segs)]
    return TranscriptView(rid=shown.rid, meta=_meta(shown, segs), lines=lines, problem=_outside(state, shown, marked))


def _meta(record: RawRecord, segs: list[Segment]) -> str:
    said = "1 line" if len(segs) == 1 else f"{len(segs)} lines"
    return ", ".join(p for p in (record.rid, record.engine, said) if p)


def _outside(state: RecordingState, shown: RawRecord, marked: set[int]) -> str | None:
    if state.at is None or marked:
        return None
    return problem(Anchor(shown.rid, "time", state.at.start, state.at.end), shown) or (
        f"no line of {shown.rid} is at {state.at_text}"
    )


def _pair(page: _Shown, shown: RawRecord, partner: RawRecord) -> TranscriptPair:
    """The two side by side, the recorder's on the left: each row a line of the left with the right's lines whose
    middle falls in it; a right line in a gap of the left's gets a row of its own. A cut stretch sits in one row, cut
    in both, as Whisper's `[cut]` spans the stretch the recorder's was cut at."""
    state = page.state
    left, right = (partner, shown) if is_whisper(shown) else (shown, partner)
    lsegs, rsegs = segments(left), segments(right)
    lmarks = page.marked if left is shown else _marked(lsegs, state)
    rmarks = page.marked if right is shown else _marked(rsegs, state)
    first = min(lmarks, default=None)
    second = min(rmarks, default=None) if first is None else None
    lines = [_line(state, s, mark=i in lmarks, target=i == first) for i, s in enumerate(lsegs)]
    other = [_line(state, s, mark=j in rmarks, target=j == second, named=False) for j, s in enumerate(rsegs)]
    rows: list[tuple[int, list[TranscriptLine], list[TranscriptLine]]] = [
        (s.start_ms, [lines[i]], []) for i, s in enumerate(lsegs)
    ]
    starts = [s.start_ms for s in lsegs]
    for j, s in enumerate(rsegs):
        mid = (s.start_ms + s.end_ms) // 2
        k = bisect.bisect_right(starts, mid) - 1
        if k >= 0 and mid < max(lsegs[k].end_ms, lsegs[k].start_ms + 1):
            rows[k][2].append(other[j])
        else:
            rows.append((s.start_ms, [], [other[j]]))
    rows.sort(key=lambda r: r[0])
    return TranscriptPair(
        left=TranscriptView(rid=left.rid, meta=_meta(left, lsegs), lines=[], problem=_outside(state, left, lmarks)),
        right=TranscriptView(rid=right.rid, meta=_meta(right, rsegs), lines=[], problem=None),
        rows=[PairRow(left=a, right=b) for _, a, b in rows],
    )


def _marked(segs: list[Segment], state: RecordingState) -> set[int]:
    return _marks(segs, state.at) if state.at is not None else set[int]()


def _summary(page: _Shown, view: RecordingView) -> None:
    facts, state = page.facts, page.state
    chosen = facts.summary
    default = facts.attached or (facts.legacy[0] if facts.legacy else None)
    for doc in (*facts.docs, *facts.legacy):
        on = chosen is not None and doc == chosen.id
        view.docs.append(DocChoice(id=doc, url=state.but(doc=None if doc == default else doc).link(), on=on))
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
        links: list[AnchorLink] = []
        cited = False
        for anchor in anchors:
            link, on = _anchor(page, anchor, chosen.covers)
            links.append(link)
            cited = cited or on
        lines.append(SummaryLine(n=line.n, body=body, style=style, anchors=links, cited=cited))
    view.summary = SummaryDoc(
        id=chosen.id, type=chosen.type, rev=chosen.rev, title=title_of(lines, chosen.id), lines=lines
    )
    if chosen.type == "legacy-note":
        view.note = "A legacy note has no anchors, so its lines don't point into the transcript."


def _anchor(page: _Shown, anchor: Anchor, covers: list[str]) -> tuple[AnchorLink, bool]:
    """Where a summary line's anchor links, and whether it points at what `at` marks. A time on this recording opens
    here; anything else opens on the Search page."""
    rid = anchored_rid(anchor, covers)
    if rid is None:
        return AnchorLink(text=anchor.token), False
    facts, shown = page.facts, page.transcript
    target = next((t for t in facts.transcripts if t.rid == rid), None)
    if target is None and facts.recording is not None and rid == facts.recording.rid:
        # A time on a recording without segments of its own is placed on its transcript (DR-0370).
        target = shown
    if target is None or anchor.kind == "part":
        hit = format_anchor(rid, anchor.kind, anchor.start, anchor.end)[1:-1]
        return AnchorLink(
            text=anchor.token, url=SearchState(hit=hit).url(), title=f"Open {hit} on the Search page"
        ), False
    at = time_at(anchor.start, anchor.end) if anchor.kind == "time" else None
    here = shown is not None and target.rid == shown.rid
    url = page.state.but(rid=page.state.rid if here else target.rid, at=at).link()
    text = format_anchor(None, "time", at.start, at.end) if here and at is not None else anchor.token
    title = f"{anchor.token}: the transcript" + (" at this time" if at is not None else "")
    return AnchorLink(text=text, url=url, title=title), here and at is not None and bool(
        _marks(page.segs, at) & page.marked
    )
