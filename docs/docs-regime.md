# The docs regime

Decisions, conventions, terms and reading lists are YAML entries that `uv run docs` reads and writes; prose stays
markdown. A session reads the one entry it needs instead of a whole page, a fact lives in one place under an id that
never changes, and the gate checks every citation. The schema is `docs-regime.yaml`; change it and this page together.

## Kinds

| Kind | Lives in | Fields worth knowing |
|---|---|---|
| decisions, `DR-nnnn` | the `index.yaml` of the folder they bind | `title` (one sentence), `description` (what and why), `binds`, `not_chosen`, `cost` (`one-way`, `medium`, `cheap`), `sources`, `status` |
| conventions, `CV-nnnn` | same | `rule`, `area`, and `enforced_by` (the check) or `review_only: true` |
| reading entries | same | a repo-relative `path`, its `role`, a short `note` |
| terms | `docs/glossary.yaml` | `definition`, `see`, `close_terms`, `not` (words never to use) |

- A decision an agent records is `proposed`; the maintainer confirms it with `docs set DR-nnnn --set status=decided`.
- A changed decision gets a new entry: `docs supersede <old> --by <new>` links the two both ways.
- `removed` and `removed_why` soft-delete any entry. It stays in its file, so its id is never freed.
- `list`, `search` and the door leave out superseded and removed entries; `--all` shows them.

## Changing entries

Only through the writer verbs, never by hand:

| Verb | Does |
|---|---|
| `docs add <kind> --in <folder> --set f=v --add f=v` | a new entry; decisions and conventions get the next id; terms need no `--in` |
| `docs set <key> --set f=v --add f=v --unset f` | changes one; `--in <folder>` when a reading path is in several folders (`.` is the root) |
| `docs supersede <old> --by <new>` | marks a decision superseded and prints the citations to fix |
| `docs remove <key> --why "..."` | soft-deletes |

Parallel branches would each take the same next id. So a plan takes a block of ten for its decisions, the next free
one above main and every open branch, and adds each with `--set id=DR-nnnn`.

A value is text; `@file` reads it from a UTF-8 file, `-` from stdin, and `@@` writes a literal `@`. Each verb checks
the edit in memory, refuses one that adds a problem or a file that changed on disk, checks every file before writing
any, and writes through a temp file and a rename.

## The canonical form

Every entry file is written one way. `docs check` reports a file that isn't; `docs format` rewrites them all and
refuses a file whose values would change or whose comments would be lost.

- Fields in the kind's order; reading entries one `{ ... }` line each.
- A value plain when YAML 1.1 and 1.2 both read it back as the same string, quoted otherwise (so dates are quoted).
- Text of several lines as a `|-` block; a short list as `[a, b]`, a long one an item a line.
- The header comments, and comments right above a top-level key, are kept. A comment anywhere else fails the check.

## Citations

The gate reads every text file for `DR-nnnn` and `CV-nnnn`. An id that doesn't exist fails. Citing a superseded or
removed one fails too, except in records (`docs/plans/`, `docs/reviews/`, `CHANGELOG.md`) and the entry files.
