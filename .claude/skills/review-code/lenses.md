# Code review lenses

Every tier of `review-code` applies every lens below, to a change or to a path. `REVIEW.md` holds the questions and
the rule for what a finding is; these lenses say where to look. A lens that does not apply (its "Applies when" names
nothing in the scope) returns no findings at once, and an empty list is a good result.

**Not a bug hunt.** Report a problem only when it is real: it makes sense, it can be reproduced, and it causes an
actual failure for a user, their data or the next change. A finding has: a title, a severity (blocker, major, minor;
REVIEW.md says which is which, and most are minor), `file:line`, **the scenario** (the inputs or state, what happens,
why it matters), what it is judged against (an `AGENTS.md` or `REVIEW.md` rule, a decision, the plan, a library's
documented behaviour, or "none"), the evidence, the fix, and `sensitive` (below). No scenario, no finding. Style is
ruff's, taste is the author's.

## Sensitive areas

The vault's data (raw records written once, anything that can lose, duplicate or half-write what a user stored), keys
and secrets, private names, the service's local surface (HTTP, MCP, files), the one-way doors in `AGENTS.md`, and the
harness itself (`AGENTS.md`, `REVIEW.md`, `.claude/`, `.github/`, `.githooks/`, `tools/devkit/`). A finding there is
fixed without the maintainer's yes only when the fix makes a check stricter or the data safer
(under an autonomy grant, recorded in the PR); a blocker there is never waived.

## plan-and-intent

Applies when: the change has a plan in `docs/plans/` or an issue.

Does the change do what its plan and issue say? A planned step missing, or behaviour nobody asked for that a user will
meet, is a finding; a small departure the PR explains is not.

## implementation

Real bugs in Python that a user would hit: wrong logic; an edge case that real input reaches (empty, missing, very
large, non-ASCII and Hungarian text, duplicates); an exception that loses its cause or is swallowed so a failure looks
like success; a file, process, connection or SQLite transaction not closed on an error path; blocking work on the
event loop of an async endpoint; a race between two writers of the same file or row (several Claude sessions use the
service at once); a library used against its documented behaviour.

## data-integrity

Applies when: the scope writes, moves or deletes anything in a vault, or builds the index from it.

Can a crash, a kill or a failure halfway leave a raw record changed after it was written, a file half-written (no temp
file and rename), a record written twice or not at all, or an index that can't be rebuilt from what's committed? Can
two machines or two sessions writing at once lose one's write? Each is at least major; data loss is a blocker.

## security-and-privacy

Name the concrete attack and what it gains. A secret or a key in code, tests, logs, an exception message or a model
prompt; a name from the private deny list anywhere; the HTTP or MCP surface reachable from another origin, another
user or the network when it should be local; a path from input that escapes the vault; content from a source (a
message, a transcript, a document) treated as an instruction to a model or a command; a send without the user's click.
Weigh the attacker's cost against the gain: a costly attack for little gain, whose fix would cost users something, is
put to the maintainer as a choice, never hardened silently.

## cross-platform

Applies when: the scope touches paths, files, processes, encodings, the service or installers.

Does it work on Windows, macOS and Linux: path separators and case, `\r\n`, text read and written as UTF-8 explicitly,
file locking and renames over open files on Windows, signals and services per system, a tool assumed on PATH?

## doors-and-dependencies

Does the scope change a one-way door in `AGENTS.md` (a name, the vault's format, the MCP tools, the plugin's skills
and hooks) without the maintainer's agreement linked in the PR, or under an autonomy grant without its decision recorded as
proposed? That is a blocker. Each new package: needed, licence
compatible with Apache-2.0, no known advisory, and behind an optional extra when it needs a GPU or a large download.

## simplicity

A second implementation of one thing, a layer with one caller, an option nobody asked for, dead code: report it only
when it costs something real (a bug it invites, a test surface no one maintains, an install that grows). These are the
maintainer's calls: title them `[owner call] `, write them as a choice ("X could become Y, which drops Z: worth it?"),
always minor.

## tests

Would each new test fail without the change? Is a fix pinned by a test that reproduced the bug? Do tests use invented
data only (no real names, messages or recordings), run without a network or a GPU unless marked, and clean up what
they create? A missing test for behaviour the change adds is major; a missing edge-case test is minor.

## docs-freshness

Did the docs that describe the scope move with it: the entries (`uv run docs`), the guide or README, `AGENTS.md` when
a path, command or rule changed, `CHANGELOG.md` when users notice it? Does anything cite a path, a command or a
decision that no longer exists? A comment or doc outside `docs/plans/` and `docs/reviews/` that cites a plan or
review finding id (`P3`, `F5`) instead of saying the reason?

## harness-integrity

Applies when: the scope touches `AGENTS.md`, `REVIEW.md`, `.claude/`, `.github/`, `.githooks/`, `tools/devkit/` or
the build settings.

Anything that weakens a check: a gate step dropped or loosened, a lint rule or a type check turned off without a
reason, a test skipped, a review step dropped, tool attribution let through, a coverage floor lowered. Blocker-class.

## Refuting

Every finding gets a refutation attempt before it is reported. Rule it not real when its scenario can't happen (read
further: it is often handled elsewhere), when it causes no actual failure, when it is taste, or when it rests on a
misread of the code or the library. Settle it with evidence: in the standard tier, quote the line that proves or kills
it; in the deep tier and for a serious finding's refuter, reproduce it where that can run here. Outside code is never
run here (`review-code`, Never). A refutation you could not check leaves the finding real. A refuted finding stays in
the record, with its reason.
