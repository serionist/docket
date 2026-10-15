---
title: Give each new decision and convention a file of its own
issue: "#81"
status: approved
approved_by: claude (autonomy grant)
approved_at: 2026-10-15
---

# Give each new decision and convention a file of its own

## Goal

Two branches that each add a decision never touch the same file, so `git rebase` and GitHub's own merge of a PR both
apply them with no conflict and no hand work. Today every branch appends to the end of the decisions in
`packages/docket/index.yaml`, any two open branches conflict there, and on 15 Oct nearly every PR needed a hand rebase.

- In scope: where `docs add` writes a decision or a convention, how the docs tool reads and checks it, `docs move`
  for the branches open now, a merge test with real git, the regime's docs.
- Out of scope:
  - moving the 180 entries already in `index.yaml` files (DR-0361);
  - reading lists and terms: they conflict only when two branches add next to each other in one list, and terms are
    sorted, so two new ones collide only when they sort side by side.

### What the probes showed

Throwaway repos with a copy of `packages/docket/index.yaml` and git 2.47: two branches from one base each add an
invented decision, then `git rebase` of one onto the other. "Bare" is `git merge-tree --write-tree` in a bare clone,
the way GitHub merges a PR.

| Option | Result |
|---|---|
| as today | conflict: two inserts at one point, split into two hunks because git lines up the entries' shared `status`, `at`, `by` and `cost` lines |
| a blank line before every entry | the same conflict |
| entries sorted by id, a sentinel line each | not built: a branch's block is above every id on main, so both branches still insert at the end, the blank-line case |
| `merge=union` | the rebase applies and the file is broken: union keeps both sides of each refined hunk, so the entries interleave (`id`, `id`, `title`, `title`, the shared lines, `sources` twice); `docs check` fails on the duplicate key |
| `merge=union` with `merge.conflictStyle=diff3` or `zdiff3` | both entries whole; but when the replayed branch adds a `not_chosen` line to main's last entry while main appended an entry, the line lands under the new entry: valid YAML, `docs check` passes, the item has silently changed owner |
| `merge=union`, bare | honoured, interleaved file written: GitHub would most likely squash-merge a broken `index.yaml` for a PR behind main (not tried on GitHub itself) |
| a merge driver, `merge=docs`, merging entry by entry (prototype) | two appends, and an edit beside an append, rebase cleanly with every line under its own entry; an entry changed on both sides stops with markers. Bare, without the driver registered, as on GitHub: conflict |
| one file per new entry, `<folder>/index.d/<id>.yaml` | no conflict, local and bare, also beside a branch that still appends to `index.yaml` and one that confirms old entries |

Taken: one file per new entry. It is the only option that also holds for GitHub's merge; the merge driver costs about
as much code, works only after a local rebase and needs registering in every clone.

## Design

| Part | After |
|---|---|
| a new decision or convention | `<folder>/index.d/<id>.yaml` in the folder it binds: the root's in `index.d/`, `packages/docket`'s in `packages/docket/index.d/` |
| its format | the `index.yaml` format with one entry (`decisions:` then the entry), no header comment, the same canonical form |
| entries already in an `index.yaml` | stay there; `set`, `supersede` and `remove` edit them in place (DR-0361) |
| reading lists, terms | where they are: the folder's `index.yaml`, `docs/glossary.yaml` |
| the regime | `locations.entry_dir: index.d` and `entry_kinds: [decisions, conventions]` |

The tool:

- `Store` reads every `*.yaml` in a folder's `index.d/` (the regime's `exclude` still applies) as an entry file of that
  folder, so `list --path`, `show`, `search`, `find --in` and the citations check see it with no change of their own.
- `docs add decisions|conventions --in <folder>` writes a new `<folder>/index.d/<id>.yaml`; `add reading` and `add
  terms` write where they do today.
- `docs door` counts a folder's decisions and conventions in its `index.yaml` and its `index.d/` together, and names
  a folder that has entries but no `index.yaml`.
- `docs check` keeps every check it has, on both places, and adds three for a file in `index.d/`: it holds exactly one
  entry, of a kind in `entry_kinds`, and is named `<key>.yaml`. Any other file there is a problem too.
- `docs move <key>...` moves a branch's own decisions and conventions from an `index.yaml` into `index.d/`, unchanged.
  With `--from <rev>` it takes each entry from that revision instead, for an entry a rebase dropped when its
  `index.yaml` conflict was settled by taking main's side; it refuses a key that is already in the tree.

The branches open now (#77, #82, #67) move their entries at their next rebase:

```sh
git fetch origin && git rebase origin/main
# only if it stops on an index.yaml, at each stop:
git checkout --ours -- <that index.yaml> && git add <that index.yaml> && git rebase --continue
uv run docs move <your ids>                    # entries still in an index.yaml
uv run docs move <your ids> --from ORIG_HEAD   # or: entries the conflict took out
uv run docs check
```

A change the branch made to an older entry in a file settled that way is made again with `docs set` or `supersede`;
`docs check` names a broken supersede link.

How each merge comes out:

| Two branches | Git | `docs check` |
|---|---|---|
| add entries with different ids, in one folder or two | no conflict: two new files | passes |
| take the same id in one folder | add/add conflict on `index.d/<id>.yaml`, the rebase stops | |
| take the same id in two folders | no conflict | fails: a key is used once |
| change the same old entry in an `index.yaml` | conflict, as it should | |
| one adds an entry, the other confirms or supersedes an old one | no conflict | passes |
| a hand merge drops an entry | | fails where a plan or the code cites it, as today |

Not taken:

| Option | Why not |
|---|---|
| `merge=union` on the entry files | interleaves entries, or with diff3 moves a list item to another entry unseen; bare git honours it, so GitHub would likely land the broken file on main |
| a merge driver | GitHub's merge still conflicts; every clone must register it; it runs the working tree's code in the middle of a rebase |
| blank lines, separators, sorting by id | both branches still insert at one point |
| every existing entry moved into `index.d/` now | 180 new files, each entry's `git blame` starts again, and every open branch that edits an old entry gets a modify/delete conflict; a later change can move them when no branch is open |
| one central folder, `docs/decisions/` | a decision binds the folder it sits in; a central one needs a field for that, and `door` and `list --path` would read it |

## Doors and data

None of `AGENTS.md`'s one-way doors: they are about users and their vaults, and this moves the repo's own docs. No
user data.

It extends a decided entry and leaves it standing: DR-0005 rules that structured docs are YAML entries the docs tool
manages, and rules out one markdown file per decision. DR-0360 keeps that rule, cites it, and adds where a new entry
sits. DR-0005 stays as it is; CV-0003 keeps its `decision` and gains a `why` naming DR-0360 for the new location.

The harness (`REVIEW.md`): the change is in `tools/devkit`, `docs/` and `AGENTS.md`. Every check still runs on every
`index.yaml`, the same checks run on `index.d/` files, and three are added; nothing loosens.

The decisions, proposed, by Claude (autonomy grant), from this branch's block that starts at DR-0360. They are added to
the root's `index.yaml` with this plan, the way today's tool writes; step 4 moves them into `index.d/`.

| Id | Decision | Cost |
|---|---|---|
| DR-0360 | A new decision or convention is a YAML entry in a file of its own, `index.d/<id>.yaml` in the folder it binds; it keeps DR-0005 | medium |
| DR-0361 | Entries already in an `index.yaml` stay there; nothing moves in bulk | cheap |

## Steps

1. `entry_dir` and `entry_kinds` in the regime; `Store` reads and checks `index.d/` files. Tests: an entry there is
   found, listed by `--path` and shown with its folder; a file with two entries, a reading entry there, a file not
   named by its key, a file that isn't YAML, and one id in both an `index.yaml` and an `index.d/` file each fail
   `docs check`.
2. `docs add` writes decisions and conventions into `index.d/`; `door` counts both places. Tests: an add writes
   `<folder>/index.d/<id>.yaml` and leaves `index.yaml` as it was; `set`, `supersede` and `remove` work on an entry in
   either place; `door` counts both from the root down. The existing tests that read `index.yaml` after adding a
   decision read the new file. An entry under `plugin/index.d/` passes the gate's plugin validation (tried, not kept).
   `docs move`: an entry moves out of an `index.yaml` unchanged; with `--from`, a rebase settled by taking main's side
   gets its entry back from `ORIG_HEAD`; a reading entry, a term, a key already in `index.d/` and, with `--from`, a
   key already in the tree are refused.
3. The merge test, in the `repo` fixture with real git: two branches from one base each `docs add` a decision, `git
   rebase` of one onto the other exits 0, and `docs check` passes. Beside it: the same id in one folder stops the
   rebase; the same id in two folders rebases and `docs check` fails; one branch adds an entry while the other sets an
   old entry's status, and both apply.
4. This branch's two decisions moved with `docs move`. DR-0360 restated to extend DR-0005, CV-0003's `why`, the docs
   below.
5. `finish-work`: `gate full`, a draft PR, `review-code`.

## Tests

In `tools/devkit/tests/test_docs.py`, on the `repo` fixture's throwaway repo with this repo's regime; the merge test
makes its branches with git there. No test is deleted or weakened; the devkit coverage floor stays at 80 or rises.

## Docs

- `docs/docs-regime.md`: the Kinds table's "Lives in", and a short "How entries merge": the merge table above, and why
  entry files carry no merge attribute.
- `docs/docs-regime.yaml`: `entry_dir` and `entry_kinds`, with their comment.
- `AGENTS.md`: the Docs table's row; a row for the root's `index.d/` in the repo table; the one-way doors' last line
  says "its decision entry" for "its decision in `index.yaml`".
- `docs/design.md`: the line that names `index.yaml` as where the decisions are.
- `CHANGELOG.md`: none; users don't see the repo's docs.
- The PR: how an open branch moves its entries, the commands above.

## Risks

| Risk | Status |
|---|---|
| Someone opens an `index.yaml` by hand and misses the newer entries in `index.d/` | accepted: `AGENTS.md` sends readers to `docs door`, `list` and `show`, which read both |
| Open branches that appended to `index.yaml` conflict with each other once more | accepted: at its next rebase each moves its entries with `docs move`, or takes main's `index.yaml` and `docs move --from ORIG_HEAD` |
| Two branches add reading entries to one folder, or terms that sort side by side | accepted: the same conflict as today, and rarer than decisions |
| `plugin/index.d/` ships inside the plugin | resolved: `plugin/index.yaml` ships today, and step 2 runs the plugin validation with an entry there |
| A later change adds `merge=union` to the entry files to stop a conflict | resolved: `docs-regime.md` says what the probe found |

## Taken while building

| Decided | Not taken |
|---|---|
| dot files in `index.d/` are passed over: the writer's own temp files and a system's, such as `.DS_Store` | every file there reported |
| `door` prints `<folder>/index.d/` for a folder whose entries all have files of their own | leaving such a folder out of the door |
| `docs move --from` refuses a key still in the tree, so it never overwrites what the rebase kept | taking the revision's text over the tree's |
| CV-0003 gains a `why` naming where entry files are; its `decision` stays DR-0005 | a second decision field |

The rebase test also ran with the old layout, and fails there. `claude plugin validate --strict plugin` passes with an
entry in `plugin/index.d/` (tried, removed).

## Plan review

Round 1, 15 Oct 2026, the lead reviewed the plan without agents: approved with DR-0005 extended, not superseded. The
review also asked for one `docs` command that moves an open branch's entries; that is `docs move`.
