# Contributing

Thanks for helping. This is the short path for a first contribution; [AGENTS.md](AGENTS.md) has the full rules, for
people and agents alike.

## Before you start

- Open an issue for anything beyond a typo, with the bug or feature form, and say there that you want to work on it.
- Write a plan first for a non-trivial change (a new capability, anything touching the vault's format, the MCP
  tools or the plugin), from the template in [docs/plans/README.md](docs/plans/README.md), as a draft PR. The maintainer
  approves it before any code.
- Use invented data. Tests, fixtures, examples and screenshots never hold a real person's messages, recordings or
  names.
- Report security issues privately, never in an issue: see [SECURITY.md](SECURITY.md).
- Your contribution is Apache-2.0 licensed, like the rest of the repo.

## Build and test

```
uv sync
uv run devkit setup
uv run gate full
```

You need [uv](https://docs.astral.sh/uv/), git and [gitleaks](https://github.com/gitleaks/gitleaks); `gate full` also
validates the plugin with [Claude Code](https://claude.com/claude-code). Without a private deny list the content check
skips on your machine; CI runs it.

A test that needs a vault takes a copy from `packages/docket/tests/docket_testkit.py` (`empty_vault`, `folder_vault`,
`host_repo`, the `template_*` builders and `copy_vault`) and writes only its copy: templates are built once per run for
every worker, and a run fails if a test wrote into one. A template is built when a test first asks for it, so a test
that patches something builds its copy before patching. Tests about `Vault.init` itself call it.

A test runs the CLI in process, `main([...])` from `docket.cli`. It starts a `python -m docket` only where the process
is what it tests: its streams, its arguments, its hash seed or the server; its setup still runs in process (DR-0756).

A test that asserts how long something took carries `@pytest.mark.perf`: `gate os`, which runs without coverage, runs
those after the rest, alone and one at a time, and prints what they print, so the other tests' workers never decide a
time limit. `gate full` keeps them in its one run, since under coverage the index's limits are off (DR-0430). A vault
a timed test needs is a shared template that an untimed test asks for too: the two runs of `gate os` share one
templates folder, so the parallel run builds it and the timed run only times (DR-0492).

A timed test that builds a big vault to time it, such as the index's 100,000 chunks or the ledger's 5,000 items, also
carries `@pytest.mark.nightly`, and so does each untimed test that builds that vault: `gate os` and `gate full` leave
them out, and `--all`, which the nightly passes, runs them on all three systems. A change to the index, the ledger,
search or the scan takes the `full-matrix` label, which runs the nightly on its PR. A test marked nightly is never the
only one to run a line, so the covered run holds the floors without it (DR-0757).

A new test file goes in `tools/devkit/test-systems.json`: under `system`, with its kind and why, when what it tests can
differ by system (paths, git, processes, the keychain, file times, modes and links, the console), else under `same`.
Windows and macOS run the files under `system`, the smoke set and every timed test not marked nightly on every PR (`gate
os`), and every file each night (`gate os --all`). The gate fails a file named nowhere, and a file under `same` that
names the system, skips by it, or makes links, file modes or file times, unless `signals` says why it runs the same
everywhere (DR-0758). A new command group or MCP tool fails `test_smoke.py` until it has its case there;
`test_web_browser_smoke.py` loads a new page as is.

Tests never touch the real Docket folders or the keychain, not even to read them: `conftest.py` points the config,
state and models folders at a temp folder of the session's before any fixture runs, and each test at its own.

## The web app's front end

Only a change under `packages/docket/web/` needs Node: the version in its `.nvmrc`, with the npm it brings.

```
cd packages/docket/web
npm ci
npm run lint && npm run typecheck && npm test
npm run build
```

- The build is committed in `packages/docket/src/docket/web/app/`, so an install needs no Node: commit it with the
  change that made it. `uv run gate` fails while a source differs from what the build was made of, and CI builds again
  and fails on any difference. A conflict in the folder is settled by building again.
- A change to an `/api/web` route's models runs `uv run devkit web-schema` first, which writes `openapi.json`, the
  types' source.
- `uv run devkit ui <page>`, `design`, `ledger`, `queue`, `meetings`, `today`, `search` or `recording`, shoots that
  page's states on an invented world, in light and dark, wide and narrow, with the accessibility, CSP and console
  checks, into `.local/ui/<page>/r<n>/`. It starts a throwaway service with folders of its own, so it never touches
  yours.
- `npm run dev` serves the sources on `localhost:5173`, proxying to the service `DOCKET_DEV_TARGET` names: a throwaway
  one, never your own on 8741.

## Open the pull request

1. Fork, and branch from `main` as `<issue>-<slug>`, such as `42-search-paging`.
2. Make the change with its tests and docs: the entries the docs tool manages (`uv run docs`), the guide or README that
   describes the behaviour, and a line in [CHANGELOG.md](CHANGELOG.md) when users notice it.
3. Commit with an imperative subject that says what changed and a body that says why. No AI co-author trailers or
   "Generated with" lines.
4. Open the PR with the template filled in, ending `Closes #<issue>`. Its title becomes the commit on `main`.

CI runs the gate on Linux, and on Windows and macOS the test files that can differ by system with the smoke set and
the timed tests; each night it runs the whole suite on all three, the timed tests on big vaults too, which the
`full-matrix` label runs on a PR as well. The maintainer reviews a PR from outside by reading it, never by running it
on their machine, and squash-merges it once CI is green.
