"""The day pages' URL (DR-0664 to DR-0666, DR-0704): one state for the Ledger, the Queue, Meetings and Today, the
shared parameters first, then each page's own, in one canonical form with the defaults left out. The Queue takes the
shared ones; `state`, `group` and `archived` are the Ledger's alone, and its canonical URL leaves them out. Meetings
takes `q` and `stream` of the shared filters, `kind`, `who` and `k` being the items' (DR-0764), and `suggest` of its
own.

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
| `text` | a material of the open item or meeting shown in the panel's viewer | none |
| `suggest` | Meetings': `1` lists the open meeting's suggestions, the items worth raising in it | none |
| `done` | what the last write did: its batch, `nothing`, `opened` or `shown` | none |
| `said` | what that batch was when it undid or redid one, `undo` or `redo`, so its notice says so | none |
| `skipped` | the rows a bulk's op didn't take | none |

Unknown parameters are ignored, and the URLs only gain optional ones, since the tasks skill opens them (DR-0669). The
app sends a page's query string to its read as it is, and writes back the canonical URL the read answers (DR-0704);
`done`, `said` and `skipped` show their notice once and leave the canonical URL, since the notice is the page's own
after a write (DR-0710).
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

PATHS = {"ledger": "/ledger", "queue": "/queue", "meetings": "/meetings"}
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
    suggest: bool = False

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
        stream = first("stream")
        if stream is not None and not SLUG.fullmatch(stream):
            raise DayStateError(f"stream is a stream's name, such as pilot, not {stream!r}")
        # The items' own filters: Meetings ignores them, as any parameter it doesn't take.
        items = page != "meetings"
        kind = first("kind") if items else None
        if kind is not None and kind not in ops.KINDS:
            raise DayStateError(f"kind is one of {', '.join(ops.KINDS)}, not {kind!r}")
        who = (" ".join((params.get("who") or "").split()) or None) if items else None
        if who is not None and len(who) > MAX_WHO:
            raise DayStateError(f"who is at most {MAX_WHO} characters")
        given = (first("k") or "") if items else ""
        # isdigit alone takes a superscript two, which int() refuses.
        if given and (not (given.isascii() and given.isdigit()) or not 1 <= int(given) <= MAX_K):
            raise DayStateError(f"k is a whole number from 1 to {MAX_K}, not {given!r}")
        # The Ledger's own: another day page ignores them, as any parameter it doesn't take.
        ledger = page == "ledger"
        state, group = (first("state") or "open", first("group") or "due") if ledger else ("open", "due")
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
            archived=flag("archived") if ledger else False,
            drop=flag("drop"),
            text=text,
            done=done,
            said=said,
            skipped=tuple(ref_of(s, "skipped") for s in skipped),
            suggest=flag("suggest") if not items else False,
        ).canonical()

    def canonical(self) -> DayState:
        """`at` only without an open panel; `drop` only with an open item, `text` with it or on Meetings its meeting;
        `said` and `skipped` only with `done`; the Ledger's own parameters only on the Ledger, the items' filters off
        Meetings, and `suggest` only there with a panel open."""
        item = self.open is not None and not is_meeting(self.open)
        ledger = self.page == "ledger"
        meetings = self.page == "meetings"
        return replace(
            self,
            state=self.state if ledger else "open",
            group=self.group if ledger else "due",
            archived=self.archived and ledger,
            kind=None if meetings else self.kind,
            who=None if meetings else self.who,
            k=DEFAULT_K if meetings else self.k,
            at=None if self.open is not None else self.at,
            drop=self.drop and item and not meetings,
            text=self.text if item or (meetings and self.open is not None) else None,
            said=self.said if self.done is not None else None,
            skipped=self.skipped if self.done is not None else (),
            suggest=self.suggest and meetings and self.open is not None,
        )

    def pairs(self) -> list[tuple[str, str]]:
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
        out += [("suggest", "1")] if self.suggest else []
        return out

    def url(self) -> str:
        """This state's canonical URL on its page, which never holds what a write said."""
        return urls.link(PATHS[self.page], self.pairs())
