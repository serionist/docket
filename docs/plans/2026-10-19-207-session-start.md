---
title: Show the day at session start, and render each stream's STATUS.md
issue: "#207, #208"
status: draft
approved_by:
approved_at:
---

# Show the day at session start, and render each stream's STATUS.md

`docket hook session-start` gains the owner's day, todo.py's dashboard read from the ledger through the service, in
at most 45 lines; he sees only a due sync and a hand-edited status file. Each stream `vault.yaml`'s `rendered` names
gets its STATUS.md frontmatter written from the ledger after every write to it, the body his. Two PRs, the day first.

## Goal

- Done: the session start says what `standup.py` says, so it can go; STATUS.md shows the ledger's six stream fields.
- Out: the Today page (#206), the skills (#209, #210), the mirror and `standup.py`'s removal (roadmap step 15).

## Design

### The day (PR 1, DR-0670 to DR-0672)

`ledger/day.py` builds a `Day` from one `snapshot` read (#201's views, today to the next working day) and the Sync
page's due rule (DR-0304), moved beside the cursors. `GET /api/day` gives its lines; the hook prints them after the
status lines. The Today page renders the same `Day`: whichever lands first adds it.

| Section, in todo.py's order | Rows | Cut |
|---|---|---|
| `day Mon 10-19; sync mail 3h, slack 2d, plaud never; due: slack, plaud (/sync)` | 1 | never |
| `queue: 12 untriaged; discuss 2, claude 3, me 1, chase 1`, then discuss and claude rows; me and chase as refs | 6 a verdict | 6th, to its count |
| meetings today not yet ended and on the next working day, not cancelled or declined: a line a day, `meeting_line`, open points `* text` (`?` proposed), open items `A-0012 title (due 10-21)` | 6 a day, 4 points, 3 items | 7th, points and items to counts on the meeting's line; 8th, the next working day to a count |
| overdue or due today, and snoozes whose day has come (`until` on or before today) | 10 | never |
| replies owed | 10 | 5th |
| decisions and tasks due within 7 days, then `n more not due this week` | 8 | 4th |
| invites; waiting on others over 3 days | 5; 6 | 3rd; 2nd |
| streams not closed: `pilot [waiting]: where -> next (10-30)` | all | 1st |

- Rows: the `ledger` tool's `item_line` without the source. An item is listed once among overdue, replies, decisions
  and tasks, invites and waiting, as todo.py's `shown` set; queue rows and a meeting's items may repeat it. A last
  line names the `ledger` tool and the skills. Past 45 lines with the status lines, sections give rows back in the cut
  order, each keeping its count and the read that lists it; the rows never cut pass 45 only on an extreme day.
- Claude gets every line, the owner the due sync and from PR 2 a hand-edited file (DR-0163), every start, resume, clear
  and compact (DR-0161). In full when the session's project folder (`CLAUDE_PROJECT_DIR`) sits in the vault's repo, the
  first folder above the vault holding `.git` (a stat); else a line of counts, amending DR-0163's counts and paths.
- Speed: at most 0.3 s more per start at the median on the maintainer's Windows machine; the route half a second over
  DR-0635's perf ledger; a busy index a second, then a fold (DR-0619). The hook waits 3 s, 8 s at worst with the
  status call, under its 15; slow or down, one line names the read that works, and the hook never folds.

### STATUS.md (PR 2, DR-0673 to DR-0675)

`rendered` (DR-0552) takes a third kind: `{kind: status, stream: pilot, path: projects/pilot/STATUS.md}`. `stream` is
needed by it and refused by the others, one entry a stream, the path rules as DR-0552. Naming it in a committed
`vault.yaml` is the owner's yes before the first render (DR-0106, #134).

The frontmatter is todo.py's `write_stream` layout: `stream`, `status`, `where`, `next`, `deadline`, `updated` in that
order (DR-0625), an unset one left out, a day bare, text plain when YAML reads it back unchanged, else JSON-quoted;
then `# Rendered by Docket from the ledger; change the stream, not these lines. sha256:<16 hex>`, hashing every other
line between the two `---` lines. The body after it is his, byte for byte. todo.py's `check` reads it unchanged, for
a stream with `where` and `next` set.

| The file at the path | The render |
|---|---|
| absent, its folder there; or no frontmatter | the frontmatter, above the body if any |
| Docket's hash holds | rewritten when the stream moved |
| no hash or a failing one, nothing but the field lines, and the five the owner sets (`updated` aside: the ledger derives it) equal to the ledger's as the writer stores them (`one_line` applied) | rewritten with the hash, nothing lost: todo.py's files after the import |
| no hash or a failing one, a field differs or another key or line stands | left, reported with each field, file against ledger |
| a frontmatter that doesn't close or doesn't parse; a stream the ledger doesn't hold | left, reported |
| a folder, not UTF-8, a public or unreached remote | refused, as DR-0554 |

- **What wins:** the ledger. A hand edit never reaches it; the file keeps it until the two agree, Claude on the
  owner's word setting the stream or putting the ledger's value back. The next render takes the file back.
- **When:** at the end of every `store.write` that changed a stream, its lock let go, so every writer renders;
  `BatchResult` gains `rendered`; a failed render never fails the write. `docket render` renders every kind and exits
  1 on a file left or refused. Never on a read, the hook or a check.
- **Where:** only at a path HEAD's `vault.yaml` names, in the consuming repo, outside the vault after links, not in a
  public repo; temp file and rename, the body's bytes, line endings and BOM kept; never committed (DR-0106). Changed
  before the rename, it is left; changed to Docket's valid hash, it renders again from a fresh read, no new lock.
- **`docket vault check`** writes nothing, probes no remote, and names a stream the ledger lacks, a hand-edited
  frontmatter with its fields, and a valid hash whose fields lag the ledger: `stale; docket render`. The day shows
  the owner a line per hand-edited file. `ledger/status.py` renders; `vault/rendered.py` shares checks, probe, write.

## Doors and data

- One-way, in PR 2's title and AGENTS.md's door list: the `status` kind and its `stream` (DR-0673); the frontmatter's
  fields, order, scalars, comment line and what its hash covers, the body never written (DR-0674).
- `medium`: the render (DR-0675). `cheap`: the day, where it shows, its budget (DR-0670 to DR-0672). Not doors:
  `/api/day`, `docket ledger day`, the words, the cap; `BatchResult.rendered` adds a field.
- Data: none moves. A `status` entry needs this Docket on every machine; the take-over waits for the import (#202).

## Steps

1. PR 1, "Show the day at session start", after #201's PR 2: the due rule; `ledger/day.py`; `GET /api/day`; the
   hook; `docket ledger day`. Proved by `test_ledger_day.py` and `test_hooks_day.py`.
2. PR 2, "Render each stream's STATUS.md (one-way door)": the kind; `ledger/status.py`; the render at the end of
   `store.write`; `docket render`; the check; the day's line. Proved by `test_status_render.py`.

## Tests

- PR 1, invented people: each section, order, cap and cut; an overdue item also queued and on a meeting; meetings after
  now and the next working day's (Friday gives Monday), cancelled and declined out; due snoozes; two machines' sync
  marks; the hook on a fake service down, slow, in the vault's repo, above it, elsewhere; the route's perf.
- PR 2: each render row, a key added, `updated` alone differing; `status` without `stream`, a stream twice; CRLF and
  a BOM kept; a change before the rename, another render's too; a public remote; golden bytes; a write's render and a
  failed one; the check, a stale file; the day.

## Docs

`docs/design.md`'s Tasks, vault and Claude first sections and its status-hook row, and `hooks.py`'s docstring (DR-0163
amended); AGENTS.md's doors; the READMEs; CHANGELOG; DR-0670 to 0675.

## Risks

- About 2,000 tokens in every session in the owner's repo: accepted, the old hook's bar.
- The week in step, noted for him: his mirror leaves STATUS.md to Docket; todo.py's `check` calls a stream without
  `where` or `next` missing, which the ledger allows.
- An editor saving a STATUS.md opened before a render puts back the old frontmatter, its hash valid: the next render
  rewrites it, and until then `docket vault check` says `stale; docket render`.
- #201's reads call a stream's `where` a locator; todo.py's is where it stands (`where -> next`). The day cuts it at 70.

## Plan review

Round 1, at `1d6a205`: changes needed, P1 a major. All eleven fixed in this revision and in DR-0670 to 0675.

- P1, major: an overdue item in the queue or on a meeting never showed as overdue; now todo.py's `shown` rule.
- P2, P10: DR-0671 amends DR-0163; the vault's repo, found by a stat for `.git`; `hooks.py` and design.md in Docs.
- P3 to P7: the take-over's five fields, as stored; the hash over every frontmatter line but its own; a broken one
  left; a render racing another renders once more; the check names a stale file, the editor risk corrected.
- P8, P9, P11: due snoozes' wording; the last cuts named; todo.py's `check` claim qualified, with a Risks row.
