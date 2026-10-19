---
name: finish-work
description: Use when a Docket change is ready ("finish", "wrap up", "we're done", "open the PR", "merge it"). Checks the docs and entries moved with the change, runs the full gate, commits, opens a draft PR, reviews it with review-code until it is clean, then marks it ready or squash-merges it, moves the board and cleans up.
---

# Finish a change

Done means `uv run gate full` passed, `REVIEW.md` is answered and `review-code` is clean, not that the work feels
complete. `main` takes PRs only, squash-merged.

## Touch points, in order

1. Docs in the same change. Check each thing that describes the change moved with it: the entries (`uv run docs`),
   the guide or README, `AGENTS.md` for a changed path, command or rule, `CHANGELOG.md` when users notice it, the plan
   when the work departed from it (say where and why). Write them the way `AGENTS.md` asks: short, plain, no walls of
   text.
2. Run `uv run gate full`. Fix every failure; never weaken a test or a check to pass. Raise a coverage floor the change
   earned.
3. Commit: the subject says what changed, the body says why, `Refs #<n>`. No tool attribution.
4. Open the draft PR. Write the body to `.local/pr-body.md` from `.github/pull_request_template.md`, check it with `uv
   run gate check-text .local/pr-body.md`, push, and run `gh pr create --draft --title "<plain sentence>" --body-file
   .local/pr-body.md`. Say it's open and how to see it all (the Files tab, or `git diff origin/main...HEAD`). Wait for
   the maintainer's go, unless under an autonomy grant.
   Every diff you read leaves the web app's build out: `-- ':!packages/docket/src/docket/web/app'` (DR-0705).
5. Review with the `review-code` skill until it is `clean`: another round only after a blocker or a major. Commit each
   round of fixes with `gate quick` passing. Write the verdict and the fixes into the body's Review section, run `gate
   check-text` on the body again, then `gh pr edit <n> --body-file .local/pr-body.md`. Every edit of the title or the
   body goes through `check-text` first; CI's PR text check runs again on each edit.
6. Wait for CI on the head commit (`gh pr checks <n> --watch`). A red check is fixed, never skipped.
7. Ask the maintainer, as one question with a recommendation, whether to mark it ready, merge it, or leave it a draft.
   Under an autonomy grant, merge it yourself once the review is clean and CI is green, unless it changes a one-way
   door or loosens a check: those wait for the maintainer.
8. Merge. Bring the branch up to date if `main` moved (`git fetch origin`, `git rebase origin/main`, `git push
   --force-with-lease`; a rebase that changes code needs the gate again). Then `gh pr ready <n>` and `gh pr merge <n>
   --squash --delete-branch --subject "$(gh pr view <n> --json title -q .title)" --body "<what and why, Closes
   #<n>>"`: the subject is the PR's own title, which CI checked. The squash message carries no HTML comments and no
   tool attribution. GitHub moves the card to Done when the issue closes.
9. Clean up: `git switch main` and `git pull`; remove a worktree from the main checkout (`git worktree remove <path>`).
   Work that turned up along the way becomes an issue now, on the board (`gh issue create`, `uv run devkit board <n>`).

## Done means

- `gate full` passed, CI is green, and `review-code` is clean for this change.
- The PR is ready for review, or merged with its issue closed, or the maintainer chose to leave it a draft.

## Never

- Open, mark ready or merge a PR with a failing gate or failing CI, or with a review of different code.
- Mark a `REVIEW.md` question yes without its evidence.
- Add AI co-author trailers, "Generated with" lines or session links: commits, PR titles, PR bodies.
- Push to `main`, or merge someone else's PR without asking them.
