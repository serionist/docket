---
title: Add the plugin's sync and tasks skills (one-way doors)
issue: "#209, #210"
status: approved
approved_by: claude (autonomy grant)
approved_at: 2026-10-19
---

# Add the plugin's sync and tasks skills

`/docket:sync` runs each live source in an agent of its own through `run_open`, then writes what they found into the
ledger in one `ledger_batch` by `sync`; `/docket:tasks` opens the Queue page and works the queue in chat, one item at a
time. Both are markdown under `plugin/skills/`; the one code change is a writer screen. Two PRs, the sync first.

## Goal

- Done: the owner's old sync and tasks skills can point here and lose nothing: closes on evidence, owed and awaited
  asks, invites, minutes and outcomes, proposed points, rollover, streams; triage on the page, the queue in chat.
- Out: the Outbox, scheduled syncs, digests for the old harvest files, briefs before meetings, forget (#182).

## Design

### /docket:sync (#210, DR-0686)

| Step | Who | What |
|---|---|---|
| sources | the skill | `docket ledger day --json`'s `syncs`: every live source in `vault.yaml` (tools listed, on, not private), or those named as arguments, an unknown name refused with the live ones listed; each skill under `.claude/skills/sources/<name>/`; the service down, it stops and says so |
| run | one agent per source, launched in one message | `fork.md` filled with the source, the vault and the day; the open items read with `ledger`; `run_open`, the source's `SKILL.md` and its `sync.md` when there is one, `ingest` and the final run, as the run's steps say; the cursors replace the old state file |
| classify, promote | each agent, its own source | a mixed source's records plainly work or plainly private, a non-work stretch cut, `--by claude`, an unsure one left for the Staging page; then `docket staging promote --source`, so its rids resolve and the calendar's records make their meetings (#224) |
| notes | a recording source's agent | a `recording-note` with `doc_put`, #211's method, for each work recording the run brought and each in the window already in raw with no note: the vendor's summary and the whole transcript read, every line anchored, `sync.md`'s layout; then the open points of its meeting, which the `meetings` view finds near its start, answered |
| find | each agent | over what its run read and the window's recordings in raw with no note yet, so their asks and outcomes come too; older context from `search` and `read`; owed asks 40 working days back while the ledger holds no item: the return block |
| reconcile, summary | the skill | a fresh `ledger` snapshot, then one `ledger_batch`, `dry_run` first, a write refused after a clean dry run taking a fresh snapshot and another dry run; an item's old source `read` can't open searched by its words around its day; then the old summary's groups in chat, which replace the harvest files with the raw |

- A line rests only on records in raw after the agent's own promotion, or docs in the vault: never a pending, private,
  dropped or held record, nor an anchor inside a cut. The agent `read`s every rid it reports.
- The ledger writer refuses an op whose `source` or a material's `ref`, its `r:` and anchor stripped, sits in a
  never-store container: Slack's and Teams' by the rid's text, any source's through the index (DR-0690).
- A run that stops short leaves its mark; the others go on. No quick mode. The owed check covers the run's window.
- The project's CLAUDE.md holds the owner's drafting rules, `who` convention, stream hints and shared-slot links. The
  plugin's two rules: `who` is the ledger's name for the person (`ledger` `list`, `who=`); drafts follow those notes.

### The return block (DR-0687)

Each agent returns this and nothing else long, about 800 words. RUN replaces the old DOCKET line; FILE goes.

```
RUN:      <source> | <run> | captures <n> | ready <n>, pending <n> | skipped <n> | mark moved yes/no | <a note>
OWED:     <who> | <locator> | <asked, a day> | <the ask, short> | <why still open> | today / this week / whenever
INVITES:  <organiser> | <the event's or the invite mail's rid> | <when> | <clashes>
WAITING:  <who> | <locator> | <since> | <what the owner waits for>
CLOSES:   <A- ref> | done / moot | <locator of the evidence> | <how, one line>
MEETINGS: <M- ref> | <field>=<value>; ... | <locator of the evidence>
STREAMS:  <stream> | <field>=<value>; ... | <the news, one line> | <locator>
NOTES:    <doc id> | <recording rid> | <start> | <end> | <M- ref when its content names one>
MINUTES:  <M- ref> | <point id> | answered / dropped / open | <the answer, one line> | <locator>
OUTCOME:  <M- ref> | <2 to 4 short lines: what was decided, who does what> | <locator>
LEARNED:  SKILL.md / sync.md | <what differs from it, for next time>
```

- A locator is one `read` opens: a rid with its anchor, built as the source's "What it makes" says, or a doc id or old
  path with its line or time. A line whose locator doesn't open is dropped.
- MEETINGS sets `response`, `attendees`, and `start` and `end` for a move seen only in mail (the calendar still makes
  meetings, DR-0647); STREAMS sets `status`, `where`, `next` and `deadline`. Invites with no response come from the
  calendar's agent, which ones matter from the mail agent.

### The reconcile (DR-0689)

One batch, `by: sync`, in this order. Each refusal the dry run lists is fixed (a look-alike gets a `log` on the item it
names), then the batch is written. No proposal queue: the summary names the batch, so the owner can undo it whole.

| Order | Ops | Rule |
|---|---|---|
| closes | `done`, `drop` | only on a CLOSES line, its locator in `note`; nothing closes for being old |
| asks | `log`, `set`, `add` | OWED, INVITES and WAITING, one item per real ask: an open one gets a `log` with the new locator and a moved due; an item a CLOSES line closes gets a `log` for an ask made before the evidence, an `add` only for one after; a new one an `add`, untriaged (the Inbox), `who` the ledger's name, `source` where the answer goes, the other locators in `note`, `opened` the ask's day, `due` only a real date, a `stream` the ledger holds |
| meetings | `set` | each MEETINGS line's fields |
| links | `material`, `fields.meeting` | DR-0651: a recording on the meeting the `meetings` view finds 10 minutes either side of its start, by content when two match; an ask from someone in a meeting, made during it or up to 120 minutes after; never a cancelled meeting or focus time |
| minutes | `point_set`, `set` | each MINUTES line by point id, then OUTCOME as the meeting's `outcome`; the vendor summary's guessed owners and dates stay out |
| rollover | `rollover` | rolls what is due; a meeting it names, over with open points and nowhere to go, goes in the summary |
| prep | `point` with `proposed`, `add` | the next 5 working days' meetings the owner presents in or owns a part of, customer meetings, new series: `suggest`'s items the owner would raise, each a question for that call, none the dry run shows rolled in; none for dailies; a prep `add` only for real work before the call |
| streams | `set` on a stream | only from STREAMS lines |
| archive | `archive` | last, with no item |

### The summary in chat (DR-0689)

| Group | What |
|---|---|
| Meetings and prep | each meeting the owner has to do something for, with its open items and points, those proposed this run marked; the outcomes written |
| Today; This week; Whenever; Invites; Waiting on others | open items by due (today or overdue, this week, the rest), then invites and waits, as the old summary listed them |
| After the list | what changed and what the sync closed by itself; sources that didn't run and why; how many records wait on the Staging page; the rollover's leftovers; LEARNED lines written and where; the batch to undo; stale briefs and proposed decisions, once |

- The owner answers in free text ("12 done, 14 drop, draft 3"), written as one batch `by: user`, or says "let's do
  tasks". Nothing is drafted before that, and the agents stay read-only. A message is a draft: a chat's draft tool,
  never its send; a mail left as a draft, sent only on the owner's word for that mail, never where their rules forbid.

### /docket:tasks (#209, DR-0691)

| Step | What |
|---|---|
| open, triage | `docket open /queue`, a sync the session start names as due said once; the owner triages on the page (keys 1 to 4, done, drop, snooze) or in chat as one batch `by: user`; Claude reads ahead and waits for "go" |
| order | `ledger` view `queue`: the `me` items in one line, then discuss, claude, chase, each by due, one at a time |
| each item | `show`; its source and log locators read only as far as the facts need, a recording's summary and transcript around the moment, a locator `read` can't open searched by its words around its day; whether it already happened; three to five lines and the next step |
| by verdict | discuss: talk, one short question round for a crisp choice; claude: draft or do it, a draft only the owner can send logged and triaged `me`; chase: the nudge drafted and logged, triage cleared; a prep item: points on its meeting, `keep` for proposed ones, `done` with "n points on M-"; a point that is work: `point_item` |
| record | one `ledger_batch` per item before the next, `by: claude` for what Claude did, `by: user` for what the owner decided: `log`, `done` with `outcome`, `drop`, `snooze`, `triage`, `set` of due, a `material` for a draft ("draft, not sent"), `notes` for work that goes on |
| end | three lines: closed, drafts waiting and where, what is left; the page stays open |

- The page and chat share only the ledger: the page reloads within 5 s of an op file changing (#203), and Claude
  reads the queue and each item again before it writes. Without the service: `docket ledger queue`, `show`, `batch`.
- Files: `sync/SKILL.md`, `sync/fork.md` (the agent's prompt), `tasks/SKILL.md`, each pre-approving reads only
  (DR-0605): `ledger`, `search`, `read`, `verify`, `docket ledger day`, `docket staging list` and `show`, `docket open`.

## Doors and data

Set for good, each PR's title naming its part, `cost: one-way`:

- the skill names `sync` and `tasks` (DR-0685), on AGENTS.md's skill line;
- the return block's eleven labels, each line gaining fields only at its end (DR-0687);
- a source's `sync.md`, optional, beside its `SKILL.md`: the owner's lore for finding there (what counts as an ask,
  evidence, names it mishears, a note's layout), LEARNED lines about the route going to `SKILL.md` (DR-0688).

`medium`: DR-0686, DR-0689 to DR-0691; `cheap`: DR-0692. Not doors: the wording, `fork.md`, CLAUDE.md. Data: none.

The cut-over (DR-0692): the owner's old skills keep their names and descriptions, each body one line, "Run
`/docket:sync` (the Skill tool) with the same arguments", old `mail` naming the mail and calendar sources, `quick`
dropped with a line saying so; the old bodies' lore goes to CLAUDE.md, the forks' to `sync.md`; a week on, they go.

## Steps

1. PR 1, "Add the plugin's sync skill: reconcile into the ledger (one-way door)" (#210): the writer screen in
   `TextRules`, `sync/SKILL.md`, `fork.md`, tests, docs; DR-0685's `sync`, DR-0686 to DR-0690, DR-0692. Before its
   rehearsal, Claude writes the live clone's `sync.md` files from the old fork prompts, the owner's step.
2. PR 2, "Add the plugin's tasks skill (one-way door)" (#209): `tasks/SKILL.md`, tests, docs; DR-0685, DR-0691.

Sync first: it stands on main, its summary is where tasks starts, and tasks gets #204's `/queue` (else `/ledger`).

## Tests

- `test_ledger_store.py`: a `source` or a material `ref` in a never-store container, by container and by thread, with
  `r:` and an anchor, refused naming the field, never the value; one outside it written; the import's dry run names
  it; a mail locator in a listed conversation written, its rid not carrying the id and its dropped record never indexed.
- `test_plugin_files.py`: reads-only pre-approvals; every `docket` command, option, op and view the skills and
  `fork.md` name exists; the block's labels equal the test's list; AGENTS.md names both skills.
- Each skill's worked batch, invented people, through `docket ledger batch --dry-run` on the fixture vault: no refusal.
- Before each merge, a rehearsal on the live clone's imported ledger beside the old skill, the same day.

## Docs

`plugin/README.md`'s skills; the package README's Live runs; `docs/design.md`'s Claude first and Tasks; AGENTS.md's
doors; `plugin.json` (CV-0012) and a CHANGELOG line per PR; DR-0685 to DR-0692, proposed by Claude.

## Risks

- As the old sync: agents share one browser, a tab each; Claude classifies plain cases only. Accepted.
- Every rid reported costs a `read`, dozens a sync; two promotes wait on the vault's lock, retried later. Accepted.

## Plan review

Round 1, 19 Oct, of a682400: one blocker, three majors, seven minors, all fixed here by the lead's calls.

| Id | Severity | Finding | Status |
|---|---|---|---|
| P1 | blocker | lines could rest on records Docket doesn't keep: never-store containers, unsure, private, cut or held ones | fixed: raw after the agent's promotion only, no anchor in a cut, every rid read, the Staging count in the summary; the writer screen (DR-0690) |
| P2 | major | no input for stream moves | fixed: a STREAMS label; the streams row reads only it |
| P3 | major | meetings lost what mail fed them | fixed: MEETINGS takes `response`, `attendees`, `start` and `end`; INVITES takes the invite mail's rid; who reports invites |
| P4 | major | the old skill bodies' lore had no home | fixed: the project's CLAUDE.md, not a door (DR-0692); `who` and drafting rules in the plugin |
| P5 | minor | an ask on an item being closed | fixed: a `log` if asked before the evidence, an `add` if after |
| P6 | minor | recordings in raw with no note were skipped | fixed: the recording agent covers them |
| P7 | minor | the rehearsal had no `sync.md` files | fixed: written from the old fork prompts first, the owner's step |
| P8 | minor | the old `mail` and `quick` arguments, unknown names | fixed: mapped or dropped in the pointer; an unknown name refused |
| P9 | minor | a locator `read` can't open | fixed: `search` its words around its day |
| P10 | minor | "locator" undefined | fixed: what one `read` opens |
| P11 | minor | a write refused after a clean dry run | fixed: a fresh snapshot and another dry run |

Round 2, 19 Oct, of 5311411: approved, five minors folded in: DR-0690's stripped forms, narrowed bind and mail test;
STREAMS in MEETINGS' form; the search fallback moved to tasks and the reconcile; docs in the vault; unnoted recordings.

## PR 1, as built

| Planned | Built |
|---|---|
| the writer screen in `TextRules` (DR-0690) | `TextRules.contained` for `source` and `ref`: `store.locator_rid` strips brackets, `r:` and the position the anchor grammar reads, so a Teams chat id's `@thread.v2` stays; the rid's text up to each slash, then the index's container and thread. The writer asks the index it holds when its ledger tables come from it, the import opens one to read, and with none only the text is checked. An index that doesn't answer refuses the value: it can't be checked |
| the import's dry run names it | `imports._fill` screens with the index `imports._records` opens; each refused line is `export.jsonl:<n>: source points into a never-store container; leave it out` |
| `sync/SKILL.md`, `sync/fork.md` | the block lives in `fork.md` alone, which `SKILL.md` fills: `{SOURCE}`, `{VAULT}`, `{TODAY}`, `{OWED_SINCE}`, and `{ZONE}` from `vault.yaml`, since `docket ledger day --json` gives `at` in UTC. An ask's day carries its local time when the source gives one, so the reconcile can link it to a meeting: no new field |
| reads-only pre-approvals | `ledger`, `search`, `read`, `verify`, and `briefs` and `decisions` too, which the summary names once; `docket ledger day`, `docket staging list` and `show` |
| one agent per source | `subagent_type: fork` where Claude Code offers it, else `general-purpose`, since `fork.md` stands alone |
| `test_plugin_files.py` | the block's labels and each line's fields pinned; every `docket` command and flag, `ledger` view, op and tool parameter the two files name checked; the worked batch dry-run clean on an invented pilot ledger. AGENTS.md names `sync`; `tasks` comes with PR 2 |
| docs | the plugin README's sync section with the cut-over for a sync of one's own; `fork.md` on the plugin's reading list as a contract; the package README's gate and live runs; design's Tasks and Claude first |
