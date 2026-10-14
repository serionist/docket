# The docs regime

`AGENTS.md` says how the docs work and holds the door rule. This page explains the schema behind it,
`docs-regime.yaml`, which the docs tool (`uv run docs`, in `tools/devkit/`) reads and the gate checks. Edit the two in
the same change.

## Why entries

Decisions, conventions, terms and reading lists are facts that get cited, superseded and looked up one at a time. As
YAML entries, a session reads the one line or the one entry it needs instead of a whole page, a fact lives in one
place under an id that never changes, and the gate checks every citation of it. Prose that explains (a design, a
guide, a README) stays markdown.

## Kinds

- **Decisions** (`DR-nnnn`), in the `index.yaml` of the folder they bind: `title` says what was decided in one
  sentence; `description` what and why in a few lines; `binds` what it commits us to; `not_chosen` the options turned
  down, one line each; `cost` how hard it is to change (`one-way`, `medium`, `cheap`); `sources` where it came from (an
  issue, a PR, a design section). `status` is `proposed` when an agent records it, `decided` once the maintainer
  confirms it. A changed decision gets a new entry; `docs supersede <old> --by <new>` links the two both ways and sets
  the old one's status.
- **Conventions** (`CV-nnnn`): one `rule`, its `area`, and either `enforced_by` (the check: a gate step, a lint rule)
  or `review_only: true` when no check exists.
- **Reading entries**: a repo-relative `path`, its `role` (contract, design, guide, convention, skill, record) and a
  short `note`. `docs door <path>` prints them from the root down.
- **Terms**, in `docs/glossary.yaml`: the `definition`, related terms or docs under `see`, the nearest word elsewhere
  under `close_terms`, and words never to use under `not`.

Every kind may also carry `removed` (a date) and `removed_why`, together and last: a removed entry stays in its file
(a soft delete), so its id is never freed. Superseded and removed entries are left out of `list`, `search` and the
door; `--all` shows them, and `show` prints any entry with its state.

## Changing entries

Only through the tool's writer verbs, never by hand:

- `uv run docs add <kind> --in <folder> --set field=value --add field=value`. Decisions and conventions get the next
  id; a decision's `status` defaults to `proposed`, `at` to today and `by` to git's `user.name`. Terms need no `--in`.
- `uv run docs set <key> --set field=value --add field=value --unset field`. A reading entry's key is its path, with
  `--in <folder>` when the path is listed in several folders.
- `uv run docs supersede <old> --by <new>`, then fix the living citations it prints.
- `uv run docs remove <key> --why "<text>"`.

`--set` gives a field one value and `--add` appends to a list field. A value is text: `@path` reads it from a UTF-8
file, `-` from stdin, and `@@` writes a literal leading `@`. Each verb makes its edit in memory, runs the check,
refuses an edit that adds a problem or a file that changed on disk since it was read, and writes the file in canonical
form through a temp file and a rename.

## The canonical form

Every entry file is written one way; `docs check` reports a file that isn't, and `docs format` rewrites them all
(refusing a file whose values would change or whose comments would be lost):

- the fields in the kind's `order`; a flow kind (reading) one `{ ... }` line per entry;
- a scalar plain when YAML 1.1 and 1.2 both read it back as the same string, double-quoted otherwise (so dates are
  quoted); text of several lines a `|-` block;
- a list of one-line values `[a, b]` when the line fits in 100 characters, one item a line otherwise;
- the header comments, and comment lines right above a top-level key, are kept; a comment anywhere else fails the
  check, because the rewrite would lose it.

## Citations

The gate reads every text file for `DR-nnnn` and `CV-nnnn`. An id that doesn't exist fails. Citing a superseded or
removed one fails too, except in records (`docs/plans/`, `docs/reviews/`, `CHANGELOG.md`) and the entry files, where
it is history.
