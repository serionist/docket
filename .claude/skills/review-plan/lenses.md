# Plan review lenses

Every tier of `review-plan` applies every lens below. A lens that does not apply returns no findings at once, and an
empty list is a good result.

**Not a hunt.** A plan finding is a defect that would really hurt: a step that can't be built as written, a user's
data at risk, a door changed silently, a test that can't prove what it claims. A finding has: a title, a severity
(blocker, major, minor; most are minor), the plan section, **the scenario** (what goes wrong when the plan is followed
as written), the rule or code it is judged against, the evidence (quote the plan), the proposed plan edit, and
`sensitive` (below). No scenario, no finding.

## Sensitive areas

The vault's data, keys and secrets, private names, the service's local surface, the one-way doors in `AGENTS.md`, and
the harness itself. A plan edit there is never made without the maintainer's yes.

## rules-and-doors

Does the plan contradict a rule in `AGENTS.md` or `REVIEW.md`, a binding decision (`uv run docs list decisions --path
<path>`), or change a one-way door without saying so in "Doors and data"? Stated openly, it is a choice for the
maintainer; silent, it is a blocker.

## omissions

Read `REVIEW.md` and the code the plan changes, and list what they require that the plan leaves out and that would
really bite: a migration for existing vaults, a system (Windows, macOS, Linux) the change breaks, a test a reviewer
will ask for, a doc users need.

## data-and-security-design

Fail closed, and name the concrete attack or loss. Raw records written once; writes atomic; the index rebuildable;
two machines and two sessions at once; secrets out of the vault, logs and prompts; the HTTP and MCP surface local
only; ingested content treated as data. A gap that loses data or leaks is a blocker.

## simplicity

Speculation that costs: a seam with no caller, a setting nobody asked for, scope beyond the goal. The maintainer's
call: title it `[owner call] `, write it as a choice with what is lost and who notices, always minor.

## sequencing-and-tests

Can each step be built, tested and committed on its own? Does each name the test that proves it, and can that test
run in CI (no network, no GPU, invented data)? Are the acceptance checks objective?

## premortem

Assume the plan shipped and a user lost data or had to redo work: what was the likeliest cause, and what plan edit
prevents it? Report it only when the cause is plausible from the plan's text.

## Refuting

Every finding gets a refutation attempt before it is reported. Rule it not real when the plan already covers it
elsewhere (read the whole plan: this is the usual false positive), when it is deferred openly, when it is taste, or when
it rests on a misread rule or code. Quote the plan and the rule or code; a refutation without quoted text leaves the
finding real. A refuted finding stays in the record, with its reason.
