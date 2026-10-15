# Docket

Docket is a personal memory and task manager that Claude works in: everything kept raw and processed in the user's own
git repo (the vault), searched and run from a local service with a web app and an MCP endpoint, maintained by a Claude
Code plugin. This file is the router for anyone who changes the repo, person or agent. The gate keeps it under 150
lines.

## Docs

Structured docs are YAML entries that the docs tool manages; prose stays markdown.

| What | Where |
|---|---|
| decisions (`DR-`), conventions (`CV-`), reading lists | the `index.yaml` of the folder they bind |
| terms | `docs/glossary.yaml` |
| plans and reviews | `docs/plans/`, `docs/reviews/`: dated records, history, never current truth |
| the design | `docs/design.md`: how the parts fit, and the phases |

Before you plan, change, review or answer questions about a path:

1. `uv run docs door <path>` lists what to read, from the root down, and what binds the path. Read what it lists.
2. `uv run docs list decisions --path <path>` and `list conventions --path <path>` give one line each; `uv run docs
   show <id>` prints one in full, `uv run docs search <text>` finds by text.
3. Never read an `index.yaml` whole, and never plan against a folder from memory of it.

The rules:

- one fact in one place, cited by id;
- entries change only through `uv run docs add`, `set`, `supersede` and `remove`, never by hand;
- ids are never reused: a changed decision is superseded by a new one, a removed entry stays with its date and why;
- a decision an agent records stays `proposed` until the maintainer confirms it;
- every convention names the check that enforces it, or says `review_only`.

The schema is `docs/docs-regime.yaml`, explained in `docs/docs-regime.md`.

## Writing

- A lead line first, then a small table or a few one-line bullets. No walls of text.
- Conclusion first. Name the specific thing: the file, the command, the number.
- Plain words. No "ensure", "robust", "comprehensive", "leverage", "seamless"; spaced hyphens, never em dashes.
- No section numbers or review finding ids in prose: name the section instead.
- A code comment says the reason, not what the next line does.

## What is in this repo

| Path | What |
|------|------|
| `packages/docket/` | the app: the vault, the service (web app, MCP endpoint, scheduler), the source contract, the `docket` command |
| `plugin/` | the Claude Code plugin: the MCP entry, the session-start status hook, the skills (recall and setup now; sync, tasks and curate later), no scripts of its own (its hooks run `docket`); source skills live in the consuming project, scaffolded by `docket source new`; `.claude-plugin/marketplace.json` makes the repo its marketplace |
| `examples/` | reference sources to copy into a project, tested in CI and never a dependency: Plaud under `sources/plaud` |
| `tools/devkit/` | the development kit, never published: `gate`, `docs`, `devkit` |
| `docs/` | the design, the regime, the glossary, plans and reviews |
| `.claude/skills/` | the work loop: `start-work`, `review-plan`, `review-code`, `finish-work` |
| `.github/` | CI, the PR text check, CodeQL, Dependabot, issue forms, the PR template |
| `.githooks/` | `commit-msg` strips tool attribution, `pre-push` runs `gate quick` |
| `index.yaml` | the root's reading list, decisions and conventions |
| `REVIEW.md` | what a finding is, and the questions every review answers |

A new top-level folder adds its row in the same change.

## Build and test

You need uv (it brings Python 3.12), git, gitleaks, and Claude Code for the full gate.

- Once per clone: `uv sync`, then `uv run devkit setup`, which points git at `.githooks/` and checks the tools.
- `uv run gate` runs the quick lane in seconds: lock file, lint, format, types, docs, commit messages, private names,
  secrets, the harness.
- `uv run gate full` adds the tests with their coverage floors, the whole history's secrets and the plugin validated
  by Claude Code. It is the merge rule; CI runs it on Linux and the types and tests on Windows and macOS.
- Coverage floors are in `tools/devkit/coverage-floors.json`. Raise one in the change that earns it, never lower one.
- The repo is public and Docket is built from its maintainer's life, so the gate checks a private deny list kept
  outside the repo (`~/.config/docket-dev/denylist.txt`; CI has its own). Tests and examples use invented people.

## How work runs

Every change is a GitHub issue on the Docket board, with a milestone per phase, and runs one loop:

1. `start-work`: the issue, the door, a plan when it needs one, `review-plan`, then a branch `<issue>-<slug>`.
2. Build it with its tests and docs: the entries, the guides, a `CHANGELOG.md` line when users notice it.
3. `finish-work`: `gate full`, a draft PR, `review-code` until it is clean, then ready or squash-merge.

While you build:

- Look up a library's API with Context7 (`.mcp.json`) before coding against it: FastAPI, Pydantic, the MCP SDK.
- Ask the language server about a symbol first; grep is for text in config, docs and strings.

## Commits and PRs

- The subject says what changed, in the imperative; the body says why.
- PRs are squash-merged and the title becomes the commit on `main`: a plain sentence of at most 72 characters.
- No tool attribution anywhere on `main`: no AI co-author trailers, no "Generated with" lines, no session links.
- `main` takes PRs only. The maintainer, @serionist, merges; merging is the approval.

## One-way doors

Users and their stored data depend on these. Change one only in a PR that names it in its title, after the maintainer
agrees in the issue:

- the names: the `docket` package and command, the plugin's and the marketplace's name `docket`;
- the vault's format: its layout, run-file names and where Docket's own files live (DR-0141), the raw record's
  version (DR-0020), `vault.yaml` (DR-0021) and its `storage` (DR-0140), the vault's own `.gitattributes` (DR-0142),
  staging's files and states (DR-0024), the processed doc's format (DR-0109), the anchor grammar (DR-0110), a
  transcript's segments as a record field (DR-0111), doc revisions as the commits that wrote them (DR-0112), entity
  files (DR-0113), a transcript's text rendered by the core (DR-0120), hold lists (DR-0121), a cut record's fidelity
  (DR-0126), a legacy note's old paths and import hash (DR-0130), old paths as locators (DR-0131);
- a source's mapper interface (DR-0119), and the names and keys of a source in `vault.yaml` (DR-0124, DR-0125);
- the MCP tools' names and parameters: `search`, `read`, `verify` and `doc_put` (DR-0150), and the plugin's server
  key `docket` that prefixes them (DR-0160);
- the plugin's skill and hook names: `recall`, `setup`, and the SessionStart hook running `docket hook session-start`
  (DR-0161).

Each lands with its decision in `index.yaml`, marked `cost: one-way`.

## Never

- Push to `main`, or merge with a failing gate.
- Commit secrets, or a real person's data.
- Put a name from the deny list anywhere: the repo, an issue, a PR, a commit message.
- Change a one-way door without the maintainer's agreement.
- Check out, build or run code from outside the maintainers on your machine: read it, and let CI run it.
- Weaken a test, a check or a review step to make a change pass.
