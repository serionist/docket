# Docket

Docket is being rebuilt, docs first. This file is the router for anyone who changes the repo, person or agent.

## Now: the docs phase

- Each topic is talked through with the maintainer, then written to `docs/`, then marked up and signed off.
- Docs are committed straight to `main` while there is no code.
- Once every doc is signed off, the roadmap becomes milestones and issues, and development starts.

## Writing

- A lead line first, then a small table or a few one-line bullets. No walls of text.
- Conclusion first. Name the specific thing: the file, the command, the number.
- Plain words. No "ensure", "robust", "comprehensive", "leverage", "seamless"; spaced hyphens, never em dashes.
- Diagrams are SVG files next to the doc that uses them.

## Once per clone

- `git config core.hooksPath .githooks`: the hooks run `tools/check_names.py` on every commit. It needs `uv` and the
  private deny list at `~/.config/docket-dev/denylist.txt`.

## Never

- Put a private name in the repo, a commit message, an issue or a PR. The list lives outside the repo; the hooks
  check staged files and commit messages, and `uv run --no-project python tools/check_names.py <file>` checks any
  text before it is posted.
- Add tool or AI attribution: no co-author trailers, no "Generated with" lines, no session links.
- Use a real person's data in examples or tests: invented people only.
