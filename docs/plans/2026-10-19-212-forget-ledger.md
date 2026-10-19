---
title: Make forget reach the ledger's ops (one-way doors)
issue: "#212"
status: draft
approved_by:
approved_at:
---

# Make forget reach the ledger's ops

A forget masks the ledger ops built on what it takes: each line keeps its id, keys and structure, its words become
`[forgotten]`, and an item or meeting built on a forgotten record stays a shell with its number. The list names the
masked ops, so every clone reads them masked at once and rewrites its op files at its next gate call, as run files go.

## Goal

- Done: no op text quoting a forgotten record, or built on one, is left in the tree, the index, a read or HEAD of any
  clone after its next gate call, and the printed history rewrite takes it out of the ops' blobs.
- Out: retention (phase 3, below); forgetting an item by its number; the pages, which show the marker.

## Design

### What a forget does to an op (DR-0693)

| Choice | Verdict |
|---|---|
| the op's text dropped; its object kept, a shell with its number when built on the family | chosen: numbers, links from points and chats, and the history's shape hold |
| the item forgotten with it | no: DR-0613 never frees a number, so a hidden item is a shell anyway, and what links it would name nothing |
| only the matching text blanked | no: op text has no anchored lines as a doc has; notes are set whole, so every later set repeats the quote |
| a new op that masks | no: the text stays in every tree and in HEAD, and a Docket from before folds an unknown kind as nothing |

- The mask is written in place, through `ledger/store.py` (DR-0618). Every key stays, and so do ids, numbers, days,
  times, kinds, verdicts, states, responses, streams, `by`, `via` and booleans. Every other string becomes
  `[forgotten]`, at any depth, and a list of them empty; a locator (`source`, `ref`, `event`, `series`) becomes
  `[forgotten:<32 hex>]`, staging's digest of it. Required fields stay filled and a key still groups its adds.

### Which ops (DR-0694)

| An op | Masked |
|---|---|
| with an `[r:` anchor in its text on the family, judged where it reads (DR-0580), or a `[d:` ref to a line the docs' rule drops | that op |
| a `material` whose ref reads on the family by `read`'s rules: a rid, a doc or a legacy note's old path the forget takes | that op |
| of an item whose `source`, in any of its ops, reads on the family | every op of the item: a shell |
| of a meeting whose `event`, in any of its adds, the forget takes: a calendar record | every op of it, its points' too: a shell |
| built on a masked op: a point's copies (`from`), an item made from a masked point or whose source names a shell meeting | the same, to a fixed point, as a doc line built on a dropped line |

- A meeting's recording is a material: the link goes, the meeting stays. A record named with no anchor is out of reach.
- Every clone's apply matches again, every op line once the list's digest moved and those a pull brought otherwise,
  so a clone's own op from before its pull is found; one no line names goes on an ops-only line, as keys do (DR-0595).

### The list and the readers (DR-0695)

- The list's line gains `ops`: the masked ops' ids, plain, since an id holds a time and random bits, never words.
- `ops.read_all` masks a listed id's line in memory before any fold, so the index, `ledger show`, the fold in memory
  (DR-0619) and the writer read one ledger, and `ops` joins the digest (DR-0593). That is how a marked line is known
  (DR-0612): both texts of a listed id read masked, so a line a union merge brought back folds as one op.
- Across clones, as run files: the forget masks every op file in its tree, other machines' too, in its one commit;
  each clone reads masked after its pull, and its next gate call writes the masks, drops a merge's twin and commits,
  waiting out the owner's rebase (DR-0597). A stream it masks renders its STATUS.md again.

### The check, the writer and the import (DR-0696)

| Where | After a forget |
|---|---|
| `docket vault check`, the writer's `pending()` | a committed line changed or gone is explained when its id is listed and the file holds its mask; a listed line not masked yet is a forget not finished, which the next gate call finishes |
| the writer | refuses an op whose source, ref or anchor names a rid forgotten whole or a listed old path, naming the field |
| the import (DR-0653) | compares and writes a listed op masked, so an import run again brings no text back |

### History, the dry run and retention (DR-0697)

- `scoped.txt` gains each line of a listed id that isn't its mask, from every blob on any ref of the op files holding
  one (DR-0598). HEAD holds masks only once the apply committed, so `--check-rewrite` passes as before.
- The dry run and the screen give numbers, never a word (`2 items and 1 meeting left as shells: A-0281, A-0290,
  M-0040`), each open shell to drop; an import's export file joins what forget can't reach.
- Retention, later, writes list lines as a forget does, so this rule masks the ops built on what expires, never
  removing one: numbers and history hang on it.

## Doors and data

| Door (AGENTS.md) | Change |
|---|---|
| the ledger's op files (DR-0610) | a committed line changes only to its mask, its id on the list: DR-0610's opening, closed (DR-0693) |
| the forgotten list (DR-0579) | `ops` in schema 1; a Docket from before fails closed on such a line, so every machine updates first (DR-0695) |

- Set for good, `cost: one-way`: the mask with its two forms (DR-0693), and `ops` (DR-0695). Not doors: DR-0694,
  DR-0696, DR-0697 and the index's digest. MCP tools: none. Existing vaults: nothing moves.

## Steps

PR 1, "Mask forgotten ledger ops on every clone (one-way doors)":

1. `ops.mask()`, `read_all`'s masking, `forgotten.py`'s `ops`. `test_ledger_mask.py`: a golden mask per kind, read as
   its kind; a key's adds one meeting; a material's remove paired; twins one op.
2. The apply's op files through `store.py`. `test_forget_apply.py`: a crash at each step; a merge's twin dropped.
3. `check.py`, `store.pending`, the import. `test_ledger_check.py`, `test_ledger_import.py`: each row of its table.

PR 2, "Find the ledger ops a forget masks":

4. `forget/ledger.py`: the rule and the plan's lines. `test_forget_ledger.py`: each row of Which ops; a version forget;
   a calendar meeting with a point rolled on and a point's item.
5. The apply's matching and ops-only line; the writer's refusal; the render. `test_forget_clones.py`: B's op from
   before its pull masked and listed at its next gate call, and on A after its pull.
6. The rewrite's rows. `test_forget_rewrite.py`, with `git filter-repo`: each original op line gone, HEAD unchanged.

## Tests

Invented vaults from `docket_testkit`: items, calendar meetings, points and streams quoting a Plaud-shaped recording
and a calendar record, and an import file. `perf`: a fold with 500 listed ids within DR-0616's budget.

## Docs

DR-0693 to DR-0697, proposed by Claude; DR-0579, DR-0593, DR-0610, DR-0612 and DR-0617 amended where they change;
the design's Tasks and Retention and forgetting; the docstrings of `ops.py` and `forgotten.py`; the package README's
Forgetting; the glossary's shell; AGENTS.md's door lines; the CHANGELOG with each PR.

## Risks

| Risk | Status |
|---|---|
| a clone's own op from before its pull shows there until its next gate call | accepted: the dry run names other clones |
| notes set whole lose every word when one set quotes | accepted: the screen names the item, to write again |
| a meeting from a forgotten calendar record loses the owner's points and notes too | open, for the maintainer: proposed as a shell (DR-0694) |
| an open shell stays in the queue | open, for the maintainer: proposed, named on the screen to drop, not hidden by the fold |
| a Docket from before stops on an `ops` line | meant: it can't mask, so it fails closed until updated |
| an ops-only line rebuilds every clone's index once | accepted: vectors carry over |

## Plan review

Not run yet.
