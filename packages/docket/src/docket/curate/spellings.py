"""The miner's first pass (DR-0466): every spelling the index's chunks hold capitalised, how far each is trusted by
where it was written, and how often each word is capitalised, for tier 3's count of its lower-case uses.

| Trust | Where | Why |
|---|---|---|
| label | a transcript's speaker label, not a generic one (`Speaker 1`) | the user typed it in the recorder's app |
| docs | a processed doc | the user's own notes and minutes |
| typed | a message, a thread, a mail, an event or another record people typed, its title included | people typed it |
| vendor | a vendor summary, a recording's title | a vendor's model wrote it from what it heard |
| heard | a transcript's text, each turn's label left out | speech recognition |

The first three are written, the last two heard. A spelling is a capitalised word, or a run of two or three in one
line with only spaces between them, an all-caps word of 2 to 6 letters, or a mixed-case word. A word at a sentence's
start (a line's first word, or after `.`, `!` or `?`) is counted apart, since every word is capitalised there. The
words are the index's (`[^\\W_]+`), so `Ada's` reads as `Ada` and a possessive is its name.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from docket.index.text import fold

TRUSTS = ("label", "docs", "typed", "vendor", "heard")
LABEL, DOCS, TYPED, VENDOR, HEARD = range(len(TRUSTS))
# A spelling with this trust or better is written; one past it is heard.
WRITTEN = TYPED
# An all-caps word longer than this is a heading shouted, not a name.
CAPS = 6
RUN = 3
WORD = re.compile(r"[^\W_]+")
SENTENCE_END = re.compile(r"[.!?]")
# The words recorders use for a speaker they can't name, in the owner's languages, and labels of a number or a letter.
GENERIC_WORDS = frozenset({"speaker", "spk", "unknown", "beszelo", "ismeretlen", "resztvevo"})
GENERIC = re.compile(r"^(?:speaker|spk|unknown|beszélő|beszelo|ismeretlen|résztvevő|resztvevo)?[\s_-]*\w{0,2}$", re.I)
SQL = (
    "SELECT c.id, c.kind, c.doc, c.speakers, c.text, r.kind FROM chunks c LEFT JOIN records r ON r.rid = c.rid "
    "ORDER BY c.id"
)


@dataclass
class Seen:
    """One spelling, as written, and what the chunks say about it."""

    text: str
    words: tuple[str, ...]
    # Times capitalised away from a sentence's start, and at one; for a run, where its first word stands.
    mid: int = 0
    start: int = 0
    # For a word, the times it stood alone, outside a run: one only ever inside a run is that run's.
    alone: int = 0
    trust: int = HEARD
    label: bool = False
    chunks: set[int] = field(default_factory=set[int])

    @property
    def written(self) -> bool:
        return self.trust <= WRITTEN


@dataclass
class Scan:
    seen: dict[str, Seen] = field(default_factory=dict[str, Seen])
    # A word's capitalised uses, folded, a turn's label included: the index counts every use; the rest are lower case.
    cased: Counter[str] = field(default_factory=Counter[str])
    trust_of: dict[int, int] = field(default_factory=dict[int, int])

    def see(
        self, text: str, chunk: int, trust: int, *, start: bool = False, label: bool = False, alone: bool = False
    ) -> None:
        found = self.seen.get(text)
        if found is None:
            found = self.seen[text] = Seen(text, tuple(fold(w) for w in WORD.findall(text)))
        if start:
            found.start += 1
        else:
            found.mid += 1
        found.alone += alone
        found.trust = min(found.trust, trust)
        found.label |= label
        found.chunks.add(chunk)

    def run(self, run: list[tuple[str, bool]], chunk: int, trust: int) -> None:
        """A run of capitalised words: each word, alone when the run is one word, and the run when it is two or
        three."""
        for word, start in run:
            self.see(word, chunk, trust, start=start, alone=len(run) == 1)
        if 1 < len(run) <= RUN:
            self.see(" ".join(word for word, _ in run), chunk, trust, start=run[0][1])

    def line(self, line: str, chunk: int, trust: int) -> None:
        """One line's capitalised words and runs; the rest are only stepped over."""
        prev = 0
        run: list[tuple[str, bool]] = []
        for m in WORD.finditer(line):
            word = m.group()
            # Most words are lower case: they only end a run, and nothing else is worked out for them.
            if word.islower() or not cased(word):
                if run:
                    self.run(run, chunk, trust)
                    run = []
                prev = m.end()
                continue
            start = prev == 0 or SENTENCE_END.search(line, prev, m.start()) is not None
            plain = not line[prev : m.start()].strip(" \t")
            prev = m.end()
            self.cased[fold(word)] += 1
            if run and plain and not start:
                run.append((word, start))
                continue
            self.run(run, chunk, trust)
            run = [(word, start)]
        self.run(run, chunk, trust)


def cased(word: str) -> bool:
    """A capitalised word, a mixed-case one (`eBay`), or all caps up to six letters; never one starting with a digit."""
    if word.islower() or not word[0].isalpha():
        return False
    return not (word.isupper() and len(word) > CAPS)


def trust_of(kind: str, doc: str | None, record_kind: str | None) -> int:
    if doc is not None:
        return DOCS
    if kind == "transcript" or record_kind == "transcript":
        return HEARD
    return VENDOR if record_kind in ("vendor-summary", "recording") else TYPED


def generic(label: str) -> bool:
    return GENERIC.match(label.strip()) is not None


def scan(conn: sqlite3.Connection) -> Scan:
    """Every chunk's capitalised spellings and its speakers' labels, in one pass over the chunks."""
    out = Scan()
    for chunk, kind, doc, said, text, record_kind in conn.execute(SQL):
        trust = trust_of(kind, doc, record_kind)
        out.trust_of[chunk] = trust
        speakers: list[str] = json.loads(said)
        prefixes = tuple(f"{s}: " for s in speakers)
        for label in speakers:
            if not generic(label):
                out.see(label.strip(), chunk, LABEL, label=True, alone=True)
        for line in text.split("\n"):
            if prefixes and line.startswith(prefixes):
                prefix = next(p for p in prefixes if line.startswith(p))
                out.cased.update(fold(w) for w in WORD.findall(prefix) if cased(w))
                line = line[len(prefix) :]  # noqa: PLW2901 - the turn's words, its label left out
            out.line(line, chunk, trust)
    return out
