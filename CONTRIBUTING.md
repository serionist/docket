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

A test that asserts how long something took carries `@pytest.mark.perf`: the gate runs those after the rest, alone and
one at a time, and prints what they print, so the other tests' workers never decide a time limit (DR-0430).

## Open the pull request

1. Fork, and branch from `main` as `<issue>-<slug>`, such as `42-search-paging`.
2. Make the change with its tests and docs: the entries the docs tool manages (`uv run docs`), the guide or README that
   describes the behaviour, and a line in [CHANGELOG.md](CHANGELOG.md) when users notice it.
3. Commit with an imperative subject that says what changed and a body that says why. No AI co-author trailers or
   "Generated with" lines.
4. Open the PR with the template filled in, ending `Closes #<issue>`. Its title becomes the commit on `main`.

CI runs the gate on Linux and the tests on Windows and macOS. The maintainer reviews a PR from outside by reading it,
never by running it on their machine, and squash-merges it once CI is green.
