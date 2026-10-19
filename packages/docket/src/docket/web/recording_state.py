"""The Recording page's URL (DR-0544): which recording, which doc beside it, and where in it.

| Parameter | What | Default |
|---|---|---|
| `rid` | a recording's or a transcript's rid; a locator such as `r:<rid>@02:30` reads too | none: the start |
| `doc` | the doc shown as the summary, by its id | the summary search attaches, else a legacy note |
| `at` | a time or a stretch on the recording, as an anchor writes it: `02:30`, `02:30-03:20` | none |

One canonical form: this order, defaults left out. Unknown parameters are ignored, so an old bookmark still opens; the
parameters only gain optional ones whose default keeps today's meaning. A link that sets `at` ends in `#at`, the id of
the first transcript line that time marks, so the browser scrolls to it without JavaScript.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from starlette.datastructures import QueryParams

from docket.errors import DocketError
from docket.vault.anchors import Anchor, AnchorError, format_anchor, parse_anchor, parse_locator
from docket.vault.processed import DOC_TYPES
from docket.vault.records import is_native_id
from docket.web import urls

PATH = "/recording"
# The id the page gives the first transcript line `at` marks, which every link setting `at` names.
FRAGMENT = "at"


class RecordingStateError(DocketError):
    """A parameter of the Recording page's URL that can't be read; the message names it."""


def time_at(start: int, end: int | None = None) -> Anchor:
    """A time on the recording, with no rid: the page's rid says whose."""
    return Anchor(None, "time", start, end)


def _rid(given: str) -> tuple[str, Anchor | None]:
    try:
        found = parse_locator(given, DOC_TYPES)
    except AnchorError:
        found = None
    if found is None or found.kind != "record" or not is_native_id(found.value.partition(":")[2]):
        raise RecordingStateError(
            f"rid is a recording's rid or its locator, such as plaud:4f1c or r:plaud:4f1c@02:30, not {given!r}"
        )
    anchor = found.anchor
    if anchor is not None and anchor.kind == "part":
        raise RecordingStateError(f"a place in a recording is a time, such as r:plaud:4f1c@02:30, not {given!r}")
    at = time_at(anchor.start, anchor.end) if anchor is not None and anchor.kind == "time" else None
    return found.value, at


def _at(given: str) -> Anchor:
    try:
        anchor = parse_anchor(f"@{given}")
    except AnchorError:
        raise RecordingStateError(f"at is a time or a stretch, such as 02:30 or 02:30-03:20, not {given!r}") from None
    return time_at(anchor.start, anchor.end)


def _doc(given: str) -> str:
    try:
        kind = parse_locator(given, DOC_TYPES).kind
    except AnchorError:
        kind = None
    if kind != "doc":
        raise RecordingStateError(f"doc is a doc's id, such as minutes:2026-10-07-supplier-call, not {given!r}")
    return given


@dataclass(frozen=True)
class RecordingState:
    rid: str | None = None
    doc: str | None = None
    at: Anchor | None = None

    @staticmethod
    def read(params: QueryParams | Mapping[str, str]) -> RecordingState:
        """The state a URL's query string holds; a value that can't be read raises `RecordingStateError` naming it.
        Without a rid there is no recording to be in, so `doc` and `at` are read only with one."""
        given = (params.get("rid") or "").strip()
        if not given:
            return RecordingState()
        rid, at = _rid(given)
        doc = (params.get("doc") or "").strip()
        when = (params.get("at") or "").strip()
        return RecordingState(rid, _doc(doc) if doc else None, _at(when) if when else at)

    @property
    def at_text(self) -> str | None:
        """`at` as the URL writes it: `02:30` or `02:30-03:20`."""
        return None if self.at is None else format_anchor(None, "time", self.at.start, self.at.end)[1:]

    def url(self) -> str:
        """This state's canonical URL."""
        pairs = [(name, v) for name, v in (("rid", self.rid), ("doc", self.doc), ("at", self.at_text)) if v]
        return urls.link(PATH, pairs)

    def link(self) -> str:
        """The URL a link takes: with `at`, it lands on the first line that time marks."""
        return f"{self.url()}#{FRAGMENT}" if self.at is not None else self.url()

    def but(self, **changes: object) -> RecordingState:
        return replace(self, **changes)  # pyright: ignore[reportArgumentType]
