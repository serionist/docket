"""The question set: questions with the locators a good answer cites, run through search and scored.

A question file is YAML of schema 1, a one-way door since users keep their own (DR-0290):

    schema_version: 1
    asked: "2026-10-14T10:00:00+02:00"
    questions:
      - q: money up front for the translator
        want: ["r:mail:<advance-1@example.invalid>#2"]
        kind: paraphrase
        lang: en

A wanted locator is `r:<rid>` with an optional time, stretch or paragraphs, a doc id with an optional `#L<n>` or
`#L<n>-<m>`, or `thread:<rid>`. A hit answers one when it is on the same record, doc or thread with positions that
overlap, or when it holds or cites it: a thread window its messages, a doc's section the raw its anchors point into
(DR-0291). A question's recall at k is the share of its wanted locators answered in the top k, its reciprocal rank one
over the rank of the first hit answering any; kinds, languages and the set take the mean (DR-0292). The vault and its
index are only read (DR-0293).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel

from docket.errors import DocketError
from docket.index import SearchResult, parse_when, search, status
from docket.index.build import open_index
from docket.index.query import MAX_K
from docket.vault.anchors import Anchor, AnchorError, parse_anchor
from docket.vault.processed import DOC_TYPES, id_problem
from docket.vault.records import RID
from docket.vault.vault import Vault

SCHEMA = "1"
KINDS = ("exact-name", "paraphrase", "inflection", "typo", "time-bound")
FILE_KEYS = ("schema_version", "asked", "questions")
QUESTION_KEYS = ("q", "want", "kind", "lang", "asked", "note")
LANG = re.compile(r"^[a-z]{2,3}$")
DOC_LINES = re.compile(r"^(.+)#L([1-9]\d*)(?:-([1-9]\d*))?$")
THREAD = "thread:"
K = 10

Position = Literal["all", "time", "part", "lines"]


class QuestionFileError(DocketError):
    """A question file that can't be read: the file, and the question by its number, with what is wrong."""


@dataclass(frozen=True)
class Wanted:
    """A locator a good answer cites: a record, a doc or a thread, and a position in it (inclusive), or all of it."""

    text: str
    what: Literal["record", "doc", "thread"]
    target: str
    position: Position = "all"
    start: int = 0
    end: int = 0


@dataclass(frozen=True)
class Question:
    n: int
    q: str
    want: tuple[Wanted, ...]
    kind: str
    lang: str
    asked: datetime
    note: str | None = None


@dataclass(frozen=True)
class QuestionSet:
    path: str
    asked: datetime
    questions: tuple[Question, ...]


@dataclass(frozen=True)
class Found:
    """One locator a hit gives, at the hit's rank: its doc lines, its group, the messages it holds (a thread window)
    and the raw it cites (a doc's section)."""

    rank: int
    locator: str
    group: str
    lines: tuple[int, int] | None = None
    holds: frozenset[str] = frozenset()
    cites: tuple[Anchor, ...] = ()


class Score(BaseModel):
    questions: int
    recall: float
    mrr: float


class QuestionResult(BaseModel):
    n: int
    q: str
    kind: str
    lang: str
    want: list[str]
    # Per wanted locator, the rank of the first hit that answers it in the top k, or None.
    ranks: list[int | None]
    recall: float
    rr: float
    refused: str | None = None


class EvalReport(BaseModel):
    file: str
    k: int
    behind: str | None = None
    all: Score
    kinds: dict[str, Score]
    langs: dict[str, Score]
    # Wanted locators naming no record, doc or thread in the index.
    unknown: list[str] = []
    questions: list[QuestionResult]


# -- the file -----------------------------------------------------------------------------------------------------


class _Loader(yaml.BaseLoader):
    """YAML's base loader, so every value is text, noting each key a mapping repeats: a second `want:` would replace
    the first without a word."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self.repeated: list[tuple[int, str]] = []

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[str] = set()
        for key_node, _ in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                key = str(key_node.value)
                if key in seen:
                    self.repeated.append((key_node.start_mark.line + 1, key))
                seen.add(key)
        return super().construct_mapping(node, deep=deep)


def parse_wanted(text: str) -> Wanted:
    """A wanted locator, or AnchorError saying which forms there are."""
    value = text.strip()
    if value.startswith("r:"):
        anchor = parse_anchor(f"[{value}]")
        rid = anchor.rid or ""
        if anchor.kind == "record":
            return Wanted(value, "record", rid)
        return Wanted(value, "record", rid, anchor.kind, anchor.start, anchor.last)
    if value.startswith(THREAD):
        rid = value[len(THREAD) :]
        if not RID.match(rid):
            raise AnchorError(f"{rid!r} isn't a rid: thread:<source>:<id>")
        return Wanted(value, "thread", rid)
    m = DOC_LINES.match(value)
    doc = m.group(1) if m else value
    # A doc's type is one Docket writes, as `read` tells a doc from a rid; a record is always written with r:.
    if id_problem(doc) is not None or doc.partition(":")[0] not in DOC_TYPES:
        raise AnchorError(
            f"{value!r} is r:<rid> with an optional @mm:ss or #n, a doc id with an optional #L<n>, or thread:<rid>"
        )
    if m is None:
        return Wanted(value, "doc", doc)
    first, last = int(m.group(2)), int(m.group(3) or m.group(2))
    if last < first:
        raise AnchorError(f"{value!r}: the lines run backwards")
    return Wanted(value, "doc", doc, "lines", first, last)


def _when(value: str, tz: ZoneInfo, where: str) -> datetime:
    """A date as the start of that day, a time as written; either in the owner's zone when it names none."""
    at: date | datetime | None = parse_when(value, where)
    if isinstance(at, datetime):
        return at if at.tzinfo is not None else at.replace(tzinfo=tz)
    day = at or date.min
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def _text(value: object, where: str) -> str:
    if value is None:
        raise QuestionFileError(f"{where} is missing")
    if not isinstance(value, str):
        raise QuestionFileError(f"{where} is text, not a list or a mapping")
    if not value.strip():
        raise QuestionFileError(f"{where} is empty")
    return value.strip()


def read_set(text: str, path: str, tz: ZoneInfo) -> QuestionSet:
    """A question set from a file's text, refused whole with the first problem, naming the question by number. A date
    without a time, or a time without an offset, is in the vault owner's zone `tz`."""
    loader = _Loader(text)
    try:
        data: Any = loader.get_single_data()
    except yaml.YAMLError as e:
        raise QuestionFileError(f"{path}: isn't YAML: {e}") from None
    finally:
        loader.dispose()
    if loader.repeated:
        line, key = loader.repeated[0]
        raise QuestionFileError(f"{path}: line {line} repeats the key {key!r}")
    if not isinstance(data, dict):
        raise QuestionFileError(f"{path}: a question file is a mapping: schema_version, asked, questions")
    top = cast(dict[str, Any], data)
    unknown = sorted(set(top) - set(FILE_KEYS))
    if unknown:
        raise QuestionFileError(f"{path}: unknown key {unknown[0]!r}; a question file has {', '.join(FILE_KEYS)}")
    if top.get("schema_version") != SCHEMA:
        raise QuestionFileError(f"{path}: schema_version is 1, which this Docket reads")
    try:
        asked = _when(_text(top.get("asked"), "asked"), tz, "asked")
    except DocketError as e:
        raise QuestionFileError(f"{path}: {e}") from None
    raw = top.get("questions")
    if not isinstance(raw, list) or not raw:
        raise QuestionFileError(f"{path}: questions is a list of one or more questions")
    items = cast(list[Any], raw)
    questions: list[Question] = []
    for n, item in enumerate(items, start=1):
        try:
            questions.append(_question(n, item, asked, tz))
        except DocketError as e:
            raise QuestionFileError(f"{path}: question {n}: {e}") from None
    return QuestionSet(path, asked, tuple(questions))


def _question(n: int, item: object, asked: datetime, tz: ZoneInfo) -> Question:
    if not isinstance(item, dict):
        raise DocketError(f"is a mapping: {', '.join(QUESTION_KEYS)}")
    fields = cast(dict[str, Any], item)
    unknown = sorted(set(fields) - set(QUESTION_KEYS))
    if unknown:
        raise DocketError(f"unknown key {unknown[0]!r}; a question has {', '.join(QUESTION_KEYS)}")
    q = _text(fields.get("q"), "q")
    want_raw: Any = fields.get("want")
    if isinstance(want_raw, str):
        want_raw = [want_raw]
    if not isinstance(want_raw, list) or not want_raw:
        raise DocketError("want is one or more locators a good answer cites")
    wanted: list[Wanted] = []
    seen: set[Wanted] = set()
    for value in cast(list[Any], want_raw):
        try:
            w = parse_wanted(_text(value, "a wanted locator"))
        except AnchorError as e:
            raise DocketError(str(e)) from None
        # A locator listed twice would count twice in the question's recall, however it is spelled.
        same = replace(w, text="")
        if same in seen:
            raise DocketError(f"want lists {w.text} twice")
        seen.add(same)
        wanted.append(w)
    kind = _text(fields.get("kind"), "kind")
    if kind not in KINDS:
        raise DocketError(f"kind is one of {', '.join(KINDS)}, not {kind!r}")
    lang = _text(fields.get("lang"), "lang")
    if not LANG.match(lang):
        raise DocketError(f"lang is a language code such as en or hu, not {lang!r}")
    own = fields.get("asked")
    when = asked if own is None else _when(_text(own, "asked"), tz, "asked")
    note = fields.get("note")
    return Question(n, q, tuple(wanted), kind, lang, when, None if note is None else _text(note, "note"))


def load(path: Path, tz: ZoneInfo) -> QuestionSet:
    """A question file as a set; it is only read."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise QuestionFileError(f"{path}: no such file") from None
    except (OSError, UnicodeDecodeError) as e:
        raise QuestionFileError(f"{path}: can't be read: {e}") from None
    return read_set(text, str(path), tz)


# -- what answers -------------------------------------------------------------------------------------------------


def _overlaps(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 <= b1 and b0 <= a1


def _holds_position(anchor: Anchor, wanted: Wanted) -> bool:
    """A record's locator against a position of the same record: the whole holds every position; a window or a part
    answers what it overlaps, ends included."""
    if wanted.position == "all" or anchor.kind == "record":
        return True
    return anchor.kind == wanted.position and _overlaps(anchor.start, anchor.last, wanted.start, wanted.end)


def _record(locator: str) -> Anchor | None:
    if not locator.startswith("r:"):
        return None
    try:
        return parse_anchor(f"[{locator}]")
    except AnchorError:
        return None


def answers(wanted: Wanted, found: Found) -> bool:
    """Whether a hit's locator answers a wanted one (DR-0291)."""
    if wanted.what == "thread":
        return found.group == wanted.target
    if wanted.what == "doc":
        if found.locator != wanted.target:
            return False
        if wanted.position != "lines":
            return True
        return found.lines is not None and _overlaps(found.lines[0], found.lines[1], wanted.start, wanted.end)
    if wanted.target in found.holds:
        return True
    own = _record(found.locator)
    if own is not None and own.rid == wanted.target and _holds_position(own, wanted):
        return True
    return any(c.rid == wanted.target and _holds_position(c, wanted) for c in found.cites)


# -- running a set ------------------------------------------------------------------------------------------------


def _members(conn: sqlite3.Connection, group: str, locator: str) -> frozenset[str]:
    """The messages a thread window holds, from the index: a hit carries only its first message's locator."""
    found: set[str] = set()
    for (rids,) in conn.execute(
        "SELECT rids FROM chunks WHERE kind = 'thread' AND grp = ? AND locator = ?", (group, locator)
    ):
        found.update(json.loads(rids))
    return frozenset(found)


def found_in(conn: sqlite3.Connection, result: SearchResult) -> list[Found]:
    """Every locator a search's hits give, at their ranks. Two kinds of pair collapse: overlapping transcripts of
    different records, and a message with a thread window holding it. So a locator collapsed under a message is a
    window holding it, and one under a window or a transcript holds nothing more than itself."""
    out: list[Found] = []
    for group in result.groups:
        for hit in group.hits:
            cites = tuple(a for a in (_record(text) for text in hit.anchors) if a is not None)
            holds = _members(conn, group.key, hit.locator) if hit.kind == "thread" else frozenset[str]()
            out.append(Found(hit.rank, hit.locator, group.key, hit.lines, holds, cites))
            for also in hit.also:
                held = _members(conn, group.key, also) if hit.kind == "message" else frozenset[str]()
                out.append(Found(hit.rank, also, group.key, holds=held))
    return sorted(out, key=lambda f: f.rank)


def _exists(conn: sqlite3.Connection, wanted: Wanted) -> bool:
    if wanted.what == "record":
        sql = "SELECT 1 FROM records WHERE rid = ?"
    elif wanted.what == "doc":
        sql = "SELECT 1 FROM docs WHERE id = ?"
    else:
        sql = "SELECT 1 FROM records WHERE grp = ?"
    return conn.execute(sql, (wanted.target,)).fetchone() is not None


def behind(vault: Vault) -> str | None:
    """What the index lacks of the vault, as a line, or None when it is current. Read only: eval measures the index
    as it is (DR-0293)."""
    st = status(vault)
    if not st.exists or st.stale is not None:
        return None
    if st.changed_runs:
        return f"the index is behind the vault: {st.changed_runs} indexed run file(s) changed; docket index rebuild"
    parts = [f"{st.new_runs} new run file(s)"] if st.new_runs else []
    parts += [f"{st.changed_docs} changed doc(s)"] if st.changed_docs else []
    parts += ["the entity files"] if st.entities_changed else []
    if not parts:
        return None
    return f"the index is behind the vault: {', '.join(parts)}; docket index update for current numbers"


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _score(results: Iterable[QuestionResult]) -> Score:
    rs = list(results)
    return Score(questions=len(rs), recall=_mean([r.recall for r in rs]), mrr=_mean([r.rr for r in rs]))


def score_question(question: Question, found: Sequence[Found], k: int) -> QuestionResult:
    """A question's recall at k and reciprocal rank, from the locators its search's hits give."""
    ranks = [min((f.rank for f in found if f.rank <= k and answers(w, f)), default=None) for w in question.want]
    hit = [r for r in ranks if r is not None]
    return QuestionResult(
        n=question.n,
        q=question.q,
        kind=question.kind,
        lang=question.lang,
        want=[w.text for w in question.want],
        ranks=ranks,
        recall=len(hit) / len(ranks),
        rr=1 / min(hit) if hit else 0.0,
    )


def summarize(path: str, k: int, results: list[QuestionResult], unknown: list[str], late: str | None) -> EvalReport:
    kinds = [kind for kind in KINDS if any(r.kind == kind for r in results)]
    langs = sorted({r.lang for r in results})
    return EvalReport(
        file=path,
        k=k,
        behind=late,
        all=_score(results),
        kinds={kind: _score(r for r in results if r.kind == kind) for kind in kinds},
        langs={lang: _score(r for r in results if r.lang == lang) for lang in langs},
        unknown=unknown,
        questions=results,
    )


def run(vault: Vault, questions: QuestionSet, k: int = K) -> EvalReport:
    """Every question through search at k, with its asked as now, scored; nothing is written."""
    if not 1 <= k <= MAX_K:
        raise DocketError(f"k is from 1 to {MAX_K}")
    late = behind(vault)
    conn = open_index(vault)
    try:
        wanted = {w.text: w for q in questions.questions for w in q.want}
        unknown = [text for text, w in wanted.items() if not _exists(conn, w)]
        results: list[QuestionResult] = []
        for question in questions.questions:
            try:
                result = search(vault, question.q, k=k, now=question.asked)
            except DocketError as e:
                missed = score_question(question, [], k)
                results.append(missed.model_copy(update={"refused": str(e)}))
                continue
            results.append(score_question(question, found_in(conn, result), k))
    finally:
        conn.close()
    return summarize(questions.path, k, results, unknown, late)
