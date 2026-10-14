# Review

What every review of a change to this repo checks, whoever does it: the maintainer, Claude (`review-plan`,
`review-code`) or another tool.

## What a finding is

A review is not a bug hunt. It reports a problem only when the problem is real: it makes sense, it can be reproduced,
and it causes an actual failure for a user, their data or the next change. Every finding states that scenario: the
inputs or state, what happens, and why it matters. No scenario, no finding. Style is the formatter's, taste is the
author's, and "could be cleaner" is not a problem.

Severity is honest, and most findings are minor:

- **blocker:** loses or corrupts a user's data, leaks a secret or a private name, breaks a one-way door, or weakens a
  check. The change can't merge with it.
- **major:** a real user hits it in normal use: a crash, a wrong result, a broken install on one of the three systems,
  a missing test for behaviour the change adds.
- **minor:** everything else that is real: an edge case few will meet, a missing doc line, a clumsy error message.

**The loop:** a review goes to another round only when its last pass found a major or a blocker. Minors are fixed in
the same round, or recorded with a reason, and the review ends.

## The questions

Each is a yes or no. A "no" is a finding, at the severity above.

1. **Gate.** Did `uv run gate full` pass on the reviewed commit, and CI on the PR's head (Linux, Windows, macOS)?
2. **Intent.** Does the change do what its issue and plan say, and nothing the plan didn't agree?
3. **Tests.** Does new behaviour have a test that fails without it, and a fix a test that reproduced the bug? Do tests
   use invented data only, and clean up what they create?
4. **Data.** Can a crash or a failure halfway lose, duplicate or half-write what a user stored? Raw records are written
   once and never changed; everything else must be rebuildable from them.
5. **Secrets and private names.** No keys, tokens or passwords, and no name from the deny list, in code, tests, docs,
   commit messages, the PR or logs?
6. **The local surface.** Does anything the service exposes (HTTP, MCP, files) stay on the machine and refuse what
   it should: other origins, other users, paths outside the vault? Is content read from sources treated as data, never
   as instructions?
7. **Three systems.** Do paths, encodings, line endings, processes and the service work on Windows, macOS and Linux?
8. **One-way doors.** Does the change leave the doors in `AGENTS.md` alone, or name the one it changes and link the
   maintainer's agreement?
9. **Docs.** Did the docs that describe the change move with it: the entries (decisions, conventions, terms), the
   guides and READMEs, `AGENTS.md` when a path, command or rule changed, `CHANGELOG.md` when users notice it?
10. **Dependencies.** Is each new package needed, under a licence compatible with Apache-2.0, without known
    advisories, and optional when it needs a GPU?
11. **The PR.** A plain imperative title, the template filled in, no tool attribution?

## The harness

A change to `AGENTS.md`, `REVIEW.md`, `.claude/`, `.github/`, `.githooks/`, `tools/devkit/` or the build settings is a
change to how every other change is checked. It must keep every check at least as strict; anything that loosens one is
a blocker unless the PR says why and the maintainer agrees.
