---
title: Import a ledger with its history
issue: "#202"
status: draft
approved_by:
approved_at:
---

# Import a ledger with its history

`docket ledger import <file>` takes the op lines an export wrote, counts them in a dry run, and writes them a month
per commit, so every ask, chase and close comes in on its own day. The export and the week's mirror are the owner's
scripts over todo.py's own `load()` and `save()`, so no importer for one person's YAML lands here. Two PRs here.

## Goal

- Done: items, meetings, points, streams and the archive come in as DR-0611's ops with their history, counted first;
  a rerun adds nothing twice; a revert empties the ledger until the switch; a whole-ledger read feeds the mirror.
- Out: the export and the mirror (the owner's, roadmap step 15), described as far as Docket relies on them; the
  calendar catch-up that joins old meetings after the import (#200); the pages (#203 on).

## Design

### The import (DR-0653)

- The file is JSON lines, each an op line (DR-0611) with `via: import`, `day` alone and the export's ids. `machine`,
  `batch` and `after` are the writer's, so the import sets them: its machine, the month's first op id, the object's op
  before. A line carrying one of them, or another `via`, refuses the file.
- Under the vault lock throughout, it writes the months oldest first, each one write through `store.py` by a call of
  its own: the live text rules (DR-0618, DR-0627), the month's file through a temp file and a rename, one commit,
  `Ledger: import 2026-09, 1,204 ops on desk`. Then the index's ledger tables catch up.
- Held ops: it skips each op of the file the ledger holds line for line, so a rerun writes nothing and a stopped import
  finishes. The ledger's own objects, the calendar step's meetings, stand beside it; its last write renumbers any whose
  number an add of the file claims first (DR-0613). An op from elsewhere on one of the file's objects, or a held line
  with another text, refuses the import, naming how many: revert first.
- It refuses, naming place and field, never the value: a line that isn't an op, a text rule's refusal (a never-store
  keyword, a person on the people list in `who`), and any problem `vault check` would name in the fold of the result.

### The dry run (DR-0654)

`--dry-run` writes nothing and lists every refusal; `--json` gives it as data, for the export's comparison.

| It counts | How |
|---|---|
| items | by kind and state, archived apart; log entries per item, its `log` ops (in `--json`); the next A- number |
| meetings, points, streams | meetings archived; the ledger's own kept, renumbered ones named; points by state; streams by status; the next M- number |
| point chains | each by number, `M-0031#2 > M-0035#1`; copies whose `from` names a meeting, apart |
| sources it can't resolve | by scheme (`mail:`, `app:`), with their items: no rid, processed doc or old path answers them by `read`'s rules (DR-0131), asked of the index |
| the catch-up's joins | its dry run, read-only, over the ledger as the import would leave it: `M-0040 10-20 10:00 Pilot sync > calendar:<id>`, near misses apart (#200) |
| ops that change nothing | by kind; a right export gives none |

### Old meetings come in keyless (DR-0655)

The import, as the export, gives no meeting an `event` or `series`. After it, #200's catch-up, `docket ledger calendar
--since <the calendar's first day>`, joins each old meeting a record matches through `continues`, on the same start to
the minute and folded subject (#200's joins), the old id and number holding whatever the step made first. One
builder of keys: #200's function.

### Reverting (DR-0656)

`docket ledger import --revert [--dry-run]` takes every op line out of `ledger/ops/`, the import's and any since, in
one commit, saying how many of each `via`; CLI only, until the old ledger retires, git keeping the lines. A catch-up's
joins need no undo first: they go with the rest, the step's meetings too, and the catch-up makes both again after the
rerun. Not `git revert`: the union merge (DR-0142) silently keeps a removed line whose hunk meets a later append.

### What an export writes (DR-0657, DR-0658)

The owner's export script loads the old ledger and each archive file with todo.py's own `load()`.

| todo.py | Ops |
|---|---|
| an item | `add`: its number; kind, title, who, stream, source, description, notes, outcome as they stand; `meeting` as that meeting's id; `opened`; `due` as its first due line had it. Its day the earlier of `opened` and its first entry's |
| materials | a `material` each, as they stand |
| a log entry | a `log` op, its text verbatim, its day and `by`; before it, on its day, the op its words name when it applies: `triage: <v>` (a chase is `triage: chase`), `triage cleared`, `snoozed to <day>`, `done`, `dropped: <why>`, `reopened (was <s>)`, `state: <a> -> <b>`, `due: <a> -> <b>` |
| the end | settling ops, on the last entry's day, where state, until, triage or due still differ from todo.py's (a hand edit, a v1 note) |
| the archive | its items and meetings, each then an `archive` 14 days after its close or start, or on its last op's day if later |
| a meeting | `meeting`: its number; start and end with the zone's offset, through todo.py's `slot()`; `note` as `notes`; attendees split on commas; the rest as they stand; recordings and links as `material`s; no key |
| a point | `point` with text, item (its id) and proposed; a `point_set` of state, answer and `to` when it has them. `from`/`to` pair point to point: the k-th point rolled to a meeting with the k-th copy from it, in todo.py's order, by equal text when the counts differ; any left name the meeting |
| a stream | `stream` per todo.py stream, from its STATUS.md frontmatter where it has one, else `active` |

- `by`: `me` is `user`, `claude` and `sync` stay, none or another name is `claude`; an add takes its first entry's;
  meetings and points are `claude`. Free text stays words: a v1 note's "chased Ada" is a log line, not a chase.
- An imported op other than `log` shows no log line (DR-0611): an item's log reads as todo.py's, and the history
  counts (#201) reach back to the old ledger's start through the ops.
- Ids: a ULID whose time is the op's day at 00:00 UTC (a meeting's: its start) plus its place among its object's ops
  in milliseconds, so ops keep their order and a copy sorts after its source, and whose random bits are the first 80
  of sha256 of `A-0281/7`, `M-0040/3` or `stream:pilot/0`. The same ledger gives the same file.

### The mirror and the checks (DR-0659)

- From `docket ledger show --all --json` (PR 2: every object whole in #201's `show` model, archived too), the owner's
  mirror renders todo.py's ledger file and `_archive/` with todo.py's own `save()` and dumps, `user` as `me`, under a
  header line of its own stamped with the op files' names, sizes and times; STATUS.md is #208's, else the mirror's.
- It renders when todo.py loads the file under that header and the stamp moved, and at rollback: Docket's commits run
  no hook (`core.hooksPath` points at a file), the service runs no owner code, the sync skill would miss the pages.
- Under the header todo.py's writer verbs, `archive` and `stream set` too, refuse; its reads and the old app's views
  work. Rollback is the mirror's `--rollback`: one more render, and the file written without the header.
- The mirror's `--check`, at cut-over on todo.py's own files and each day on the mirror: `todo.py check` says ok, and
  `show --all --json` equals `todo.py snapshot --all` and the archive's items and meetings, by number and field, but:

| Field | Why it may differ |
|---|---|
| a log entry's `by` | none or another name in todo.py reads `claude` |
| a log line Docket wrote | its words, from the cut-over on |
| `closed` | where todo.py's isn't its close line's day |
| attendees | split on commas, joined with `, ` |
| text | a redaction rule's marker; control characters and trailing spaces cleaned |
| next numbers | Docket's are one past the highest held |
| `event`, `series` | Docket's alone |
| meetings | a joined open one's fields are the calendar's (#200); one only the step made is Docket's |

## Doors and data

No one-way door: the file is DR-0611's op lines less the three keys the writer sets. `medium`: DR-0653 to DR-0656,
the dry run's JSON only gaining fields; `cheap`: DR-0657 to DR-0659. Data: none moves; an import is how one arrives.

## Steps

| Where | Step | What | After |
|---|---|---|---|
| Docket | PR 1, "Import a ledger with its history" | `ledger/imports.py`: the file, the plan, the dry run; `store.py`'s import and revert; `docket ledger import` | #200's PR 1 |
| owner | the export | the export script, with `--compare`: the dry run's JSON against todo.py's own counts | PR 1 |
| owner | trial runs in the live clone | dry run, compare, import, catch-up, build the pages on it, revert, again | - |
| Docket | PR 2, "Show the whole ledger as JSON" | `docket ledger show --all --json`, or in #201's reads if they're still open | #201's PR 2 |
| owner | the mirror | the mirror with `--check` and `--rollback`; todo.py's refusal and its render on load | PR 2 |

## Tests

- PR 1, on an invented ledger a test export writes with these ids (every recognised line, settling ops, a v1-style
  item, rolls paired and unpaired, an archive, streams): the dry run against hand counts, no fold problem, no idle op;
  a commit per month; a rerun writes nothing, a stopped one finishes, a changed line or a live op refuses; the step's
  meetings kept and renumbered, the dry run's joins the catch-up's; `--revert` and a rerun give the same ledger; text
  refusals name place and field; only `log` lines show; #201's counts from imported ops; only `store.py` writes ops;
  `perf`: 50,000 ops dry run under 3 s. PR 2: a golden of `show --all --json`, archived objects in.

## Docs

`docs/design.md`'s Importing what exists; the package README; CHANGELOG with PR 1; DR-0653 to DR-0659, proposed.

## Risks

- A future meeting's ops sort up to two weeks ahead; live ids follow them until the clock passes: DR-0612, accepted.
  So they sort after the step's: one the step numbered as high keeps that number. Rare; the dry run names it.
- A line form the export doesn't know: the settling ops keep the state right, and its report counts lines by what they
  became, so a lost chase shows. `--revert` after the switch would take live writes: CLI only, git keeps them.

## Plan review

Not reviewed yet.
