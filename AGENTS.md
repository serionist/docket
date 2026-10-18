# Docket

Docket is a personal memory and task manager that Claude works in: everything kept raw and processed in the user's own
git repo (the vault), searched and run from a local service with a web app and an MCP endpoint, maintained by a Claude
Code plugin. This file is the router for anyone who changes the repo, person or agent. The gate keeps it under 150
lines.

## Docs

Structured docs are YAML entries that the docs tool manages; prose stays markdown.

| What | Where |
|---|---|
| decisions (`DR-`), conventions (`CV-`) | a file each, `index.d/<id>.yaml` in the folder they bind; older ones in its `index.yaml` |
| reading lists | the `index.yaml` of the folder they bind |
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
- entries change only through `uv run docs add`, `set`, `supersede`, `remove` and `move`, never by hand;
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
| `plugin/` | the Claude Code plugin: the MCP entry, the session-start status hook, the capture hook, the skills (recall, setup, cuts and curate now; sync and tasks later), no scripts of its own (its hooks run `docket`); source skills live in the consuming project, scaffolded by `docket source new`; `.claude-plugin/marketplace.json` makes the repo its marketplace |
| `examples/` | reference sources to copy into a project, tested in CI and never a dependency: Plaud, Slack, Teams, Outlook mail and the calendar under `sources/<name>` |
| `tools/devkit/` | the development kit, never published: `gate`, `docs`, `devkit` |
| `docs/` | the design, the regime, the glossary, plans and reviews |
| `.claude/skills/` | the work loop: `start-work`, `review-plan`, `review-code`, `finish-work` |
| `.github/` | CI, the PR text check, CodeQL, Dependabot, issue forms, the PR template |
| `.githooks/` | `commit-msg` strips tool attribution, `pre-push` runs `gate quick` |
| `index.yaml` | the root's reading list, and its decisions and conventions from before `index.d/` |
| `index.d/` | the root's newer decisions and conventions, a file each |
| `REVIEW.md` | what a finding is, and the questions every review answers |

A new top-level folder adds its row in the same change.

## Build and test

You need uv (it brings Python 3.12), git, gitleaks, and Claude Code for the full gate.

- Once per clone: `uv sync`, then `uv run devkit setup`, which points git at `.githooks/` and checks the tools.
- `uv run gate` runs the quick lane in seconds: lock file, lint, format, types, docs, commit messages, private names,
  secrets, the harness.
- `uv run gate full` adds the tests with their coverage floors, the whole history's secrets and the plugin validated
  by Claude Code. It is the merge rule; CI runs it on Linux and the types and tests on Windows and macOS, Windows in
  two halves (`gate os --shard K/N`, DR-0495).
- Coverage floors are in `tools/devkit/coverage-floors.json`. Raise one in the change that earns it, never lower one.
- A test that asserts an elapsed time carries the `perf` marker; a lane without coverage (`gate os`) runs those after
  the rest, alone (DR-0430), on vaults an untimed test had the parallel run build (DR-0492).
- Eval floors, recall at 10 and MRR per kind of question on the invented set, are in
  `tools/devkit/eval-floors.json`: the full lane fails a kind above or below its floor, and a lowered one needs a new
  `lowered` note saying why.
- The same set with the embedder has floors of its own in `tools/devkit/eval-floors-models.json`, and with the CPU
  reranker in `tools/devkit/eval-floors-rerank.json`, held by `uv run gate models` with `DOCKET_TEST_MODELS` naming a
  folder `docket models install --device cpu` filled; the models CI job runs it and is required for merge.
- The repo is public and Docket is built from its maintainer's life, so the gate checks a private deny list kept
  outside the repo (`~/.config/docket-dev/denylist.txt`; CI has its own). Tests and examples use invented people.

## How work runs

Every change is a GitHub issue on the Docket board, with a milestone per phase, and runs one loop:

1. `start-work`: the issue, the door, a plan when it needs one, `review-plan`, then a branch `<issue>-<slug>`.
2. Build it with its tests and docs: the entries, the guides, a `CHANGELOG.md` line when users notice it.
3. `finish-work`: `gate full`, a draft PR, `review-code` until it is clean, then ready or squash-merge.

While you build:

- Look up a library's API with Context7 (`.mcp.json`) before coding against it: FastAPI, Pydantic, the MCP SDK,
  Jinja2, htmx.
- Ask the language server about a symbol first; grep is for text in config, docs and strings.

## Commits and PRs

- The subject says what changed, in the imperative; the body says why.
- PRs are squash-merged and the title becomes the commit on `main`: a plain sentence of at most 72 characters.
- No tool attribution anywhere on `main`: no AI co-author trailers, no "Generated with" lines, no session links.
- `main` takes PRs only. The maintainer, @serionist, merges; merging is the approval.

## One-way doors

Users and their stored data depend on these. Change one only in a PR that names it in its title, after the maintainer
agrees in the issue or, under an autonomy grant, with its decision recorded as proposed for his review (DR-0012):

- the names: the `docket` package and command, the plugin's and the marketplace's name `docket`;
- the vault's format: its layout, run-file names and where Docket's own files live (DR-0141), the raw record's version
  (DR-0020), `vault.yaml` (DR-0021), its `storage` (DR-0140), its redaction keys (DR-0192) and its `api_provider`
  (DR-0537), the vault's own `.gitattributes` (DR-0142), staging's files and states (DR-0024), the processed doc's
  format (DR-0109, DR-0580), the anchor grammar (DR-0198), a transcript's segments as a record field (DR-0111), doc
  revisions as the commits that wrote them (DR-0112), entity files (DR-0113), a transcript's text rendered by the core
  (DR-0120), hold lists (DR-0121), release logs (DR-0200), a cut record's fidelity (DR-0126), a legacy note's old paths
  and import hash (DR-0130), old paths as locators (DR-0131), each machine's cursor file (DR-0245), the curate log's
  path, lines and keys (DR-0460, DR-0461), a folder source's rids, `<source>:<12 hex>` with `/page<n>`, `/slide<n>` and
  `/sheet<n>` under them (DR-0482), media's paths, staged and stored, and its sidecar and withheld marks (DR-0547), the
  sidecar's cuts and `_decided.jsonl`'s kinds (DR-0561), the media cut log and the sidecar's replaced keys (DR-0562), a
  Whisper transcript's rid suffix `/whisper`, its engine string and its `via` (DR-0570), the forgotten list (DR-0579);
- a source's mapper interface (DR-0119), and its name and keys in `vault.yaml` (DR-0124, DR-0125), among them `folder`,
  `exclude` (DR-0481), `media_folders` (DR-0604) and `lookback_days` (DR-0606), `media_hosts` read and ignored;
- the question file `docket eval` reads (DR-0290) and its speech file (DR-0571); the user config's `speech` (DR-0568);
- the media key export's file, `schema_version` 1, which users keep as their backup (DR-0550);
- the MCP tools' names and parameters: `search`, `read`, `verify` and `doc_put` (DR-0150), `run_open` and `ingest`
  (DR-0241), `media_put` (DR-0547), `briefs` (DR-0556), `decisions` (DR-0565), `transcribe` (DR-0571), and the
  plugin's server key `docket` that prefixes them (DR-0160);
- the plugin's skill and hook names: `recall`, `setup`, `cuts` (DR-0605), `curate` (DR-0472), `brief` (DR-0555), `decision` (DR-0563), the SessionStart
  hook running `docket hook session-start` (DR-0161), and the PostToolUse hook running `docket hook post-tool-use`
  for `mcp__.*`, with its line prefixes (DR-0242);
- the release's yes: `docket hold release` lets a held recording in on its owner's yes, at their own terminal or in
  chat to Claude, the cuts shown first; no page, route or tool releases one (DR-0600, DR-0382). It lasts for good.
- the forget's yes: only the owner forgets, at their own console, on the OS prompt or a code typed back (DR-0572); no
  page, route or tool forgets (DR-0577). It lasts for good.
- the gate's settings: the CLI and the pages make any `vault.yaml` change, each commit saying who; turning
  `redaction.credentials` off asks at the owner's console (DR-0572) and no page makes it (DR-0601); an uncommitted
  edit lets more in only once committed, the gate obeying HEAD's `vault.yaml` plus what it only tightens (DR-0521).

Each lands with its decision entry, marked `cost: one-way`.

## Never

- Push to `main`, or merge with a failing gate.
- Commit secrets, or a real person's data.
- Put a name from the deny list anywhere: the repo, an issue, a PR, a commit message.
- Change a one-way door without the maintainer's agreement or, under an autonomy grant, its proposed decision.
- Check out, build or run code from outside the maintainers on your machine: read it, and let CI run it.
- Weaken a test, a check or a review step to make a change pass.
