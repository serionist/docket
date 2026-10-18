---
name: calendar
description: Harvest an Outlook calendar into Docket from Outlook on the web's week view, with one fixed script run in the user's own Chrome. Use when a sync runs the calendar source, or the user asks to fetch or catch up on the calendar.
---

<!-- docket: steps from run_open -->

# Calendar, a Docket example source

Reads an Outlook calendar from Outlook on the web's week view with one fixed script, `scripts/calendar-week.js`, run
through Claude in Chrome's `javascript_tool`; `mapper.py` next to this file makes one record per event occurrence. No
event is ever clicked. Mail is a source of its own, the `outlook` example.

This folder is an example to copy, not part of Docket: it suits a calendar no calendar MCP server can reach, and where
one can, an MCP route is simpler. Claude Code doesn't load this file as a skill: `run_open("calendar")` names it and
gives the run's steps. Keep the marker line at the top, since Docket refuses a run of a `SKILL.md` without it.

## Set it up

`docket source new calendar --example --vault <vault>` copies this folder to `.claude/skills/sources/calendar/` in the
repo that holds the vault and adds the source to `vault.yaml`. By hand, the same two steps:

1. Copy this folder to `.claude/skills/sources/calendar/` in the repo that holds the vault.
2. In `vault.yaml`, add the source, then commit `vault.yaml`: a source added by hand applies once it is committed.

```yaml
sources:
  calendar:
    sensitivity: work             # every event goes straight to ready; make it mixed to classify each first
    mapper: .claude/skills/sources/calendar/mapper.py
    tools: [javascript_tool]
    lookback_days: 7              # a run reads whole weeks, so a week back always takes in last week
```

Then run its tests where it was copied: `python -m pytest .claude/skills/sources/calendar/tests`. The tests that go
through Docket's own checks run only where `docket` can be imported. The copy is the project's own from then on:
change the route as your calendar needs, and keep its tests passing.

A session or agent holds one open run: opening another, of any source, drops the one before. Run this source's
run to its close before another opens in the same session, or run each source in its own agent.

## Access

- Claude in Chrome, the user's own Chrome, signed in to OWA at outlook.cloud.microsoft. Load the Chrome tools with one
  ToolSearch, call `tabs_context_mcp`, work in a new tab of your own and close it at the end.
- Signed out: stop and say the user has to sign in. Never enter anything.
- Read only: no click on an event, no accept or decline, nothing typed. Seen once: a real click on an event froze
  screenshots (CDP timeout) and opened no peek; a navigate recovered the tab. Attendees come from the invite mail, not
  the event.

## The reads

| Read | How |
|---|---|
| this week | navigate to `https://outlook.cloud.microsoft/calendar/view/week`. A date in the URL doesn't move the week: OWA redirects back to this one |
| a week | `scripts/calendar-week.js`, pasted whole as `javascript_tool`'s text. It waits up to 6 s for the view to settle and returns the version line, a header and one string per event |
| another week | the paging snippet below with `previous week`, `next week` or `today`: it clicks OWA's week button, never an event, and returns the range before and after |
| a later part | when the last string is `more<TAB><n>`, run the script again with `<n>` in place of the 0 on its last line. Seen once: weeks of 30 to 60 events, about 14 to 20 KB, came back in one part each |

```js
await (async (to) => {
  const range = () => { for (const e of document.querySelectorAll('button, span, div, h1, h2')) { if (e.childElementCount) continue; const t = (e.textContent || '').trim(); if (/^\d{4}, [A-Z][a-z]+ \d{1,2}/.test(t)) return t; } return ''; };
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const before = range();
  const button = document.querySelector(`[aria-label^="Go to ${to}"]`);
  if (!button) return ['no button', to];
  button.click();
  for (const t0 = Date.now(); range() === before && Date.now() - t0 < 8000;) await tick();
  return ['from ' + before, 'to ' + range()];
})('next week');
```

The header is `week<TAB><range shown><TAB><events><TAB><from><TAB><events whose data was read><TAB><time zone>`. Each
event string is `ev`, how many strings it takes, then its id, start, end, response, organiser, address, place and
aria-label. The id, the label, and the event's own data behind the element are all the script reads. The labels'
wording is OWA's in English; another display language needs the script's and the mapper's patterns changed together.

### How the page behaves

Seen on one tenant's OWA. Microsoft changes the page; the script's header counts show when it has.

- The week runs Sunday to Saturday. Every event of the week is in the page whatever the scroll, so nothing is scrolled.
- The URL opens the Week view. Seen once: in a narrow window "Work week" showed one day. If the header's range isn't a
  whole week, click "Week" in the Home ribbon (a view button, not an event).
- `/calendar` shows a splash first. A header with 0 events right after a navigate means it hadn't loaded: run the script
  again a few seconds later. An empty week reads 0 too.
- Ids: every event carries `data-calitemid`, an Exchange immutable id (single event or series `AAkA…`, 68 characters;
  occurrence `AAkB…`, 84). An occurrence's id holds its series' id and its slot's date, so occurrences of a series have
  their own ids and a moved occurrence keeps its id. Ids are the same after a reload.
- The aria-label holds the subject, time, date, place, organiser (`By …`), show-as and `Recurring event` or `Exception
  to recurring event`. It is never cut short. It leaves out the response, the organiser's address, and on past days the
  place; the script takes those from the event's data.
- The label's show-as (Free, Tentative, Busy) is not the response: an invite you haven't answered shows Tentative with
  response none. A cancelled event shows `Canceled: ` in its subject and Free.
- The owner's own events have no `By` in the label. A to-do shows `Todo icon, ` before the subject. All-day events read
  `all day event, <date>[ to <date>]`.
- Day numbers can be padded (`October 09`); the script and the mapper take both.
- A declined occurrence drops out of the week view at once, so its record keeps its last version; the decline is in Sent
  Items. A multi-day all-day placeholder moved out of the weeks read just vanishes; the update mail says where it went.
- Chrome cuts a string at about 1,000 characters and an array at 100 items, and blocks a string that looks like a query
  string or a long base64 run. The script returns at most 95 strings and 30,000 characters, a long event in `+<TAB>`
  strings of 900, links and query strings taken out. A blocked or cut string is set aside by the mapper.
- A known gap, seen once: the script's `clean()` doesn't escape `;` or `=`, and an occurrence's id ends in `=`, so a
  `;` in the place or label (`Microsoft Teams Meeting; Room 4.12`) is enough for Chrome to block the whole event line
  as "Cookie/query string data". The mapper sets each aside as `blocked`, so they make no record. The fix belongs in
  `clean()` (escape them as the mail scripts do, `=` as `¤e`), with a new version line the mapper learns first.

## The window

`run_open` gives the window: the latest mark less the lookback (`lookback_days: 7` under `sources.calendar` in
`vault.yaml`), to now. Read this week first, then next week, then each earlier week back to the one holding the
window's start. On a daily sync that is three weeks.

Why 7: a run reads whole weeks, so a week's lookback always takes in the previous week. That covers the last working
day across weekends and holidays and catches late changes to last week's meetings, for one more read of about 20 KB.
Next week is read whatever the window, so the run holds what is coming too.

## A run

Docket gives the run's steps: what to skip, what is owed, the budget and how to close. This is what is the calendar's
own.

| Part | What |
|---|---|
| Sign in | OWA's sign-in page instead of the calendar: stop without closing |
| List | the weeks above, each read once with the script; page between them with the snippet |
| Data missing | the header's events-whose-data-was-read is 0 while events is not: OWA changed. Stop without closing, or every event would get a new version without its response and address, and say so when you report the run |
| Held | a held event or series is dropped by the mapper; a week view can't leave one out |
| Kept | nothing to skip: a kept event read again makes no new version |
| Other calls | while the run is open, `javascript_tool` runs only the script and the snippet: every call is captured. Anything else waits until after the close |
| Too large | under the script's caps it can't happen; if it does, skip the week without an id, as `too-large` |
| Budget | past it, read no more weeks; skip each one left without an id, as `later` |
| By id | nothing: the mapper leaves nothing waiting, and no week is a retry id |
| Close | `ingest(run, captures=<highest n>, skipped=[...])`; "another run is closing" means call it again a minute later. `run_open(source="calendar", final=true)` then answers that no final run is needed |

A skipped week goes in `skipped` without an id: a week is no record, so an id would come back to every run. The next
run reads it again while it is inside that run's window.

## What it makes

One record per occurrence, kind `event`:

| Field | What |
|---|---|
| rid | `calendar:<id>`, the page's id in Microsoft Graph's REST form (`+` as `-`, `/` as `_`) |
| thread | `calendar:<series id>` for an occurrence or exception, rebuilt from its id; none for a single event |
| ts | the start, in UTC |
| title | the subject as shown, `Canceled: ` included |
| author | the organiser, `Name <address>` |
| text | the time and date as shown, `Where: <place>`, `Response: <accepted, tentative, declined, none or organiser>`, and `Cancelled` when it is. Never a join link |
| native | the id as shown, single, occurrence or exception, the slot's date, show-as, all-day, the end, the raw response, the label, the week and the time zone |
| fidelity | verbatim; `derived` when the page has no ids |

- A cancellation or a new response is a new version of the same rid. A new show-as alone isn't, and neither is reading a
  past day's label without its place.
- Without ids on the page, the rid is `calendar:d-<hash of organiser, subject and start>`. A cancellation keeps it; a
  moved meeting gets a second record.
- The ids are the ones Microsoft Graph gives with `Prefer: IdType="ImmutableId"`, in its REST form, so a later Graph
  route can keep the rids. Check that on one event before relying on it.
- With `sensitivity: work`, the records go straight to ready.

## Changing the mapper

Every record the lookback reads again gets a new version when the mapper's output changes.

- Keep the output stable: change it only for a real change in what the calendar says.
- Pin golden versions in the tests, as `tests/test_with_docket.py` does, so a change shows before it runs.
- A change to what the script returns gets a new version line (`docket:calendar-week/2`), and the mapper learns it before
  the script ships.
- Pull the project on every machine before its next sync, so two machines never map one event two ways.
