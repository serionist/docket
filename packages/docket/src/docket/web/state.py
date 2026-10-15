"""The Search page's URL: the `search` tool's parameters under the same names, then the panel's (DR-0174).

| Parameter | What | Default |
|---|---|---|
| `query` | the text | empty |
| `sources` | narrow to these sources; repeated | none |
| `people`, `entities` | favour these; repeated | none |
| `since`, `until` | a date or a time, read as the tool reads them | none |
| `k` | how many hits, 1 to 100 | 20 |
| `hit` | the open hit's locator, as `read` takes it | none |
| `lines` | a doc hit's lines: repeated, each a line or a range | none |
| `around` | context either side of the raw, 0 to 30 | 1 |
| `verify` | `1`: the summary verified, line by line | off |

One canonical form: this order, defaults left out, repeated values sorted (`lines` by where each starts), so one
search has one URL. Unknown parameters are ignored, so an old bookmark still opens; the parameters only gain optional
ones whose default keeps today's meaning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from urllib.parse import urlencode, urlsplit

from starlette.datastructures import QueryParams

from docket.errors import DocketError
from docket.index import parse_when
from docket.index.query import MAX_K
from docket.vault.anchors import AnchorError, parse_locator
from docket.vault.processed import DOC_TYPES

DEFAULT_K = 20
MORE = 20
DEFAULT_AROUND = 1
MAX_AROUND = 30
LINES = re.compile(r"^([1-9]\d*)(?:-([1-9]\d*))?$")
# Kept as they are in a query string, so a locator reads in the address bar as it does everywhere else.
URL_SAFE = ":@/,"


class StateError(DocketError):
    """A parameter of the page's URL that can't be read; the message names it."""


def _number(params: QueryParams, name: str, default: int, low: int, high: int) -> int:
    given = (params.get(name) or "").strip()
    if not given:
        return default
    if not given.isdigit() or not low <= int(given) <= high:
        raise StateError(f"{name} is a whole number from {low} to {high}, not {given!r}")
    return int(given)


def _many(params: QueryParams, name: str) -> tuple[str, ...]:
    return tuple(sorted({v.strip() for v in params.getlist(name) if v.strip()}))


def _when(params: QueryParams, name: str) -> str | None:
    given = (params.get(name) or "").strip()
    if not given:
        return None
    try:
        parse_when(given, name)
    except DocketError as e:
        raise StateError(str(e)) from None
    return given


def _span(value: str) -> tuple[int, int]:
    m = LINES.match(value)
    if m is None:
        raise StateError(f"lines takes a line or a range, such as 4 or 4-9, not {value!r}")
    first = int(m.group(1))
    last = int(m.group(2) or first)
    if last < first:
        raise StateError(f"lines takes a range that runs forward, such as 4-9, not {value!r}")
    return first, last


def _lines(params: QueryParams) -> tuple[str, ...]:
    spans = sorted({_span(v.strip()) for v in params.getlist("lines") if v.strip()})
    return tuple(str(a) if a == b else f"{a}-{b}" for a, b in spans)


def is_locator(text: str) -> bool:
    """Whether a query is one locator to open rather than words to search: `r:...`, `[r:...]`, or a doc id."""
    value = text.strip()
    if not value or any(ch.isspace() for ch in value):
        return False
    head, _, rest = value.partition(":")
    if not (value.startswith(("r:", "[r:")) or (head in DOC_TYPES and rest)):
        return False
    try:
        return parse_locator(value, DOC_TYPES).kind != "path"
    except AnchorError:
        return False


@dataclass(frozen=True)
class SearchState:
    query: str = ""
    sources: tuple[str, ...] = ()
    people: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    since: str | None = None
    until: str | None = None
    k: int = DEFAULT_K
    hit: str | None = None
    lines: tuple[str, ...] = ()
    around: int = DEFAULT_AROUND
    verify: bool = False

    @classmethod
    def read(cls, params: QueryParams) -> SearchState:
        """The state a URL's query string holds; a value that can't be read raises `StateError` naming it."""
        verify = (params.get("verify") or "").strip()
        if verify not in ("", "0", "1"):
            raise StateError(f"verify is 1 or left out, not {verify!r}")
        hit = (params.get("hit") or "").strip()
        return cls(
            query=" ".join((params.get("query") or "").split()),
            sources=_many(params, "sources"),
            people=_many(params, "people"),
            entities=_many(params, "entities"),
            since=_when(params, "since"),
            until=_when(params, "until"),
            k=_number(params, "k", DEFAULT_K, 1, MAX_K),
            hit=hit or None,
            lines=_lines(params),
            around=_number(params, "around", DEFAULT_AROUND, 0, MAX_AROUND),
            verify=verify == "1",
        )

    @classmethod
    def from_url(cls, url: str) -> SearchState | None:
        """The state of a Search page URL, such as htmx's `HX-Current-URL`; None for another page or a bad value."""
        parts = urlsplit(url)
        if parts.path != "/search":
            return None
        try:
            return cls.read(QueryParams(parts.query))
        except StateError:
            return None

    def pairs(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if self.query:
            out.append(("query", self.query))
        out += [("sources", v) for v in self.sources]
        out += [("people", v) for v in self.people]
        out += [("entities", v) for v in self.entities]
        out += [(name, v) for name, v in (("since", self.since), ("until", self.until)) if v is not None]
        if self.k != DEFAULT_K:
            out.append(("k", str(self.k)))
        if self.hit is not None:
            out.append(("hit", self.hit))
        out += [("lines", v) for v in self.lines]
        if self.around != DEFAULT_AROUND:
            out.append(("around", str(self.around)))
        if self.verify:
            out.append(("verify", "1"))
        return out

    def url(self, path: str = "/search") -> str:
        """This state's canonical URL on a path: the page itself, or one of its fragments."""
        query = urlencode(self.pairs(), safe=URL_SAFE)
        return f"{path}?{query}" if query else path

    def but(self, **changes: object) -> SearchState:
        return replace(self, **changes)  # pyright: ignore[reportArgumentType]

    def search_only(self) -> SearchState:
        """The same search with the panel closed."""
        return self.but(hit=None, lines=(), around=DEFAULT_AROUND, verify=False)

    @property
    def locator_query(self) -> bool:
        return is_locator(self.query)

    @property
    def asked(self) -> bool:
        """Whether there is anything to search for: words, a name, a time or a source."""
        return bool(
            (self.query and not self.locator_query)
            or self.sources
            or self.people
            or self.entities
            or self.since
            or self.until
        )

    @property
    def open_hit(self) -> str | None:
        """The locator the panel shows: the hit, else a query that is one locator."""
        return self.hit or (self.query if self.locator_query else None)

    def line_numbers(self) -> list[int]:
        """`lines` as the verify tool takes them: every line number, in order."""
        out: set[int] = set()
        for value in self.lines:
            first, last = _span(value)
            out.update(range(first, last + 1))
        return sorted(out)
