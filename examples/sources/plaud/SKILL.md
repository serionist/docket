---
name: plaud
description: Harvest Plaud recordings into Docket with the user's own Plaud MCP. Use when a sync runs the Plaud source, or the user asks to fetch, catch up on or import Plaud recordings.
---

# Plaud, Docket's reference source

Reads Plaud with the user's own Plaud MCP and hands what it returned to Docket untouched; `mapper.py` next to this
file turns a run's captures into records. This folder is an example to copy, not part of Docket.

## Set it up

1. Copy this folder to `.claude/skills/sources/plaud/` in the repo that holds the vault.
2. In `vault.yaml`, add the source:

```yaml
sources:
  plaud:
    sensitivity: mixed            # calls can be private, so every record waits to be classified
    mapper: .claude/skills/sources/plaud/mapper.py
    tools: [list_files, get_file, get_transcript, get_note]
```

3. Run its tests where you copied it: `pytest .claude/skills/sources/plaud/tests`.

A live run needs Docket's service and capture hook; until then, recordings already fetched go in with
`docket ingest plaud <folder>`.

## A run

The capture hook posts each tool's result as it came; Claude never retypes a payload. The run's manifest counts the
captures, and Docket maps them when the manifest closes the run.

| Step | What |
|---|---|
| Sign in | "not authenticated" means the server's `login` tool |
| Skip | read the held ids and what raw and staging already hold through the service; skip those, to save calls |
| List | `list_files` newest first, without date filters, until a page's oldest `start_at` is before the window's start |
| Per recording | every page of `get_transcript` for `transaction_polish` (`limit` 500, following `next_cursor`), else for `transaction`; then `get_note` |
| List again | `list_files` once more after the last `get_note`: Plaud renames a recording when it writes its notes, and the newest listing wins |
| Rate limits | `429 Too Many Requests`: pause and retry with growing pauses; still limited, end the run, and the kept window resumes it |
| Waiting | an id the mapper reports waiting is asked for again on later runs, and after 7 days as final |

A backfill run, for recordings fetched before Docket: `list_files` paged down past the oldest backfill recording, no
other calls, its manifest naming the backfill folder. The files get their start times from that listing.

## What it makes

Three records per recording, each with the recording's rid as thread and container:

| Record | rid | When |
|---|---|---|
| the recording | `plaud:<file id>` | with its notes, or alone once final |
| its transcript | `plaud:<file id>/<block>` | when its page chain is complete; an empty one stages nothing |
| Plaud's notes | `plaud:<file id>/summary` | when a tab has content |

Held recordings are skipped before anything of them is parsed, and nothing the mapper reports carries a title.

## Changing the mapper

Every record the lookback reads again gets a new version when the mapper's output changes, and the gate asks about it
again.

- Keep the output stable: change it only for a real change in what Plaud says.
- Pin golden versions in the tests, as `tests/test_with_docket.py` does, so a change shows before it runs.
- Pull the project on every machine before its next sync, so two machines never map one recording two ways.
