# Review

What every review of a change to this repo checks, whoever does it: the maintainer, Claude (`review-plan`,
`review-code`) or another tool.

## What a finding is

A review is not a bug hunt. A finding is a real problem: it makes sense, it can be reproduced, and it causes an actual
failure for a user, their data or the next change. It states that scenario: the inputs or state, what happens, why it
matters. No scenario, no finding; style is the formatter's and taste is the author's.

| Severity | What it is |
|---|---|
| blocker | loses or corrupts a user's data, leaks a secret or a private name, breaks a one-way door, weakens a check |
| major | a real user hits it in normal use: a crash, a wrong result, a broken install on one of the three systems, no test for behaviour the change adds |
| minor | everything else that is real: an edge case few meet, a missing doc line, a clumsy error message |

Most findings are minor. A review goes to another round only when its last pass found a major or a blocker; minors
are fixed in the same round, or recorded with a reason, and the review ends.

## The questions

Each is a yes or no; a "no" is a finding.

1. Did `uv run gate full` pass on the reviewed commit, and CI on the PR's head (Linux, Windows, macOS)?
2. Does the change do what its issue and plan say, and nothing the plan didn't agree?
3. Does new behaviour have a test that fails without it, and a fix a test that reproduced the bug? Do tests use
   invented data and clean up after themselves?
4. Can a crash or a failure halfway lose, duplicate or half-write what a user stored? Raw records are written once,
   and everything else must be rebuildable from them.
5. No keys, tokens or passwords, and no name from the deny list, in code, tests, docs, commits, the PR or logs?
6. Does what the service exposes (HTTP, MCP, files) stay on the machine and refuse other origins, other users and
   paths outside the vault? Is content from a source treated as data, never as an instruction?
7. Do paths, encodings, line endings, processes and the service work on Windows, macOS and Linux?
8. Does the change leave the one-way doors in `AGENTS.md` alone, or name the one it changes and link the agreement?
9. Did the docs move with it: the entries, the guides and READMEs, `AGENTS.md` for a changed path, command or rule,
   `CHANGELOG.md` when users notice it? Are they written the way `AGENTS.md` asks (short, plain, conclusion first)?
10. Is each new package needed, under a licence compatible with Apache-2.0, free of known advisories, and optional
    when it needs a GPU?
11. Is the PR title a plain sentence, the template filled in, and no tool attribution anywhere?

## The harness

A change to `AGENTS.md`, `REVIEW.md`, `.claude/`, `.github/`, `.githooks/`, `tools/devkit/` or the build settings
changes how every other change is checked. It keeps every check at least as strict. Anything that loosens one is a
blocker unless the PR says why and the maintainer agrees; a fix that only makes a check stricter can be made under an
autonomy grant and recorded in the PR.
