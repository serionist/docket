---
name: plaud
description: Harvest Plaud recordings into Docket with the user's own Plaud MCP. Use when a sync runs the Plaud source, or the user asks to fetch, catch up on or import Plaud recordings.
---

<!-- docket: steps from run_open -->

# Plaud, Docket's reference source

Reads Plaud with the user's own Plaud MCP and hands what it returned to Docket untouched; `mapper.py` next to this
file turns a run's captures into records. This folder is an example to copy, not part of Docket.

## Set it up

`docket source new plaud --example --vault <vault>` copies this folder to `.claude/skills/sources/plaud/` in the repo
that holds the vault and adds the source to `vault.yaml`, after you type back the code it shows; run it in your own
terminal, as `/docket:setup` tells you. By hand, the same two steps:

1. Copy this folder to `.claude/skills/sources/plaud/` in the repo that holds the vault.
2. In `vault.yaml`, add the source:

```yaml
sources:
  plaud:
    sensitivity: mixed            # calls can be private, so every record waits to be classified
    mapper: .claude/skills/sources/plaud/mapper.py
    tools: [list_files, get_file, get_transcript, get_note]
```

Then commit `vault.yaml`: a source added by hand applies once it is committed.

Then run its tests where it was copied: `pytest .claude/skills/sources/plaud/tests`. The tests that go through
Docket's own checks run only where `docket` can be imported, and are left out elsewhere.

After upgrading Docket, copy this folder's `SKILL.md` and `mapper.py` over your copy: Docket refuses a run of a
`SKILL.md` without the marker line at its top.

## A run

Docket gives the run's steps: `run_open("plaud")` opens the run, names this file and says what to skip, what is owed,
the budget and how to close. Claude never retypes a payload; the capture hook hands Docket what each call returned.
This file holds only what is Plaud's own.

| Part | What |
|---|---|
| Sign in | "not authenticated" means the server's `login` tool |
| List | `list_files` newest first, without date filters (they aren't exact), until a page's oldest `start_at` is before the window's start |
| Each recording | every page of `get_transcript` for `transaction_polish` with `limit` 100, following `next_cursor`, else for `transaction`; then `get_note` |
| List again | `list_files` once more after the last `get_note`: Plaud renames a recording when it writes its notes, and the newest listing wins |
| By id | for a retry or waiting id: `get_file`, then its transcript and its notes as above; "not found" or an error means it is gone: skip it as `gone` |
| Rate limits | `429 Too Many Requests`: pause and retry with growing pauses; still limited in the listing, stop without closing; on one recording, skip it as `rate-limited` |

A page of 100 utterances stays under the 50,000 characters past which Claude Code hands the hook a file note instead
of the result; a larger `limit` makes long calls too large to keep.

A backfill, for recordings fetched before Docket: first `docket ingest plaud <folder> --vault <vault>` in the user's
own terminal, which reads the folder's `hold.txt` and lets a run name the folder. Then `run_open("plaud",
since=<the oldest backfill day>)`, `list_files` paged down past the oldest backfill recording and no other call, and
`ingest(run, captures=<n>, folder=<folder>)`, without `final`: the files get their start times from that listing, and a
backfill file is final by itself. A recording the listing shows and the folder lacks waits to be fetched by id.

Recordings with stretches their owner left out go in `hold.txt` beside the backfill files before the first import, and
stay out until they are released with their cuts. `/docket:cuts` places the owner's words for each stretch on its
transcript and hands over `docket hold release plaud --from <folder> --proposals <file>`, which the owner runs in their
own terminal: each recording goes in only when they type back the code it shows. Plaud's notes of a released recording
are left out unless the owner keeps them, since they summarise the stretch too.

## What it makes

Three records per recording, each with the recording's rid as thread and container:

| Record | rid | When |
|---|---|---|
| the recording | `plaud:<file id>` | with its notes, or alone once final |
| its transcript | `plaud:<file id>/<block>` | when its page chain is complete; an empty one stages nothing |
| Plaud's notes | `plaud:<file id>/summary` | when a tab has content |

Only a recording the run fetched by id, or read from a backfill file, gives records, and only those are ever final. One
the run only listed waits to be fetched when it falls in the run's window, and gives nothing outside it. Held
recordings are skipped before anything of them is parsed, and nothing the mapper reports carries a title.

## Changing the mapper

Every record the lookback reads again gets a new version when the mapper's output changes, and the gate asks about it
again.

- Keep the output stable: change it only for a real change in what Plaud says.
- Pin golden versions in the tests, as `tests/test_with_docket.py` does, so a change shows before it runs.
- Pull the project on every machine before its next sync, so two machines never map one recording two ways.
