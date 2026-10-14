# Docket

A personal memory and task manager that Claude works in: everything kept raw and processed in the user's own git repo
(the vault), indexed, and run from a local service with a web app and an MCP endpoint, which a Claude Code plugin
maintains. This file is for anyone who changes the repo, person or agent. It is a router, not the truth: the gate keeps
it under 150 lines.

## Docs and the door rule

Structured docs are YAML entries that the docs tool manages: decisions (`DR-`), conventions (`CV-`) and reading lists
sit in the `index.yaml` of the folder they bind, terms in `docs/glossary.yaml`. Prose (designs, guides, READMEs) stays
markdown. Plans and reviews are dated records in `docs/plans/` and `docs/reviews/`: history, never current truth.

Before you plan, change, review or answer questions about a path:

1. `uv run docs door <path>` lists what to read, from the root down, and what binds the path. Read what it lists.
2. `uv run docs list decisions --path <path>` and `list conventions --path <path>` give one line each;
   `uv run docs show <id>` prints one in full, `uv run docs search <text>` finds by text.
3. Never read an `index.yaml` whole, and never plan against a folder from memory of it.

The rules: one fact in one place, cited by id. Entries change only through `uv run docs add`, `set`, `supersede` and
`remove`, which check the change and write it in canonical form; never by hand. Ids are never reused: a decision that
changes is superseded by a new one, and `remove` keeps the entry with the date and why. A decision an agent records is
`proposed` until a maintainer confirms it (`docs set DR-nnnn --set status=decided`). Every convention names the check
that enforces it, or says `review_only`. The schema is `docs/docs-regime.yaml`, explained in `docs/docs-regime.md`.

## What is in this repo

| Path | What |
|------|------|
| `packages/docket/` | The app: the vault, the service (web app, MCP endpoint, scheduler) and the `docket` command. |
| `plugin/` | The Claude Code plugin: skills, hooks and the MCP configuration. `.claude-plugin/marketplace.json` makes the repo its marketplace. |
| `tools/devkit/` | The development kit, never published: `gate`, `docs`, `devkit` (setup and the commit-msg hook). |
| `docs/` | The regime, the glossary, `plans/` and `reviews/`. |
| `.claude/skills/` | The work loop: `start-work`, `review-plan`, `review-code`, `finish-work`. |
| `.github/` | CI, CodeQL, Dependabot, the issue forms and the PR template. |
| `.githooks/` | `commit-msg` (strips tool attribution) and `pre-push` (`gate quick`). |
| `index.yaml` | The root's reading list, decisions and conventions. |
| `REVIEW.md` | The questions every review answers. |

A new top-level folder adds its row in the same change.

## Build and test

You need uv (it brings Python 3.12), git, gitleaks, and Claude Code for the full gate.

- **Once per clone:** `uv sync`, then `uv run devkit setup` (points git at `.githooks/` and checks the tools).
- **The gate:** `uv run gate` (quick: lock file, lint, format, types, docs, commit messages, private names, secrets,
  the harness), `uv run gate full` (quick, the tests with coverage floors, the whole history's secrets, the plugin
  validated by Claude Code), `uv run gate list`. What passes `gate full` is the merge rule; CI runs it on Linux and the
  types and tests on Windows and macOS.
- **Tests:** `uv run pytest`. Coverage floors are in `tools/devkit/coverage-floors.json`: raise one in the change
  that earns it, never lower one.
- **Private names:** the repo is public and Docket is built from its maintainer's life, so the gate checks for names
  on a private deny list kept outside the repo (`~/.config/docket/denylist.txt`; CI has its own). Tests and examples
  use invented people, companies and places.

## How work runs

Every change is a GitHub issue on the Docket board (a milestone per phase) and runs one loop:

1. `start-work`: find or create the issue, read the door, write the plan when it needs one, `review-plan` until it is
   approved, then a branch `<issue>-<slug>` from `origin/main`.
2. Build it, with its tests and its docs in the same change: the entries, the guides, a `CHANGELOG.md` line when users
   notice it.
3. `finish-work`: `gate full`, a draft PR, `review-code` until it is clean, then ready or squash-merge.

While you build:

- Before coding against a library's API (FastAPI, Pydantic, the MCP SDK, SQLite), look it up with Context7
  (`.mcp.json`). Libraries move faster than any model's memory.
- A question about a symbol goes to the language server first; grep is for text in config, docs and strings.
- Code, tests and docs explain themselves: no plan or review finding ids (`P3`, `F5`) outside `docs/plans/` and
  `docs/reviews/`. A comment says the reason itself; citing a decision by id (`DR-0007`) is fine, the gate checks it.

## Commits and PRs

- A subject in the imperative that says what changed, a body that says why. PRs are squash-merged: the PR title
  becomes the commit on `main`, so it is a plain imperative sentence of at most 72 characters.
- Author email linked to your GitHub account.
- No tool attribution anywhere on `main`: no AI co-author trailers, no "Generated with" lines, no session links. The
  commit-msg hook strips them and the gate refuses them.
- `main` takes PRs only. The maintainer, @serionist, merges; merging is the approval.

## One-way doors

Users and their stored data depend on these. Change one only in a PR that names it in its title, after the maintainer
agrees in its issue:

- **Names:** the `docket` package and command, the plugin's name `docket` and the marketplace's name `docket`.
- **The vault's format**, once it lands: its layout, `vault.yaml`, the raw record schema. A vault written by one
  version opens in every later one.
- **The MCP tools' names and parameters**, and the plugin's skill and hook names, once they land: Claude sessions and
  users' own skills call them.

Each lands with its decision in `index.yaml`, marked `cost: one-way`.

## Never

- Push to `main`, or merge with a failing gate.
- Commit secrets, or a real person's data: tests and fixtures are invented.
- Put a name from the deny list anywhere in the repo, an issue, a PR or a commit message.
- Change a one-way door without the maintainer's agreement.
- Check out, build or run code from outside the maintainers on your machine: review it by reading; CI runs it.
- Weaken a test, a check or a review step to make a change pass.
