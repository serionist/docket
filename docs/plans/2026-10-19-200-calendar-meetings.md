---
title: Make the ledger's meetings from calendar records (one-way door)
issue: "#200"
status: approved
approved_by: claude (autonomy grant)
approved_at: 2026-10-19
---

# Make the ledger's meetings from calendar records

Docket's core turns each event record a promotion writes into a meeting op keyed by its rid, in one write by `sync`:
a new event adds a meeting or joins the old one at its start and subject, a changed one sets what changed. The sync
links recordings and asks by time with #201's window read. Two PRs: the step and its catch-up, then promotion.

## Goal

- Done: each timed calendar event in raw has its meeting, moved, renamed or cancelled in place as the calendar changes
  it; an old meeting a record matches is joined, its number kept; nothing kept out of raw makes one.
- Out: writing links (#210), the Meetings page (#205), the import (#202), events that vanished (Risks).

## Design

### Who, and when (DR-0647)

| Option | Verdict |
|---|---|
| the core, after each promotion, from the versions it wrote | chosen: DR-0105 gives it to the core; one machine writes each version's ops, with no Claude, by the same rule everywhere |
| the core, on index update | no: it runs on every clone and on a rebuild, so it would apply a stale version over the sync's own set (a decline the week view no longer shows) |
| the sync skill through `ledger_batch` | no: a fixed mapping needs no judgement, and every machine's copy of the skill would have to agree |

- `Gate.promote` notes its event records' rids as pending in the vault's private folder before it writes the run
  file; a rid whose version never reached raw reads raw's latest and changes nothing. Then `ledger/calendar.py` makes
  one write `by: sync`, via the caller's `cli` or `page`, and clears them once it commits or has nothing to write. The
  next promotion or catch-up replays what is pending first, sets included, as DR-0463 finishes a stopped write first.
- One version per rid, raw's latest by line order, so an event moved twice before one promotion lands at its last
  time; the adds then go in start order, then rid. A pending rid is read at raw's latest version too, so one left
  over a full revert (#202) applies as any record would to the ledger it meets.
- It is a change inside the writer that the batch doesn't take: its reads and ops are one plan under the vault lock,
  and a dry run shows them. It never refuses: a record it can't take is left out and named, with why.
- `docket ledger calendar [--since DAY] [--dry-run]`, the catch-up: what is pending, then the latest version of each
  event record from DAY, 14 days back by default, adding and joining only, so a stale version never undoes what the
  sync set by hand. It runs when #200 lands, after an import or a reset, after a split.
- Two clones: a pulled run comes with its ops, never promoted again; two promoting one version write the same ops in
  the same order, one meeting per key (DR-0621), renumbered (DR-0613) only when their ledgers differed.

### The record (one-way, DR-0646) and its fields (DR-0648)

What the core reads from a record of kind `event`, one per occurrence. Every calendar mapper writes `native.end` and
`native.all_day`; the text's header, its lines before the first blank one, holds the first `Where: `, `Response: ` and
`Cancelled` lines read, and any body goes after it, never read. The reference calendar source writes it all.

| Meeting field | From the record |
|---|---|
| `event` | the rid, which a move keeps: an occurrence's id holds its series and its original slot (DR-0105's key) |
| `series` | the thread, when there is one |
| `start`, `end` | `ts` and `native.end`, in `vault.yaml`'s zone with that moment's offset |
| `subject` | the title, a `Canceled: ` start dropped from a cancelled one; `(no subject)` for none |
| `organiser` | the author's name without its `<address>`, or the address alone |
| `where` | the header's `Where: ` line |
| `response` | `cancelled` when the header has a `Cancelled` line, else its `Response: ` line |
| `attendees` | the names in `to`; the reference mapper gives none, since the week view shows none |

- A key the ledger holds gets one `set` of what differs, on the add carrying the key (#199's writer): a move, a
  rename, a new place or response, a cancellation. A version that changes only show-as or the label writes nothing; a
  field the record doesn't carry is never cleared; a set on an archived meeting is idle (DR-0624).
- Left out: an all-day event, a derived record (its rid built from subject and start), one with no end or an end
  before its start, and a cancelled one that holds no meeting and joins none.
- The owner's zone, not `native.zone`: the import's and the writer's, so a join compares like with like.

### Joins (DR-0649)

- the match, as todo.py's `meeting add` updated one: a standing meeting with no event, archived or not, whose start
  is the record's to the minute and whose subject is the record's with case and spaces folded; the earliest by id;
- the join: a `meeting` add with `continues`, carrying the record's fields as any add does. The old meeting's number,
  points and outcome hold; an open one takes the calendar's fields, as todo.py's update did; an archived one only
  gains its key, the fields being an idle set there. Tried for each version, unless the key continues one already;
- undos, read by how they were asked: one by `of` from `user` or `claude` is meant, however many meetings its write
  took out. A removal stays removed, a new version too; a `continues` add undone alone is a split, never rejoined,
  its key given an add of its own. One by `batch`, which the undo op's new `of_batch` names (DR-0614), or by `sync` is
  as if never: the step adds or joins again;
- near misses named in the output, to join by hand: an unkeyed meeting at the add's start under another subject, and
  one with its folded subject on its day at another start, so the cut-over spot-check catches moved meetings.

### Kept out (DR-0650)

- A record makes nothing when media's screen would keep it out now (forgotten, its source private or not in force, a
  never-store match, withheld, classified private by hand: DR-0547) or a hold covers it. The gate keeps those out of
  raw already; this catches one a list, a hold or a decision took after it went in. Its meeting stays, for #182.
- A listed attendee in `to` makes the record a never-store one: no name is left out piecemeal (DR-0627 as backstop).

### Links by time (DR-0651)

| todo.py | Docket |
|---|---|
| `meeting match <time>`, 10 minutes either side | #201's `meetings` view from 10 minutes before the time to 10 after |
| `--after 120`, for asks | the same view from 120 minutes before to 10 after |
| `meeting link --recording` | a `material` on the meeting, its ref the recording's rid |
| `meeting link --item` | the item's `meeting` (DR-0626) |

- The sync picks and writes them (#210), as /sync does: one meeting, link it; two, the one the note's content names,
  else none; skip cancelled ones and focus time. The core links nothing: a 1:1 in a big invite's slot would link wrong.

### Old meetings, the import and resets (DR-0652)

- #202's import brings old meetings in keyless; the catch-up after it, from the calendar's first day, joins each one a
  record matches, its id and number kept. #200 builds every key; its dry run is the roadmap's spot-check report.
- A rehearsal reset is #202's full `--revert`, which takes every op out, the step's meetings and joins with them, then
  the import and the catch-up, which makes them again from raw. Resets revert, never undo (DR-0617 compares with HEAD).

## Doors and data

One-way, PR 1's title naming them: what the core reads from an event record (DR-0646), AGENTS.md's mapper line gaining
it, and an undo op's `of_batch` (DR-0614 amended), which a Docket before reads and ignores. `medium`: DR-0647 to
DR-0652. The pending file is per clone, never committed. Data: the first catch-up writes the window's meetings.

## Steps

1. PR 1, "Make the ledger's meetings from calendar records (one-way door)", on #219: `ledger/calendar.py`, the
   writer's `calendar` change, the pending file, `docket ledger calendar`; proven by `test_ledger_calendar.py`.
2. PR 2, "Make meetings at each promotion": `Gate.promote` notes and runs the step, `docket staging promote` prints its
   line, the Staging page runs it too; proven by the example calendar's `test_with_docket.py`.

## Tests

- PR 1, invented people: each field; the header read, a body after its blank line not; both sides of the 25 October
  DST change; each left-out case named, the rest written; a move, a rename, a cancellation, a show-as-only version; an
  archived meeting's idle set; joins a minute off, by case and spaces, from two candidates, onto an archived meeting,
  from a key that continues one; both near misses; an `of` undo's removal and split kept, a `batch` or sync undo's
  done again, `of_batch` written; two clones in both orders; the catch-up never setting; perf, 300 records under 3 s.
- PR 2: the example week through promotion, its Friday sync cancelled in a second run; a moved event's two versions
  in one promotion; a stopped step replayed with its sets; a pending rid after a full revert; perf, 30 under 0.5 s.

## Docs

`docs/design.md` 3.4's calendar bullet; `examples/sources/calendar/SKILL.md`, "What it makes" naming what the core
reads; `packages/docket/README.md`'s `docket ledger calendar`; glossary: join; a CHANGELOG line with PR 2.

## Risks

- An event deleted, or moved out of the weeks read, keeps its meeting: accepted, as todo.py did; the sync cancels it
  by hand. A renamed series' old meetings don't join: named as near misses, joined by hand.
- Two clones harvesting minutes apart with the event changed between: the older version's set can sort last and win
  until the next version corrects it. Rare, accepted (DR-0647).
- A reset by undoing each meeting instead of `--revert` would read them as removed: resets revert.

## Plan review

Round 1: three majors (one version per rid, stopped sets replayed, which undos count) and four minors (the header and
keys, the race, near misses, the import's reason). Round 2: approved, two minors fixed (undos by how asked, pending).
