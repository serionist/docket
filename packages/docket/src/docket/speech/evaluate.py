"""`docket eval speech <file>` (DR-0571): each engine's transcripts against stretches the owner corrected by hand.

A speech file is YAML of schema 1, kept outside the repo as the question file is (DR-0290), and a one-way door:

    schema_version: 1
    stretches:
      - at: "r:plaud:<id>@12:30-13:05"
        text: the stretch as it was said
        names: [Ada Example, Crane Works]

For each stretch the recording's transcripts are read (its own segments, and every transcript under it), each by its
engine: the segments whose middle falls in the stretch, `[cut]` left out. Word and character error rates come after
lowercasing and stripping punctuation, keeping diacritics. The name error rate is the share of the listed names missing
from an engine's text, accents and case folded; the names it added are the entities' names and aliases in its text that
the corrected text lacks, an entity a never-store list holds counted and never named. A file inside a git work tree is
refused. The vault is only read.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel

from docket.errors import DocketError
from docket.vault import neverstore
from docket.vault.anchors import AnchorError, parse_anchor
from docket.vault.cuts import CUT_MARKER
from docket.vault.entities import Entity, load_entities
from docket.vault.git import toplevel
from docket.vault.records import RawRecord
from docket.vault.spans import RawLookup
from docket.vault.vault import Vault

SCHEMA = 1
FILE_KEYS = ("schema_version", "stretches")
STRETCH_KEYS = ("at", "text", "names", "note")
RECORDER = "the recorder"


class SpeechFileError(DocketError):
    """A speech file that can't be read: the file, and the stretch by its number, with what is wrong."""


@dataclass(frozen=True)
class Stretch:
    n: int
    rid: str
    start_ms: int
    end_ms: int
    text: str
    names: tuple[str, ...]


class EngineScore(BaseModel):
    """One engine over the stretches it has a transcript for."""

    engine: str
    stretches: int
    words: int
    wer: float
    cer: float
    names: int
    names_missed: int
    name_error: float | None
    added: list[str]
    # Names it added of entities a never-store list holds: counted, never shown.
    hidden: int = 0


class SpeechReport(BaseModel):
    file: str
    stretches: int
    engines: list[EngineScore]
    # The stretches no transcript reached, by their number in the file.
    unmatched: list[int] = []


def normal(text: str) -> list[str]:
    """Words for the error rates: lowercase, punctuation gone, diacritics kept."""
    kept = "".join(" " if unicodedata.category(c).startswith("P") else c for c in text.casefold())
    return kept.split()


def edits(ref: Sequence[str], hyp: Sequence[str]) -> int:
    """The Levenshtein distance between two sequences."""
    before = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        now = [i]
        for j, h in enumerate(hyp, start=1):
            now.append(min(before[j] + 1, now[j - 1] + 1, before[j - 1] + (r != h)))
        before = now
    return before[-1]


def says(text: str, name: str) -> bool:
    """Whether a text says a name, as whole words, accents and case folded."""
    folded = neverstore.fold(name).strip()
    return bool(folded) and re.search(rf"(?<!\w){re.escape(folded)}(?!\w)", neverstore.fold(text)) is not None


def load(path: Path) -> list[Stretch]:
    """A speech file's stretches; it is only read. It holds real speech corrected by hand, so one inside a git work
    tree, where a commit could take it, is refused."""
    if toplevel(path.resolve().parent) is not None:
        raise SpeechFileError(
            f"{path} is inside a git work tree, where a commit could take it; keep your speech file outside any repo"
        )
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise SpeechFileError(f"{path}: no such file") from None
    except (OSError, UnicodeDecodeError) as e:
        raise SpeechFileError(f"{path}: can't be read: {e}") from None
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError:
        raise SpeechFileError(f"{path}: isn't YAML") from None
    if not isinstance(data, dict):
        raise SpeechFileError(f"{path}: a speech file is a map with schema_version and stretches")
    top = cast(dict[str, Any], data)
    unknown = sorted(set(top) - set(FILE_KEYS))
    if unknown:
        raise SpeechFileError(f"{path}: unknown key {unknown[0]!r}; a speech file has {', '.join(FILE_KEYS)}")
    if top.get("schema_version") != SCHEMA:
        raise SpeechFileError(f"{path}: schema_version is {SCHEMA}")
    items = top.get("stretches")
    if not isinstance(items, list) or not items:
        raise SpeechFileError(f"{path}: stretches is a list of at least one")
    return [_stretch(path, n, item) for n, item in enumerate(cast(list[Any], items), start=1)]


def _stretch(path: Path, n: int, item: Any) -> Stretch:
    where = f"{path}: stretch {n}"
    if not isinstance(item, dict):
        raise SpeechFileError(f"{where}: a stretch is a map with at, text and names")
    fields = cast(dict[str, Any], item)
    unknown = sorted(set(fields) - set(STRETCH_KEYS))
    if unknown:
        raise SpeechFileError(f"{where}: unknown key {unknown[0]!r}")
    at, said, names = fields.get("at"), fields.get("text"), fields.get("names", [])
    if not isinstance(at, str):
        raise SpeechFileError(f"{where}: at is a recording's stretch, r:<rid>@mm:ss-mm:ss")
    try:
        anchor = parse_anchor(f"[{at}]")
    except AnchorError as e:
        raise SpeechFileError(f"{where}: {e}") from None
    if anchor.kind != "time" or anchor.end is None or anchor.rid is None:
        raise SpeechFileError(f"{where}: at is a stretch of a recording, r:<rid>@mm:ss-mm:ss")
    if not isinstance(said, str) or not said.strip():
        raise SpeechFileError(f"{where}: text is the stretch as it was said")
    if not isinstance(names, list) or not all(isinstance(x, str) for x in cast(list[Any], names)):
        raise SpeechFileError(f"{where}: names is a list of names")
    listed = tuple(cast(list[str], names))
    return Stretch(n, anchor.rid, anchor.start * 1000, (anchor.end + 1) * 1000, said, listed)


@dataclass
class _Tally:
    stretches: int = 0
    words: int = 0
    word_edits: int = 0
    chars: int = 0
    char_edits: int = 0
    names: int = 0
    missed: int = 0
    added: set[str] = field(default_factory=set[str])
    hidden: set[str] = field(default_factory=set[str])


def _forms(entity: Entity) -> list[str]:
    return [f for f in (*entity.names.values(), *(a.text for a in entity.aliases)) if f]


def _engine(record: RawRecord) -> str:
    return record.engine or RECORDER


def heard(record: RawRecord, stretch: Stretch) -> str:
    """A transcript's words in a stretch: the segments whose middle falls in it, `[cut]` left out."""
    kept = [
        s.text
        for s in record.segments or []
        if s.text != CUT_MARKER and stretch.start_ms <= (s.start_ms + s.end_ms) // 2 < stretch.end_ms
    ]
    return " ".join(kept)


def transcripts(lookup: RawLookup, rid: str) -> list[RawRecord]:
    """A recording's transcripts at their latest versions: its own segments, then each under it."""
    own = lookup.latest(rid)
    found = [own] if own is not None and own.segments else []
    return found + [t for t in lookup.transcripts(rid) if t.rid != rid]


def run(vault: Vault, path: Path, stretches: Iterable[Stretch]) -> SpeechReport:
    lookup = RawLookup(vault.root)
    lists = vault.config.never_store
    entities = load_entities(vault.root).entities
    held = [e for e in entities if neverstore.person_listed(e, lists) or neverstore.slug_listed(e.id, lists)]
    known = sorted({form for e in entities if e not in held for form in _forms(e)})
    hidden = sorted({form for e in held for form in _forms(e)})
    tallies: dict[str, _Tally] = {}
    unmatched: list[int] = []
    asked = list(stretches)
    for stretch in asked:
        found = [(t, heard(t, stretch)) for t in transcripts(lookup, stretch.rid)]
        found = [(t, text) for t, text in found if text]
        if not found:
            unmatched.append(stretch.n)
            continue
        ref_words = normal(stretch.text)
        ref_chars = list(" ".join(ref_words))
        for record, text in found:
            tally = tallies.setdefault(_engine(record), _Tally())
            words = normal(text)
            tally.stretches += 1
            tally.words += len(ref_words)
            tally.word_edits += edits(ref_words, words)
            tally.chars += len(ref_chars)
            tally.char_edits += edits(ref_chars, list(" ".join(words)))
            tally.names += len(stretch.names)
            tally.missed += sum(1 for name in stretch.names if not says(text, name))
            tally.added |= {name for name in known if says(text, name) and not says(stretch.text, name)}
            tally.hidden |= {name for name in hidden if says(text, name) and not says(stretch.text, name)}
    engines = [
        EngineScore(
            engine=engine,
            stretches=t.stretches,
            words=t.words,
            wer=t.word_edits / t.words if t.words else 0.0,
            cer=t.char_edits / t.chars if t.chars else 0.0,
            names=t.names,
            names_missed=t.missed,
            name_error=t.missed / t.names if t.names else None,
            added=sorted(t.added),
            hidden=len(t.hidden),
        )
        for engine, t in sorted(tallies.items())
    ]
    return SpeechReport(file=str(path), stretches=len(asked), engines=engines, unmatched=unmatched)


def lines(report: SpeechReport) -> list[str]:
    """`docket eval speech`'s table: a row per engine, then the names each added and the stretches none reached."""
    out = [f"eval      {report.file}: {report.stretches} stretch(es)"]
    out.append(f"{'engine':<46}{'n':>4}{'words':>7}{'WER':>8}{'CER':>8}{'names':>7}{'missed':>8}")
    for e in report.engines:
        missed = f"{e.name_error:.1%}" if e.name_error is not None else "-"
        out.append(f"{e.engine:<46}{e.stretches:>4}{e.words:>7}{e.wer:>8.1%}{e.cer:>8.1%}{e.names:>7}{missed:>8}")
    for e in report.engines:
        said = [*e.added, *([f"{e.hidden} hidden (a never-store list holds them)"] if e.hidden else [])]
        if said:
            out.append(f"added     {e.engine}: {', '.join(said)}")
    if report.unmatched:
        out.append(f"no transcript reached stretch(es) {', '.join(str(n) for n in report.unmatched)}")
    return out
