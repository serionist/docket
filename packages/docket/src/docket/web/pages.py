"""The navigation and the keys: every page of the design, by use, with the phase it arrives in, and the keys the
Search page answers to. The shell, the keys dialog and the footer read these, and `docket.js` handles the same keys.

Only built pages are links; the others are slots that say when they come (DR-0176).
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
            Page("today", "Today", "/today", 3, "the day: meetings with their briefs and points"),
            Page("queue", "Queue", "/queue", 3, "triage, then the items one at a time"),
            Page("ledger", "Ledger", "/ledger", 3, "items with their raw source one click away"),
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
            Page("briefs", "Briefs", "/briefs", 2, "per person, project and topic; the decision log per stream"),
            Page("recording", "Recording", "/recording", 2, "the summary next to the transcript, both transcripts"),
        ),
    ),
    Group(
        "Keeping",
        (
            Page("staging", "Staging", "/staging", 1, "records waiting for the gate: classify, cut, drop"),
            Page("curate", "Curate", "/curate", 1, "the weekly batch of proposals"),
            Page("sync", "Sync", "/sync", 1, "runs, failures, cursors, rate limits"),
            Page("settings", "Settings", "/settings", 1, "sources, never-store lists, models, redaction rules"),
        ),
    ),
)

PAGES: tuple[Page, ...] = tuple(page for group in NAV for page in group.pages)

# (keys, what they do), in the order the dialog lists them. Each key is one `KeyboardEvent.key` value.
KEYS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("/",), "to the query box"),
    (("j", "k"), "next, previous hit"),
    (("Enter", "o"), "open the hit beside the results"),
    (("v",), "verify the summary, and back"),
    (("[", "]"), "less, more raw around the hit"),
    (("c",), "copy the hit's locator"),
    (("Escape",), "close the panel, or leave the box"),
    (("?",), "these keys"),
)
