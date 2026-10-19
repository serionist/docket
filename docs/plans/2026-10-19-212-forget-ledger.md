---
title: Make forget reach the ledger's ops (one-way doors)
issue: "#212"
status: draft
approved_by:
approved_at:
---

# Make forget reach the ledger's ops

A forget masks the ledger ops on what it takes: each line keeps its id, keys and structure, its words become
`[forgotten]`, and an item or meeting whose making op is masked stays a shell with its number. The list names the
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

- The mask is written in place, through `ledger/store.py` (DR-0618). The text fields (`title`, `who`, `description`,
  `notes`, `outcome`, `subject`, `organiser`, `where`, `text`, `answer`, `next`, `note`, `label`) and any string under
  a name this Docket doesn't know become `[forgotten]`; `attendees` empties; the locators `source`, `ref`, `event` and
  `series` become `[forgotten:<32 hex>]`, staging's digest. All else stays, `machine` and a stream's `status` too.
- It is idempotent: a value already `[forgotten]` or `[forgotten:<32 hex>]` stays, so `mask(mask(x)) == mask(x)`.
  Required fields stay filled, a key still groups its adds (DR-0621) and a material's remove still finds its add.

### Which ops (DR-0694)

| An op | Masked |
|---|---|
| with an `[r:` anchor on the family, judged where it reads (DR-0580), or a `[d:` ref to a line the docs' rule drops | that op |
| a `material` whose ref reads on the family by `read`'s rules: a rid, a doc or a legacy note's old path it takes | that op |
| an item's add when its `source`, in the add or a `set`, reads on the family; a meeting's adds when the forget takes its `event` | that op, so its object is a shell |
| of a shell: an item or meeting whose making op, any add of a meeting, is listed, one an anchor matched too | every op of it, its points' too |
| a point's copies along `from`, and an item `point_item` made, from a point masked for its own words: raised on a shell, or matched by an anchor | the same, to a fixed point |

- Shells come from the list, so a clone knows one after its locators are masked. A copy rolled into a shell goes as
  that meeting's op; the chain beyond it, and an item made from it, keep their words. A meeting's recording is a
  material: the link goes, the meeting stays. A record named with no anchor is out of reach.
- Every clone's apply matches again, every op line once the list's digest moved and those a pull brought otherwise,
  so a clone's own op from before its pull is found. It masks a shell's ops that still hold text, and those no line
  names go on an ops-only line, as keys do (DR-0595).

### The list and the readers (DR-0695)

- The list's line gains `ops`: the masked ops' ids, plain, since an id holds a time and random bits, never words.
- `ops.parse`, given the listed ids, masks each one's line before anything reads it, so `read_all`, the index's update,
  the fold in memory (DR-0619), the writer and the import read one ledger, and `ops` joins the digest (DR-0593). That
  is how a marked line is known (DR-0612): both texts of a listed id read masked, so a merge's twin folds as one op.
- Across clones, as run files: the forget masks every op file in its tree, other machines' too, in its one commit;
  each clone reads masked after its pull, and its next gate call writes the masks, drops a merge's twin and commits,
  waiting out the owner's rebase (DR-0597). A stream it masks renders its STATUS.md again.

### The check, the writer and the import (DR-0696)

| Where | After a forget |
|---|---|
| `docket vault check`, the writer's `pending()` | a committed line changed or gone is explained when its id is listed and the file holds its mask; a listed line not masked yet is a forget not finished, which the next gate call finishes |
| the writer | refuses an op naming a rid forgotten whole or a listed old path in a source, ref or anchor, naming the field; refuses one holding text on a shell, naming its number; one with no text passes, a drop's required note written as `[forgotten]` |
| the import (DR-0653) | compares and writes a listed op masked, so a rerun brings no text back; refuses an unlisted line holding text on a shell, naming the line |

### History, the dry run and retention (DR-0697)

- `scoped.txt` gains each line of a listed id that isn't its mask, from every blob under `ledger/ops/` on any ref,
  deleted files too, as `doc_blobs` reads `processed/`, pre-filtered by the ids' bytes (DR-0598). HEAD holds only
  masks once the apply committed, so `--check-rewrite` passes; an ops-only line arriving later is in the next forget's.
- The dry run and the screen give numbers, never a word: each shell (`A-0281, M-0038 left as shells`), each open one
  to drop, and each object that lost a material or link and kept its text (`M-0040: recording link removed; notes
  and points kept`). An import's export file joins what forget can't reach.
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

1. `ops.mask()` and `ops.parse`'s masking; `forgotten.py`'s `ops`. `test_ledger_mask.py`: a golden mask per kind,
   read as its kind and masked again unchanged; a key's adds one meeting; a remove paired; twins one op.
2. The apply's op files through `store.py`. `test_forget_apply.py`: a crash at each step; a merge's twin dropped.
3. `check.py`, `store.pending`, the import's comparison. `test_ledger_check.py`, `test_ledger_import.py`.

PR 2, "Find the ledger ops a forget masks":

4. `forget/ledger.py`: the rule, shells by the list, the `from` chain; the plan's lines. `test_forget_ledger.py`: each
   row of Which ops; a version forget; a weekly 1:1's point rolled across ten meetings, one meeting's calendar record
   forgotten, and only that meeting's copy goes; a point raised on a shell, its copies and its item masked.
5. The apply's matching and ops-only line; the refusals; the render. `test_forget_clones.py`: B's ops from before its
   pull, a log line on a shell among them, masked and listed at its next gate call, and on A after its pull.
6. The rewrite's rows. `test_forget_rewrite.py`, with `git filter-repo`: original op lines gone, a deleted file's too.

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
| an ops-only line makes every clone rebuild its index, re-read its run files and judge its docs again | accepted: rare, since the writer refuses new text on the family and on a shell |

## Plan review

### Review, round 1, at a4fc1b3: two majors and seven minors, each settled in place by the maintainer's lead

| Finding | Severity | Status |
|---|---|---|
| P1: once its locators are masked, a clone can't tell a shell | major | fixed: an item or meeting whose making op is listed is a shell, one an anchor matched too; every clone's re-match masks its unlisted ops (DR-0694) |
| P2: the `from` chain masks live points | major | fixed: followed only from a point masked for its own words; a copy rolled into a shell goes as its op, and the chain beyond keeps its words (DR-0694) |
| P3: the mask must be idempotent | minor | fixed: bound in DR-0693, with a golden test |
| P4: masking in `read_all` misses other readers | minor | fixed: in `ops.parse`, given the listed ids (DR-0695) |
| P5: the bind didn't name what is masked | minor | fixed: the text fields, `attendees`, the four locators and unknown names' strings; the rest stays (DR-0693) |
| P6: new text could land on a shell | minor | fixed: the writer refuses it, naming the number, and the import, naming the line (DR-0696) |
| P7: the rewrite missed deleted op files and later ops-only lines | minor | fixed: every blob under `ledger/ops/` on any ref, pre-filtered; a later line goes in the next forget's files (DR-0697) |
| P8: an object that lost only a link went unnamed | minor | fixed: the dry run and the screen name it by number |
| P9: an ops-only line costs more than an index rebuild | minor | accepted, in Risks |
