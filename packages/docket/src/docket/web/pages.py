"""The navigation and the keys: every page of the design, by use, with the phase it arrives in, and the keys each
built page answers to. The shell, the keys dialog and the footer read these, and `docket.js` handles the same keys.
A page in the React app lists its keys in its own registry (DR-0711), so the Ledger and the Queue have none here.

Only built pages are links, first in their group; the others are slots that say when they come (DR-0546).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Page:
    key: str
    title: str
    path: str
    phase: int
    what: str
    built: bool = False


@dataclass(frozen=True)
class Group:
    title: str
    pages: tuple[Page, ...]


NAV: tuple[Group, ...] = (
    Group(
        "The day",
        (
            # Built pages come first in their group (DR-0546); each day page's PR marks it built (DR-0669).
            Page("queue", "Queue", "/queue", 3, "triage, then the items one at a time", built=True),
            Page("ledger", "Ledger", "/ledger", 3, "items with their raw source one click away", built=True),
            Page("today", "Today", "/today", 3, "the day: meetings with their briefs and points"),
            Page("meetings", "Meetings", "/meetings", 3, "meetings with their briefs, points and minutes"),
            Page("outbox", "Outbox", "/outbox", 3, "drafts waiting for a click"),
        ),
    ),
    Group(
        "Finding",
        (
            Page(
                "search",
                "Search",
                "/search",
                0,
                "the search Claude uses, with facets by entity, source, person and time",
                built=True,
            ),
            # Built pages come first in their group, before its one "later" label.
            Page(
                "recording",
                "Recording",
                "/recording",
                2,
                "the summary next to the transcript, both transcripts",
                built=True,
            ),
            Page(
                "briefs",
                "Briefs",
                "/briefs",
                2,
                "per person, project and topic, beside the raw they cite; the decision log per stream later",
                built=True,
            ),
        ),
    ),
    Group(
        "Keeping",
        (
            Page(
                "staging",
                "Staging",
                "/staging",
                1,
                "records waiting for the gate: classify, cut, drop, promote",
                built=True,
            ),
            Page(
                "curate",
                "Curate",
                "/curate",
                1,
                "the weekly round of proposals: accept, edit, reject, undo",
                built=True,
            ),
            Page(
                "sync",
                "Sync",
                "/sync",
                1,
                "runs, failures, cursors, rate limits, what needs a Claude session",
                built=True,
            ),
            Page(
                "settings",
                "Settings",
                "/settings",
                1,
                "sources, never-store lists, models, redaction rules",
                built=True,
            ),
        ),
    ),
)

PAGES: tuple[Page, ...] = tuple(page for group in NAV for page in group.pages)

Keys = tuple[tuple[tuple[str, ...], str], ...]

# (keys, what they do), in the order the dialog lists them. Each key is one `KeyboardEvent.key` value.
KEYS: Keys = (
    (("/",), "to the query box"),
    (("j", "k"), "next, previous hit"),
    (("Enter", "o"), "open the hit beside the results"),
    (("v",), "verify the summary, and back"),
    (("[", "]"), "less, more raw around the hit"),
    (("c",), "copy the hit's locator"),
    (("Escape",), "close the panel, or leave the box"),
    (("?",), "these keys"),
)
SYNC_KEYS: Keys = ((("?",), "these keys"),)
# The Staging page's list and panel answer Search's keys for moving and opening; no key writes.
STAGING_KEYS: Keys = (
    (("j", "k"), "next, previous record"),
    (("Enter", "o"), "open the record beside the list"),
    (("Escape",), "close the record"),
    (("?",), "these keys"),
)

# The Curate page's cards answer the same keys; no key decides a card.
CURATE_KEYS: Keys = (
    (("j", "k"), "next, previous card"),
    (("Enter", "o"), "open the card beside the list"),
    (("Escape",), "close the card"),
    (("?",), "these keys"),
)

# The keys each page answers, which its dialog lists, and the few its footer names, as (keys, a word).
# The Settings page has nothing to move through; no key writes.
SETTINGS_KEYS: Keys = ((("?",), "these keys"),)
# The Recording page's lines are plain links, which Tab reaches; no key of its own yet.
RECORDING_KEYS: Keys = ((("?",), "these keys"),)
# The Briefs page's briefs and lines are plain links too.
BRIEFS_KEYS: Keys = ((("?",), "these keys"),)

PAGE_KEYS: dict[str, Keys] = {
    "search": KEYS,
    "sync": SYNC_KEYS,
    "staging": STAGING_KEYS,
    "curate": CURATE_KEYS,
    "settings": SETTINGS_KEYS,
    "recording": RECORDING_KEYS,
    "briefs": BRIEFS_KEYS,
}
HINTS: dict[str, Keys] = {
    "search": ((("/",), "search"), (("j", "k"), "move"), (("Enter",), "open"), (("?",), "keys")),
    "sync": ((("?",), "keys"),),
    "staging": ((("j", "k"), "move"), (("Enter",), "open"), (("?",), "keys")),
    "curate": ((("j", "k"), "move"), (("Enter",), "open"), (("?",), "keys")),
    "settings": ((("?",), "keys"),),
    "recording": ((("?",), "keys"),),
    "briefs": ((("?",), "keys"),),
}
