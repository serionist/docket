# Plans

A non-trivial change gets a plan here before any code: a new capability, anything touching a one-way door (the
vault's format, the MCP tools, the plugin's names), work across several packages, a dependency that changes
behaviour. A fix a reviewer can follow from the PR alone needs none; its PR says `Trivial: <why>`.

- One file per plan: `YYYY-MM-DD-<issue>-<slug>.md`, dated the day it was started.
- Written with `start-work`, reviewed with `review-plan` until approved, approved by the maintainer, and committed as
  the first commit of its branch.
- A plan records what was decided and why at the time. It changes while its branch is open, when the work departs from
  it, and not after the merge. Decisions it takes that outlive the change go into the entries (`uv run docs add
  decisions`) as well.
- Fill every section. "None" is an answer; a blank is not.

## Template

```markdown
---
title: <a plain imperative sentence, which can become the PR title>
issue: <#n>
status: draft
approved_by:
approved_at:
---

# <title>

## Goal

What is true when this is done, and why it matters. In scope; out of scope.

## Design

How it works: the modules, types and data it adds or changes. The alternatives considered and why this one.

## Doors and data

The one-way doors it touches (AGENTS.md), what it does to users' stored data and how existing vaults move over. Or
"none".

## Steps

Ordered, each one buildable and testable on its own, each naming the test that proves it.

## Tests

The new and changed tests, and what they run against (the synthetic fixture vault, recorded replays).

## Docs

The entries, guides and READMEs that change with it.

## Risks

What could go wrong, and whether each risk is resolved or accepted.

## Plan review

Written by `review-plan`: tier, model, verdict, and the findings with their status.
```
