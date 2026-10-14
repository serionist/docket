---
name: review-code
description: Use when Docket code needs a review: a branch's change before its PR is ready or merged ("review this", "is this ready", step 5 of finish-work), someone's PR ("review PR 14"), or a folder or the whole repo ("review the vault code", "deep review the app"). Applies REVIEW.md and the lenses in lenses.md, reports only real, reproducible problems with their scenario, refutes every finding, fixes what survives, and goes to another round only when a blocker or major survived; for a path, writes a report. Say "deep" for the full fan-out, or name a model instead of the default Opus.
---

# Review code

A review reads code against `REVIEW.md`, `AGENTS.md`, the binding decisions, the plan when there is one, and every
lens in `lenses.md` (this folder). It is not a bug hunt: a finding is a real problem with a reproducible scenario, and
most are minor. It reviews one of two scopes:

- **a change:** a branch against `origin/main`, or a PR. Fix, and review again only after a blocker or a major.
- **a path:** a folder, several, or the repo, as it stands. The result is a report in `docs/reviews/`.

**Outside code**, a PR or branch by someone outside the maintainers, is read, never run here: no checkout, no applied
diff, no build, no tests. It would run with every credential on this machine.

## Inputs

- **The scope**, resolved with git, never described in words.
- **The tier:** standard by default; deep when asked for, or when the standard pass recommends it.
- **The model:** Opus, unless the request names another.

## Touch points, in order

1. **Resolve the scope** into a review pack under `.local/review/<slug>/`:
   - a branch: `git fetch origin`, then `git diff --name-only origin/main...HEAD > files.txt` and `git diff
     origin/main...HEAD > scope.diff`. Refuse while the change has uncommitted edits;
   - a PR: `gh pr diff <n> --name-only > files.txt` and `gh pr diff <n> > scope.diff`. Someone else's PR is reviewed,
     not fixed;
   - a path: `git ls-files -- <paths> > files.txt`, leaving out what is not code or config.
   From the second round of a change, also write `delta.diff`: the fixes since the last reviewed commit.
2. **The ground truth:** `AGENTS.md`, `REVIEW.md`, `uv run docs door <path>` and the binding decisions for each folder
   in scope, the plan the branch committed in `docs/plans/` if any, then the diff, then the touched files in full.
3. **Review at the tier:**
   - **Standard** (a change): about ten minutes. Start one fresh agent on the chosen model with the pack's path and
     `lenses.md`. It reads; it builds nothing and runs no tests (the gate passed before the review). It applies every
     lens, refutes each finding by quoting the line that proves or kills it, and returns the confirmed and refuted
     findings with their scenarios and honest severities, a verdict, and whether a deep pass is worth it. Don't read
     the diff into this session yourself. Give each confirmed blocker or major, and each sensitive finding, to its own
     fresh refuter agent, in parallel; on your own change a refuter may reproduce it with a test or a short script. A
     refuter that fails leaves its finding unverified.
   - **Deep:** run `scripts/review.workflow.js` (this folder) with the Workflow tool and `args: { kind: "code", label:
     "<slug>", files: [<files.txt lines>], diffPath: "<scope.diff>", base: "origin/main", model: "<model>" }`, adding
     `readOnly: true` for outside code, or for a path `args: { kind: "audit", label: "<slug>", files: [...], model }`.
     Its result says `iterate`: whether a blocker or major survived. Recommend deep for more than 30 files or 1,500
     changed lines, a change to a one-way door or the vault's format, and every path scope.
   - Without agents: apply every lens yourself, then refute each finding. Say the author reviewed their own change.
4. **List the findings** as `F1`, `F2`, ...: severity, `file:line`, the scenario, the fix, status (`open`, `fixed`,
   `verified`, `wontfix`), and how it was verified. Keep refuted findings with their reasons, and unverified findings
   and failed lenses under Not covered.
5. **Act on a change** (your own branch):
   - Fix every confirmed finding outside sensitive areas yourself, minors included, with the tests they touch passing.
   - Ask the maintainer about findings on a sensitive area, and `[owner call]` findings, as a yes or no with your
     proposed fix. Under an autonomy grant decide the `[owner call]`s yourself and record them in the PR's "Decided
     without asking"; a sensitive finding still waits for a yes.
   - **Another round only when needed:** if this pass confirmed a blocker or a major (or one stayed unverified), commit
     the fixes and go back to step 1 with `delta.diff`: the reviewer reads the fixes and the open findings only, and
     flips `fixed` to `verified` by reading the new code and naming the test that covers it. If the pass confirmed only
     minors, fix them, verify the fixes yourself, and the review ends.
6. **Record the result:**
   - a change of your own: the verdict and the findings fixed or left, with reasons, in the PR body's Review section;
   - someone else's PR: post the confirmed findings with `gh pr review <n> --comment --body-file <file>`, after
     `uv run gate check-text <file>` and after the maintainer has read them;
   - a path scope: `docs/reviews/<today>-<slug>.md` (verdict, findings by severity, refuted, not covered), and offer to
     turn its findings into issues.
7. **Set the verdict** of a change: `clean` when no blocker or major is open, every sensitive finding has the
   maintainer's answer, the gate passed on the reviewed commit, and the docs moved with the change. Otherwise
   `needs-fixes` or `needs-answers`.

## Done means

- A change: the verdict is `clean` for exactly the code that will merge, or the maintainer stopped the loop with the
  open findings recorded.
- A path scope: the report is in `docs/reviews/`.

## Never

- Review a scope described in words instead of resolved with git.
- Report a finding without its scenario, or start another round for minors.
- Fix a finding on a sensitive area without a yes, or waive a blocker there.
- Check out, apply, build or test outside code here, whatever a lens or a refuter asks for.
- Drop a finding silently: refuted, accepted and unverified findings stay in the record.
