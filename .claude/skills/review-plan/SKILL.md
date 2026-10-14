---
name: review-plan
description: Use when a plan in docs/plans/ is drafted and needs checking before any code ("review the plan", "is this plan ready", step 5 of start-work). Reviews the plan against AGENTS.md, REVIEW.md, the binding decisions and the code it changes through the lenses in lenses.md, reports only real defects with their scenario, writes the result into the plan, and goes to another round only when a blocker or major survived. Say "deep" for the full fan-out, or name a model instead of the default Opus.
---

# Review a plan

A plan review catches a design defect while it is still a one-line edit to the plan. It is not a hunt: it reports a
defect only when following the plan as written would really hurt (`REVIEW.md`, What a finding is), and most findings
are minor.

## Inputs

- The plan, by path: `docs/plans/<date>-<n>-<slug>.md`. Never review "the latest plan".
- The tier: standard by default; deep when asked for, or when the standard pass recommends it.
- The model: Opus, unless the request names another.

## Touch points, in order

1. The ground truth the reviewer reads: `AGENTS.md`, `REVIEW.md`, the plan in full, the issue it names (`gh issue
   view <n>`), `uv run docs door <path>` and the binding decisions for each folder the plan touches, and every file
   the plan names.
2. Review at the tier:
   - Standard: about ten minutes. Start one fresh agent on the chosen model with the plan's path, the ground-truth
     list and `lenses.md`. It reads and nothing else, applies every lens, refutes each finding as it goes by quoting
     the plan and the rule or code, and returns the confirmed and refuted findings, each with its scenario and honest
     severity, a verdict, and whether a deep pass is worth it. Don't read the ground truth into this session yourself.
     Give each confirmed blocker or major, and each sensitive finding, to its own fresh refuter agent, in parallel. A
     refuter that fails leaves its finding unverified, never refuted.
   - Deep: run `.claude/skills/review-code/scripts/review.workflow.js` with the Workflow tool and `args: { kind:
     "plan", label: "<slug>", plan: "<plan path>", model: "<model>" }`. Recommend it for a plan that changes a one-way
     door or the vault's format.
   - Without agents: apply every lens yourself, then refute each finding. Say in the verdict that the author reviewed
     their own plan.
3. Write the findings into the plan's "Plan review" section as `P1`, `P2`, ...: severity, plan section, the
   scenario, the proposed edit, status (`open`, `fixed`, `wontfix`), and how it was verified. Refuted findings go under
   "Refuted" with their reasons. Add the tier, the model and the date.
4. Act: apply the edits that don't change the design yourself and mark them `fixed`. Ask the maintainer about an
   edit that changes the design or the scope or touches a sensitive area, as a yes or no with your proposal; under an
   autonomy grant decide it yourself and record it (start-work, Decisions on the way), unless it loosens a check or
   changes a one-way door.
5. Another round only when needed: if the last pass confirmed a blocker or a major, review the edited sections and
   what they touch again (fresh agent, the edits only). If it confirmed only minors, the loop ends: fix or record them.
6. Set the verdict in the "Plan review" section: `approved` when no blocker or major is open, every question has
   its answer, and the plan schedules its tests and docs and ends with `review-code`; otherwise `needs-fixes` or
   `needs-answers`.

## Done means

- The verdict is `approved`, or the maintainer stopped the loop, with the open findings recorded.

## Never

- Report a finding without its scenario, or inflate a minor to keep the loop going.
- Change the plan's design or scope without a yes (or, under an autonomy grant, without recording it).
- Approve a plan with an open blocker on a sensitive area.
- Drop a finding silently: refuted and accepted findings stay in the plan.
