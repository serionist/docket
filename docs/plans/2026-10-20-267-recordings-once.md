---
title: Read each recording once, in an agent of its own (one-way door)
issue: "#267"
status: draft
approved_by:
approved_at:
---

# Read each recording once, in an agent of its own

Each recording that needs reading gets one agent, launched as soon as its source's agent is back: it reads the
recording from one file the sync folder holds, writes its block lines to a block file of its own and puts its note.
The source's agent only runs, classifies and promotes, and the note agents go. One PR.

## Goal

- Done: the third rehearsal reads each recording once, its slowest step takes under 8 minutes, the whole sync spends
  fewer tokens than the old sync and finds every ask the old sync found (Success, below).
- In: the recording agents and their files, `docket sync recordings`, the reconcile reading their blocks, the two
  misses from the second rehearsal, where a copied `SKILL.md`'s route lessons go, and Sonnet for the recording agents
  as a measured trial.
- Out: the source run's own fetch (the capture hook needs every transcript page as a call result), classify's reads
  (Risks), the block's labels, `read`, `ledger_batch`, the note method's checks.

## Design

| Step | Second rehearsal | After |
|---|---|---|
| start | `context.md` lists the recordings with no note | it doesn't: the recording step lists them after the run |
| the recording source's agent | runs, classifies, promotes, then reads every recording whole in 15-minute `read`s and writes their lines | runs, classifies, promotes, writes RUN (and LEARNED); reads no recording |
| recordings | none | as each source's agent is back, `docket sync recordings <sync> <source>` writes each of its recordings' files and prompt; one agent each, all in one message, on Sonnet |
| a recording's agent | the note agent read it all again | reads its files whole with Read, writes its block, puts its note, answers one line |
| reconcile | after the source agents | after every agent, the recordings' too, reading their blocks |

### When the recording agents start

A second wave, launched the moment the recording source's agent is back, while the other sources still run.

- Not in the first launch: a run's new recordings reach raw only when its agent promotes them, and a recording's file
  comes from raw, which is the gate. `docket sync start` can't list them.
- One path for new recordings and the backlog: a recording in raw from before is listed in the same call, which
  comes a few minutes in, after its source's newest version is in.
- The skill runs `docket sync recordings` for each source as its agent comes back; a source with no recording says so
  in a line. Where Claude Code returns a message's agents together, the wave starts after all of them: slower, the
  same result.
- The command lists the source's recordings in raw, in its window, that no current note covers, as start did (DR-0721's
  scope), after bringing the index up to date. A recording given an agent in this sync isn't given another, so a
  second call is harmless.

### The recording's files

| What | Rule |
|---|---|
| where | `recordings/<source>/<key>.<n>.txt` in the sync folder, n from 1 |
| what | a head line: the rid, its title, its local start, part n of m, the anchors to write; then each record of the recording in raw at its latest version: the recording, the vendor's notes a paragraph a line with its `#n`, each transcript a segment a line, `@mm:ss Speaker: text` |
| the gate | rendered from raw through the lookup `read` uses, under the forgotten list in force: promoted records only, cuts already out, nothing held or private |
| size | a part is at most 40,000 bytes of UTF-8, split between segments, under the Read tool's 25,000 tokens; a line over 1,000 characters wraps, its rest indented, under Read's 2,000 |
| forget | inside the sync folder, which a forget's apply removes whole (DR-0720): checked, and a test holds it for a recording's file |

`<key>` is the recording's own id, the rid's part after `<source>:`, when it is 1 to 64 lowercase letters, digits, `_`
or `-`, as Plaud's are; else `x` and the first 16 hex of the rid's sha256. A source's name holds dots and hyphens, so
a folder per source keeps `blocks/plaud.txt` and a recording's block apart.

### The recording agent

`agents/<source>/<key>.md`, from the package's new `recording.md`, which replaces `note.md`:

1. One ToolSearch: `read`, `search`, `ledger`, `doc_put`, `verify`.
2. `context.md` whole, then each of its files whole, a Read each.
3. Its meetings: the prompt names those by time, found by the same code as the reconcile's NOTES rule (10 minutes
   either side, not cancelled, not the owner's own block). One: each open point answered as MINUTES, then OUTCOME.
4. Its lines, as the source agent's table says: OWED, WAITING, CLOSES, STREAMS, NOTES, MINUTES, OUTCOME, and LEARNED
   for `sync.md` only. New: a who-does-what in OUTCOME that falls on the owner, or that the owner waits on, also gets
   its own OWED or WAITING line.
5. The block to `blocks/<source>/<key>.txt` with the Write tool. Every locator is a moment or paragraph of the file
   it read whole, so no check read; the reconcile drops a line whose locator doesn't open.
6. The note by #211's method: people and terms searched back two weeks, at most three `search` calls, the open items
   and points from `context.md`, every line anchored, the layout from the source's `sync.md`, the summary view read
   just before `doc_put`, `verify` of every number, date and commitment. A new doc on an id already taken is refused
   (its rev), so two agents never overwrite one note: the agent adds `-2` and puts again.
7. The answer: `put <doc id> rev <n>, <k> lines`, or `not put <doc id>: <why>, <k> lines`.

The block goes before the note, so a note that fails still leaves the lines. `agent.md` loses its recordings step and
its NOTES, MINUTES and OUTCOME rows; the eleven labels stay in the block, split between the two prompts.

### The skill

| Part | Change |
|---|---|
| the waves | the source agents as now; as each is back, `docket sync recordings <sync> <source> --vault <vault>`, then one `Agent` call per `recording` line it prints, all in one message: `general-purpose`, `model: sonnet`, `description` `recording <key>`, the prompt `Read and follow <path>` |
| Sonnet | a trial, said in the skill: the next rehearsal measures it against the default, and it goes back to the default if it misses an ask the default finds |
| the notes step | goes, with `docket sync notes`; the summary waits for every agent |
| LEARNED | a route line goes into the source's `SKILL.md`, unless that `SKILL.md` is a copy of one of Docket's examples (its title says Docket's reference source, or a Docket example source), which an upgrade copies over: then into `sync.md`, under a part `Route` |
| pre-approvals | `docket sync recordings *` in place of `docket sync notes *` |

The LEARNED rule takes `sync.md` over a section the copy keeps: the copy is the owner copying files by hand, as
Plaud's `SKILL.md` says, so nothing could keep a section, and `sync.md` already survives it (DR-0688).

### The reconcile

- It reads the source blocks and every block `sync.json`'s `recordings` names, quoting each line by its file, as
  `blocks/plaud/rec-0101.txt:3`; a recording with no block file is said.
- New, in the table's log rows: a line whose continues ref names an open item whose title isn't a DR-0642 look-alike
  of the line's, and whose locator's rid or thread is in none of the item's trail, is unsure. The log stays the op,
  the add is its alternative, and the item's `context.md` line is quoted. A thread that is its record's container (a
  chat, a recording) never backs it, as P1 has it. The case: an ask to put the lane calls in the team's calendars,
  logged as sure on an item about sending the lane leads the sprints.
- The other rules stand, P1 to P9 among them.

## Doors and data

Data: none in the vault. `sync.json` gains `recordings` (each rid, key, prompt, block and files), its schema still 1;
the sync folder is scratch (DR-0720).

| Decision | What | Cost |
|---|---|---|
| supersedes DR-0720 | the sync folder: as it stands, but `context.md` lists no recordings, `docket sync recordings` adds each recording's files, prompt and `sync.json` entry, and `agent.md`, `recording.md` and `block.py` ship together | medium |
| supersedes DR-0721 | the agents: as it stands, but the recording source's agent reads no recording, each recording gets an agent of its own as above, the note agents and `docket sync notes` go, and the reconcile waits for every agent | medium |
| new | a recording agent's block is `blocks/<source>/<key>.txt`, the key as above; DR-0722's labels and fields, a line gaining fields only at its end | one-way |
| supersedes DR-0723 | the reconcile: as it stands, but it reads the recordings' blocks, and a continues ref whose item's title isn't a look-alike, with no locator or thread in its trail, is unsure, the add its alternative | medium |
| supersedes DR-0688 | `sync.md`: as it stands, but a route lesson for a `SKILL.md` copied from Docket's examples goes into `sync.md`, under `Route` | one-way |
| new | the recording agents run on Sonnet, a trial measured against the default in the next rehearsal and reverted on a missed ask | cheap |

- All proposed by "Claude (autonomy grant)" with `uv run docs add` and `supersede` in the PR; the ids come then.
- Stand: DR-0722 (the labels and fields), DR-0724, DR-0725, DR-0642, DR-0651, DR-0605.
- AGENTS.md's door line gains the block file's decision and DR-0688's successor, within its 150 lines.
- Not chosen: the recording agents in the first launch (their recordings aren't in raw yet); the source agent writing
  the files (it would carry them in its context); one agent for a meeting's two recordings (rare, and the reconcile
  already marks a second OUTCOME unsure to join them); the note agent kept apart (the second read the issue measured).

## Steps

| Commit | What | Test |
|---|---|---|
| 1 | `docket sync recordings`, the files, `recording.md`, `sync.json`'s `recordings`; `notes` and `note.md` go; start lists no recordings | `test_sync_recordings.py`, `test_sync_folder.py` |
| 2 | the reconcile reads the recordings' blocks; the continues ref rule | `test_sync_reconcile.py` |
| 3 | `agent.md`, the skill, the Plaud `SKILL.md` line, the docs and decisions | `test_plugin_files.py`, `gate full` |

## Tests

Invented people and data only, on `test_plugin_files.py`'s pilot ledger unless named.

| File | What |
|---|---|
| `test_sync_recordings.py`, new | per recording of the source in raw, in its window, with no note: its files and prompt; one with a note left out; a cut stretch and a forgotten record absent from the file; parts under 40,000 bytes split between segments, a long line wrapped; the head lines and anchors; `sync.json`'s entries; a second call gives no second agent; an unknown source refused; a source with none says so; the key for an id with odd characters; the meetings by time in the prompt, a cancelled one and the owner's own block left out |
| `test_sync_recordings.py`, the rehearsal case | two recordings: the pilot weekly at 10:01 in M-0001, long enough for two parts, and a call with Cleo at 15:00 in no meeting. Start, a Plaud block of RUN alone, `docket sync recordings`: two prompts, three files. The blocks as the agents would write them: the weekly's MINUTES and OUTCOME, "Cleo sends the quote and the PO by Friday" in the OUTCOME and as a WAITING line, "Put the pilot calls in the team's calendars" continuing A-0001 "Send Ben the pilot dates", NOTES; the call's CLOSES and NOTES. The reconcile drafts both, the calendars line unsure with its add, the link for the weekly only, and the draft settled dry-runs clean |
| `test_sync_reconcile.py` | the continues ref rule: an unlike title with nothing in the trail unsure, the add its alternative and the item's line quoted; an unlike title in the item's mail thread sure; a look-alike title sure; a chat's or recording's container never backs it; the log after a close's evidence held to the same rule; a recording with no block said; lines quoted by their files; the P1 to P9 tests unchanged |
| `test_sync_folder.py` | `context.md` without the recordings part; the `notes` tests go |
| `test_forget_apply.py` | a forget's apply removes a sync folder holding a recording's files |
| `test_plugin_files.py` | each label's fields in `agent.md` and `recording.md` as `block.FIELDS` has them, all eleven between the two, a shared label's line the same in both; placeholders filled equal those used; the pre-approvals exactly; the launch line with `model: sonnet` and the trial's sentence; the OUTCOME rule's sentence; the LEARNED rule |
| `perf` | `docket sync recordings` for a two-hour recording of 1,500 segments under 1 s, best of three |

## Docs

- `plugin/README.md`'s sync section: the recordings row replacing the notes, the allow rules naming the recording
  agents; the package README's commands; `docs/design.md`'s sync line; the CHANGELOG; `plugin.json`'s version.
- Reading lists by `uv run docs`: `note.md` removed, `recording.md` added to `packages/docket/index.yaml`.
- Plaud's `SKILL.md` and its template: after the upgrade line, the route notes a sync learned live in `sync.md`.

## Risks

| Risk | Resolved or accepted |
|---|---|
| Sonnet misses asks the default finds | the rehearsal compares ask by ask; a miss puts the default back |
| The source agent still passes 8 minutes: the run's fetch carries every transcript page, and on a normal day classify reads each new one through `docket staging show`, its segments as JSON and its text | accepted: the rehearsal's copy holds its recordings in raw, so it classifies none; a compact `staging show` is the follow-up if a normal day's source agent passes 8 minutes |
| The continues ref rule makes more unsure lines | a mail or Slack thread in the item's trail keeps a reworded ask sure; the rehearsal counts the unsure lines |
| Two recordings of one meeting make two notes and two OUTCOME lines | accepted: the reconcile marks the second unsure, to join them |
| Two agents pick one note id | `doc_put` refuses a new doc on a taken id; the prompt adds `-2` |
| A long gap brings many recordings, and as many agents at once | as the note agents before; accepted |
| `docket sync recordings` runs before its source promoted | it reads raw only, so a recording not yet in raw gets no agent now and is listed by the next sync, which finds it with no note |
| The recording agents ask for permission in the background | the README's rules cover them, as they covered the note agents |

## Success

The third rehearsal, over the same window as the second, on a copy of the live clone set back as before.

| Measure | Holds when |
|---|---|
| reads | each recording read whole by one agent, from its files; no agent reads a transcript in raw beyond a moment |
| time | the slowest step, an agent or the reconcile with its batch, under 8 minutes; the whole sync also noted against the old sync's 18.1 and the second rehearsal's 20.3 |
| tokens | every agent's tokens summed, the main thread's too, fewer than the old sync's on the same window |
| asks | every ask, wait and close the old sync wrote for that window in the batch, on the same item; no add doubling an open item |
| model | Sonnet's recording agents find every ask the default's would; else the default comes back |

A miss leaves the issue open with the step that missed.

## Plan review

Not yet: the plan waits for review before the build.
