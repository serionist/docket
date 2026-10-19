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
| meetings today not yet ended and on the next working day, not cancelled or declined: a line a day, `meeting_line`, open points `* text` (`?` proposed), open items `A-0012 title (due 10-21)` | 6 a day, 4 points, 3 items | 7th, to 2 and 1 |
| overdue or due today, and snoozes woken today | 10 | never |
| replies owed | 10 | 5th |
| decisions and tasks due within 7 days, then `n more not due this week` | 8 | 4th |
| invites; waiting on others over 3 days | 5; 6 | 3rd; 2nd |
| streams not closed: `pilot [waiting]: where -> next (10-30)` | all | 1st |

- Rows are the `ledger` tool's `item_line` without the source, an item once; a last line names the `ledger` tool and
  the skills, as `standup.py`'s did. The whole hook says at most 45 lines, the status lines never cut; past 45,
  sections give rows back in the cut order, each keeping its count and the read that lists it.
- Claude gets every line; the owner sees the due sync and, from PR 2, a hand-edited file (DR-0163's rule). In full
  only when the session's project folder (`CLAUDE_PROJECT_DIR`) holds the vault's or sits in it; elsewhere one line of
  counts, since the plugin runs in every project. Every start, resume, clear and compact (DR-0161).
- Speed: at most 0.3 s more per start at the median, measured on the maintainer's Windows machine; the route in half
  a second over DR-0635's perf ledger; a busy index a second, then a fold (DR-0619); a write holding the vault nothing.
- The hook waits 3 s, 8 s at worst with the status call, under its 15. Slow: `day: no answer in 3 s; the ledger
  tool's snapshot reads it`. Down: `day: the service is down; docket ledger day reads the vault`; the hook never folds.

### STATUS.md (PR 2, DR-0673 to DR-0675)

`rendered` (DR-0552) takes a third kind: `{kind: status, stream: pilot, path: projects/pilot/STATUS.md}`. `stream` is
needed by it and refused by the others, one entry a stream, the path rules as DR-0552. Naming it in a committed
`vault.yaml` is the owner's yes before the first render (DR-0106, #134).

The frontmatter is todo.py's `write_stream` layout: `stream`, `status`, `where`, `next`, `deadline`, `updated` in that
order (DR-0625), an unset one left out, a day bare, text plain when YAML reads it back unchanged, else JSON-quoted;
then `# Rendered by Docket from the ledger; change the stream, not these lines. sha256:<16 hex>`, hashing the field
lines, before the closing `---`. The body after it is his, byte for byte. todo.py's `check` reads it unchanged.

| The file at the path | The render |
|---|---|
| absent, its folder there; or no frontmatter | the frontmatter, above the body if any |
| Docket's hash holds | the frontmatter rewritten when the stream moved |
| no hash or a failing one, the fields equal the ledger's | rewritten with the hash, nothing lost: todo.py's files after the import |
| no hash or a failing one, a field differs | left, reported with each field, file against ledger |
| a stream the ledger doesn't hold | left, reported |
| a folder, not UTF-8, a public or unreached remote | refused, as DR-0554 |

- **What wins:** the ledger. A hand edit never reaches it; the file keeps it until the two agree, Claude on the
  owner's word setting the stream or putting the ledger's value back. The next render takes the file back.
- **When:** at the end of every `store.write` that changed a stream, its lock let go, so the tool, the CLI, the pages
  and the import all render; `BatchResult` gains `rendered`; a failed render never fails the write. `docket render`
  renders every kind and exits 1 on a file left or refused. Never on a read, the hook or a check.
- **Outside the vault:** only at a path HEAD's `vault.yaml` names, in the consuming repo and outside the vault after
  links, never into a public repo; through `write_replacing`'s temp file and rename, the file read again just before
  and left when it changed; the body's bytes, line endings and BOM kept; never committed by Docket (DR-0106).
- **`docket vault check`** reads the files, writes nothing, probes no remote, and names a stream the ledger lacks and
  a hand-edited frontmatter with its fields; the day shows the owner a line per hand-edited file. `ledger/status.py`
  renders; `vault/rendered.py` shares its path checks, remote probe and write.

## Doors and data

- One-way, PR 2's title naming them and AGENTS.md's door list gaining both: the `status` kind and its `stream`
  (DR-0673); the frontmatter's fields, order, scalars and comment line, the body never written (DR-0674).
- `medium`: the render's rules and when (DR-0675). `cheap`: the day (DR-0670), where it shows (DR-0671), its budget
  (DR-0672). Not doors: `/api/day`, `docket ledger day`, the words, the cap; `BatchResult.rendered` adds a field.
- Data: none moves. A `status` entry needs this Docket on every machine. The owner's STATUS.md files are taken over
  once the import (#202) has made their streams with the same fields; any that differ are reported.

## Steps

1. PR 1, "Show the day at session start", after #201's PR 2: the due rule; `ledger/day.py`; `GET /api/day`; the
   hook; `docket ledger day`. Proved by `test_ledger_day.py` and `test_hooks_day.py`.
2. PR 2, "Render each stream's STATUS.md (one-way door)": the kind; `ledger/status.py`; the render at the end of
   `store.write`; `docket render`; the check; the day's line. Proved by `test_status_render.py`.

## Tests

- PR 1, invented people: each section, order and cap; meetings after now and on the next working day (Friday gives
  Monday), cancelled and declined left out; woken snoozes; the sync line from two machines' marks; an overflow cut in
  order. The hook on a fake service: the owner's lines, down, slow, a project elsewhere. Perf: the route.
- PR 2: `status` without `stream`, a stream twice; each render row; CRLF and a BOM kept; a change before the rename;
  a public remote; a todo.py file taken over; golden bytes; a write's render, a failed one too; the check; the day.

## Docs

`docs/design.md`'s Tasks, vault and Claude first sections; AGENTS.md's doors; the READMEs; CHANGELOG; DR-0670 to 0675.

## Risks

- About 2,000 tokens in every session in the owner's repo: accepted, the old hook's bar.
- The mirror writing STATUS.md too in the week in step: it leaves STATUS.md to Docket; his script, noted for him.
- An editor saving a STATUS.md opened before a render puts the old frontmatter back: the next render reports it.
- #201's reads call a stream's `where` a locator; todo.py's is where it stands (`where -> next`). The day cuts it at 70.

## Plan review

Not reviewed yet.
