"""Where a recording's two transcripts disagree, line by line of the recorder's (DR-0583). Segments in, stretches out:
`spots.py` gives them their kinds, screens and ranks them.

| Step | What |
|---|---|
| words | `plaintext.WORD`'s matches in NFC, at their places, compared casefolded with accents kept; fillers left out |
| cuts | every `[cut]` span of either transcript, merged; a segment overlapping one by its own times goes whole |
| times | each Whisper word at its share of its segment's characters, from its middle |
| lines | a word goes to the recorder line its time falls in, else to the nearer one within a second |
| crosstalk | a word two lines hold goes to the one its segment matches more words of, the earlier on a tie |
| align | each line's words against the words it was given, by difflib's `SequenceMatcher`, autojunk off |
| edges | an insert or delete at a line's end is dropped only when its words are the neighbouring line's at that edge |
| widen | by the agreeing word each side until the recorder's text stands once in its line; past six words, not listed |
| merge | stretches whose places in one line share a character are one |

Words are compared as `docket eval speech` scores them (DR-0571). A text is a substring of the line, or of Whisper's
segments other than `[cut]` joined by a space in time order, from its first word's first character to its last word's
last character: case, inner punctuation and spacing as written.
"""

from __future__ import annotations

import bisect
import difflib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from docket.plaintext import WORD, nfc
from docket.vault import cuts as cutting
from docket.vault.cuts import CUT_MARKER, Cut
from docket.vault.records import RawRecord, Segment
from docket.vault.segments import segments

# The filler sounds left out of the comparison, by language; a transcript with no language leaves out every one.
FILLERS: dict[str, frozenset[str]] = {
    "en": frozenset({"um", "umm", "uh", "uhm", "er", "erm", "hm", "hmm", "mm", "mhm"}),
    "hu": frozenset({"öö", "ööö", "öhm", "hm", "hmm", "mm"}),
}
# How far a word Whisper timed between two recorder lines may sit from the nearer one, in milliseconds.
GAP_MS = 1000
# The most words a text may hold on either side; past it the stretch was heard differently.
MAX_WORDS = 6


def fillers(lang: str | None) -> frozenset[str]:
    return FILLERS[lang] if lang in FILLERS else frozenset[str]().union(*FILLERS.values())


@dataclass(frozen=True)
class Word:
    """A word at its place in a text: as written, as compared, where it is, and for Whisper's, its time and its
    segment."""

    text: str
    key: str
    start: int
    end: int
    at: int = 0
    segment: int = 0


def words(text: str, lang: str | None) -> list[Word]:
    """A text's words at their places, the language's filler sounds left out; `text` is in NFC, as every place is
    taken."""
    left_out = fillers(lang)
    found = (Word(m.group(), m.group().casefold(), m.start(), m.end()) for m in WORD.finditer(text))
    return [w for w in found if w.key not in left_out]


def _starts(text: str, line: str) -> list[int]:
    found: list[int] = []
    at = line.find(text) if text else -1
    while at >= 0:
        end = at + len(text)
        if (at == 0 or not line[at - 1].isalnum()) and (end == len(line) or not line[end].isalnum()):
            found.append(at)
        at = line.find(text, at + 1)
    return found


def places(text: str, line: str) -> list[tuple[int, int]]:
    """Where a text stands in a line, the line read in NFC: each place it occurs with no letter or digit either side.
    Both are compared in NFC, so a line stored decomposed still holds a text written composed."""
    text = nfc(text)
    return [(at, at + len(text)) for at in _starts(text, nfc(line))]


def place(text: str, line: str) -> tuple[int, int] | None:
    """The place of a text that stands once in a line, else None."""
    found = places(text, line)
    return found[0] if len(found) == 1 else None


def cut_spans(*records: RawRecord) -> list[Cut]:
    """Every `[cut]` span of the transcripts, merged."""
    spans = [
        Cut(start=s.start_ms, end=max(s.end_ms, s.start_ms + 1), unit="ms")
        for record in records
        for s in record.segments or []
        if s.text == CUT_MARKER
    ]
    return cutting.merge(spans)


def in_cut(segment: Segment, spans: Sequence[Cut]) -> bool:
    """Whether a segment overlaps a cut by its own times, as `cuts.py` takes a segment."""
    return any(cutting.removes([segment], cut) for cut in spans)


@dataclass(frozen=True)
class Line:
    """A recorder line outside every cut: its place among the transcript's lines other than `[cut]`, its segment, its
    text in NFC and its words."""

    index: int
    segment: Segment
    text: str
    words: tuple[Word, ...]

    @property
    def start(self) -> int:
        return self.segment.start_ms

    @property
    def end(self) -> int:
        # A line with no length still sits at its millisecond, as a cut takes it.
        return max(self.segment.end_ms, self.segment.start_ms + 1)


def said(record: RawRecord) -> list[Segment]:
    """A transcript's lines other than `[cut]`, in time order: what a fix's anchor is read against."""
    return [s for s in segments(record) if s.text != CUT_MARKER]


def lines(record: RawRecord, spans: Sequence[Cut]) -> list[Line]:
    """The recorder's lines outside every cut, in time order."""
    out: list[Line] = []
    for i, s in enumerate(said(record)):
        if not in_cut(s, spans):
            text = nfc(s.text)
            out.append(Line(i, s, text, tuple(words(text, record.lang))))
    return out


@dataclass(frozen=True)
class Heard:
    """Whisper's transcript as one text: its segments other than `[cut]` in time order, joined by a space, as a fix's
    anchored segments are read (DR-0582); its words outside every cut, each with its time and its segment; and the
    places in it of the segments a cut overlaps, whose text is never shown."""

    text: str
    segments: tuple[Segment, ...]
    words: tuple[Word, ...]
    cut: tuple[tuple[int, int], ...] = ()

    def span(self, ks: Sequence[int]) -> tuple[int, int] | None:
        """The place of the text from the first of these words to the last."""
        return (self.words[ks[0]].start, self.words[ks[-1]].end) if ks else None

    def shown(self, span: tuple[int, int] | None) -> str:
        """The text at a place, each segment a cut overlaps in it read `[cut]`."""
        if span is None:
            return ""
        out: list[str] = []
        at = span[0]
        for start, end in self.cut:
            if start < span[1] and end > at:
                out += [self.text[at : max(at, start)], CUT_MARKER]
                at = min(end, span[1])
        return "".join([*out, self.text[at : span[1]]])


def heard(record: RawRecord, spans: Sequence[Cut]) -> Heard:
    """Whisper's transcript joined, each word outside every cut timed at its share of its segment's characters."""
    segs = said(record)
    parts: list[str] = []
    found: list[Word] = []
    cut: list[tuple[int, int]] = []
    at = 0
    for i, s in enumerate(segs):
        text = nfc(s.text)
        if in_cut(s, spans):
            cut.append((at, at + len(text)))
        else:
            length = s.end_ms - s.start_ms
            found += [
                Word(
                    w.text,
                    w.key,
                    at + w.start,
                    at + w.end,
                    s.start_ms + length * (w.start + w.end) // (2 * len(text)),
                    i,
                )
                for w in words(text, record.lang)
            ]
        parts.append(text)
        at += len(text) + 1
    return Heard(" ".join(parts), tuple(segs), tuple(found), tuple(cut))


def given(lines: Sequence[Line], heard: Heard) -> list[list[int]]:
    """The Whisper words each recorder line is given, by their index in `heard.words`, in time order."""
    starts = [line.start for line in lines]
    # The latest end of the lines up to each, and the first line that reaches it: no line before can hold a word past
    # it, and it is the nearer line before a gap.
    reach: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        reach.append((line.end, i) if not reach or line.end > reach[-1][0] else reach[-1])
    out: list[list[int]] = [[] for _ in lines]
    scores: dict[tuple[int, int], int] = {}
    keys: dict[int, list[str]] = {}
    for word in heard.words:
        keys.setdefault(word.segment, []).append(word.key)
    for k, word in enumerate(heard.words):
        last = bisect.bisect_right(starts, word.at) - 1
        hits: list[int] = []
        i = last
        while i >= 0 and reach[i][0] > word.at:
            if lines[i].end > word.at:
                hits.append(i)
            i -= 1
        if not hits:
            near = _nearer(lines, reach, last, word.at)
            if near is None:
                continue
            hits = [near]
        elif len(hits) > 1:
            hits = [max(sorted(hits), key=lambda i: _score(scores, keys[word.segment], word.segment, lines[i], i))]
        out[hits[0]].append(k)
    return out


def _nearer(lines: Sequence[Line], reach: list[tuple[int, int]], last: int, at: int) -> int | None:
    """The nearer of the lines either side of a word in a gap, when it is at most a second away: the one before on a
    tie."""
    before = (at - reach[last][0], reach[last][1]) if last >= 0 else None
    after = (lines[last + 1].start - at, last + 1) if last + 1 < len(lines) else None
    found = min((side for side in (before, after) if side is not None), default=None)
    return found[1] if found is not None and found[0] <= GAP_MS else None


def _score(scores: dict[tuple[int, int], int], theirs: list[str], segment: int, line: Line, i: int) -> int:
    """How many of a recorder line's words a Whisper segment's words match: which of two lines talking over each
    other a word of that segment belongs to."""
    if (segment, i) not in scores:
        mine = [w.key for w in line.words]
        blocks = difflib.SequenceMatcher(None, theirs, mine, autojunk=False).get_matching_blocks()
        scores[segment, i] = sum(b.size for b in blocks)
    return scores[segment, i]


@dataclass(frozen=True)
class Stretch:
    """Where a line and Whisper disagree, or a word both spell alike that the caller marks: its words on each side,
    by index into the line's words and into the words the line was given. `cores` are the stretches before widening,
    the words that differ."""

    line: int
    i1: int
    i2: int
    j1: int
    j2: int
    alike: bool = False
    cores: tuple[Stretch, ...] = ()


@dataclass(frozen=True)
class Aligned:
    """A transcript pair aligned: the recorder's lines, Whisper's text, and the words each line was given."""

    lines: list[Line]
    heard: Heard
    given: list[list[int]]

    def left(self, s: Stretch) -> tuple[int, int] | None:
        """The place of a stretch's text in its line."""
        found = self.lines[s.line].words[s.i1 : s.i2]
        return (found[0].start, found[-1].end) if found else None

    def right(self, s: Stretch) -> tuple[int, int] | None:
        """The place of a stretch's text in Whisper's text."""
        return self.heard.span(self.given[s.line][s.j1 : s.j2])

    def texts(self, s: Stretch) -> tuple[str, str]:
        left, right = self.left(s), self.right(s)
        line = self.lines[s.line].text
        return line[left[0] : left[1]] if left else "", self.heard.text[right[0] : right[1]] if right else ""

    def sides(self, s: Stretch) -> tuple[list[Word], list[Word]]:
        """A stretch's words on each side."""
        given = self.given[s.line][s.j1 : s.j2]
        return list(self.lines[s.line].words[s.i1 : s.i2]), [self.heard.words[k] for k in given]


def align(transcript: RawRecord, whisper: RawRecord) -> Aligned:
    """The recorder's transcript and Whisper's, latest versions, with every cut either holds."""
    spans = cut_spans(transcript, whisper)
    found = lines(transcript, spans)
    words_heard = heard(whisper, spans)
    return Aligned(found, words_heard, given(found, words_heard))


def _unmarked(line: Line, word: Word) -> bool:
    return False


def _kept(stretch: Stretch) -> bool:
    return True


def candidates(aligned: Aligned, alike: Callable[[Line, Word], bool] = _unmarked) -> list[Stretch]:
    """Every replace, insert and delete of each line against the words it was given, but an insert or delete whose
    words Whisper's timing moved across the line's edge; and each word both spell alike that `alike` marks."""
    out: list[Stretch] = []
    for n, line in enumerate(aligned.lines):
        mine = [w.key for w in line.words]
        theirs = [aligned.heard.words[k].key for k in aligned.given[n]]
        if not mine or not theirs:
            continue
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, mine, theirs, autojunk=False).get_opcodes():
            if tag == "equal":
                out += [
                    Stretch(n, i, i + 1, j1 + i - i1, j1 + i - i1 + 1, True)
                    for i in range(i1, i2)
                    if alike(line, line.words[i])
                ]
            elif not _moved(aligned, n, (tag, i1, i2, j1, j2)):
                out.append(Stretch(n, i1, i2, j1, j2))
    return out


def _moved(aligned: Aligned, n: int, op: tuple[str, int, int, int, int]) -> bool:
    """Whether an insert or delete at a line's end holds the words at the touching edge of the neighbouring line: an
    insert's are that line's first (or last) words, a delete's the first (or last) Whisper words it was given."""
    tag, i1, i2, j1, j2 = op
    if tag == "replace":
        return False
    lines, given, heard = aligned.lines, aligned.given, aligned.heard

    def keys(found: Sequence[Word]) -> list[str]:
        return [w.key for w in found]

    def given_to(m: int) -> list[Word]:
        return [heard.words[k] for k in given[m]]

    size = len(lines[n].words)
    moved = keys(given_to(n)[j1:j2]) if tag == "insert" else keys(lines[n].words[i1:i2])
    count = len(moved)
    if i2 == size and n + 1 < len(lines):
        edge = lines[n + 1].words[:count] if tag == "insert" else given_to(n + 1)[:count]
        if keys(edge) == moved:
            return True
    if i1 == 0 and n > 0:
        edge = lines[n - 1].words[-count:] if tag == "insert" else given_to(n - 1)[-count:]
        return keys(edge) == moved
    return False


def widen(aligned: Aligned, s: Stretch) -> Stretch | None:
    """A stretch widened by the agreeing word on each side, both transcripts alike, until neither text is empty and
    the recorder's stands once in its line; None when it is past six words on either side first, or can't widen."""
    line = aligned.lines[s.line]
    size, given = len(line.words), len(aligned.given[s.line])
    out = replace(s, cores=(s,))
    while True:
        left, right = aligned.texts(out)
        if _count(left) > MAX_WORDS or _count(right) > MAX_WORDS:
            return None
        if left and right and len(_starts(left, line.text)) == 1:
            return out
        i1, i2, j1, j2 = out.i1, out.i2, out.j1, out.j2
        if i1 > 0 and j1 > 0:
            i1, j1 = i1 - 1, j1 - 1
        if i2 < size and j2 < given:
            i2, j2 = i2 + 1, j2 + 1
        if (i1, i2, j1, j2) == (out.i1, out.i2, out.j1, out.j2):
            return None
        out = replace(out, i1=i1, i2=i2, j1=j1, j2=j2)


def _count(text: str) -> int:
    return len(WORD.findall(text))


def stretches(
    aligned: Aligned,
    alike: Callable[[Line, Word], bool] = _unmarked,
    keep: Callable[[Stretch], bool] = _kept,
) -> tuple[list[Stretch], int]:
    """The candidates `keep` keeps, widened, those whose places overlap made one; and how many were heard
    differently."""
    return merged(aligned, [widen(aligned, c) for c in candidates(aligned, alike) if keep(c)])


def merged(aligned: Aligned, found: Sequence[Stretch | None]) -> tuple[list[Stretch], int]:
    """Widened stretches whose places in one line share a character made one, and how many were heard differently:
    a stretch that couldn't widen, or that grew past six words once made one."""
    differently = sum(1 for s in found if s is None)
    ordered = sorted((s for s in found if s is not None), key=lambda s: (s.line, s.i1, s.i2))
    out: list[Stretch] = []
    for s in ordered:
        last = out[-1] if out else None
        if last is not None and last.line == s.line and overlap(aligned.left(last), aligned.left(s)):
            out[-1] = replace(
                last,
                i1=min(last.i1, s.i1),
                i2=max(last.i2, s.i2),
                j1=min(last.j1, s.j1),
                j2=max(last.j2, s.j2),
                alike=last.alike and s.alike,
                cores=last.cores + s.cores,
            )
        else:
            out.append(s)
    kept: list[Stretch] = []
    for s in out:
        left, right = aligned.texts(s)
        if _count(left) > MAX_WORDS or _count(right) > MAX_WORDS:
            differently += 1
        else:
            kept.append(s)
    return kept, differently


def overlap(a: tuple[int, int] | None, b: tuple[int, int] | None) -> bool:
    return a is not None and b is not None and a[0] < b[1] and b[0] < a[1]
