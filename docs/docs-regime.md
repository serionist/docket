# The docs regime

Decisions, conventions, terms and reading lists are YAML entries that `uv run docs` reads and writes; prose stays
markdown. A session reads the one entry it needs instead of a whole page, a fact lives in one place under an id that
never changes, and the gate checks every citation. The schema is `docs-regime.yaml`; change it and this page together.

## Kinds

| Kind | Lives in | Fields worth knowing |
|---|---|---|
| decisions, `DR-nnnn` | a file of its own, `index.d/<id>.yaml` in the folder they bind; older ones in that folder's `index.yaml` | `title` (one sentence), `description` (what and why), `binds`, `not_chosen`, `cost` (`one-way`, `medium`, `cheap`), `sources`, `status` |
| conventions, `CV-nnnn` | same | `rule`, `area`, and `enforced_by` (the check) or `review_only: true` |
| reading entries | the `index.yaml` of the folder they bind | a repo-relative `path`, its `role`, a short `note` |
| terms | `docs/glossary.yaml` | `definition`, `see`, `close_terms`, `not` (words never to use) |

- A decision an agent records is `proposed`; the maintainer confirms it with `docs set DR-nnnn --set status=decided`.
- A changed decision gets a new entry: `docs supersede <old> --by <new>` links the two both ways.
- `removed` and `removed_why` soft-delete any entry. It stays in its file, so its id is never freed.
- `list`, `search` and the door leave out superseded and removed entries; `--all` shows them.
- A file in `index.d/` holds exactly one decision or convention and is named by its id; `docs check` fails any other
  file there.

## Changing entries

Only through the writer verbs, never by hand:

| Verb | Does |
|---|---|
| `docs add <kind> --in <folder> --set f=v --add f=v` | a new entry; decisions and conventions get the next id and a file of their own; terms need no `--in` |
| `docs set <key> --set f=v --add f=v --unset f` | changes one where it is; `--in <folder>` when a reading path is in several folders (`.` is the root) |
| `docs supersede <old> --by <new>` | marks a decision superseded and prints the citations to fix |
| `docs remove <key> --why "..."` | soft-deletes |
| `docs move <key>... [--from <rev>]` | moves a branch's own decisions or conventions out of an `index.yaml` into files of their own; `--from` takes them as they stood at a revision |

Parallel branches would each take the same next id. So a plan takes a block of ten for its decisions, the next free
one above main and every open branch, and adds each with `--set id=DR-nnnn`.

A value is text; `@file` reads it from a UTF-8 file, `-` from stdin, and `@@` writes a literal `@`. Each verb checks
the edit in memory, refuses one that adds a problem or a file that changed on disk, checks every file before writing
any, and writes through a temp file and a rename.

## How entries merge

Each new decision or convention is a new file, so two branches that add entries never touch the same lines: a rebase
and GitHub's own merge of a PR both apply them.

| Two branches | What happens |
|---|---|
| add entries with different ids | two new files, no conflict |
| take the same id in one folder | an add/add conflict on its file |
| take the same id in two folders | no conflict; `docs check` fails, since a key is used once |
| change the same older entry in an `index.yaml` | a conflict, settled by hand |
| one adds an entry, the other changes an older one | no conflict |

- Entry files carry no merge attribute. `merge=union` interleaves two appended entries, GitHub's merge honours it
  too, and with diff3 it can move a list item to another entry while `docs check` still passes.
- A branch that appended to an `index.yaml` before this layout moves its entries at its next rebase: `docs move
  <ids>`. If the rebase stopped on that `index.yaml` and was settled by taking main's side (`git checkout --ours`),
  `docs move <ids> --from ORIG_HEAD` brings them back from the branch as it was.

## The canonical form

Every entry file is written one way. `docs check` reports a file that isn't; `docs format` rewrites them all and
refuses a file whose values would change or whose comments would be lost.

- Fields in the kind's order; reading entries one `{ ... }` line each.
- A file of its own is the `index.yaml` form with one entry: `decisions:` or `conventions:`, the entry, no header.
- A value plain when YAML 1.1 and 1.2 both read it back as the same string, quoted otherwise (so dates are quoted).
- Text of several lines as a `|-` block; a short list as `[a, b]`, a long one an item a line.
- The header comments, and comments right above a top-level key, are kept. A comment anywhere else fails the check.

## Citations

The gate reads every text file for `DR-nnnn` and `CV-nnnn`. An id that doesn't exist fails. Citing a superseded or
removed one fails too, except in records (`docs/plans/`, `docs/reviews/`, `CHANGELOG.md`) and the entry files.
