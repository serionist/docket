---
title: Import a ledger with its history
issue: "#202"
status: approved
approved_by: claude (autonomy grant)
approved_at: 2026-10-19
---

# Import a ledger with its history

`docket ledger import <file>` takes the op lines an export wrote, counts them in a dry run, and writes them a month
per commit, so every ask, chase and close comes in on its own day. The export and the week's mirror are the owner's
scripts over todo.py's own `load()` and `save()`, so no importer for one person's YAML lands here. Two PRs here.

## Goal

- Done: items, meetings, points, streams and the archive come in as DR-0611's ops with their history, counted first;
  a rerun adds nothing twice; a revert empties the ledger until the switch; a whole-ledger read feeds the mirror.
- Out: the export and the mirror, the owner's (roadmap step 15); #200's catch-up, which joins old meetings; the pages.

## Design

### The import (DR-0653)

- The file is JSON lines of op lines (DR-0611) with `via: import`, `day` alone and the export's ids. The import sets
  the writer's `machine`, `batch` (the month's first op id) and `after`; a line carrying one, or another `via`, refuses.
- Under the vault lock it writes the months oldest first, each one write through `store.py` by a call of its own, with
  the live text rules (DR-0618, DR-0627) and one commit, `Ledger: import 2026-09, 1,204 ops on desk`; then the index.
- Held ops: it skips the file's ops the ledger holds line for line, so a rerun writes nothing and a stopped one
  finishes. The ledger's own objects (the step's meetings) stand beside it, renumbered by its last write where a file's
  add claims first (DR-0613). An op from elsewhere on a file's object, or a held line with another text, refuses it.
- Old meetings come in keyless (DR-0655): no `event` or `series`. After the import, #200's catch-up, `docket ledger
  calendar --since <the calendar's first day>`, joins each one a record matches through `continues`, on the same start
  to the minute and folded subject, the old id and number holding whatever the step made first.
- It refuses, naming place and field, never the value: a line that isn't an op, a text rule's refusal (a never-store
  keyword, a person on the people list in `who`), and any problem `vault check` would name in the fold of the result.

### The dry run (DR-0654)

`--dry-run` writes nothing and lists every refusal; `--json` gives it as data, for the export's comparison.

| It counts | How |
|---|---|
| items | by kind and state, archived apart; log entries per item, its `log` ops (in `--json`); the next A- number |
| meetings, points, streams | meetings archived; points by state, each chain by number (`M-0031#2 > M-0035#1`), copies whose `from` names a meeting apart; streams by status; the next M- number |
| ops not from the file | by `via`, kind and machine, and the objects its last write would renumber |
| sources it can't resolve | by scheme (`mail:`, `app:`), with their items: no rid, processed doc or old path answers them by `read`'s rules (DR-0131), asked of the index |
| the catch-up's joins | its dry run, read-only, over the ledger as the import would leave it: `M-0040 10-20 10:00 Pilot sync > calendar:<id>`, near misses apart (#200) |
| ops that change nothing | by kind; a right export gives none |

### Reverting (DR-0656)

- `docket ledger import --revert [--dry-run]` deletes every op file with `git rm` in one commit, saying how many of
  each `via` went; CLI only, until the old ledger retires; git keeps the lines. The catch-up's joins go too, with the
  step's meetings, and the catch-up makes them again after the rerun.
- A rerun is revert, push, every writing clone pulls, then import and catch-up. A clone that wrote meanwhile meets a
  loud modify/delete conflict, where an emptied file would union-merge its lines back (DR-0142), as `git revert` would;
  the live clone and the owner's checkout share a machine name, so file names.
- It resolves to the clone's writes since the base, `git show :2:<f> | grep -vxFf <(git show :1:<f>) > <f>` (`:3:` in
  a rebase), then `git add`, or `git rm` when empty. The dry run lists ops not from the file after every later merge
  (at cut-over, after a rollback and re-import), so they are seen; one on a removed object is re-made after the import.

### What the owner's export writes, from todo.py's own `load()` (DR-0657, DR-0658)

| todo.py | Ops |
|---|---|
| an item | `add`: its number; kind, title, who, stream, source, description, notes, outcome as they stand; `meeting` as that meeting's id; `opened`; `due` as its first due line had it. Its day the earlier of `opened` and its first entry's |
| materials | a `material` each, as they stand |
| a log entry | a `log` op, its text verbatim, its day and `by`; before it, on its day, the op its words name when it applies: `triage: <v>` (a chase is `triage: chase`), `triage cleared`, `triage: <a> -> <b>` on an open item, `snoozed to <day>`, `state: open -> snoozed` with its `until:` line, either side, `done`, `dropped: <why>`, `reopened (was <s>)`, `state: <a> -> <b>`, `due: <a> -> <b>` |
| `undo: back to <s>[, triage <t>]` | the tasks app's undo: an `undo` of each state op since the item last stood at `<s>` and `<t>`, then the `log` op |
| the end | settling ops, on the last entry's day, where state, until, triage or due still differ from todo.py's (a hand edit, a v1 note) |
| the archive | each archive file's items and meetings, each then an `archive` 14 days after its close or start, or on its last op's day if later |
| a meeting | `meeting`: its number; start and end with the zone's offset, through todo.py's `slot()`; `note` as `notes`; attendees split on commas; the rest as they stand; recordings and links as `material`s; no key |
| a point | `point` with text, item (its id) and proposed; a `point_set` of state, answer and `to` when it has them. `from`/`to` pair point to point: the k-th point rolled to a meeting with the k-th copy from it, in todo.py's order, by equal text when the counts differ; any left name the meeting |
| a stream | `stream` per todo.py stream, from its STATUS.md frontmatter where it has one, else `active` |

- `by`: `me` is `user`, `claude` and `sync` stay, none or another name is `claude`; an add takes its first entry's;
  meetings and points are `claude`. Free text stays words: "chased Ada" is a log line, not a chase. An imported op
  other than `log` shows no log line (DR-0611), so an item's log reads as todo.py's and the counts (#201) use the ops.
- Ids: a ULID, its time the op's day at 00:00 UTC (a meeting's: its start) plus its place among its object's ops in
  ms, so ops keep their order and a copy sorts after its source; its random bits sha256 of `A-0281/7`, `M-0040/3`,
  `stream:pilot/0`. todo.py's `today()`, which bounds a v1 note's dates in `load()`, is pinned to the export's day
  (`--today`, kept in the file's name): the same ledger gives the same file.

### The mirror and the checks (DR-0659)

- `docket ledger show --all --json` (PR 2) gives every object whole in #201's `show` model, archived too, with the op
  files' stamp (names, sizes, times) taken under the vault lock before it reads, and the lock's file.
- The owner's mirror renders todo.py's ledger file and `_archive/` from it with todo.py's dumps, `user` as `me`, under
  its own lock file and a uniquely named temp file (`save()`'s is fixed), since the old app's polling and the
  session-start dashboard may render at once; its header carries the read's stamp. STATUS.md is #208's or the mirror's.
- It renders when todo.py loads the file under that header and the stamp moved: Docket's commits run no hook
  (`core.hooksPath` points at a file), the service runs no owner code, the sync skill would miss the pages.
- Under the header todo.py's writers, `archive` and `stream set` too, refuse; reads work. `--rollback`, the service
  stopped: it reads, takes Docket's own vault lock (`filelock.FileLock` on the path the read gives, Docket's primitive)
  and, while the stamp still matches, writes the file without its header.
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
| a joined meeting | start, end, subject, organiser, where and response, each only where it is the value its calendar add wrote (#200); a meeting only the step made is Docket's |

## Doors and data

No one-way door: the file is DR-0611's op lines less the three keys the writer sets. `medium`: DR-0653 to DR-0656,
the dry run's JSON only gaining fields; `cheap`: DR-0657 to DR-0659. Data: none moves; an import is how one arrives.

## Steps

| Where | Step | What | After |
|---|---|---|---|
| Docket | PR 1, "Import a ledger with its history" | `ledger/imports.py`: the file, the plan, the dry run; `store.py`'s import and revert; `docket ledger import` | #200's PR 1 |
| owner | the export | the export script, with `--compare`: the dry run's JSON against todo.py's own counts | PR 1 |
| owner | trial runs in the live clone | dry run, compare, import, catch-up, build the pages on it; revert, push, every clone pulls; again | - |
| Docket | PR 2, "Show the whole ledger as JSON" | `docket ledger show --all --json` with its stamp, or in #201's reads if still open | #201's PR 2 |
| owner | the mirror | the mirror with `--check` and `--rollback`; todo.py's refusal and its render on load | PR 2 |
| owner | cut-over | the live clone merged into the checkout, every clone pulled; export; a dry run whose ops not from the file are the step's meetings alone; import; catch-up; `--check`; the mirror on | all |

## Tests

- PR 1, on an invented ledger a test export writes with these ids (every line form above, settling ops, a v1-style
  item, rolls paired and unpaired, an archive, streams): the dry run against hand counts, no fold problem, no idle op;
  a commit per month; a rerun writes nothing, a stopped one finishes, a changed line or a live op refuses; the step's
  meetings kept and renumbered, the dry run's joins the catch-up's; `--revert` and a rerun give the same ledger, and a
  clone's write after it conflicts; text refusals name place and field; only `log` lines show; #201's counts from the
  ops; only `store.py` writes ops; `perf`: 50,000 ops dry run under 3 s. PR 2: `show --all --json`'s golden, stamped.

## Docs

`docs/design.md`'s Importing what exists; the package README; CHANGELOG with PR 1; DR-0653 to DR-0659, proposed.

## Risks

- A future meeting's ops sort ahead of the clock (DR-0612, accepted) and after the step's: one the step numbered as
  high keeps that number. Rare; the dry run names it. A line form the export doesn't know: the settling ops keep the
  state right, and its report counts lines by what they became, so a lost chase shows.

## Plan review

Round 1: one major (a revert's emptied files, which a union merge brings back) and four minors, all fixed in place.
Round 2: approved; three minors (a rerun's order and the conflict's fix, the mirror's lock) fixed in place.
