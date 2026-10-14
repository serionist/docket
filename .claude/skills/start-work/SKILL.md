---
name: start-work
description: Use when someone wants to work on something in Docket ("let's do #12", "pick up the search paging issue", "let's add X", "fix Y"), before any code or docs change. Finds or creates the issue and puts it on the board, reads the door, decides whether it needs a plan, writes the plan and reviews it with review-plan until it is approved, then starts the branch from origin/main.
---

# Start a change

Every change to `main` arrives through a PR (`AGENTS.md`, How work runs). This skill gets a change to the point where
code can start: an issue on the board, a plan when the change needs one, the plan approved, and a branch.
`finish-work` closes it.

## Touch points, in order

1. Find the issue.
   - A number (`#12`): `gh issue view 12 --comments`. If it is closed, ask before going on.
   - A sentence: look for an open issue first (`gh issue list --search "<words>"`); if none fits, write the body to
     `.local/issue-body.md`, check it with `uv run gate check-text .local/issue-body.md` (private names, attribution),
     and create it: `gh issue create --title "<title>" --body-file .local/issue-body.md --label area:<area>
     --milestone "<phase milestone>"`. A typo or comment fix needs no issue.
   - Put it on the board as in progress: `uv run devkit board <n> --status "In Progress"` (the phase comes from the
     milestone; add `--size S|M|L`).
2. Read before planning: `uv run docs door <path>` for every folder the change touches and what it lists, the
   decisions that bind them (`uv run docs list decisions --path <path>`), the code and the tests there, and
   `REVIEW.md`.
3. Decide whether it needs a plan, by the rule in `docs/plans/README.md`. Recommend one for anything touching a
   one-way door, the vault's data, or more than one package.
4. Write the plan at `docs/plans/<today>-<n>-<slug>.md` from the template, with `issue` set. Fill every section;
   "none" is an answer, a blank is not.
5. Review the plan with the `review-plan` skill until its verdict is `approved`.
6. Get it approved by the maintainer: give the plan's path and the review's verdict, and ask. Under an autonomy
   grant (the maintainer said to run the work without asking), approve it yourself, set `approved_by: claude (autonomy
   grant)`, and record each decision you would have asked about (below). Then set `status: approved`, `approved_by`
   and `approved_at` in the plan's frontmatter.
7. Start the branch `<n>-<slug>` from `origin/main`, after `git fetch origin`:
   - a new branch in this checkout (`git switch -c <branch> origin/main`) when the checkout is clean and nothing
     else runs here;
   - a new worktree (`git worktree add .claude/worktrees/<branch> -b <branch> origin/main`) when other work is in
     flight or several sessions run at once; then move the session there with the worktree tool.
8. Commit the plan as the branch's first commit: `Plan: <title>`, with `Refs #<n>` in the body.
9. Choose how to execute: directly in this session, or forked agents for independent steps (each in its own
   worktree, its brief naming the plan, its step and the folders it may touch). This session stays the anchor.

## Decisions on the way

A choice that outlives the change (a library, a format, a name, a layout) becomes an entry, not only a plan line:
`uv run docs add decisions --in <folder> --set title=... --set cost=... --set description=@file --add binds=...
--add not_chosen=... --add sources="#<n>"`. An agent's decision stays `proposed` until the maintainer confirms it.
Under an autonomy grant, list each one in the PR's "Decided without asking" section too.

## Done means

- The issue exists and is In Progress on the board (unless the change is a typo or comment fix).
- A change that needs a plan has one, approved, committed first on its branch, with its plan review in it.
- The branch exists from `origin/main`, and how to execute is chosen.

## Never

- Write code before the plan is approved, or on `main`.
- Plan from memory of the code or the docs instead of reading the door and the files.
- Post an issue or a comment that `gate check-text` hasn't passed.
- Keep a plan only in the session, an issue comment or a PR description: the plan is a file.
