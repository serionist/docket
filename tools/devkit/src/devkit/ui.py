"""`uv run devkit ui <page>`: the screenshot loop's tooling (DR-0712).

It seeds an invented world into a folder of its own, starts a throwaway service on a free port with config, state and
models folders of its own, a file token and the failing keyring, and drives the built app in Chrome: each of the
page's states, light and dark, at 1440x900 and 390x844, with the clock, zone and motion fixed. A state opens a URL,
signed in or as a stranger, then presses keys, clicks, fills a field, waits for a part to show, writes from the page
as another tab would or clicks in another tab of the owner's on the same URL, opens a sync run as Claude's session
would, fails or holds one of the page's reads and reloads, and may undo its own write once shot, so the next shot
finds the world as it was; one may ask for another world, such as the empty one, or a world of its own for each shot,
for a write no undo takes back, or be served the page a failure answers. The shots and the machine checks go to
`.local/ui/<page>/r<n>/`, a new round each run.

| Check | Fails on |
|---|---|
| axe | any violation of WCAG 2.1 A and AA or axe's best practices |
| console | an error in the console, or a page error |
| csp | a securitypolicyviolation event |
| sideways | the document scrolling sideways |
| targets | a control smaller than 24 px, or 44 on a coarse pointer, inline text links aside |

It never reaches the owner's service, port 8741, Docket's folders or the keychain: everything it runs gets
`environment()`, which a test holds. Signing in goes to `localhost`, in a browser profile of Playwright's own, so the
owner's session cookie on 127.0.0.1 is never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from devkit.repo import KitError

OWNERS_PORT = 8741
WORLD_SCRIPT = "packages/docket/tests/ui_world.py"
KITS = "packages/docket/tests"
AXE = "packages/docket/web/node_modules/axe-core/axe.min.js"
ROUNDS = ".local/ui"
ZONE = "Europe/Budapest"
# The clock every shot is taken at: a Tuesday morning in the invented week.
CLOCK = datetime(2026, 10, 20, 9, 30, tzinfo=ZoneInfo(ZONE))
GITCONFIG = (
    "[user]\n\tname = UI Example\n\temail = ui@example.invalid\n[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n"
)
START_SECONDS = 60
SIZES: dict[str, tuple[int, int]] = {"wide": (1440, 900), "narrow": (390, 844)}
THEMES: tuple[Literal["light", "dark"], ...] = ("light", "dark")
TARGET = 24
TARGET_COARSE = 44
# Variables of the caller's that would point a child at the owner's things.
DROPPED_PREFIXES = ("DOCKET_", "GIT_")
DROPPED = frozenset({"PYTHON_KEYRING_BACKEND", "PYTHONPATH"})


# What a state's step does: press a key, click a selector, fill a field, `selector|text`, wait for a selector to
# show, such as a write's notice, post JSON from the page, `path|body`, as another tab of the owner's would, click
# a selector in another tab of the owner's opened on the page's URL, for a write that comes first, open a run of a
# source and bind it as Claude's session does (`run`), answer a read's path with a failure (`fail`) or never (`hang`),
# and reload the page.
VERBS = ("press", "click", "fill", "wait", "post", "other", "run", "fail", "hang", "reload")
# A post from the page: its session's cookie, and the Origin and Fetch Metadata the browser adds, as the app's own.
POST = """async ([path, body]) => {
  const answer = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body});
  return answer.status;
}"""
# What Chrome logs for a refusal's 400, which a state that shows a refusal brings about on purpose, and for a write
# refused as the decision moved since the page showed it.
REFUSED = "the server responded with a status of 400"
MOVED = "the server responded with a status of 409"
FAILED_READ = "the server responded with a status of 500"
STRANGER = "the server responded with a status of 401"
MISSING = "the server responded with a status of 404"
# The supplier call's hit in the index fixture, and the harbour crane check's recording note in recording_kit's.
CALL_HIT = "r:calls:rec-0100@01:50-03:20"
CRANE_NOTE = "recording-note:2026-10-12-harbour-crane-check"
# decisions_kit's fleet management, which has decisions and no brief, and its plain candidate at its first rev.
FLEET = "/briefs?brief=brief:fleet-management"
WEEKLY_PICK = '[data-decision="decision:2026-10-14-weekly-review"] >> role=button[name="{}"]'
# The page read again after a pick: one decision left waiting.
ONE_WAITING = '[aria-labelledby="decisions-waiting"][data-count="1"]'
WEEKLY_BY_ANOTHER_TAB = (
    "/api/web/briefs/decision|"
    '{"brief": "brief:fleet-management", "decision": "decision:2026-10-14-weekly-review", "rev": 1, "pick": "confirm"}'
)
# ui_world's staging: the supplier call to cut, its second utterance, the typed field, and a write's notice.
CALL_ROW = '[data-row] a:has-text("Supplier call")'
SECOND = 'role=checkbox[name="Cut 00:12-00:31"]'
TYPED = 'input[placeholder="12:30-14:05"]'
WORK = 'button:has-text("Work,")'
# ui_world's curate: the alias card for Northwind, Cleo Test's new card, and the round's log.
ALIAS_ROW = '[data-row] a:has-text("Alias for Northwind")'
CLEO_ROW = '[data-row] a:has-text("Cleo Test")'
ACCEPT = 'role=button[name="Accept"]'
# ui_world's sync: the calls run an hour ago, and memo's run that its mapper dropped.
CALLS_RUN = "run-20261020T081500-a1b2c3d4e5f60718"
MAPPER_RUN = "run-20261020T063000-0f1e2d3c4b5a6978"
# ui_world's settings: the add's field, its submit, and the add's own question.
ADD_PEOPLE = (
    ("click", "#list-people summary"),
    ("fill", "#add-people|Ada Example"),
    ("click", "#list-people form button"),
)


@dataclass(frozen=True)
class State:
    """One state of a page: where it opens, then what is pressed, clicked or filled, in order; `only` limits it to a
    size, `world` serves it another world than the page's, and `after` runs once it is shot, such as an undo of the
    write it shows; `fresh` serves each of its shots a world of its own, for a write nothing undoes, such as a
    decision's pick. `expects` names console lines the state brings about on purpose, such as the 400 of the refusal
    it shows, which the console check then leaves out."""

    name: str
    url: str
    steps: tuple[tuple[str, str], ...] = ()
    only: str | None = None
    world: str | None = None
    after: tuple[tuple[str, str], ...] = ()
    expects: tuple[str, ...] = ()
    fresh: bool = False
    # Opened by a stranger, with no session: the sign-in and used-link pages.
    signed: bool = True
    # Served the page a failure on a page path answers, as the service writes it, in place of the URL's own.
    failed: bool = False


@dataclass(frozen=True)
class Page:
    world: str
    states: tuple[State, ...]


PAGES: dict[str, Page] = {
    "design": Page(
        world="week",
        states=(
            State("list", "/design"),
            State("panel", "/design?open=A-0012"),
            State("calls", "/design", (("press", "x"), ("press", "j"), ("press", "x"), ("press", "1"))),
            State("tokens", "/design?part=tokens"),
            State("components", "/design?part=components"),
            State("select", "/design?part=components", (("click", "#stream"),)),
            State("keys", "/design", (("press", "?"),)),
            State("session", "/design?part=components", (("click", "text=End the session here"),)),
            State("sheet", "/design", (("click", "[aria-label='Open the pages']"),), only="narrow"),
            State("theme", "/design", (("click", "button:has-text('Theme')"),), only="wide"),
            State(
                "sheet-theme",
                "/design",
                (("click", "[aria-label='Open the pages']"), ("click", "button:has-text('Theme')")),
                only="narrow",
            ),
        ),
    ),
    "ledger": Page(
        world="week",
        states=(
            State("list", "/ledger"),
            State("panel", "/ledger?open=A-0005"),
            State("meeting", "/ledger?open=A-0002"),
            State("ticked", "/ledger", (("press", "j"), ("press", "x"), ("press", "j"), ("press", "x"))),
            State("notice", "/ledger", (("press", "j"), ("press", "2")), after=(("press", "u"),)),
            State(
                "refusal",
                "/ledger?open=A-0003&drop=1",
                (("click", '#close-A-0003 >> text="Drop"'),),
                expects=(REFUSED,),
            ),
            State("viewer", "/ledger?open=A-0005&text=notes/plan.md"),
            # The date picker over each of its fields: the panel's due, the snooze's own day, the new item's due.
            State("due", "/ledger?open=A-0003", (("click", "#f-A-0003-due"),)),
            State("snooze", "/ledger?open=A-0003", (("click", "#until-A-0003"),)),
            State(
                "new-due",
                "/ledger",
                (("press", "n"), ("fill", "#new-title|Book the train for the visit"), ("click", "#new-due")),
            ),
            State(
                "alike",
                "/ledger",
                (
                    ("press", "n"),
                    ("fill", "#new-title|Send Ada the dates again"),
                    ("fill", "#new-who|Ada Example"),
                    ("fill", "#new-stream|pilot"),
                    ("click", 'role=dialog >> text="Add"'),
                ),
                expects=(REFUSED,),
            ),
            State("empty", "/ledger", world="empty"),
            # The who field's names under the panel's field and the new item's, and the kinds' tabs on every row.
            State("who", "/ledger?open=A-0003", (("fill", "#f-A-0003-who|e"),)),
            State("new-who", "/ledger", (("press", "n"), ("fill", "#new-who|Cl"))),
        ),
    ),
    "queue": Page(
        world="week",
        states=(
            State("list", "/queue"),
            State("panel", "/queue?open=A-0022"),
            State("ticked", "/queue", (("press", "j"), ("press", "j"), ("press", "x"), ("press", "j"), ("press", "x"))),
            State("notice", "/queue", (("press", "j"), ("press", "j"), ("press", "2")), after=(("press", "u"),)),
            State(
                "refusal",
                "/queue?open=A-0003&drop=1",
                (("click", '#close-A-0003 >> text="Drop"'),),
                expects=(REFUSED,),
            ),
            State("who", "/queue?open=A-0003", (("fill", "#f-A-0003-who|Ada E"),)),
            State("filtered", "/queue?stream=pilot"),
            State("empty", "/queue", world="empty"),
        ),
    ),
    # ui_world's meetings: M-0001 the Pilot sync tomorrow, M-0003 the budget review under way, M-0004 yesterday's
    # supplier call with its recording and its point rolled on, M-0008 the retro with no stream.
    "meetings": Page(
        world="meetings",
        states=(
            State("list", "/meetings"),
            State("panel", "/meetings?open=M-0003"),
            State(
                "editing",
                "/meetings?open=M-0003",
                (
                    ("click", 'role=button[name="Answer: Ben: the spring numbers"]'),
                    ("fill", 'textarea[id$="-answer"]|By Friday, with both quotes'),
                ),
            ),
            State(
                "written",
                "/meetings?open=M-0003",
                (("click", 'role=button[name="Keep: Ask about the printer budget"]'), ("wait", "role=status")),
                after=(("press", "u"),),
            ),
            State(
                "refused",
                "/meetings?open=M-0008",
                (
                    ("click", 'role=button[name="Make an item: What the pilot taught us"]'),
                    ("click", 'role=button[name="Make the item"]'),
                ),
                expects=(REFUSED,),
            ),
            State("suggest", "/meetings?open=M-0001&suggest=1"),
            State("over", "/meetings?open=M-0004"),
            State("empty", "/meetings", world="empty"),
        ),
    ),
    # ui_world's today: the budget review under way (M-0003) leads with its points and an item on it, then the week's
    # items overdue (A-0008 four days late first), due today and back from a snooze, the follow-up later today and the
    # Pilot sync tomorrow; chat and mail synced this morning. The stale world has chat three days back, mail never.
    "today": Page(
        world="today",
        states=(
            State("day", "/today"),
            State("meeting", "/today?open=M-0003"),
            State("item", "/today?open=A-0008"),
            State(
                "written",
                "/today",
                (("press", "j"), ("press", "j"), ("press", "2"), ("wait", "role=status")),
                after=(("press", "u"),),
            ),
            State(
                "refused",
                "/today?open=A-0008&drop=1",
                (("click", '#close-A-0008 >> text="Drop"'),),
                expects=(REFUSED,),
            ),
            State("empty", "/today", world="empty"),
            State("stale", "/today", world="stale"),
        ),
    ),
    # ui_world's search: the index fixture, Nortwin the supplier, Ben Sample a person, rec-0100 the supplier call with
    # its minutes and recording note.
    "search": Page(
        world="search",
        states=(
            State("start", "/search"),
            State("results", "/search?query=Nortwin"),
            State("hit", f"/search?query=Nortwin&hit={CALL_HIT}"),
            State("verify", f"/search?query=Nortwin&hit={CALL_HIT}&verify=1"),
            State("doc", "/search?query=fleet&hit=recording-note:2026-10-13-fleet-review&lines=14"),
            State("chosen", "/search?query=Nortwin&people=ben-sample&since=2026-10-01&until=2026-10-31"),
            State("facet", "/search?query=Nortwin", (("click", "[data-facet=people]"),)),
            State("keys", "/search?query=Nortwin", (("press", "j"), ("press", "j"), ("press", "Enter"))),
            State("empty", "/search?query=zeppelin&sources=chat"),
            State("refused", "/search?query=fleet&since=soon", expects=(REFUSED,)),
        ),
    ),
    # ui_world's recording: the harbour crane check with its minutes and note, the yard tour with its audio stored, a
    # stretch cut, Whisper's transcript beside the recorder's and a correction, a held call and a legacy note.
    "recording": Page(
        world="recording",
        states=(
            State("start", "/recording"),
            State("marked", f"/recording?rid=plaud:rec-crane&doc={CRANE_NOTE}&at=00:40-01:39"),
            State("cuts", "/recording?rid=plaud:rec-tour"),
            State(
                "playing",
                "/recording?rid=plaud:rec-tour",
                (("click", "tbody tr:nth-child(2) td:first-child a"), ("wait", "[data-playing]")),
            ),
            State("correction", "/recording?rid=plaud:rec-tour&doc=correction:2026-10-14-yard-tour&at=00:20"),
            State("held", "/recording?rid=plaud:rec-held"),
            State("legacy", "/recording?rid=plaud:rec-old"),
            State("missing", "/recording?rid=plaud:rec-nothing"),
            State("refused", "/recording?rid=not+a+rid", expects=(REFUSED,)),
        ),
    ),
    # ui_world's briefs: Northwind stale with a flagged line, Ben stale with a new record, the harbour site current,
    # Dóra orphaned; fleet management's decisions with no brief, two waiting, one of them contradicting the current.
    "briefs": Page(
        world="briefs",
        states=(
            State("list", "/briefs"),
            State("stale", "/briefs?brief=brief:northwind"),
            State(
                "line",
                "/briefs?brief=brief:northwind",
                (("click", 'a[data-row]:has-text("supplier call")'), ("wait", "#raw")),
            ),
            State(
                "cited",
                "/briefs?brief=brief:ben-sample",
                (("click", 'a[data-row]:has-text("Ben wants the deadline")'), ("wait", "[data-cited-line]")),
            ),
            State("waiting", FLEET),
            State(
                "confirm",
                FLEET,
                (
                    ("click", WEEKLY_PICK.format("Confirm")),
                    ("wait", "role=status >> text=Confirmed"),
                    ("wait", ONE_WAITING),
                ),
                fresh=True,
            ),
            State(
                "reject",
                FLEET,
                (
                    ("click", WEEKLY_PICK.format("Reject")),
                    ("wait", "role=status >> text=Rejected"),
                    ("wait", ONE_WAITING),
                ),
                fresh=True,
            ),
            # Another tab confirms it first: this page's pick, at the rev it showed, is refused, and the page reads it.
            State(
                "conflict",
                FLEET,
                (
                    ("post", WEEKLY_BY_ANOTHER_TAB),
                    ("click", WEEKLY_PICK.format("Confirm")),
                    ("wait", "role=alert"),
                    ("wait", ONE_WAITING),
                ),
                expects=(MOVED,),
                fresh=True,
            ),
            State("missing", "/briefs?brief=brief:harbour-pilot"),
            State("empty", "/briefs", world="empty"),
        ),
    ),
    # ui_world's staging: the calls source mixed, memos work; a mail redacted since, a cut by Claude, a memo held
    # back, a call on hold and the held recordings.
    "staging": Page(
        world="staging",
        states=(
            State("list", "/staging"),
            State("record", "/staging", (("click", CALL_ROW), ("wait", "[data-actions]"))),
            State("redacted", "/staging", (("click", '[data-row] a:has-text("Ring Ben")'), ("wait", "[data-actions]"))),
            State(
                "held",
                "/staging",
                (("click", '[data-held="rec-cut-03"] summary'), ("click", '[data-held="rec-cut-03"] [data-paste]')),
            ),
            State(
                "preview",
                "/staging",
                (
                    ("click", CALL_ROW),
                    ("click", SECOND),
                    ("click", 'role=button[name="Preview"]'),
                    ("wait", "[data-preview]"),
                ),
            ),
            State(
                "work",
                "/staging",
                (
                    ("click", CALL_ROW),
                    ("click", SECOND),
                    ("click", WORK),
                    ("wait", "role=status >> text=classified work"),
                ),
                fresh=True,
            ),
            State(
                "private",
                "/staging",
                (
                    ("click", '[data-row] a:has-text("The offer")'),
                    ("click", "text=Private: drop it"),
                    ("wait", "[data-confirm]"),
                ),
            ),
            State(
                "dropped",
                "/staging",
                (
                    ("click", '[data-row] a:has-text("The offer")'),
                    ("click", "text=Private: drop it"),
                    ("click", "text=Drop it as private"),
                    ("wait", "role=status >> text=dropped as private"),
                ),
                fresh=True,
            ),
            State(
                "promoted",
                "/staging",
                (("click", 'role=button[name="Promote memo"]'), ("wait", "role=status >> text=Promoted")),
                fresh=True,
            ),
            State(
                "refused",
                "/staging",
                (("click", CALL_ROW), ("fill", f"{TYPED}|12:30"), ("click", WORK), ("wait", "role=alert")),
                expects=(REFUSED,),
            ),
            # Another tab classifies the call first: this page's Work, on the record as it read it, writes nothing.
            State(
                "conflict",
                "/staging",
                (("click", CALL_ROW), ("other", WORK), ("click", WORK), ("wait", "text=This record changed")),
                expects=(MOVED,),
                fresh=True,
            ),
            State("filtered", "/staging?source=memo"),
            State("empty", "/staging", world="empty"),
        ),
    ),
    # ui_world's sync: calls synced an hour ago, memo dropped twice since its whole run and owing ids, chat refused
    # for want of its skill, another machine's mark, a folder source.
    "sync": Page(
        world="sync",
        states=(
            State("page", "/sync"),
            State("run", f"/sync?run={CALLS_RUN}"),
            State("dropped", f"/sync?run={MAPPER_RUN}"),
            State("ids", "/sync", (("click", '[data-machine="ui-box"] summary'),)),
            State("narrowed", "/sync?source=calls"),
            State("none-of", "/sync?source=chat"),
            State("keys", "/sync", (("press", "j"), ("press", "j"), ("press", "Enter"), ("wait", "#panel-title"))),
            State("live", "/sync", (("run", "memo"), ("reload", ""), ("wait", '[data-state="open"]')), fresh=True),
            State(
                "failed",
                "/sync",
                (("fail", "/api/web/sync"), ("reload", ""), ("wait", "role=alert")),
                expects=(FAILED_READ,),
            ),
            State("loading", "/sync", (("hang", "/api/web/sync"), ("reload", ""), ("wait", "[data-slot=skeleton]"))),
            State("refused", "/sync?k=0", expects=(REFUSED,)),
            State("empty", "/sync", world="empty"),
        ),
    ),
    # ui_world's settings: settings_kit's sources, lists and pattern, Ada's memos stored and two waiting; `edited` has
    # credentials off by hand, not committed.
    "settings": Page(
        world="settings",
        states=(
            State("page", "/settings"),
            State("values", "/settings?show=lists"),
            State("change", "/settings", (("click", "#source-calls summary"),)),
            State("question", "/settings", (*ADD_PEOPLE, ("wait", "[data-question=add]"))),
            State(
                "added",
                "/settings",
                (*ADD_PEOPLE, ("click", "text=Add it to people"), ("wait", "role=status >> text=Added one value")),
                fresh=True,
            ),
            State(
                "field-error",
                "/settings",
                (
                    ("click", "#list-people summary"),
                    ("click", "#list-people form button"),
                    ("wait", "#add-people-error"),
                ),
                expects=(REFUSED,),
            ),
            State(
                "private",
                "/settings",
                (
                    ("click", "#source-memo summary"),
                    ("click", "#source-memo li:first-child button"),
                    ("wait", "[data-question=private]"),
                ),
            ),
            State(
                "rule",
                "/settings",
                (("click", 'role=button[name="Turn it on: phone"]'), ("wait", "role=status >> text=Phone numbers")),
                fresh=True,
            ),
            # Another tab turns phone redaction on first: this page's change, on the file it read, writes nothing.
            State(
                "conflict",
                "/settings",
                (
                    ("click", "#source-calls summary"),
                    ("other", 'role=button[name="Turn it on: phone"]'),
                    ("click", 'role=button[name="Make it work: calls"]'),
                    ("wait", "role=alert"),
                ),
                expects=(MOVED,),
                fresh=True,
            ),
            State(
                "failed",
                "/settings",
                (("fail", "/api/web/settings"), ("reload", ""), ("wait", "role=alert")),
                expects=(FAILED_READ,),
            ),
            State(
                "loading",
                "/settings",
                (("hang", "/api/web/settings"), ("reload", ""), ("wait", "[data-slot=skeleton]")),
            ),
            State("edited", "/settings", world="edited"),
            State("empty", "/settings", world="empty"),
        ),
    ),
    # The pages a stranger reaches, the not-found page in the shell, and the page a failure on a page path answers.
    "signin": Page(
        world="empty",
        states=(
            State("sign-in", "/settings?show=lists", signed=False, expects=(STRANGER,)),
            State("used", "/session?code=used-already&next=/sync", signed=False, expects=(STRANGER,)),
            State("not-found", "/nowhere", expects=(MISSING,)),
            State("error", "/today", failed=True, expects=(FAILED_READ,)),
        ),
    ),
    # ui_world's curate: Northwind's alias card, new people and terms, one accepted and one rejected.
    "curate": Page(
        world="curate",
        states=(
            State("list", "/curate"),
            State("card", "/curate", (("click", ALIAS_ROW), ("wait", "[data-actions]"))),
            State(
                "edit", "/curate", (("click", CLEO_ROW), ("click", 'role=button[name="Edit"]'), ("wait", "#edit-id"))
            ),
            State(
                "accepted",
                "/curate",
                (("click", ALIAS_ROW), ("click", ACCEPT), ("wait", "role=status >> text=accepted")),
                fresh=True,
            ),
            State(
                "round",
                "/curate",
                (("click", "text=Start a new round"), ("wait", "role=status >> text=A new round")),
                fresh=True,
            ),
            State(
                "undone",
                "/curate",
                (
                    ("click", '[aria-labelledby="log-title"] >> role=button[name^="Undo"]'),
                    ("wait", "role=status >> text=Undone"),
                ),
                fresh=True,
            ),
            State(
                "lifted",
                "/curate",
                (("click", 'role=button[name^="Lift"]'), ("wait", "role=status >> text=Lifted")),
                fresh=True,
            ),
            State(
                "refused",
                "/curate",
                (
                    ("click", CLEO_ROW),
                    ("click", 'role=button[name="Edit"]'),
                    ("fill", "#edit-id|Not An Id"),
                    ("click", 'role=button[name="Accept as edited"]'),
                    ("wait", "role=alert"),
                ),
                expects=(REFUSED,),
            ),
            # Another tab accepts the card first: this page's Accept, on the entity file it showed, writes nothing.
            State(
                "conflict",
                "/curate",
                (
                    ("click", CLEO_ROW),
                    ("other", ACCEPT),
                    ("click", ACCEPT),
                    ("wait", "text=changed since the page showed"),
                ),
                expects=(MOVED,),
                fresh=True,
            ),
            State("empty", "/curate", world="empty"),
        ),
    ),
}

CHECKS = ("axe", "console", "csp", "sideways", "targets")

# The page-side half of the checks: CSP violations as they happen, from before the app's first script.
WATCH_CSP = (
    "window.__csp = [];"
    "document.addEventListener('securitypolicyviolation', e => "
    "window.__csp.push(`${e.violatedDirective} ${e.blockedURI || 'inline'}`));"
)
# A modal (a dialog, an open listbox or menu) hides the page behind it from assistive technology, as it should; axe
# then reads that page as missing its landmarks, so a modal state is checked inside the modal on top, the last opened.
AXE_RUN = """async () => {
  const modal = [...document.querySelectorAll(
    '[role=dialog][data-state=open], [role=alertdialog][data-state=open], [role=listbox][data-state=open], ' +
    '[role=menu][data-state=open]')].pop();
  const found = await axe.run(modal ?? document, {
    runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']},
  });
  return found.violations.map(v => `${v.id} (${v.impact}): ${v.nodes.length} node(s), ` +
    v.nodes.slice(0, 3).map(n => n.target.join(' ')).join(' | '));
}"""
MEASURE = """(least) => {
  const coarse = matchMedia('(pointer: coarse)').matches;
  const floor = coarse ? Math.max(least, 44) : least;
  const out = [];
  const seen = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=checkbox], ' +
    '[role=switch], [role=tab], [role=menuitem], [role=menuitemradio], [role=option]';
  for (const el of document.querySelectorAll(seen)) {
    // A control hidden from everyone, such as the native select a Select keeps for a form's autofill.
    if (el.closest('[aria-hidden="true"]')) continue;
    const box = el.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'inline' || style.clipPath !== 'none') continue;
    if (box.width < floor || box.height < floor) {
      const name = (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 40);
      out.push(`${el.tagName.toLowerCase()} "${name}" ${Math.round(box.width)}x${Math.round(box.height)}`);
    }
  }
  const root = document.documentElement;
  return {coarse, small: out, sideways: root.scrollWidth > root.clientWidth};
}"""


@dataclass
class Shot:
    state: str
    theme: str
    size: str
    file: str
    problems: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    @property
    def ok(self) -> bool:
        return not any(self.problems.values())


def environment(work: Path, base: Mapping[str, str], root: Path) -> dict[str, str]:
    """What every process devkit ui runs gets: the caller's environment without Docket's and git's own variables,
    then folders under `work` for Docket's config, state and models, git's config, the failing keyring, and the
    kits on the path for the world script."""
    env = {k: v for k, v in base.items() if not k.startswith(DROPPED_PREFIXES) and k not in DROPPED}
    env |= {
        "DOCKET_CONFIG_DIR": str(work / "config"),
        "DOCKET_STATE_DIR": str(work / "state"),
        "DOCKET_MODELS_DIR": str(work / "models"),
        "DOCKET_MACHINE": "ui-box",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
        "GIT_CONFIG_GLOBAL": str(work / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "PYTHONPATH": str(root / KITS),
        "PYTHONUTF8": "1",
    }
    return env


def next_round(root: Path, page: str) -> Path:
    """`.local/ui/<page>/r<n>/`, n one past the highest there: every round's shots are kept."""
    folder = root / ROUNDS / page
    taken = [int(p.name[1:]) for p in folder.glob("r*") if p.is_dir() and p.name[1:].isdigit()]
    out = folder / f"r{max(taken, default=0) + 1}"
    out.mkdir(parents=True)
    return out


def checked_port(port: int) -> int:
    """The throwaway service's port, which is never the owner's."""
    if port == OWNERS_PORT:
        raise KitError(f"the throwaway service got port {OWNERS_PORT}, the owner's; stopping")
    return port


def shot_name(state: str, theme: str, size: str) -> str:
    width, height = SIZES[size]
    return f"{state}-{theme}-{width}x{height}.png"


def summary(shots: Sequence[Shot]) -> list[str]:
    """The checks' lines: one per problem, by shot, then a count."""
    lines: list[str] = []
    for shot in shots:
        for check in CHECKS:
            lines += [f"{shot.file}: {check}: {p}" for p in shot.problems.get(check, [])]
    bad = sum(1 for s in shots if not s.ok)
    lines.append(f"{len(shots)} shots, {bad} with problems" if bad else f"{len(shots)} shots, every check passed")
    return lines


def _run(cmd: list[str], env: dict[str, str], cwd: Path) -> None:
    proc = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if proc.returncode != 0:
        raise KitError(f"{' '.join(cmd[:3])}: {(proc.stderr or proc.stdout).strip()[-2000:]}")


def remove(folder: Path) -> None:
    """The throwaway folders, gone: git keeps its objects read-only, which Windows deletes only once made writable,
    and a process just stopped can hold a file a moment longer."""

    def writable(func: Callable[[str], object], path: str, _error: BaseException) -> None:
        Path(path).chmod(stat.S_IWRITE | stat.S_IREAD)
        func(path)

    try:
        shutil.rmtree(folder, onexc=writable)
    except OSError:
        time.sleep(1)
        shutil.rmtree(folder, onexc=writable)


@contextmanager
def service(root: Path, world: str) -> Generator[tuple[int, str]]:
    """A throwaway service over a new invented world: its port and its token, stopped and its folders removed after."""
    work = Path(tempfile.mkdtemp(prefix="docket-ui-"))
    env = environment(work, os.environ, root)
    (work / "gitconfig").write_text(GITCONFIG, encoding="utf-8")
    vault = work / "vault"
    said = work / "service.err"
    proc: subprocess.Popen[bytes] | None = None
    try:
        _run([sys.executable, str(root / WORLD_SCRIPT), str(vault), world], env, root)
        token = (work / "config" / "service-token").read_text(encoding="utf-8").strip()
        cmd = [sys.executable, "-m", "docket", "serve", "--vault", str(vault), "--port", "0"]
        with said.open("wb") as err:
            proc = subprocess.Popen(cmd, cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=err)
        record = work / "state" / "service.json"
        deadline = time.monotonic() + START_SECONDS
        while not record.is_file():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise KitError(f"the throwaway service didn't start: {said.read_text(errors='replace')[-2000:]}")
            time.sleep(0.1)
        port = checked_port(int(json.loads(record.read_text(encoding="utf-8"))["port"]))
        yield port, token
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(15)
        remove(work)


def sign_in_link(port: int, token: str, after: str) -> str:
    """A one-time link that opens `after`, on localhost: never 127.0.0.1, whose session cookie is the owner's."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/session",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310 - the throwaway service on this machine
        url = str(json.loads(answer.read())["url"])
    code = url.split("code=", 1)[1]
    return f"http://localhost:{port}/session?code={code}&next={urllib.parse.quote(after, safe='')}"


def unexpected(lines: Sequence[str], expects: Sequence[str]) -> list[str]:
    """A state's console lines, but for those it brings about on purpose."""
    return [line for line in lines if not any(expected in line for expected in expects)]


def open_run(port: int, token: str, source: str) -> None:
    """A run of the source opened and bound as Claude's session binds it, so the Sync page shows it open."""

    def call(path: str, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310 - the throwaway service here
            found: dict[str, Any] = json.loads(answer.read())
            return found

    run = call("/api/runs", {"source": source})["run"]
    call(f"/api/runs/{run}/session", {"session": "ui-session", "agent": None})


def _on_path(path: str) -> Callable[[str], bool]:
    return lambda url: urllib.parse.urlsplit(url).path == path


def _failed_read(route: Any) -> None:
    from docket.web.display import FAILED  # noqa: PLC0415 - the service's own words, read where they are used

    route.fulfill(status=500, content_type="application/json", body=json.dumps({"error": FAILED}))


def _never(_route: Any) -> None:
    """A read left unanswered, so the page's loading state stays to be shot."""


def _failed_page(route: Any) -> None:
    from docket.web import bare  # noqa: PLC0415 - the page as the service writes it

    headers = {"Content-Security-Policy": bare.CSP, "Cache-Control": "no-store"}
    route.fulfill(status=500, content_type="text/html; charset=utf-8", headers=headers, body=bare.failed_page())


def _steps(page: Any, steps: Sequence[tuple[str, str]], opens: Callable[[str], None] | None = None) -> None:
    for verb, arg in steps:
        if verb == "press":
            page.keyboard.press(arg)
        elif verb == "fill":
            selector, _, text = arg.partition("|")
            page.fill(selector, text)
        elif verb == "wait":
            page.wait_for_selector(arg)
        elif verb == "post":
            path, _, body = arg.partition("|")
            status = page.evaluate(POST, [path, body])
            if status != 200:
                raise KitError(f"the state's post to {path} answered {status}")
        elif verb == "other":
            other = page.context.new_page()
            other.goto(page.url)
            other.wait_for_load_state("networkidle")
            other.click(arg)
            other.wait_for_load_state("networkidle")
            other.close()
        elif verb == "run":
            if opens is None:
                raise KitError("a state's run step needs the service's token")
            opens(arg)
        elif verb == "fail":
            page.route(_on_path(arg), _failed_read)
        elif verb == "hang":
            # Never answered: the page stays as it is while its read is on its way.
            page.route(_on_path(arg), _never)
        elif verb == "reload":
            page.reload(wait_until="domcontentloaded")
        else:
            page.click(arg)
        page.wait_for_timeout(150)


def _shoot(page: Any, state: State, out: Path, shot: Shot, opens: Callable[[str], None] | None = None) -> None:
    _steps(page, state.steps, opens)
    page.wait_for_timeout(250)
    page.screenshot(path=str(out / shot.file))
    measured = page.evaluate(MEASURE, TARGET)
    shot.problems["targets"] = list(measured["small"])
    shot.problems["sideways"] = ["the document scrolls sideways"] if measured["sideways"] else []
    shot.problems["csp"] = list(page.evaluate("window.__csp"))
    shot.problems["axe"] = list(page.evaluate(AXE_RUN))
    if state.after:
        _steps(page, state.after)
        page.wait_for_timeout(250)


class Console:
    """What a tab's console said in error, and the page errors it threw."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def message(self, message: Any) -> None:
        if message.type == "error":
            self.lines.append(str(message.text))

    def error(self, error: Any) -> None:
        self.lines.append(str(error))


def shoot(
    browser: Any,
    states: Sequence[State],
    port: int,
    token: str,
    *,
    out: Path,
    axe: Path,
    sizes: Sequence[str] = tuple(SIZES),
    themes: Sequence[Literal["light", "dark"]] = THEMES,
) -> list[Shot]:
    """Every state, at each size, in each theme, each in a browser context of its own: its own cookies, so each signs
    in afresh through a one-time link."""
    shots: list[Shot] = []
    for size in sizes:
        width, height = SIZES[size]
        for theme in themes:
            for state in states:
                if state.only not in (None, size):
                    continue
                shot = Shot(state.name, theme, size, shot_name(state.name, theme, size))
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    color_scheme=theme,
                    reduced_motion="reduce",
                    timezone_id=ZONE,
                    locale="en-GB",
                    has_touch=size == "narrow",
                    is_mobile=size == "narrow",
                    device_scale_factor=1,
                )
                context.add_init_script(WATCH_CSP)
                context.add_init_script(path=str(axe))
                tab = context.new_page()
                errors = Console()
                tab.on("console", errors.message)
                tab.on("pageerror", errors.error)
                tab.clock.set_fixed_time(CLOCK)
                if state.failed:
                    tab.route(_on_path(state.url.split("?")[0]), _failed_page)
                tab.goto(
                    sign_in_link(port, token, state.url) if state.signed else f"http://localhost:{port}{state.url}"
                )
                # A query may hold a slash, such as a material's path, which one star doesn't match.
                tab.wait_for_url(f"**{state.url.split('?')[0]}**")
                tab.wait_for_selector("#main")
                # The page's reads answered, so a key pressed next finds its rows.
                tab.wait_for_load_state("networkidle")
                tab.evaluate("document.fonts.ready")
                _shoot(tab, state, out, shot, lambda source: open_run(port, token, source))
                shot.problems["console"] = unexpected(errors.lines, state.expects)
                shots.append(shot)
                context.close()
    return shots


@contextmanager
def chrome() -> Generator[Any]:
    """The machine's own Chrome, driven by Playwright in a profile of its own; nothing is downloaded."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 - only this command drives a browser

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        try:
            yield browser
        finally:
            browser.close()


def run(
    page_name: str,
    *,
    root: Path,
    browser: Callable[[], AbstractContextManager[Any]] = chrome,
    serve: Callable[[Path, str], AbstractContextManager[tuple[int, str]]] = service,
) -> int:
    """A round of the page: its shots and checks in a new round folder; 1 when a check failed."""
    if page_name not in PAGES:
        raise KitError(f"no page {page_name!r} to shoot; the pages are {', '.join(PAGES)}")
    page = PAGES[page_name]
    axe = root / AXE
    if not axe.is_file():
        raise KitError(f"{AXE} is missing: run `npm ci` in packages/docket/web first")
    out = next_round(root, page_name)
    shots: list[Shot] = []
    worlds = list(dict.fromkeys(state.world or page.world for state in page.states))
    with browser() as driven:
        for world in worlds:
            states = [state for state in page.states if (state.world or page.world) == world]
            shared = [state for state in states if not state.fresh]
            if shared:
                with serve(root, world) as (port, token):
                    shots += shoot(driven, shared, port, token, out=out, axe=axe)
            for state in (state for state in states if state.fresh):
                for size in SIZES:
                    for theme in THEMES if state.only in (None, size) else ():
                        with serve(root, world) as (port, token):
                            shots += shoot(
                                driven, [state], port, token, out=out, axe=axe, sizes=(size,), themes=(theme,)
                            )
    lines = summary(shots)
    (out / "checks.json").write_text(
        json.dumps([s.__dict__ | {"ok": s.ok} for s in shots], indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (out / "checks.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    for line in lines:
        print(line)
    print(f"shots and checks in {out.relative_to(root).as_posix()}")
    return 0 if all(s.ok for s in shots) else 1
