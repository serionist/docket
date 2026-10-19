---
title: Add the plugin's sync and tasks skills (one-way doors)
issue: "#209, #210"
status: draft
approved_by:
approved_at:
---

# Add the plugin's sync and tasks skills

`/docket:sync` runs each live source in an agent of its own through `run_open`, then writes what they found into the
ledger in one `ledger_batch` by `sync`; `/docket:tasks` opens the Queue page and works the queue in chat, one item at a
time. Both are markdown under `plugin/skills/`, with no new tool, op or command. Two PRs, the sync first.

## Goal

- Done: the owner's old sync and tasks skills can point here and lose nothing: closes on evidence, owed and awaited
  asks, invites, minutes and outcomes, proposed points, rollover, streams; triage on the page, the queue in chat.
- Out: the Outbox, scheduled syncs, digests for the old harvest files, briefs before meetings, forget (#182).

## Design

### /docket:sync (#210, DR-0686)

| Step | Who | What |
|---|---|---|
| sources | the skill | `docket ledger day --json`'s `syncs`: every live source in `vault.yaml` (tools listed, on, not private), or those named as arguments, each with its skill under `.claude/skills/sources/<name>/`; the service down, it stops and says so |
| agents | one per source, launched in one message | `fork.md` filled with the source, the vault and the day; each reads the open items itself with `ledger` |
| run | each agent | `run_open`, the source's `SKILL.md` and its `sync.md` when there is one, `ingest` and the final run, as the run's steps say |
| classify, promote | each agent, its own source | a mixed source's records plainly work or plainly private, a non-work stretch cut, `--by claude`, an unsure one left for the Staging page; then `docket staging promote --source`, so its rids resolve and the calendar's records make their meetings (#224) |
| notes | a recording source's agent | a `recording-note` per work recording with `doc_put`, #211's method: the vendor's summary and the whole transcript read, every line anchored, `sync.md`'s layout |
| find | each agent | over what its run read, older context from `search` and `read`: the return block |
| reconcile | the skill | a fresh `ledger` snapshot, then one `ledger_batch`, `dry_run` first |
| summary | the skill | the old summary's groups in chat; the owner goes through them |

- An agent reads its source only inside its run, so what it reports is what Docket keeps; a run that stops short
  leaves its mark, the others go on. Cursors replace the old state file; the raw and the summary, the harvest files.
- No quick mode: a run reads a day back for chat and mail. The owed check covers the run's window, or 40 working days
  of the vault while the ledger holds no item.

### The return block (DR-0687)

Each agent returns this and nothing else long, about 800 words. A locator is a rid, built as the source's `SKILL.md`
says under "What it makes", with its anchor; `read` opens it.

```
RUN:      <source> | <run> | captures <n> | ready <n>, pending <n> | skipped <n> | mark moved yes/no | <a note>
OWED:     <who> | <rid> | <asked, a day> | <the ask, short> | <why still open> | today / this week / whenever
INVITES:  <organiser> | <event rid> | <when> | <clashes>
WAITING:  <who> | <rid> | <since> | <what the owner waits for>
CLOSES:   <A- ref> | done / moot | <rid of the evidence> | <how, one line>
MEETINGS: <M- ref> | response=declined / cancelled | <rid of the evidence>
NOTES:    <doc id> | <recording rid> | <start> | <end> | <M- ref when its content names one>
MINUTES:  <M- ref> | <point id> | answered / dropped / open | <the answer, one line> | <rid@mm:ss>
OUTCOME:  <M- ref> | <2 to 4 short lines: what was decided, who does what> | <rid>
LEARNED:  SKILL.md / sync.md | <what differs from it, for next time>
```

- RUN replaces the old DOCKET line; FILE goes with the harvest files. MEETINGS no longer makes meetings: the calendar's
  records do at promotion, keyed by event (DR-0647); it carries what the calendar can't show, a decline seen in mail.
- A recording's agent answers the open points of its meeting, which the `meetings` view finds near its start.

### The reconcile (DR-0689)

One batch, `by: sync`, in this order. Each refusal the dry run lists is fixed (a look-alike gets a `log` on the item it
names), then the batch is written. No proposal queue: the summary names the batch, so the owner can undo it whole.

| Order | Ops | Rule |
|---|---|---|
| closes | `done`, `drop` | only on a CLOSES line, its rid in `note`; nothing closes for being old |
| asks | `log`, `set`, `add` | OWED, INVITES and WAITING, one item per real ask: an open one gets a `log` with the new rid and a moved due; a new one an `add`, untriaged (the Inbox), `source` the rid the answer goes to, the other rids in `note`, `opened` the ask's day, `due` only a real date, a `stream` the ledger holds |
| meetings | `set` | each MEETINGS line |
| links | `material`, `fields.meeting` | DR-0651: a recording on the meeting the `meetings` view finds 10 minutes either side of its start, by content when two match; an ask from someone in a meeting, made during it or up to 120 minutes after; never a cancelled meeting or focus time |
| minutes | `point_set`, `set` | each MINUTES line by point id, then OUTCOME as the meeting's `outcome`; the vendor summary's guessed owners and dates stay out |
| rollover | `rollover` | rolls what is due; a meeting it names, over with open points and nowhere to go, goes in the summary |
| prep | `point` with `proposed`, `add` | the next 5 working days' meetings the owner presents in or owns a part of, customer meetings, new series: `suggest`'s items the owner would raise, each a question for that call, none the dry run shows rolled in; none for dailies; a prep `add` only for real work before the call |
| streams | `set` on a stream | only when the news moves where it stands or its next step |
| archive | `archive` | last, with no item |

### The summary in chat (DR-0690)

| Group | What |
|---|---|
| Meetings and prep | each meeting the owner has to do something for, with its open items and points, those proposed this run marked; the outcomes written |
| Today; This week; Whenever | open items by due: today or overdue, this week, the rest |
| Invites; Waiting on others | as the old summary listed them |
| After the list | what changed and what the sync closed by itself; sources that didn't run and why; the rollover's leftovers; LEARNED lines written and where; the batch to undo; stale briefs and proposed decisions, once |

- The owner answers in free text ("12 done, 14 drop, draft 3"), written as one batch `by: user`, or says "let's do
  tasks". Nothing is drafted before that, and the agents stay read-only.
- A message is a draft: a chat's draft tool, never its send; a mail left as a draft, sent only on the owner's word for
  that mail, never where the owner's rules say drafts only.

### /docket:tasks (#209, DR-0691)

| Step | What |
|---|---|
| open | `docket open /queue`; a sync the session start names as due, said once |
| triage | the owner's, on the page (keys 1 to 4, done, drop, snooze) or in chat as one batch `by: user`; Claude reads ahead and waits for "go" |
| order | `ledger` view `queue`: the `me` items in one line, then discuss, claude, chase, each by due, one at a time |
| each item | `show`; its source and log locators read only as far as the facts need, a recording's summary and transcript around the moment; whether it already happened; three to five lines and the next step |
| by verdict | discuss: talk, one short question round for a crisp choice; claude: draft or do it, a draft only the owner can send logged and triaged `me`; chase: the nudge drafted and logged, triage cleared; a prep item: points on its meeting, `keep` for proposed ones, `done` with "n points on M-"; a point that is work: `point_item` |
| record | one `ledger_batch` per item before the next, `by: claude` for what Claude did, `by: user` for what the owner decided: `log`, `done` with `outcome`, `drop`, `snooze`, `triage`, `set` of due, a `material` for a draft ("draft, not sent"), `notes` for work that goes on |
| end | three lines: closed, drafts waiting and where, what is left; the page stays open |

- The page and chat share only the ledger: the page reloads within 5 s of an op file changing (#203), and Claude
  reads the queue and each item again before it writes. Without the service: `docket ledger queue`, `show`, `batch`.
- Files: `sync/SKILL.md` with a worked batch, `sync/fork.md`, the agent's prompt, and `tasks/SKILL.md`. Each
  pre-approves reads only (DR-0605): `ledger`, `search`, `read`, `verify`; the sync `docket ledger day` and `docket
  staging list` and `show`, tasks `docket open`.

## Doors and data

Set for good, each PR's title naming its part, `cost: one-way`:

- the skill names `sync` and `tasks` (DR-0685), on AGENTS.md's skill line;
- the return block's labels, each line gaining fields only at its end (DR-0687);
- a source's `sync.md`, optional, beside its `SKILL.md`: the owner's lore for finding there (what counts as an ask,
  evidence, names it mishears, a note's layout), LEARNED lines about the route going to `SKILL.md` (DR-0688).

`medium`: DR-0686, DR-0689 to DR-0691; `cheap`: DR-0692. Not doors: the wording, `fork.md`. Data: none.

The cut-over (DR-0692): the owner's old skills keep their names and descriptions, so "sync" and "let's do tasks" still
reach them, and each body becomes one line, "Run `/docket:sync` (the Skill tool, `docket:sync`) with the same
arguments". The old fork files' lore moves into each source's `sync.md`; a week later the old skills go.

## Steps

1. PR 1, "Add the plugin's sync skill: reconcile into the ledger (one-way door)" (#210): `sync/SKILL.md`, `fork.md`,
   tests, docs; DR-0685's `sync`, DR-0686 to DR-0690, DR-0692.
2. PR 2, "Add the plugin's tasks skill (one-way door)" (#209): `tasks/SKILL.md`, tests, docs; DR-0685, DR-0691.

Sync first: it stands on main as it is, and its summary is where tasks starts; tasks, second, can name #204's `/queue`
(still waiting, it opens `/ledger` and #204's PR changes that line).

## Tests

- `test_plugin_files.py`: each skill pre-approves only the reads above; every `docket` command and option the skills
  and `fork.md` name exists; every op and view they name is in `ledger-batch.json` and the tool's views; the block's
  labels in `SKILL.md` and `fork.md` equal the test's list; AGENTS.md names both skills.
- Each skill's worked batch, invented people, through `docket ledger batch --dry-run` on the fixture vault: no refusal.
- Before each merge, a rehearsal on the live clone's imported ledger beside the old skill, the same day.

## Docs

`plugin/README.md`'s table gains both skills and loses "arrive in later phases"; the package README's Live runs names
`/docket:sync` and `sync.md`; `docs/design.md`'s Claude first section; AGENTS.md's door lines; `plugin.json`'s version
per PR (CV-0012); a CHANGELOG line per PR; DR-0685 to DR-0692, proposed by Claude.

## Risks

- As the old sync: agents share one browser, a tab each; Claude classifies plain cases only. Accepted.
- A rid built wrong stays a locator `read` can't open: an agent reads one rid of each shape before it reports. Accepted.
- Two agents promoting at once wait on the vault's lock: a promote that times out runs again a minute later. Accepted.
- A proposed point repeating one rollover carries: the dry run shows the rolled copies first. Resolved.

## Plan review

Not yet: `review-plan` writes this.
