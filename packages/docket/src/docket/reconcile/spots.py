"""A recording's doubtful spots (DR-0583): where its two transcripts disagree on a name, a number or a word, worked out
on each call from the latest versions in raw and never stored; and the recordings waiting to be reconciled.

| Step | What |
|---|---|
| the pair | the recorder's transcript the summary covers, else its first, as the Recording page pairs them; Whisper's |
| the machine | this machine ingests the source, as `docket transcribe` asks: withheld marks are each clone's own |
| the screen | media's reader's family screen passes (`reader.kept_out`), a source only off aside |
| stretches | `align.py`, per recorder line, outside every cut; a number written two ways is none (`numbers.py`) |
| kinds | name, number, word: the first that fits the words that differ |
| left out | a spot whose texts or line name a person a never-store list holds, or that touches a redaction marker |
| fixes | a spot a fix line decides isn't listed again; one at a settled place comes last, with the picks turned down |
| rank and cap | names, numbers, words, by time within each; 40 a recording, the rest counted by kind |

A word that differs makes a name spot when it is within matching's edit distance of a word of a name or an alias of
the meeting's entities or the glossary's terms (`meeting.py`), or is capitalised inside a sentence; so does a
capitalised word both transcripts spell alike that is near such a name and none of its forms, or is a one-word `asr`
alias. A number spot holds a digit or a number word on either side. Left-out spots are counted, never named.

The fixes doc's form comes with #183's second PR: until then `report` takes the fix lines it is given and reads none,
and a doc's id is its file's.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from docket import index, meeting, recall
from docket.errors import DocketError
from docket.index.matching import max_distance, within
from docket.media import layout, reader
from docket.plaintext import WORD, fold, nfc
from docket.reconcile import align, numbers
from docket.reconcile.align import Aligned, Stretch, Word
from docket.vault import brief_form, cursors
from docket.vault.anchors import format_anchor
from docket.vault.config import NeverStore
from docket.vault.cuts import Cut
from docket.vault.docstore import DocStore
from docket.vault.entities import Entity, load_entities
from docket.vault.neverstore import matches_text
from docket.vault.processed import doc_path, parse_doc
from docket.vault.records import RID, RawRecord, Segment, is_native_id
from docket.vault.redact import MARKER
from docket.vault.spans import RawLookup
from docket.vault.staging import Staging
from docket.vault.vault import Vault

Kind = Literal["name", "number", "word"]
KINDS: tuple[Kind, ...] = ("name", "number", "word")
State = Literal["sure", "unsure", "confirmed", "rejected"]
CAP = 40
# The listing: the recordings waiting, at most MOST of the NEWEST with both transcripts.
NEWEST = 30
MOST = 10
SUFFIX = "/whisper"
FIXES = "transcript-fixes"
# A name word under three letters matches too much to say a spot is a name.
SHORT = 3
# What ends a sentence, so the capital after it says nothing.
ENDS = frozenset(".!?…")
# What may stand between a sentence's end and its next word.
OPENS = frozenset("\"'\u201c\u201d\u2018\u2019\u201e\u00ab\u00bb([-\u2013\u2014")


@dataclass(frozen=True)
class Fix:
    """A line of a recording's fixes doc as spots reads it (DR-0582): its two texts, the chosen one, its state, and
    its anchors' whole seconds on the recorder's transcript and on Whisper's. #183's second PR parses the doc into
    these."""

    n: int
    left: str
    right: str
    chosen: str
    state: State
    at: tuple[int, int]
    heard_at: tuple[int, int]


class Alias(BaseModel):
    text: str
    asr: bool


class Person(BaseModel):
    """An entity a spot may be about: one of the meeting's, or a glossary term near a spot."""

    id: str
    type: str
    name: str
    aliases: list[Alias] = Field(default_factory=list[Alias])


class Spot(BaseModel):
    """Where the two disagree: the two texts, both anchors, the speaker, the recorder's line and Whisper's words for
    it, the entities near it, and at a settled place the picks the owner turned down."""

    id: str
    kind: Kind
    left: str
    right: str
    recorder_anchor: str
    whisper_anchor: str
    speaker: str | None
    line: str
    heard: str
    near: list[str] = Field(default_factory=list[str])
    rejected: list[str] = Field(default_factory=list[str])


class FixView(BaseModel):
    """A fix line as spots sees it: why it is not in force, and its texts unless it is cut or hidden."""

    n: int
    state: State
    why: Literal["waiting", "rejected", "no longer matching", "cut", "hidden"] | None
    left: str | None = None
    right: str | None = None
    chosen: str | None = None


class LeftOut(BaseModel):
    """The spots not listed, counted by why: past the cap, by kind; heard differently; near a never-store value."""

    cap: dict[str, int] = Field(default_factory=lambda: dict.fromkeys(KINDS, 0))
    heard_differently: int = 0
    never_store: int = 0


class DocRef(BaseModel):
    """The recording's fixes doc: its id, and its rev, None while it has none."""

    id: str
    rev: int | None = None


class SpotsReport(BaseModel):
    rid: str
    transcript: str
    whisper: str
    doc: DocRef
    why: str | None = None
    people: list[Person] = Field(default_factory=list[Person])
    spots: list[Spot] = Field(default_factory=list[Spot])
    fixes: list[FixView] = Field(default_factory=list[FixView])
    left_out: LeftOut = Field(default_factory=LeftOut)


class Waiting(BaseModel):
    """A recording to reconcile: its spots no fix decides, by kind, and its fixes waiting for the owner."""

    rid: str
    title: str | None
    ts: datetime
    spots: dict[str, int]
    waiting: int


class Listing(BaseModel):
    recordings: list[Waiting] = Field(default_factory=list[Waiting])
    hidden: int = 0


@dataclass
class Names:
    """The words the meeting's entities and the glossary's terms are named by, folded, and whose each is."""

    entities: list[Entity]
    owners: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    asr: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    vocab: list[str] = field(default_factory=list[str])
    _near: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    @classmethod
    def of(cls, entities: Sequence[Entity]) -> Names:
        found = cls(list({e.id: e for e in entities}.values()))
        for e in found.entities:
            for form in meeting.forms(e, asr=False):
                for word in WORD.findall(nfc(form)):
                    owners = found.owners.setdefault(fold(word), [])
                    if e.id not in owners:
                        owners.append(e.id)
            for alias in e.aliases:
                if alias.kind == "asr" and len(WORD.findall(alias.text)) == 1:
                    found.asr.setdefault(fold(alias.text.strip()), []).append(e.id)
        found.vocab = sorted(w for w in found.owners if len(w) >= SHORT)
        return found

    def near(self, word: str) -> list[str]:
        """The entities a word is a word of, or within matching's edit distance of a word of their names."""
        key = fold(word)
        if key not in self._near:
            found = list(self.owners.get(key, [])) if len(key) >= SHORT else []
            limit = max_distance(word)
            for term, _ in within(key, limit, self.vocab) if limit else []:
                found += [e for e in self.owners[term] if e not in found]
            self._near[key] = found
        return self._near[key]

    def alike(self, word: str) -> list[str]:
        """The entities a capitalised word both transcripts spell alike may misname: one of their one-word `asr`
        aliases, or near a word of a name and none of the forms."""
        if not word[:1].isupper():
            return []
        key = fold(word)
        if key in self.asr:
            return self.asr[key]
        return [] if key in self.owners else self.near(word)


@dataclass(frozen=True)
class _Placed:
    """A fix line against the latest transcripts: why it has no place, or its line and its text's place in it."""

    fix: Fix
    why: Literal["no longer matching", "cut", "hidden"] | None
    line: int | None = None
    span: tuple[int, int] | None = None


@dataclass(frozen=True)
class Found:
    """What the stretches came to: the spots listed, what was left out, the fix lines, the waiting count, the spots
    no fix decides by kind before the cap, and the entities near a listed spot."""

    spots: list[Spot]
    left_out: LeftOut
    fixes: list[FixView]
    waiting: int
    undecided: dict[str, int]
    near: list[str]


def spot_id(rid: str, start_ms: int, left: str, right: str) -> str:
    """The first 8 hex digits of the sha256 of the recorder's rid, its line's start and the two texts: stable while
    the transcripts are."""
    return hashlib.sha256("\n".join((rid, str(start_ms), left, right)).encode()).hexdigest()[:8]


def anchor(rid: str, start_ms: int, end_ms: int) -> str:
    """A time anchor spanning a stretch, its start rounded down and its end up."""
    return format_anchor(rid, "time", start_ms // 1000, -(-end_ms // 1000))


def find(
    transcript: RawRecord,
    whisper: RawRecord,
    names: Names,
    *,
    lists: NeverStore,
    shapes: NeverStore,
    fixes: Sequence[Fix] = (),
) -> Found:
    """A pair's spots, ranked and capped, on what the caller read: `lists` are the never-store lists, `shapes` the
    people on them in every shape the brief screen reads (`brief_form.phrases`)."""
    aligned = align.align(transcript, whisper)
    langs = (transcript.lang, whisper.lang)
    found, differently = align.stretches(
        aligned, alike=lambda _, word: bool(names.alike(word.text)), keep=lambda s: _differs(aligned, s, langs)
    )
    left_out = LeftOut(heard_differently=differently)
    placed = _placed(transcript, whisper, fixes, lists, shapes)
    marked = [m.span() for m in MARKER.finditer(aligned.heard.text)]
    undecided: list[tuple[Kind, Stretch, list[str]]] = []
    settled: list[tuple[Kind, Stretch, list[str], list[str]]] = []
    for s in found:
        if _hidden(aligned, s, shapes, marked):
            left_out.never_store += 1
            continue
        kind, near = _kind(aligned, s, names, langs)
        line = aligned.lines[s.line]
        left = aligned.left(s)
        at = [p for p in placed if p.why is None and p.line == line.index and align.overlap(p.span, left)]
        if not at:
            undecided.append((kind, s, near))
        elif all(p.fix.state == "rejected" for p in at):
            settled.append((kind, s, near, list(dict.fromkeys(p.fix.chosen for p in at))))
    undecided.sort(key=lambda found: (KINDS.index(found[0]), *_when(aligned, found[1])))
    for kind, _, _ in undecided[CAP:]:
        left_out.cap[kind] += 1
    settled.sort(key=lambda found: _when(aligned, found[1]))
    spots = [
        _spot(aligned, s, kind, near, rid=transcript.rid, whisper=whisper.rid) for kind, s, near in undecided[:CAP]
    ]
    spots += [
        _spot(aligned, s, kind, near, rid=transcript.rid, whisper=whisper.rid, rejected=picks)
        for kind, s, near, picks in settled
    ]
    counts: dict[str, int] = dict.fromkeys(KINDS, 0)
    for kind, _, _ in undecided:
        counts[kind] += 1
    views = [_view(p) for p in placed]
    waiting = sum(1 for v in views if v.why == "waiting")
    near = list(dict.fromkeys(e for spot in spots for e in spot.near))
    return Found(spots, left_out, views, waiting, counts, near)


def _differs(aligned: Aligned, s: Stretch, langs: tuple[str | None, str | None]) -> bool:
    """Whether a stretch's two sides say different things: a number written two ways says the same."""
    if s.alike:
        return True
    left, right = aligned.texts(s)
    read = numbers.reading(left, langs[0], align.fillers(langs[0]))
    return read != numbers.reading(right, langs[1], align.fillers(langs[1]))


def _when(aligned: Aligned, s: Stretch) -> tuple[int, int, int]:
    line = aligned.lines[s.line]
    return line.start, line.index, s.i1


def _kind(aligned: Aligned, s: Stretch, names: Names, langs: tuple[str | None, str | None]) -> tuple[Kind, list[str]]:
    """The first kind that fits the words that differ, and the entities near them."""
    near: list[str] = []
    named = False
    for core in s.cores:
        mine, theirs = aligned.sides(core)
        sides = [(w, aligned.lines[core.line].text) for w in mine] + [(w, aligned.heard.text) for w in theirs]
        for word, text in sides:
            ids = names.alike(word.text) if core.alike else names.near(word.text)
            near += [e for e in ids if e not in near]
            named = named or core.alike or bool(ids) or _capitalised(word, text)
    if named:
        return "name", near
    texts = [t for core in s.cores for t in zip(aligned.texts(core), langs, strict=True)]
    return ("number" if any(numbers.holds(text, lang) for text, lang in texts) else "word"), near


def _capitalised(word: Word, text: str) -> bool:
    """A word of two letters or more that starts with a capital inside a sentence."""
    if len(word.text) < 2 or not word.text[0].isupper():
        return False
    i = word.start - 1
    while i >= 0 and (text[i].isspace() or text[i] in OPENS):
        i -= 1
    return i >= 0 and text[i] not in ENDS


def _heard_for(aligned: Aligned, s: Stretch) -> str:
    """Whisper's words for a stretch's line: its text from the first word the line was given to the last."""
    span = aligned.heard.span(aligned.given[s.line])
    return aligned.heard.text[span[0] : span[1]] if span else ""


def _hidden(aligned: Aligned, s: Stretch, shapes: NeverStore, marked: list[tuple[int, int]]) -> bool:
    """Whether a spot names a person a never-store list holds, in its texts, its line or Whisper's words for it, or
    touches a redaction marker on either side."""
    line = aligned.lines[s.line].text
    left, right = aligned.left(s), aligned.right(s)
    if shapes.keywords:
        texts = (*aligned.texts(s), line, _heard_for(aligned, s))
        if any(matches_text(text, shapes) for text in texts):
            return True
    mine = [m.span() for m in MARKER.finditer(line)]
    return _touches(left, mine) or _touches(right, marked)


def _touches(span: tuple[int, int] | None, marks: list[tuple[int, int]]) -> bool:
    return span is not None and any(span[0] <= end and span[1] >= start for start, end in marks)


def _spot(
    aligned: Aligned,
    s: Stretch,
    kind: Kind,
    near: list[str],
    *,
    rid: str,
    whisper: str,
    rejected: Sequence[str] = (),
) -> Spot:
    line = aligned.lines[s.line]
    left, right = aligned.texts(s)
    given = aligned.given[s.line]
    segs = [aligned.heard.segments[aligned.heard.words[k].segment] for k in given[s.j1 : s.j2]]
    return Spot(
        id=spot_id(rid, line.start, left, right),
        kind=kind,
        left=left,
        right=right,
        recorder_anchor=anchor(rid, line.segment.start_ms, line.segment.end_ms),
        whisper_anchor=anchor(whisper, min(g.start_ms for g in segs), max(g.end_ms for g in segs)),
        speaker=line.segment.speaker,
        line=line.text,
        heard=_heard_for(aligned, s),
        near=near,
        rejected=list(rejected),
    )


def _placed(
    transcript: RawRecord, whisper: RawRecord, fixes: Sequence[Fix], lists: NeverStore, shapes: NeverStore
) -> list[_Placed]:
    """Each fix line against the latest transcripts (DR-0582): cut, hidden, no longer matching, or at its place."""
    if not fixes:
        return []
    spans = align.cut_spans(transcript, whisper)
    lines, heard = align.said(transcript), align.said(whisper)
    return [_place(fix, lines, heard, spans, lists=lists, shapes=shapes) for fix in fixes]


def _place(
    fix: Fix, lines: list[Segment], heard: list[Segment], spans: list[Cut], *, lists: NeverStore, shapes: NeverStore
) -> _Placed:
    """The anchored recorder line is the one whose rounded span is the anchor, the one its text stands in when two
    round alike; the anchored Whisper segments are those other than `[cut]` inside its anchor, joined by a space, and
    the ones its text touches are the fix's. A line the transcript no longer holds is cut when its anchor overlaps a
    cut, so a stretch cut since never shows its texts."""
    rounded = [i for i, s in enumerate(lines) if (s.start_ms // 1000, -(-s.end_ms // 1000)) == fix.at]
    standing = [i for i in rounded if align.place(fix.left, lines[i].text) is not None]
    line = next(iter(standing or rounded), None)
    if line is None:
        start, end = fix.at[0] * 1000, max(fix.at[1] * 1000, fix.at[0] * 1000 + 1)
        cut = any(c.start < end and c.end > start for c in spans)
    else:
        cut = align.in_cut(lines[line], spans)
    anchored = [s for s in heard if s.start_ms >= fix.heard_at[0] * 1000 and s.end_ms <= fix.heard_at[1] * 1000]
    touched = _touched(anchored, fix.right)
    if cut or any(align.in_cut(s, spans) for s in touched):
        return _Placed(fix, "cut")
    said = (fix.left, fix.right, fix.chosen, lines[line].text if line is not None else "")
    if any(matches_text(text, shapes) or matches_text(text, lists) for text in said):
        return _Placed(fix, "hidden")
    if not standing or not touched:
        return _Placed(fix, "no longer matching")
    return _Placed(fix, None, line, align.place(fix.left, lines[standing[0]].text))


def _touched(anchored: list[Segment], text: str) -> list[Segment]:
    """The anchored segments a text's first place in them touches; none when it doesn't stand there."""
    joined = " ".join(nfc(s.text) for s in anchored)
    found = align.places(text, joined)
    if not found:
        return []
    start, end = found[0]
    out: list[Segment] = []
    at = 0
    for s in anchored:
        size = len(nfc(s.text))
        if at < end and at + size > start:
            out.append(s)
        at += size + 1
    return out


def _view(p: _Placed) -> FixView:
    fix = p.fix
    if p.why in ("cut", "hidden"):
        return FixView(n=fix.n, state=fix.state, why=p.why)
    why = p.why or ("waiting" if fix.state == "unsure" else "rejected" if fix.state == "rejected" else None)
    return FixView(n=fix.n, state=fix.state, why=why, left=fix.left, right=fix.right, chosen=fix.chosen)


# -- reading the vault ---------------------------------------------------------------------------------------------


def doc_id(recording: RawRecord, tz: ZoneInfo) -> str:
    """A new fixes doc's id (DR-0582): the recording's date in the owner's time zone and the first 12 hex digits of
    the sha256 of its rid, so no name sits in a path."""
    return f"{FIXES}:{recording.ts.astimezone(tz).date().isoformat()}-{_digest(recording.rid)}"


def _digest(rid: str) -> str:
    return hashlib.sha256(rid.encode()).hexdigest()[:12]


def doc_of(vault: Vault, recording: RawRecord) -> DocRef:
    """The recording's fixes doc: the one whose id holds its rid's hash, whatever date it says, with its rev as its
    last commit holds it; else the id a new one gets."""
    digest = _digest(recording.rid)
    folder = vault.root / "processed" / FIXES
    shaped = re.compile(rf"^\d{{4}}-\d{{2}}-\d{{2}}-{digest}$")
    for path in sorted(folder.glob(f"*/*/*-{digest}.md")) if folder.is_dir() else []:
        found = f"{FIXES}:{path.stem}"
        if shaped.match(path.stem) and doc_path(found).as_posix() == path.relative_to(vault.root).as_posix():
            text = DocStore(vault).committed_text(found)
            return DocRef(id=found, rev=parse_doc(text, lenient=True).meta.rev if text is not None else None)
    return DocRef(id=doc_id(recording, ZoneInfo(vault.config.owner.time_zone)))


def _recording(lookup: RawLookup, rid: str) -> RawRecord:
    """The recording a rid names: its own, or the one a transcript of it is under."""
    if RID.match(rid) is None or not is_native_id(rid.partition(":")[2]):
        raise DocketError(f"{rid!r} isn't a rid: <source>:<id>")
    record = lookup.latest(rid)
    if record is None:
        raise DocketError(f"no record {rid} is in raw")
    above = lookup.recordings(record)
    return (lookup.latest(above[0]) if above else None) or record


def _pair(vault: Vault, lookup: RawLookup, recording: RawRecord) -> tuple[RawRecord, RawRecord]:
    """The recorder's transcript the recording's summary covers, else its first by rid, the recording's own segments
    first, and Whisper's: the pair the Recording page shows, from raw only."""
    whisper = lookup.latest(f"{recording.rid}{SUFFIX}")
    if whisper is None:
        raise DocketError(f"{recording.rid} has no Whisper transcript in raw; docket transcribe makes one")
    own = [recording] if recording.segments else []
    recorders = [t for t in (*own, *lookup.transcripts(recording.rid)) if t.rid != whisper.rid]
    if not recorders:
        raise DocketError(f"{recording.rid} has no transcript of its own source's in raw to set beside Whisper's")
    covered = _covered(vault, recording.rid)
    return next((t for t in recorders if t.rid in covered), recorders[0]), whisper


def _covered(vault: Vault, rid: str) -> list[str]:
    """What the summary search attaches to the recording covers; nothing while there is no index to ask."""
    try:
        ref = index.summary_for(vault, rid)
    except DocketError:
        return []
    doc = DocStore(vault).get(ref.id) if ref is not None else None
    return doc.meta.covers if doc is not None else []


def _why(vault: Vault, lookup: RawLookup, rid: str, staging: Staging) -> str | None:
    """Why a recording gets no spots here: another machine ingests its source, or the family screen keeps it out.
    Never a value: the screen's reasons name rids and sources only."""
    source = layout.source_of(rid)
    if source not in cursors.read(vault.root, vault.machine):
        return f"this machine doesn't ingest {source}; reconcile its recordings on the machine that runs it"
    found = reader.kept_out(vault, rid, lookup, staging, staging.scan().entries)
    return f"its family is kept out: {found[1]}" if found is not None else None


@dataclass(frozen=True)
class _Vault:
    """What every recording's spots read of the vault, once."""

    known: list[Entity]
    lists: NeverStore
    shapes: NeverStore
    languages: list[str]


def _read(vault: Vault) -> _Vault:
    known = load_entities(vault.root).entities
    lists = vault.config.never_store
    return _Vault(known, lists, brief_form.phrases(known, lists), vault.config.owner.languages)


def _spots(
    vault: Vault, lookup: RawLookup, read: _Vault, recording: RawRecord, pair: tuple[RawRecord, RawRecord], *,
    fixes: Sequence[Fix],
) -> tuple[list[Entity], Names, Found]:  # fmt: skip
    """A pair's spots, with the meeting's entities and the names they were matched against."""
    transcript, whisper = pair
    family = [r for other in lookup.nested(recording.rid) if (r := lookup.latest(other)) is not None]
    chosen = meeting.entities([r for r in family if r.rid != whisper.rid], read.known, vault.config)
    names = Names.of([*chosen, *meeting.glossary(read.known, vault.config)])
    return chosen, names, find(transcript, whisper, names, lists=read.lists, shapes=read.shapes, fixes=fixes)


def report(vault: Vault, rid: str, fixes: Sequence[Fix] = ()) -> SpotsReport:
    """A recording's spots, by its rid or either transcript's, or why it has none here."""
    read = _read(vault)
    with recall.raw_lookup(vault) as lookup:
        recording = _recording(lookup, rid)
        transcript, whisper = _pair(vault, lookup, recording)
        doc = doc_of(vault, recording)
        out = SpotsReport(rid=recording.rid, transcript=transcript.rid, whisper=whisper.rid, doc=doc)
        out.why = _why(vault, lookup, recording.rid, Staging(vault.root, vault.home))
        if out.why is not None:
            return out
        chosen, names, found = _spots(vault, lookup, read, recording, (transcript, whisper), fixes=fixes)
    by_id = {e.id: e for e in names.entities}
    meeting_ids = {e.id for e in chosen}
    shown = [*chosen, *(by_id[e] for e in found.near if e not in meeting_ids)]
    out.people = [_person(e, read.languages) for e in shown]
    out.spots, out.fixes, out.left_out = found.spots, found.fixes, found.left_out
    return out


def _person(entity: Entity, languages: Sequence[str]) -> Person:
    name = next((entity.names[lang] for lang in languages if lang in entity.names), None)
    aliases = [Alias(text=a.text, asr=a.kind == "asr") for a in entity.aliases]
    return Person(id=entity.id, type=entity.type, name=name or entity.names[min(entity.names)], aliases=aliases)


def listing(vault: Vault, fixes: Mapping[str, Sequence[Fix]] | None = None) -> Listing:
    """The recordings to reconcile: at most 10 of the newest 30 with both transcripts in raw, from the sources this
    machine ingests, that pass the screen and have spots no fix decides or fixes waiting for the owner; and how many
    the screen kept out. A settled place counts toward neither."""
    read = _read(vault)
    ingested = cursors.read(vault.root, vault.machine)
    staging = Staging(vault.root, vault.home)
    entries = staging.scan().entries
    out = Listing()
    with recall.raw_lookup(vault) as lookup:
        newest: list[RawRecord] = []
        for source in sorted(s for s in ingested if s in vault.config.sources):
            for rid in lookup.of_kind(source, "transcript"):
                recording = lookup.latest(rid.removesuffix(SUFFIX)) if rid.endswith(SUFFIX) else None
                newest += [recording] if recording is not None else []
        for recording in sorted(newest, key=lambda r: (r.ts, r.rid), reverse=True)[:NEWEST]:
            if reader.kept_out(vault, recording.rid, lookup, staging, entries) is not None:
                out.hidden += 1
                continue
            if len(out.recordings) >= MOST:
                continue
            try:
                pair = _pair(vault, lookup, recording)
            except DocketError:
                continue
            found = _spots(vault, lookup, read, recording, pair, fixes=(fixes or {}).get(recording.rid, ()))[2]
            if any(found.undecided.values()) or found.waiting:
                title = recording.title
                out.recordings.append(
                    Waiting(
                        rid=recording.rid,
                        title=title if title and not matches_text(title, read.shapes) else None,
                        ts=recording.ts,
                        spots=found.undecided,
                        waiting=found.waiting,
                    )
                )
    return out
