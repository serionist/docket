"""What the Search page shows, built from what search found: the line saying how the query was read, the chosen
filters as chips, the facets with their links, and the groups of hits.

Every link is a pair: the whole page's URL for the `href`, and the fragment's for htmx, both from `SearchState`, so a
click without JavaScript lands on the same state. Sources and time narrow; people and entities favour, as the
`search` tool's parameters do (DR-0175).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import timedelta
from zoneinfo import ZoneInfo

from markupsafe import Markup

from docket.index import EntityLabel, SearchResult
from docket.index.query import DocRef, Group, Hit, QueryReading, RerankRun, VectorsRun
from docket.plaintext import words
from docket.vault.anchors import parse_locator
from docket.vault.processed import DOC_TYPES
from docket.web import templating
from docket.web.state import MORE, SearchState
from docket.web.templating import at, clock

RESULTS = "/search/results"
PANEL = "/search/panel"

KIND_ICON = {"recording": "recording", "thread": "thread", "record": "record", "doc": "doc"}
HIT_ICON = {"transcript": "recording", "message": "thread", "thread": "thread", "record": "record", "doc": "doc"}
FOUND_BY = {
    "any": "some words",
    "prefix": "a word's start",
    "entity": "by name",
    "time": "by time",
    "vector": "by meaning",
}


@dataclass(frozen=True)
class Link:
    url: str
    fragment: str


def link(state: SearchState) -> Link:
    """A link to a search: the page, and the fragment that swaps the results."""
    return Link(state.url(), state.url(RESULTS))


def panel_link(state: SearchState) -> Link:
    """A link that opens, changes or closes the panel: the page, and the panel's fragment."""
    return Link(state.url(), state.url(PANEL))


@dataclass(frozen=True)
class FacetItem:
    label: str
    count: int
    on: bool
    link: Link
    note: str = ""


@dataclass(frozen=True)
class Facet:
    key: str
    title: str
    favours: bool
    items: list[FacetItem]


@dataclass(frozen=True)
class Chip:
    label: str
    link: Link


@dataclass(frozen=True)
class HitView:
    locator: str
    icon: str
    stretch: str
    when: str
    who: str
    snippet: Markup
    entities: list[str]
    found: str
    also: list[str]
    link: Link
    open: bool


@dataclass(frozen=True)
class GroupView:
    kind: str
    icon: str
    title: str
    source: str
    when: str
    summary: DocRef | None
    summary_link: Link | None
    hits: list[HitView]


@dataclass
class SearchView:
    reading: list[str] = field(default_factory=list[str])
    chips: list[Chip] = field(default_factory=list[Chip])
    facets: list[Facet] = field(default_factory=list[Facet])
    groups: list[GroupView] = field(default_factory=list[GroupView])
    hits: int = 0
    candidates: int = 0
    more: Link | None = None
    drops: list[Chip] = field(default_factory=list[Chip])

    @property
    def announce(self) -> str:
        if not self.groups:
            return "nothing found"
        hits = "1 hit" if self.hits == 1 else f"{self.hits} hits"
        groups = "1 group" if len(self.groups) == 1 else f"{len(self.groups)} groups"
        return f"{hits} in {groups}"


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


def chips(state: SearchState, names: dict[str, EntityLabel], reading: QueryReading | None = None) -> list[Chip]:
    """The chosen filters and boosts, each a link to the same search without it."""
    base = state.search_only()
    people = resolved(base.people, reading, names, person=True)
    entities = resolved(base.entities, reading, names, person=False)
    out = [Chip(f"source: {s}", link(base.but(sources=_toggle(base.sources, s)))) for s in base.sources]
    out += [
        Chip(f"favour {_name(people[p], names)}", link(base.but(people=_toggle(base.people, p)))) for p in base.people
    ]
    out += [
        Chip(f"favour {_name(entities[e], names)}", link(base.but(entities=_toggle(base.entities, e))))
        for e in base.entities
    ]
    month = _month_of(state)
    if month is not None:
        out.append(Chip(month_label(month), link(base.but(since=None, until=None))))
    else:
        if base.since:
            out.append(Chip(f"since {base.since}", link(base.but(since=None))))
        if base.until:
            out.append(Chip(f"until {base.until}", link(base.but(until=None))))
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
            "sources",
            "Sources",
            favours=False,
            items=[
                FacetItem(s, n, s in base.sources, link(base.but(sources=_toggle(base.sources, s))))
                for s, n in sorted(sources.items())
            ],
        ),
        Facet(
            "people",
            "People",
            favours=True,
            items=[
                FacetItem(
                    _name(p, names),
                    n,
                    p in chosen_people,
                    link(base.but(people=_toggle(base.people, chosen_people.get(p, p)))),
                )
                for p, n in sorted(people.items(), key=lambda kv: (-kv[1], _name(kv[0], names)))
            ],
        ),
        Facet(
            "entities",
            "Entities",
            favours=True,
            items=[
                FacetItem(
                    _name(e, names),
                    n,
                    e in chosen_entities,
                    link(base.but(entities=_toggle(base.entities, chosen_entities.get(e, e)))),
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
        months.append(FacetItem(month_label(m), n, chosen, link(target)))
    out.append(Facet("time", "Time", favours=False, items=months))
    return [f for f in out if f.items]


def _stretch(hit: Hit) -> str:
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


def hit_view(hit: Hit, state: SearchState, tz: ZoneInfo, names: dict[str, EntityLabel], marks: list[str]) -> HitView:
    lines = () if hit.lines is None else (f"{hit.lines[0]}-{hit.lines[1]}",)
    target = state.search_only().but(hit=hit.locator, lines=lines)
    return HitView(
        locator=hit.locator,
        icon=HIT_ICON.get(hit.kind, "record"),
        stretch=_stretch(hit),
        when=at(hit.ts, tz),
        who=", ".join(hit.speakers or hit.authors),
        snippet=templating.marked(hit.snippet, marks),
        entities=[_name(e, names) for e in hit.entities],
        found=FOUND_BY.get(hit.found_by, ""),
        also=list(hit.also),
        link=panel_link(target),
        open=state.hit == hit.locator,
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


def build(result: SearchResult, state: SearchState, names: dict[str, EntityLabel], tz: ZoneInfo) -> SearchView:
    marks = terms(result.query)
    view = SearchView(
        reading=[*reading_line(result.query, names, tz), *vectors_line(result.models), *rerank_line(result.rerank)],
        chips=chips(state, names, result.query),
        facets=facets(result, state, names),
        candidates=result.candidates,
    )
    for group in result.groups:
        summary_link = None
        if group.summary is not None:
            summary_link = panel_link(state.search_only().but(hit=group.summary.id))
        view.groups.append(
            GroupView(
                kind=group.kind,
                icon=KIND_ICON.get(group.kind, "record"),
                title=group.title,
                source=group.source or "docs",
                when=_when(group, tz),
                summary=group.summary,
                summary_link=summary_link,
                hits=[hit_view(hit, state, tz, names, marks) for hit in group.hits],
            )
        )
        view.hits += len(group.hits)
    if view.hits >= state.k and state.k < 100:
        view.more = link(state.search_only().but(k=min(100, state.k + MORE)))
    if not view.groups:
        view.drops = chips(state, names, result.query)
    return view


def label_ids(result: SearchResult, state: SearchState) -> list[str]:
    """Every entity id the page may name: in the facets, the hits, the reading and the chosen boosts."""
    ids = set(result.facets.get("entities", {}))
    ids |= {e for group in result.groups for hit in group.hits for e in hit.entities}
    ids |= {found.id for found in result.query.entities} | set(result.query.boosts)
    return sorted(ids | set(state.people) | set(state.entities))
