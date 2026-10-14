---
name: finish-work
description: Use when a Docket change is ready ("finish", "wrap up", "we're done", "open the PR", "merge it"). Checks the docs and entries moved with the change, runs the full gate, commits, opens a draft PR, reviews it with review-code until it is clean, then marks it ready or squash-merges it, moves the board and cleans up.
---

# Finish a change

Done is defined by `uv run gate full`, `REVIEW.md` and a clean `review-code` pass, not by the sense that the work is
complete. `main` takes PRs only, squash-merged (`AGENTS.md`, Commits and PRs).

## Touch points, in order

1. **Docs in the same change.** List what describes the change and check each moved with it: the entries (`uv run
   docs`: decisions taken, conventions added, terms), the guide or README, `AGENTS.md` when a path, command or rule
   changed, `CHANGELOG.md` when users notice it, the plan when the work departed from it (say where and why).
2. **The gate:** `uv run gate full`. Fix every failure; never weaken a test or a check to pass. Raise a coverage floor
   the change earned.
3. **Commit:** a subject in the imperative that says what changed, a body that says why, `Refs #<n>`. No tool
   attribution of any kind.
4. **Draft PR.** Write the body to `.local/pr-body.md` from `.github/pull_request_template.md`, check it with `uv run
   gate check-text .local/pr-body.md`, push (`git push -u origin <branch>`), and open it with `gh pr create --draft
   --title "<plain imperative sentence>" --body-file .local/pr-body.md`. Say the draft is open and how to see it all
   (the PR's Files tab, or `git diff origin/main...HEAD`). Wait for the maintainer's go, unless under an autonomy
   grant.
5. **Review** with the `review-code` skill until its verdict is `clean`: another round only after a blocker or a
   major. Commit each round of fixes with the gate's quick lane passing and the tests they touch. Write the verdict and
   what it fixed into the PR body's Review section (`gh pr edit <n> --body-file .local/pr-body.md`).
6. **CI:** wait for the checks on the head commit (`gh pr checks <n> --watch`). A red check is fixed, never skipped.
7. **Close it:** ask the maintainer, as one question with a recommendation, whether to mark it ready, merge it, or
   leave it as a draft. Under an autonomy grant, merge it yourself once the review is clean and CI is green, unless it
   changes a one-way door or the harness's strictness: those wait for the maintainer.
8. **Merge:** bring the branch up to date if `main` moved (`git fetch origin`, `git rebase origin/main`, `git push
   --force-with-lease`; a rebase that changes code needs the gate again), then `gh pr ready <n>` and `gh pr merge <n>
   --squash --delete-branch --subject "<PR title>" --body "<what and why, Closes #<n>>"`. The squash message carries no
   HTML comments and no tool attribution. GitHub moves the card to Done when the issue closes.
9. **Clean up:** `git switch main` and `git pull`. Remove a worktree from the main checkout (`git worktree remove
   <path>`); in Claude Code, leave it with the worktree tool. Work that turned up along the way becomes an issue now,
   on the board (`gh issue create`, `uv run devkit board <n>`).

## Done means

- `gate full` passed, CI is green, and `review-code` is clean for this change.
- One of: the PR is ready for review; it is merged and its issue closed; or the maintainer chose to leave it a draft.

## Never

- Open, mark ready or merge a PR with a failing gate or failing CI, or with a review of different code.
- Mark a `REVIEW.md` question yes without its evidence.
- Add AI co-author trailers, "Generated with" lines or session links anywhere: commits, PR titles, PR bodies.
- Push to `main`, or merge someone else's PR without asking them.
