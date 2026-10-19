---
title: Make the ledger's meetings from calendar records (one-way door)
issue: "#200"
status: draft
approved_by:
approved_at:
---

# Make the ledger's meetings from calendar records

Docket's core turns each event record a promotion writes into a meeting op keyed by its rid, in one write by `sync`:
a new event adds a meeting or joins the old one at its start and subject, a changed one sets what changed. The sync
links recordings and asks by time with #201's window read. Two PRs: the step and its catch-up, then promotion.

## Goal

- Done: each timed calendar event in raw has its meeting, moved, renamed or cancelled in place as the calendar changes
  it; an old meeting a record matches is joined, its number kept; nothing kept out of raw makes one.
- Out: writing links, the sync's (#210); joins and splits on the Meetings page (#205); the import (#202) and the
  export script (roadmap step 15); meetings whose event vanished from the calendar (Risks).

## Design

### Who, and when (DR-0647)

| Option | Verdict |
|---|---|
| the core, after each promotion, from the versions it wrote | chosen: DR-0105 gives it to the core; one machine writes each version's ops, with no Claude, by the same rule everywhere |
| the core, on index update | no: it runs on every clone and on a rebuild, so it would apply a stale version over the sync's own set (a decline the week view no longer shows) |
| the sync skill through `ledger_batch` | no: a fixed mapping needs no judgement, and every machine's copy of the skill would have to agree |

- `Gate.promote` keeps the event records it wrote; once its lock is released, `ledger/calendar.py` takes them in start
  order, then rid, as one write `by: sync`, via the caller's `cli` or `page`.
- It is a change inside the writer that the batch's ops don't take, so its reads and ops are one plan under the vault
  lock and a dry run shows them. It never refuses: a record it can't take is left out and named, with why. If the
  write fails, the promotion stands and its output names the catch-up.
- `docket ledger calendar [--since DAY] [--dry-run]`, the catch-up, takes the latest version of each event record in
  raw from DAY, 14 days back by default, and only adds and joins: a key the ledger holds gets no set from it, so a
  stale version never undoes what the sync set by hand. It runs when #200 lands, after an import, after a split.
- Two clones: one that pulls another's run gets its ops with it and promotes nothing. Two that promote one version
  write the same ops in the same order: one meeting per key, the first add's id and number (DR-0621), the same numbers
  when their ledgers agreed, else DR-0613's renumber.

### The record (one-way, DR-0646) and its fields (DR-0648)

What the core reads from a record of kind `event`, one per occurrence. The reference calendar source writes it all.

| Meeting field | From the record |
|---|---|
| `event` | the rid, which a move keeps: an occurrence's id holds its series and its original slot (DR-0105's key) |
| `series` | the thread, when there is one |
| `start`, `end` | `ts` and `native.end`, in `vault.yaml`'s zone with that moment's offset |
| `subject` | the title, a `Canceled: ` start dropped from a cancelled one; `(no subject)` for none |
| `organiser` | the author's name without its `<address>`, or the address alone |
| `where` | the text's `Where: ` line |
| `response` | `cancelled` when the text has a `Cancelled` line, else its `Response: ` line |
| `attendees` | the names in `to`; the reference mapper gives none, since the week view shows none |

- A key the ledger holds gets one `set` of what differs, on the add carrying the key (#199's writer): a move, a
  rename, a new place or response, a cancellation. A version that changes only show-as or the label writes nothing; a
  field the record doesn't carry is never cleared; a set on an archived meeting is idle (DR-0624).
- Left out: an all-day event (`native.all_day`), a derived record (its rid built from subject and start), one with no
  end or an end before its start, and a cancelled one that holds no meeting and joins none.
- The owner's zone, not the record's `native.zone`: the import and the writer use it too, so a join compares like
  with like, and a week read on a trip doesn't rewrite every meeting.

### Joins (DR-0649)

As todo.py's `meeting add` updated the meeting with the same start and subject, the step joins it:

- the match: a standing meeting with no event, archived or not, whose start is the record's to the minute and whose
  subject is the record's with case and spaces folded; of several, the earliest by id;
- the join: a `meeting` add with `continues`, carrying the record's fields as any add does. The old meeting's number,
  points and outcome hold; an open one takes the calendar's fields, as todo.py's update did; an archived one only
  gains its key, the fields being an idle set there;
- tried for each version the step takes, key held or not, unless the key continues a meeting already;
- never a meeting whose join from this key was undone. A key whose adds are all undone gets a new add only when each
  was a join undone alone (a split, DR-0621); any other was removed, and stays removed after a new version too;
- an add with an unkeyed meeting at its start under another subject names it, so a renamed series' old meeting can
  be joined by hand (`ledger_batch`'s `meeting_add` with `continues` now, the Meetings page later).

### Kept out (DR-0650)

- A record makes nothing when media's screen would keep it out now (forgotten, its source private or not in force, a
  never-store match, withheld, classified private by hand: DR-0547) or a hold covers it. The gate keeps those out of
  raw already; this catches one a list, a hold or a decision took after it went in. Its meeting stays, for #182.
- `to` is part of what never-store matches, so a listed attendee makes the record a never-store one and no name is
  left out piecemeal. DR-0627's refusal stays the writer's backstop.

### Links by time (DR-0651)

| todo.py | Docket |
|---|---|
| `meeting match <time>`, 10 minutes either side | #201's `meetings` view from 10 minutes before the time to 10 after |
| `--after 120`, for asks | the same view from 120 minutes before to 10 after |
| `meeting link --recording` | a `material` on the meeting, its ref the recording's rid |
| `meeting link --item` | the item's `meeting` (DR-0626) |

- The sync picks and writes them in its batch (#210), as /sync does today: one meeting, link it; two, the one the
  note's content names, else none; a cancelled one or the owner's own focus time passed over. The core links nothing,
  as a 1:1 inside a big invite's slot would link wrong; #201's note that #200 links by time itself changes to this.

### Old meetings and the import (DR-0652)

- The export script leaves every old meeting keyless; after the import, `docket ledger calendar --since <the
  calendar's first day>` joins each one a record matches. Its dry run is the report the roadmap asks to spot-check.
- A join's number is the continued meeting's, so the old number wins whatever ran first, and one matcher, tested
  here, does it. DR-0621 had the export script give the event itself, which loses the old number to a meeting the
  step made first in the live clone.

## Doors and data

One-way, PR 1's title naming it: what the core reads from an event record (DR-0646); AGENTS.md's mapper line gains it.
`medium`: the step and the catch-up (DR-0647), the fields (DR-0648), joins (DR-0649), kept out (DR-0650), links
(DR-0651), the import's joins (DR-0652). No new op, kind, field or via. Data: the first catch-up writes the window's
meetings in one write, undone as one batch if wrong.

## Steps

1. PR 1, "Make the ledger's meetings from calendar records (one-way door)", on #219: `ledger/calendar.py`, the
   writer's `calendar` change, `docket ledger calendar`; proven by `test_ledger_calendar.py`.
2. PR 2, "Make meetings at each promotion": `Gate.promote` runs the step, `docket staging promote` prints its line,
   the Staging page runs it too; proven by the example calendar's `test_with_docket.py`.

## Tests

- PR 1, invented people: each field; times either side of the 25 October DST change; each left-out case named while
  the rest are written; a move, a rename, a cancellation, a show-as-only version; an archived meeting's idle set;
  joins a minute off, by case and spaces, from two candidates, onto an archived meeting, from a key that continues one
  already; a split never rejoined, its key given an add; a removed meeting staying removed; the same records on two
  clones in both orders; the catch-up never setting; `--dry-run`; perf, 300 records into 2,000 meetings under 2 s.
- PR 2: the example week through close, promotion and the step, the Friday sync cancelled in a second run; a
  pulled run making no op; a failing step leaving the promotion; the Staging page; perf, 30 records under 0.5 s.

## Docs

`docs/design.md` 3.4's calendar bullet; `examples/sources/calendar/SKILL.md`, "What it makes" naming what the core
reads; `packages/docket/README.md`'s `docket ledger calendar`; glossary: join; a CHANGELOG line with PR 2.

## Risks

- An event deleted, or moved out of the weeks read, keeps its meeting: accepted, as todo.py did; the sync cancels it
  by hand. A renamed series' old meetings don't join: named in the dry run, joined by hand.
- Two clones promoting one week before they sync: one meeting per event, renumbered only if their ledgers differed.
- An import reverted after the catch-up joined its meetings: undo the catch-up's batch first (#202).

## Plan review

Not reviewed yet: `review-plan` runs next.
