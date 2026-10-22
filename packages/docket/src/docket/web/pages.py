"""The navigation and the keys: every page of the design, by use, with the phase it arrives in, and the keys each
built page answers to. The shell, the keys dialog and the footer read these, and `docket.js` handles the same keys.
A page in the React app lists its keys in its own registry (DR-0711), so the day pages, Search, the Recording page,
Briefs, Staging and Curate have none here.

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
            Page("today", "Today", "/today", 3, "the day: the next meeting and its prep, what's due", built=True),
            Page("queue", "Queue", "/queue", 3, "triage, then the items one at a time", built=True),
            Page("ledger", "Ledger", "/ledger", 3, "items with their raw source one click away", built=True),
            Page("meetings", "Meetings", "/meetings", 3, "meetings with their briefs, points and minutes", built=True),
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
SYNC_KEYS: Keys = ((("?",), "these keys"),)

# The keys each page answers, which its dialog lists, and the few its footer names, as (keys, a word).
# The Settings page has nothing to move through; no key writes.
SETTINGS_KEYS: Keys = ((("?",), "these keys"),)

PAGE_KEYS: dict[str, Keys] = {"sync": SYNC_KEYS, "settings": SETTINGS_KEYS}
HINTS: dict[str, Keys] = {"sync": ((("?",), "keys"),), "settings": ((("?",), "keys"),)}
