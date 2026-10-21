"""The Briefs page's URL (DR-0780): which brief is open, which of its lines, and which entity type the list shows.

| Parameter | What | Default |
|---|---|---|
| `brief` | a brief's id, `brief:<entity id>` | none: the list |
| `line` | a line of that brief, by its number in the file | none: no line open |
| `type` | an entity type, or `orphaned`: the list shows only those briefs | none: every brief |
| `done` | what a click on a decision did: one of the picks, from a URL before the app | none |

One canonical form: this order, defaults left out, never `done`, whose notice the page says once. Unknown parameters
are ignored, so an old bookmark still opens; the parameters only gain optional ones whose default keeps today's
meaning. `line` is read only with a brief, and `type` stays beside one so the way back to the list keeps it. A link
that sets `line` ends in `#raw`, the id of the part that shows the line's raw, which the app scrolls to once its read
answers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from starlette.datastructures import QueryParams

from docket.errors import DocketError
from docket.vault.decision_form import PICKS
from docket.vault.entities import ENTITY_TYPES
from docket.vault.processed import id_problem
from docket.web import urls

PATH = "/briefs"
# The id of the part that shows the open line's raw, which every link setting `line` names.
FRAGMENT = "raw"
# The group a brief whose entity is gone is listed in.
ORPHANED = "orphaned"
TYPES = (*ENTITY_TYPES, ORPHANED)


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


def _done(given: str) -> str:
    if given not in PICKS:
        raise BriefsStateError(f"done is what a click did: {', '.join(PICKS)}, not {given!r}")
    return given


@dataclass(frozen=True)
class BriefsState:
    brief: str | None = None
    line: int | None = None
    type: str | None = None
    done: str | None = None

    @staticmethod
    def read(params: QueryParams | Mapping[str, str]) -> BriefsState:
        """The state a URL's query string holds; a value that can't be read raises `BriefsStateError` naming it."""
        brief = (params.get("brief") or "").strip()
        line = (params.get("line") or "").strip()
        kind = (params.get("type") or "").strip()
        done = (params.get("done") or "").strip()
        found = _brief(brief) if brief else None
        line_n = _line(line) if line and found else None
        return BriefsState(found, line_n, _type(kind) if kind else None, _done(done) if done else None)

    def url(self) -> str:
        """This state's canonical URL, which never holds what a click did."""
        line = str(self.line) if self.line is not None else None
        named = (("brief", self.brief), ("line", line), ("type", self.type))
        pairs = [(name, v) for name, v in named if v]
        return urls.link(PATH, pairs)

    def link(self) -> str:
        """The URL a link takes: with `line`, it lands on the line's raw."""
        return f"{self.url()}#{FRAGMENT}" if self.line is not None else self.url()

    def but(self, **changes: object) -> BriefsState:
        return replace(self, **changes)  # pyright: ignore[reportArgumentType]
