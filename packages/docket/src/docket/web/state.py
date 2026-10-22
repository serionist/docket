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
from collections.abc import Callable
from dataclasses import dataclass, replace

from starlette.datastructures import QueryParams

from docket.errors import DocketError
from docket.index import parse_when
from docket.index.query import MAX_K
from docket.vault.anchors import AnchorError, parse_locator
from docket.vault.processed import DOC_TYPES
from docket.web import urls

PATH = "/search"
DEFAULT_K = 20
MORE = 20
DEFAULT_AROUND = 1
MAX_AROUND = 30
LINES = re.compile(r"^([1-9]\d*)(?:-([1-9]\d*))?$")


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


def _verify(params: QueryParams) -> bool:
    given = (params.get("verify") or "").strip()
    if given not in ("", "0", "1"):
        raise StateError(f"verify is 1 or left out, not {given!r}")
    return given == "1"


# Each field and how it is read from the query string, in the URL's order.
_READERS: tuple[tuple[str, Callable[[QueryParams], object]], ...] = (
    ("query", lambda p: " ".join((p.get("query") or "").split())),
    ("sources", lambda p: _many(p, "sources")),
    ("people", lambda p: _many(p, "people")),
    ("entities", lambda p: _many(p, "entities")),
    ("since", lambda p: _when(p, "since")),
    ("until", lambda p: _when(p, "until")),
    ("k", lambda p: _number(p, "k", DEFAULT_K, 1, MAX_K)),
    ("hit", lambda p: (p.get("hit") or "").strip() or None),
    ("lines", _lines),
    ("around", lambda p: _number(p, "around", DEFAULT_AROUND, 0, MAX_AROUND)),
    ("verify", _verify),
)


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
        state, problems = cls.read_what_reads(params)
        if problems:
            raise StateError(problems[0])
        return state

    @classmethod
    def read_what_reads(cls, params: QueryParams) -> tuple[SearchState, list[str]]:
        """Every value that reads, and a message for each that doesn't, which keeps its default: a page with one bad
        value still shows the query and the filters that were fine."""
        values: dict[str, object] = {}
        problems: list[str] = []
        for name, reader in _READERS:
            try:
                values[name] = reader(params)
            except StateError as e:
                problems.append(str(e))
        return cls(**values), problems  # pyright: ignore[reportArgumentType]

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

    def url(self) -> str:
        """This state's canonical URL, which the app writes back as the server wrote it (DR-0792)."""
        return urls.link(PATH, self.pairs())

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
