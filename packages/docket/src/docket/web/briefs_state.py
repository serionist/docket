"""The Briefs page's URL (DR-0557): which brief is open, which of its lines, and which entity type the list shows.

| Parameter | What | Default |
|---|---|---|
| `brief` | a brief's id, `brief:<entity id>` | none: the list |
| `line` | a line of that brief, by its number in the file | none: no line open |
| `type` | an entity type, or `orphaned`: the list shows only those briefs | none: every brief |

One canonical form: this order, defaults left out. Unknown parameters are ignored, so an old bookmark still opens; the
parameters only gain optional ones whose default keeps today's meaning. `line` is read only with a brief, and `type`
stays beside one so the way back to the list keeps it. A link that sets `line` ends in `#raw`, the id of the part
that shows the line's raw, so the browser scrolls to it without JavaScript.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from urllib.parse import urlencode

from starlette.datastructures import QueryParams

from docket.errors import DocketError
from docket.vault.entities import ENTITY_TYPES
from docket.vault.processed import id_problem

PATH = "/briefs"
# The id of the part that shows the open line's raw, which every link setting `line` names.
FRAGMENT = "raw"
# The group a brief whose entity is gone is listed in.
ORPHANED = "orphaned"
TYPES = (*ENTITY_TYPES, ORPHANED)
# Kept as they are in a query string, so a brief's id reads in the address bar as it does everywhere else.
URL_SAFE = ":"


class BriefsStateError(DocketError):
    """A parameter of the Briefs page's URL that can't be read; the message names it."""


def _brief(given: str) -> str:
    doc_type, _, _ = given.partition(":")
    if doc_type != "brief" or id_problem(given) is not None:
        raise BriefsStateError(f"brief is a brief's id, such as brief:ben-sample, not {given!r}")
    return given


def _line(given: str) -> int:
    if not given.isascii() or not given.isdigit() or int(given) < 1:
        raise BriefsStateError(f"line is a line's number in the brief, such as 12, not {given!r}")
    return int(given)


def _type(given: str) -> str:
    if given not in TYPES:
        raise BriefsStateError(f"type is an entity type, such as person or project, or orphaned, not {given!r}")
    return given


@dataclass(frozen=True)
class BriefsState:
    brief: str | None = None
    line: int | None = None
    type: str | None = None

    @staticmethod
    def read(params: QueryParams | Mapping[str, str]) -> BriefsState:
        """The state a URL's query string holds; a value that can't be read raises `BriefsStateError` naming it."""
        brief = (params.get("brief") or "").strip()
        line = (params.get("line") or "").strip()
        kind = (params.get("type") or "").strip()
        found = _brief(brief) if brief else None
        return BriefsState(found, _line(line) if line and found else None, _type(kind) if kind else None)

    def url(self) -> str:
        """This state's canonical URL."""
        line = str(self.line) if self.line is not None else None
        pairs = [(name, v) for name, v in (("brief", self.brief), ("line", line), ("type", self.type)) if v]
        query = urlencode(pairs, safe=URL_SAFE)
        return f"{PATH}?{query}" if query else PATH

    def link(self) -> str:
        """The URL a link takes: with `line`, it lands on the line's raw."""
        return f"{self.url()}#{FRAGMENT}" if self.line is not None else self.url()

    def but(self, **changes: object) -> BriefsState:
        return replace(self, **changes)  # pyright: ignore[reportArgumentType]
