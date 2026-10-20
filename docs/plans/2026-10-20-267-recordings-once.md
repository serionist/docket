---
title: Read each recording once, in an agent of its own (one-way door)
issue: "#267"
status: approved
approved_by: claude (autonomy grant)
approved_at: 2026-10-20
---

# Read each recording once, in an agent of its own

Each recording is fetched once and judged once. Its source's run fetches it, and the classify works from that fetch;
then one agent of its own, launched as soon as the source's agent is back, reads it from a file in the sync folder,
writes its block lines to a block file of its own and puts its note. The note agents go. One PR.

## Goal

- Done: the third rehearsal reads each recording once, its slowest step takes under 8 minutes, the whole sync spends
  fewer tokens than the old sync and finds every ask the old sync found, and a normal day's recording source's agent
  takes under 8 minutes too (Success, below).
- Fetched once, judged once is the floor: the capture hook needs every transcript page as a call result, and the
  gate wants a recording's file made from raw after the promotion.
- In: the recording agents and their files, `docket sync recordings`, the reconcile reading their blocks, a compact
  `docket staging show`, the classify from the fetch, the pages fetched side by side, the two misses from the second
  rehearsal, where a copied `SKILL.md`'s route lessons go, and Sonnet for the recording agents as a measured trial.
- Out: the block's labels, `read`, `ledger_batch`, the note method's checks, the run's 400 KB budget.

## Design

| Step | Second rehearsal | After |
|---|---|---|
| start | `context.md` lists the recordings with no note | it doesn't: the recording step lists them after the run |
| the recording source's agent | fetches each recording's pages one after another, classifies through `docket staging show`, promotes, then reads every recording whole in 15-minute `read`s and writes their lines | fetches the pages side by side, classifies from them, promotes, writes RUN (and LEARNED); writes no line from a recording |
| recordings | none | as each source's agent is back, `docket sync recordings <sync> <source>` writes each of its recordings' files and prompt; one agent each, all in one message, on Sonnet |
| a recording's agent | the note agent read it all again | reads its files whole with Read, writes its block, puts its note, answers one line |
| reconcile | after the source agents | after every agent, the recordings' too, reading their blocks |

### The recording source's agent

What a new recording cost before: the fetch's pages, then `docket staging show`, which prints the record as indented
JSON with both `segments` and `native.segments` and then its text again, about four times the text, 150k to 350k
characters for a 30 to 60 minute call, far past the 30k a Bash result keeps.

| Part | Change |
|---|---|
| the fetch | Plaud's `SKILL.md`: the recordings' page chains side by side, one message holding the next `get_transcript` page of each, at most four chains at once, so the turns are the longest chain, not the sum, and a heavy day stays near its budget. Captures number as they arrive and the mapper chains by cursor |
| the classify | `agent.md`'s step 3: each record the run fetched is judged from its pages, already in the agent's context with the same segments; a page's `start_time` and `end_time`, in milliseconds, make the `--cut mm:ss-mm:ss` (754000 is 12:34). `docket staging show` only for a record the run didn't fetch, a new version of one classified before, or a cut by characters |
| `docket staging show` | a record with segments shows them once, a segment a line, `@mm:ss-mm:ss Speaker: text`, with no `segments`, `native` or `text` in its JSON; the after-cuts and cut-before parts the same. A text record keeps its character offsets. The gate is unchanged |
| its lines | `agent.md`'s Find step: a recording source writes no line from a recording; its recordings' agents do |

The run's budget, 400 KB of results, stands: a heavy day leaves some recordings `later` for the next run under any
design.

### When the recording agents start

A second wave, launched the moment the recording source's agent is back, while the other sources still run.

- Not in the first launch: a run's new recordings reach raw only when its agent promotes them, and a recording's file
  comes from raw, which is the gate. `docket sync start` can't list them.
- One path for new recordings and the backlog: a recording in raw from before is listed in the same call, after its
  source's newest version is in.
- The skill runs `docket sync recordings` for each source as its agent comes back; a source with none says so in a
  line. Where Claude Code returns a message's agents together, the wave starts after all of them: slower, the same
  result.
- The command lists the source's recordings in raw, in its window, that no current note covers, as start did (DR-0721's
  scope), after bringing the index up to date. A recording given an agent in this sync isn't given another.
- A recording with a record still in staging, pending, ready or held, is skipped and said: its note would come from
  what is in raw alone, a summary without its transcript, and would then cover the recording for good. The next sync
  lists it again once it is all in raw.

### The recording's files

| What | Rule |
|---|---|
| where | `recordings/<source>/<key>.<n>.txt` in the sync folder, n from 1; `recordings/<source>.json` lists the source's recordings, each call writing its own source's file, so two sources' calls never lose each other's; a source with none writes none |
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
| Sonnet | a trial, said in the skill. The rehearsal changes the design and the model at once, so for each ask the old sync found from a recording and the third rehearsal misses, that recording's prompt runs again on the default model on a fresh copy: the default finding it puts the default back |
| the notes step | goes, with `docket sync notes`; the summary waits for every agent |
| LEARNED | a route line goes into the source's `SKILL.md`, unless that `SKILL.md` is a copy of one of Docket's examples (its title says Docket's reference source, or a Docket example source), which an upgrade copies over: then into `sync.md`, under a part `Route` |
| pre-approvals | `docket sync recordings *` in place of `docket sync notes *` |

The LEARNED rule takes `sync.md` over a section the copy keeps: the copy is the owner copying files by hand, as
Plaud's `SKILL.md` says, so nothing could keep a section, and `sync.md` already survives it (DR-0688).

### The reconcile

- It reads the source blocks and every block the `recordings/<source>.json` files name, quoting each line by its
  file, as `blocks/plaud/rec-0101.txt:3`; a recording with no block file is said.
- New, in the table's log rows: a line whose continues ref names an open item whose title isn't a DR-0642 look-alike
  of the line's, and whose locator's rid or thread is in none of the item's trail, is unsure. The log stays the op,
  the add is its alternative, and the item's `context.md` line is quoted. A thread that is its record's container (a
  chat, a recording) never backs it, as P1 has it. The case: an ask to put the lane calls in the team's calendars,
  logged as sure on an item about sending the lane leads the sprints.
- The other rules stand, P1 to P9 among them.

## Doors and data

Data: none in the vault. The sync folder gains `recordings/`; it is scratch (DR-0720).

| Decision | What | Cost |
|---|---|---|
| supersedes DR-0720 | the sync folder: as it stands, but `context.md` lists no recordings, `docket sync recordings` adds each recording's files and prompt and its source's list, and `agent.md`, `recording.md` and `block.py` ship together | medium |
| supersedes DR-0721 | the agents: as it stands, but the recording source's agent classifies from its fetch and writes no line from a recording, each recording gets an agent of its own as above, the note agents and `docket sync notes` go, and the reconcile waits for every agent | medium |
| new | a recording agent's block is `blocks/<source>/<key>.txt`, the key as above; DR-0722's labels and fields, a line gaining fields only at its end | one-way |
| supersedes DR-0723 | the reconcile: as it stands, but it reads the recordings' blocks, and a continues ref whose item's title isn't a look-alike, with no locator or thread in its trail, is unsure, the add its alternative | medium |
| supersedes DR-0688 | `sync.md`: as it stands, but a route lesson for a `SKILL.md` copied from Docket's examples goes into `sync.md`, under `Route` | one-way |
| new | the recording agents run on Sonnet, a trial decided by rerunning each missed recording's prompt on the default | cheap |
| new | `docket staging show` prints a transcript once, a segment a line with its times, and no segments or native in its JSON | cheap |

- All proposed by "Claude (autonomy grant)" with `uv run docs add` and `supersede` in the PR; the ids come then.
- Stand: DR-0722 (the labels and fields), DR-0724, DR-0725, DR-0642, DR-0651, DR-0605.
- AGENTS.md's door line gains the block file's decision and DR-0688's successor, within its 150 lines.
- Not chosen: the recording agents in the first launch (their recordings aren't in raw yet); the source agent writing
  the files (it would carry them in its context); one agent for a meeting's two recordings (rare, and the reconcile
  already marks a second OUTCOME unsure to join them); the note agent kept apart (the second read the issue measured);
  `sync.json` gaining the recordings (two sources' calls at once could lose one's).

## Steps

| Commit | What | Test |
|---|---|---|
| 1 | the compact `docket staging show` | `test_cli.py` |
| 2 | `docket sync recordings`, the files, `recording.md`, the source lists; `notes` and `note.md` go; start lists no recordings | `test_sync_recordings.py`, `test_sync_folder.py` |
| 3 | the reconcile reads the recordings' blocks; the continues ref rule | `test_sync_reconcile.py` |
| 4 | `agent.md`, the skill, Plaud's `SKILL.md`, the docs and decisions | `test_plugin_files.py`, `gate full` |

## Tests

Invented people and data only, on `test_plugin_files.py`'s pilot ledger unless named.

| File | What |
|---|---|
| `test_cli.py` | `docket staging show` of a transcript: each segment once as `@mm:ss-mm:ss Speaker: text`, no `segments` or `native` in its JSON, a time read off a line cuts that segment; the after-cuts and cut-before parts as lines; a text record's offsets as today |
| `test_sync_recordings.py`, new | per recording of the source in raw, in its window, with no note: its files and prompt; one with a note left out; one with its transcript still in staging skipped and said; a cut stretch and a forgotten record absent from the file; parts under 40,000 bytes split between segments, a long line wrapped; the head lines and anchors; the source's list, written only when it has one; a second call gives no second agent; two sources' lists apart; an unknown source refused; a source with none says so; the key for an id with odd characters; the meetings by time in the prompt, a cancelled one and the owner's own block left out |
| `test_sync_recordings.py`, the rehearsal case | two recordings: the pilot weekly at 10:01 in M-0001, long enough for two parts, and a call with Cleo at 15:00 in no meeting. Start, a Plaud block of RUN alone, `docket sync recordings`: two prompts, three files. The blocks as the agents would write them: the weekly's MINUTES and OUTCOME, "Cleo sends the quote and the PO by Friday" in the OUTCOME and as a WAITING line, "Put the pilot calls in the team's calendars" continuing A-0001 "Send Ben the pilot dates", NOTES; the call's CLOSES and NOTES. The reconcile drafts both, the calendars line unsure with its add, the link for the weekly only, and the draft settled dry-runs clean |
| `test_sync_reconcile.py` | the continues ref rule: an unlike title with nothing in the trail unsure, the add its alternative and the item's line quoted; an unlike title in the item's mail thread sure; a look-alike title sure; a chat's or recording's container never backs it; the log after a close's evidence held to the same rule; a recording with no block said; lines quoted by their files; the P1 to P9 tests unchanged |
| `test_sync_folder.py` | `context.md` without the recordings part; the `notes` tests go |
| `test_forget_apply.py` | a forget's apply removes a sync folder holding a recording's files |
| `test_plugin_files.py` | each label's fields in `agent.md` and `recording.md` as `block.FIELDS` has them, all eleven between the two, a shared label's line the same in both; placeholders filled equal those used; the pre-approvals exactly; the launch line with `model: sonnet` and the trial's sentence; the OUTCOME rule's sentence; the LEARNED rule; `agent.md`'s classify from the fetch and its sentence that a recording source writes no line from a recording |
| `perf` | `docket sync recordings` for a two-hour recording of 1,500 segments under 1 s, best of three |

## Docs

- `plugin/README.md`'s sync section: the recordings row replacing the notes, the allow rules naming the recording
  agents; the package README's commands; `docs/design.md`'s sync line; the CHANGELOG; `plugin.json`'s version.
- Reading lists by `uv run docs`: `note.md` removed, `recording.md` added to `packages/docket/index.yaml`.
- Plaud's `SKILL.md` and its template: the chains side by side, the classify from the pages, and after the upgrade
  line, the route notes a sync learned live in `sync.md`.

## Risks

| Risk | Resolved or accepted |
|---|---|
| Sonnet misses asks the default finds | each miss reruns its recording's prompt on the default; the default finding it puts the default back |
| The source agent still passes 8 minutes on a normal day | the classify reads nothing new, the pages come side by side, and the normal-day measure below watches it |
| A heavy day passes the run's 400 KB budget | accepted: the recordings past it wait `later` for the next run, under any design |
| The continues ref rule makes more unsure lines | a mail or Slack thread in the item's trail keeps a reworded ask sure; the rehearsal counts the unsure lines |
| Two recordings of one meeting make two notes and two OUTCOME lines | accepted: the reconcile marks the second unsure, to join them |
| Two agents pick one note id | `doc_put` refuses a new doc on a taken id; the prompt adds `-2` |
| A long gap brings many recordings, and as many agents at once | as the note agents before; accepted |
| `docket sync recordings` runs before its source promoted, or a transcript waits in staging | it reads raw only, and skips a recording with a record in staging; the next sync lists either again |
| The recording agents ask for permission in the background | the README's rules cover them, as they covered the note agents |

## Success

The third rehearsal, over the same window as the second, on a copy of the live clone set back as before.

| Measure | Holds when |
|---|---|
| reads | each recording read whole by one agent, from its files; no agent reads a transcript in raw beyond a moment, and the classify reads no transcript it fetched |
| time | the slowest step, an agent or the reconcile with its batch, under 8 minutes; the whole sync also noted against the old sync's 18.1 and the second rehearsal's 20.3 |
| a normal day | the copy with 2 to 4 of its newest recordings taken out of raw, or the first live sync after the build: the Plaud agent, fetching and classifying them, under 8 minutes |
| tokens | every agent's tokens summed, the main thread's too, fewer than the old sync's on the same window |
| asks | every ask, wait and close the old sync wrote for that window in the batch, on the same item; no add doubling an open item |
| model | each recording ask missed reruns on the default, as the skill says; the trial stands only when the default misses it too |

A miss leaves the issue open with the step that missed.

## Plan review

Round 1, 20 Oct, of 449ef65: ready once one major is fixed; one major and four minors, all fixed here.

| Severity | Finding | Fixed |
|---|---|---|
| major | the 8-minute measure fails on a normal day and Success can't see it: a new recording went through the fetch, then `docket staging show`, about four times the text, then its file, and the Plaud agent stays at 10 to 15 minutes for 3 to 6 new recordings | a compact `staging show`; the classify from the fetch, `staging show` only for what the run didn't fetch; the pages side by side, at most four chains at once for the budget; a normal-day row in Success; the goal as fetched once, judged once; the 400 KB budget noted |
| minor | nothing stopped the Plaud agent reporting from its fetch | `agent.md`'s Find step: a recording source writes no line from a recording; a `test_plugin_files.py` row |
| minor | `sync.json` could lose a source's recordings when two sources' calls ran at once | each call writes its own `recordings/<source>.json`, none for a source with none; the reconcile reads those |
| minor | the Sonnet trial couldn't be decided, the rehearsal changing the design and the model at once | each missed recording ask reruns its prompt on the default on a fresh copy, in the plan and the skill |
| minor | a transcript left pending was never read: its recording listed, its note from the summary alone, covering it for good | `docket sync recordings` skips a recording with a record in staging, and says so |

The second wave, the block path, the file caps, the note ids, the lock, the two notes per meeting, both misses and
the LEARNED rule checked out.

## Built

One PR, as planned: DR-0746 to DR-0752 proposed, DR-0720, DR-0721, DR-0723 and DR-0688 superseded. Where the build
departed from the plan, and why:

| Where | What | Why |
|---|---|---|
| the ids | DR-0746 the sync folder, DR-0747 the agents, DR-0748 the block file, DR-0749 the reconcile, DR-0750 `sync.md`, DR-0751 the Sonnet trial, DR-0752 the compact `staging show` | the next ids when the docs tool added them; `supersede` writes both sides after the add |
| the key | a recording's own id shaped like a hashed key, `x` and 16 hex, is hashed too | otherwise one recording's own id could be another's hashed key |
| the window | the recordings listed run from the source's window's start to now, not to the start's end | a recording made while the sync runs is brought by the run and has no note |
| the block's folder | `docket sync recordings` makes `blocks/<source>/` | the agent's Write lands in a folder that is there |
| a recording's block | no RUN line; the agent answers its one line instead | it runs no source; the reconcile says a block file that isn't there |
| the meetings by time | `reconcile.Owner`, `overlapping` and `recorded_in` at module level, shared by the prompt and the NOTES link | the prompt names the meetings the link takes, by one rule |
| the summary | says the notes not put and why, in place of the notes still being written | the summary now waits for every recording agent |
| the rehearsal case | in `test_sync_recordings.py`, its draft settled and dry-run through `docket ledger batch` | the files and both blocks come from the recordings step there |
| the perf test | a recording of 1,500 segments, 0.05 s here, the budget 1 s | a recorder's long day |
| the index | `docket sync recordings` brings the index up to date before it lists, as a search does | a promotion just made is in raw before the index has it; the index is derived, so the pre-approval stands |

## Review, round 1

20 Oct, on 1a7f995: approved, no blocker or major; two minors, both fixed here, and the missing-block test the plan
listed added.

| Finding | Fixed |
|---|---|
| minor: a cut read off a `staging show` line took the work segment before it too, since the line rounded its start down to the whole second and a cut takes every segment it overlaps: Ben's work line at 12:34 to 12:40.5 went with Cleo's private one shown as 12:40 | each time keeps its milliseconds where it isn't a whole second, as `cuts.exact` writes them for `parse` (`@12:40.500-12:45`); a segment with no length ends a millisecond on; `agent.md` and Plaud's `SKILL.md` say the same of a page's times; a test with back-to-back segments, a sub-second one and one with no length, in `test_cli.py` |
| minor: `agent.md`'s step 1 still said `context.md` lists the recordings with no note | the words are gone |
| the plan's "a recording with no block said" had no test | `test_sync_recordings.py` holds it |
