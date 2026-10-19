"""The day pages' URL (DR-0662 to DR-0666): one state for the Ledger, the Queue, Meetings and Today, the shared
parameters first, then each page's own, in one canonical form with the defaults left out.

| Parameter | What | Default |
|---|---|---|
| `open` | the item or meeting whose panel is open: an A- or M- number, or an id | none |
| `at` | the selected row while no panel is open, named as `open` names one | none |
| `q` | words to find, each folded, in an item's or a meeting's text and log (DR-0665) | none |
| `stream`, `kind`, `who` | the list view's own filters | none |
| `k` | how many rows the list shows, 100 at a time | 100 |
| `state` | the Ledger's: open, snoozed, closed or all | open |
| `group` | the Ledger's: open items by due, stream, kind, who or meeting | due |
| `archived` | the Ledger's: `1` lists archived items too (DR-0615) | none |
| `drop` | `1` opens the open item's drop form, its why focused | none |
| `text` | a material of the open item shown in the panel's viewer | none |
| `done` | what the last post did: its batch, `nothing`, `opened` or `shown` | none |
| `said` | what that batch was when it undid or redid one, `undo` or `redo`, so its notice says so | none |
| `skipped` | the rows a bulk's op didn't take | none |

Unknown parameters are ignored, and the URLs only gain optional ones, since the tasks skill opens them (DR-0669). A
form carries the state as hidden fields, with `page` naming its page, as the Staging page's forms do (DR-0661).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from starlette.datastructures import QueryParams

from docket.errors import DocketError
from docket.ledger import ops
from docket.ledger.fold import NUMBER, number_text
from docket.ledger.meetings import meeting_text
from docket.ledger.store import MEETING_NUMBER, SLUG
from docket.web import urls

PATHS = {"ledger": "/ledger"}
PANEL = "/ledger/panel"
WRITE = "/ledger/write"
SAVE = "/ledger/save"
CHANGED = "/ledger/changed"
OPEN = "/ledger/open"
TEXT = "/ledger/text"
DEFAULT_K = 100
MORE = 100
MAX_K = 100_000
STATES = ("open", "snoozed", "closed", "all")
GROUPS = ("due", "stream", "kind", "who", "meeting")
NOTICES = ("nothing", "opened", "shown")
SAID = ("undo", "redo")
MAX_Q = 200
MAX_WHO = 120
MAX_TEXT = 1000
MAX_SKIPPED = 500


class DayStateError(DocketError):
    """A parameter of a day page's URL, or of a form it posts, that can't be read; the message names it."""


def ref_of(given: str, name: str) -> str:
    """An item or a meeting as a URL names it: an A- or M- number in its canonical form, or an id."""
    text = given.strip()
    if ops.ULID.fullmatch(text):
        return text
    if m := NUMBER.fullmatch(text):
        return number_text(int(m.group(1)))
    if m := MEETING_NUMBER.fullmatch(text):
        return meeting_text(int(m.group(1)))
    raise DayStateError(f"{name} is an A- or M- number or an id, such as A-0012, not {given!r}")


def is_meeting(ref: str) -> bool:
    return ref.startswith("M-")


@dataclass(frozen=True)
class DayState:
    page: str = "ledger"
    open: str | None = None
    at: str | None = None
    q: str | None = None
    stream: str | None = None
    kind: str | None = None
    who: str | None = None
    k: int = DEFAULT_K
    state: str = "open"
    group: str = "due"
    archived: bool = False
    drop: bool = False
    text: str | None = None
    done: str | None = None
    said: str | None = None
    skipped: tuple[str, ...] = ()

    @staticmethod
    def read(params: QueryParams | Mapping[str, str], page: str = "ledger") -> DayState:
        """The state a query string or a form holds; a value that can't be read raises `DayStateError` naming it."""

        def first(name: str) -> str | None:
            return (params.get(name) or "").strip() or None

        def flag(name: str) -> bool:
            value = first(name)
            if value not in (None, "1"):
                raise DayStateError(f"{name} is 1, not {value!r}")
            return value == "1"

        def every(name: str) -> list[str]:
            got = params.getlist(name) if isinstance(params, QueryParams) else [params.get(name) or ""]
            return [v.strip() for v in got if v.strip()]

        opened, at = first("open"), first("at")
        q = " ".join((params.get("q") or "").split()) or None
        if q is not None and len(q) > MAX_Q:
            raise DayStateError(f"q is at most {MAX_Q} characters")
        stream, kind = first("stream"), first("kind")
        if stream is not None and not SLUG.fullmatch(stream):
            raise DayStateError(f"stream is a stream's name, such as pilot, not {stream!r}")
        if kind is not None and kind not in ops.KINDS:
            raise DayStateError(f"kind is one of {', '.join(ops.KINDS)}, not {kind!r}")
        who = " ".join((params.get("who") or "").split()) or None
        if who is not None and len(who) > MAX_WHO:
            raise DayStateError(f"who is at most {MAX_WHO} characters")
        given = first("k") or ""
        # isdigit alone takes a superscript two, which int() refuses.
        if given and (not (given.isascii() and given.isdigit()) or not 1 <= int(given) <= MAX_K):
            raise DayStateError(f"k is a whole number from 1 to {MAX_K}, not {given!r}")
        state, group = first("state") or "open", first("group") or "due"
        if state not in STATES:
            raise DayStateError(f"state is one of {', '.join(STATES)}, not {state!r}")
        if group not in GROUPS:
            raise DayStateError(f"group is one of {', '.join(GROUPS)}, not {group!r}")
        text = first("text")
        if text is not None and len(text) > MAX_TEXT:
            raise DayStateError(f"text is a material's ref, at most {MAX_TEXT} characters")
        done = first("done")
        if done is not None and done not in NOTICES and not ops.ULID.fullmatch(done):
            raise DayStateError(f"done is what the last change did, a batch's id or one of {', '.join(NOTICES)}")
        said = first("said")
        if said is not None and said not in SAID:
            raise DayStateError(f"said is undo or redo, not {said!r}")
        skipped = every("skipped")
        if len(skipped) > MAX_SKIPPED:
            raise DayStateError(f"skipped names at most {MAX_SKIPPED} rows")
        return DayState(
            page=page,
            open=ref_of(opened, "open") if opened else None,
            at=ref_of(at, "at") if at else None,
            q=q,
            stream=stream,
            kind=kind,
            who=who,
            k=int(given) if given else DEFAULT_K,
            state=state,
            group=group,
            archived=flag("archived"),
            drop=flag("drop"),
            text=text,
            done=done,
            said=said,
            skipped=tuple(ref_of(s, "skipped") for s in skipped),
        ).canonical()

    def canonical(self) -> DayState:
        """`at` only without an open panel; `drop` and `text` only with an open item; `said` and `skipped` only with
        `done`."""
        item = self.open is not None and not is_meeting(self.open)
        return replace(
            self,
            at=None if self.open is not None else self.at,
            drop=self.drop and item,
            text=self.text if item else None,
            said=self.said if self.done is not None else None,
            skipped=self.skipped if self.done is not None else (),
        )

    def pairs(self, *, notice: bool = False) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        out += [("open", self.open)] if self.open else []
        out += [("at", self.at)] if self.at else []
        out += [("q", self.q)] if self.q else []
        out += [("stream", self.stream)] if self.stream else []
        out += [("kind", self.kind)] if self.kind else []
        out += [("who", self.who)] if self.who else []
        out += [("k", str(self.k))] if self.k != DEFAULT_K else []
        out += [("state", self.state)] if self.state != "open" else []
        out += [("group", self.group)] if self.group != "due" else []
        out += [("archived", "1")] if self.archived else []
        out += [("drop", "1")] if self.drop else []
        out += [("text", self.text)] if self.text else []
        if notice and self.done:
            out.append(("done", self.done))
            out += [("said", self.said)] if self.said else []
            out += [("skipped", s) for s in self.skipped]
        return out

    def url(self, path: str | None = None, *, notice: bool = False) -> str:
        """This state's canonical URL on its page, or on a fragment's path, which names the page unless it is the
        Ledger."""
        named = [("page", self.page)] if path is not None and self.page != "ledger" else []
        return urls.link(path or PATHS[self.page], named + self.pairs(notice=notice))

    def but(self, **changes: object) -> DayState:
        return replace(self, **changes).canonical()  # pyright: ignore[reportArgumentType]

    def quiet(self) -> DayState:
        """This state without what a post said or what the panel holds open beyond its item: what a form carries."""
        return self.but(drop=False, text=None, done=None, said=None, skipped=())

    def opened(self, ref: str | None) -> DayState:
        """The panel open on another item, or closed with None."""
        return self.quiet().but(open=ref, at=None)

    def hidden(self) -> list[tuple[str, str]]:
        """A form's hidden fields: its page and the state it goes back to."""
        return [("page", self.page), *self.quiet().pairs()]


def canonical(url: str) -> str | None:
    """A day page's URL in its canonical form, or None for one that isn't a day page's or doesn't read."""
    path, _, query = url.partition("?")
    page = next((name for name, where in PATHS.items() if path.endswith(where)), None)
    if page is None and not path.endswith(PANEL):
        return None
    try:
        return DayState.read(QueryParams(query), page or "ledger").url()
    except DocketError:
        return None
