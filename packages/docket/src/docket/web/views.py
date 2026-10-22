"""What the Search page shows, built from what search found: the line saying how the query was read, the chosen
filters as chips, the facets with their links, and the groups of hits, each a model the app's JSON read answers as it
is (DR-0709).

Every link is the page's own URL for the state it opens, from `SearchState`, so the app puts it in the address bar as
the server wrote it (DR-0792). Sources and time narrow; people and entities favour, as the `search` tool's parameters
do (DR-0175). A snippet is text in pieces, each marked or not by the query's words, never HTML: text from a record is
data.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterable
from datetime import timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from docket.index import EntityLabel, SearchResult
from docket.index.query import SPLIT_FIRST, SPLIT_SECOND, Group, Hit, QueryReading, RerankRun, VectorsRun
from docket.index.text import HYPHENS, has_letter
from docket.plaintext import WORD, fold, words
from docket.vault.anchors import parse_locator
from docket.vault.processed import DOC_TYPES
from docket.web.display import at, clock
from docket.web.recording_state import RecordingState, time_at
from docket.web.state import MORE, SearchState

# The kind of a group or a hit, as the page draws its icon.
Icon = Literal["recording", "thread", "record", "doc"]
KIND_ICON: dict[str, Icon] = {
    "recording": "recording",
    "thread": "thread",
    "record": "record",
    "doc": "doc",
    "document": "doc",
}
HIT_ICON: dict[str, Icon] = {
    "transcript": "recording",
    "message": "thread",
    "thread": "thread",
    "record": "record",
    "doc": "doc",
}
FOUND_BY = {
    "any": "some words",
    "prefix": "a word's start",
    "entity": "by name",
    "time": "by time",
    "vector": "by meaning",
}
# A word of a snippet is marked when it is a query word, or starts with one this long (an inflected form).
MARK_PREFIX = 4


class Piece(BaseModel):
    """A stretch of a snippet's text, marked when it is one of the query's words."""

    text: str
    mark: bool = False


class FacetItem(BaseModel):
    label: str
    count: int
    on: bool
    # The search with this value chosen, or without it when it is.
    url: str
    note: str = ""


class Facet(BaseModel):
    key: str
    title: str
    # People and entities favour; sources and time narrow (DR-0175).
    favours: bool
    items: list[FacetItem]


class Chosen(BaseModel):
    """A filter or a boost the search holds, with the same search without it."""

    label: str
    url: str


class SummaryLink(BaseModel):
    """The doc that summarises a group, opened beside the results."""

    id: str
    title: str
    type: str
    rev: int
    url: str


class HitLine(BaseModel):
    locator: str
    icon: Icon
    stretch: str
    when: str
    who: str
    snippet: list[Piece]
    entities: list[str]
    found: str
    also: list[str]
    # The search with this hit open beside the results.
    url: str
    # A transcript hit opens on the Recording page too, at its stretch (DR-0776).
    recording: str | None = None


class HitGroup(BaseModel):
    kind: str
    icon: Icon
    title: str
    source: str
    when: str
    summary: SummaryLink | None
    hits: list[HitLine]


class Results(BaseModel):
    # How search read the query, one short fact each: the facts Claude also gets.
    reading: list[str] = []
    chips: list[Chosen] = []
    facets: list[Facet] = []
    groups: list[HitGroup] = []
    hits: int = 0
    # How many candidates the facets count over (DR-0175).
    candidates: int = 0
    # The same search with 20 more hits, while there may be more.
    more: str | None = None
    # With nothing found: each filter or boost, to search again without it.
    drops: list[Chosen] = []
    # What the page's live region says once the results come.
    announce: str = ""


def _pair(text: str, a: re.Match[str], b: re.Match[str], wanted: set[str]) -> bool:
    """Two words in a row joining into a query word (DR-0282): a hyphenated pair always; a spaced one only with the
    halves a split has, since the page can't see which splits the index admitted (DR-0281)."""
    if fold(a.group(0)) + fold(b.group(0)) not in wanted:
        return False
    if text[a.end() : b.start()] in HYPHENS:
        return True
    first, second = a.group(0), b.group(0)
    return len(first) >= SPLIT_FIRST and len(second) >= SPLIT_SECOND and has_letter(first) and has_letter(second)


def pieces(text: str, terms: Iterable[str]) -> list[Piece]:
    """A snippet cut where the query's words are marked; the pieces joined give the text back as it was."""
    wanted = {fold(t) for t in terms if t}
    found = list(WORD.finditer(text))
    marks: set[int] = set()
    for i, m in enumerate(found):
        word = fold(m.group(0))
        if word in wanted or any(len(t) >= MARK_PREFIX and word.startswith(t) for t in wanted):
            marks.add(i)
        if i + 1 < len(found) and _pair(text, m, found[i + 1], wanted):
            marks |= {i, i + 1}
    out: list[Piece] = []
    last = 0
    for i in sorted(marks):
        m = found[i]
        if m.start() > last:
            out.append(Piece(text=text[last : m.start()]))
        out.append(Piece(text=m.group(0), mark=True))
        last = m.end()
    if last < len(text) or not out:
        out.append(Piece(text=text[last:]))
    return out


def announce(hits: int, groups: int) -> str:
    """What the live region says once results come."""
    if not groups:
        return "nothing found"
    said = "1 hit" if hits == 1 else f"{hits} hits"
    return f"{said} in {'1 group' if groups == 1 else f'{groups} groups'}"


def month_range(month: str) -> tuple[str, str]:
    """A facet month (`2026-10`) as the dates `since` and `until` take: its first day and its last."""
    year, number = (int(p) for p in month.split("-"))
    return f"{month}-01", f"{month}-{calendar.monthrange(year, number)[1]:02d}"


def month_label(month: str) -> str:
    year, number = (int(p) for p in month.split("-"))
    return f"{calendar.month_abbr[number]} {year}"


def _name(entity: str, names: dict[str, EntityLabel]) -> str:
    found = names.get(entity)
    return found.name if found is not None else entity


def _toggle(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    return tuple(v for v in values if v != value) if value in values else (*values, value)


def reading_line(reading: QueryReading, names: dict[str, EntityLabel], tz: ZoneInfo) -> list[str]:
    """How search read the query, one short fact each: the facts Claude also gets."""
    out: list[str] = []
    if reading.since is not None or reading.until is not None:
        start = at(reading.since, tz, with_time=False) if reading.since else "the start"
        # `until` is the first moment left out; a range to midnight reads as up to the day before.
        end_at = reading.until
        if end_at is not None:
            local = end_at.astimezone(tz)
            if (local.hour, local.minute, local.second) == (0, 0, 0):
                end_at = local - timedelta(days=1)
        end = at(end_at, tz, with_time=False) if end_at else "now"
        said = f" ({', '.join(reading.time_words)})" if reading.time_words else ""
        out.append(f"from {start} to {end}{said}")
    out += [f'"{found.words}" is {_name(found.id, names)}' for found in reading.entities]
    if reading.unknown:
        out.append(f"not found as a person or entity: {', '.join(reading.unknown)}")
    if reading.absent:
        out.append(f"in no chunk: {', '.join(reading.absent)}")
    return out


def vectors_line(run: VectorsRun) -> list[str]:
    """A line only when models are on here and this search went without vectors, with why."""
    return [f"no vectors in this search: {run.why}"] if run.state == "skipped" and run.why else []


def rerank_line(run: RerankRun) -> list[str]:
    """A line only when the reranker is on here and this search went without it, with why (DR-0459)."""
    return [f"not reranked: {run.why}"] if run.state == "skipped" and run.why else []


def resolved(
    values: tuple[str, ...], reading: QueryReading | None, names: dict[str, EntityLabel], *, person: bool
) -> dict[str, str]:
    """Each chosen person or entity, as the URL holds it (an id, a name or an alias), to the id search resolved it
    to, so `people=Ben Sample` and the facet's `ben-sample` are one entry. A value search didn't resolve stays as it
    is."""
    boosts = list(reading.boosts) if reading is not None else []
    pool = [b for b in boosts if not person or (b in names and names[b].type == "person")]
    out: dict[str, str] = {}
    for value in values:
        if value in names:
            out[value] = value
            continue
        same = [b for b in pool if b in names and names[b].name.casefold() == value.casefold()]
        out[value] = same[0] if same else value
    # A value given by an alias matches no name: when one such value and one unexplained boost are left, they are one.
    unknown = set(reading.unknown) if reading is not None else set[str]()
    left = [v for v, found in out.items() if found == v and v not in names and v not in unknown]
    spare = [b for b in pool if b not in out.values()]
    if len(left) == 1 and len(spare) == 1:
        out[left[0]] = spare[0]
    return out


def chips(state: SearchState, names: dict[str, EntityLabel], reading: QueryReading | None = None) -> list[Chosen]:
    """The chosen filters and boosts, each a link to the same search without it."""
    base = state.search_only()
    people = resolved(base.people, reading, names, person=True)
    entities = resolved(base.entities, reading, names, person=False)
    out = [Chosen(label=f"source: {s}", url=base.but(sources=_toggle(base.sources, s)).url()) for s in base.sources]
    out += [
        Chosen(label=f"favour {_name(people[p], names)}", url=base.but(people=_toggle(base.people, p)).url())
        for p in base.people
    ]
    out += [
        Chosen(label=f"favour {_name(entities[e], names)}", url=base.but(entities=_toggle(base.entities, e)).url())
        for e in base.entities
    ]
    month = _month_of(state)
    if month is not None:
        out.append(Chosen(label=month_label(month), url=base.but(since=None, until=None).url()))
    else:
        if base.since:
            out.append(Chosen(label=f"since {base.since}", url=base.but(since=None).url()))
        if base.until:
            out.append(Chosen(label=f"until {base.until}", url=base.but(until=None).url()))
    return out


def _month_of(state: SearchState) -> str | None:
    """The month `since` and `until` cover exactly, when they do."""
    if state.since is None or state.until is None or len(state.since) != len("2026-10-01"):
        return None
    month = state.since[:7]
    return month if (state.since, state.until) == month_range(month) else None


def facets(result: SearchResult, state: SearchState, names: dict[str, EntityLabel]) -> list[Facet]:
    base = state.search_only()
    found = result.facets
    sources = dict(found.get("sources", {}))
    for chosen in base.sources:
        sources.setdefault(chosen, 0)
    entity_counts = dict(found.get("entities", {}))
    # Each entity id chosen, to the value the URL holds for it, which its link takes away again.
    chosen_people = {i: v for v, i in resolved(base.people, result.query, names, person=True).items()}
    chosen_entities = {i: v for v, i in resolved(base.entities, result.query, names, person=False).items()}
    people = {e: n for e, n in entity_counts.items() if (names.get(e) and names[e].type == "person")}
    for chosen in chosen_people:
        people.setdefault(chosen, 0)
    others = {e: n for e, n in entity_counts.items() if e not in people}
    for chosen in chosen_entities:
        others.setdefault(chosen, 0)
    month = _month_of(state)
    out = [
        Facet(
            key="sources",
            title="Sources",
            favours=False,
            items=[
                FacetItem(label=s, count=n, on=s in base.sources, url=base.but(sources=_toggle(base.sources, s)).url())
                for s, n in sorted(sources.items())
            ],
        ),
        Facet(
            key="people",
            title="People",
            favours=True,
            items=[
                FacetItem(
                    label=_name(p, names),
                    count=n,
                    on=p in chosen_people,
                    url=base.but(people=_toggle(base.people, chosen_people.get(p, p))).url(),
                )
                for p, n in sorted(people.items(), key=lambda kv: (-kv[1], _name(kv[0], names)))
            ],
        ),
        Facet(
            key="entities",
            title="Entities",
            favours=True,
            items=[
                FacetItem(
                    label=_name(e, names),
                    count=n,
                    on=e in chosen_entities,
                    url=base.but(entities=_toggle(base.entities, chosen_entities.get(e, e))).url(),
                    note=names[e].type if e in names else "",
                )
                for e, n in sorted(others.items(), key=lambda kv: (-kv[1], _name(kv[0], names)))
            ],
        ),
    ]
    months: list[FacetItem] = []
    for m, n in sorted(found.get("months", {}).items(), reverse=True):
        since, until = month_range(m)
        chosen = m == month
        target = base.but(since=None, until=None) if chosen else base.but(since=since, until=until)
        months.append(FacetItem(label=month_label(m), count=n, on=chosen, url=target.url()))
    out.append(Facet(key="time", title="Time", favours=False, items=months))
    return [f for f in out if f.items]


def _stretch(hit: Hit) -> str:
    if hit.place is not None:
        # A document's page, slide or sheet rows (DR-0483).
        return hit.place
    if hit.lines is not None:
        first, last = hit.lines
        return f"lines {first}-{last}" if last != first else f"line {first}"
    if hit.start is not None and hit.end is not None:
        return f"{clock(hit.start)}-{clock(hit.end)}"
    # A hit's locator is the index's own, so it always parses.
    anchor = parse_locator(hit.locator, DOC_TYPES).anchor
    if anchor is not None and anchor.kind == "part":
        return f"\N{PILCROW SIGN} {anchor.start}" + (f"-{anchor.end}" if anchor.end is not None else "")
    return ""


def terms(reading: QueryReading) -> list[str]:
    """The words a snippet marks: the query's words, its phrases' words and the names it found."""
    out = list(reading.words)
    for phrase in reading.phrases:
        out += words(phrase)
    for found in reading.entities:
        out += words(found.words)
    return out


def recording_link(hit: Hit) -> str | None:
    """A transcript hit's place on the Recording page: its rid, at its stretch."""
    if hit.kind != "transcript" or hit.rid is None:
        return None
    at = time_at(hit.start, hit.end if hit.end != hit.start else None) if hit.start is not None else None
    return RecordingState(rid=hit.rid, at=at).link()


def hit_line(hit: Hit, state: SearchState, tz: ZoneInfo, names: dict[str, EntityLabel], marks: list[str]) -> HitLine:
    # The lines as the canonical URL writes them, a single line as itself, so the row and the open panel agree.
    lines = (
        ()
        if hit.lines is None
        else (str(hit.lines[0]) if hit.lines[0] == hit.lines[1] else "-".join(map(str, hit.lines)),)
    )
    return HitLine(
        locator=hit.locator,
        icon=HIT_ICON.get(hit.kind, "record"),
        stretch=_stretch(hit),
        when=at(hit.ts, tz),
        who=", ".join(hit.speakers or hit.authors),
        snippet=pieces(hit.snippet, marks),
        entities=[_name(e, names) for e in hit.entities],
        found=FOUND_BY.get(hit.found_by, ""),
        also=list(hit.also),
        url=state.search_only().but(hit=hit.locator, lines=lines).url(),
        recording=recording_link(hit),
    )


def _when(group: Group, tz: ZoneInfo) -> str:
    if group.first is None:
        return ""
    first = at(group.first, tz)
    if group.last is None or group.last == group.first:
        return first
    if group.last.astimezone(tz).date() == group.first.astimezone(tz).date():
        return f"{first}-{group.last.astimezone(tz):%H:%M}"
    return f"{first} to {at(group.last, tz)}"


def build(result: SearchResult, state: SearchState, names: dict[str, EntityLabel], tz: ZoneInfo) -> Results:
    marks = terms(result.query)
    view = Results(
        reading=[*reading_line(result.query, names, tz), *vectors_line(result.models), *rerank_line(result.rerank)],
        chips=chips(state, names, result.query),
        facets=facets(result, state, names),
        candidates=result.candidates,
    )
    for group in result.groups:
        summary = None
        if group.summary is not None:
            ref = group.summary
            url = state.search_only().but(hit=ref.id).url()
            summary = SummaryLink(id=ref.id, title=ref.title, type=ref.type, rev=ref.rev, url=url)
        view.groups.append(
            HitGroup(
                kind=group.kind,
                icon=KIND_ICON.get(group.kind, "record"),
                title=group.title,
                source=group.source or "docs",
                when=_when(group, tz),
                summary=summary,
                hits=[hit_line(hit, state, tz, names, marks) for hit in group.hits],
            )
        )
        view.hits += len(group.hits)
    if view.hits >= state.k and state.k < 100:
        view.more = state.search_only().but(k=min(100, state.k + MORE)).url()
    if not view.groups:
        view.drops = chips(state, names, result.query)
    view.announce = announce(view.hits, len(view.groups))
    return view


def label_ids(result: SearchResult, state: SearchState) -> list[str]:
    """Every entity id the page may name: in the facets, the hits, the reading and the chosen boosts."""
    ids = set(result.facets.get("entities", {}))
    ids |= {e for group in result.groups for hit in group.hits for e in hit.entities}
    ids |= {found.id for found in result.query.entities} | set(result.query.boosts)
    return sorted(ids | set(state.people) | set(state.entities))
