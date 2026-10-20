# The Docket plugin for Claude Code

The plugin connects Claude Code to the Docket service on the same machine: its MCP tools (search, read, verify and
write the vault), a status line at each session's start, and the skills that set Docket up and answer from it.

## Install

The plugin runs the `docket` command, so install Docket first and keep it on your PATH:

```
uv tool install "git+https://github.com/serionist/docket#subdirectory=packages/docket"
```

Then add this repo as a marketplace and install the plugin, in Claude Code:

```
/plugin marketplace add serionist/docket
/plugin install docket@docket
```

`/docket:setup` then makes your vault, adds your sources and offers to start the service.

## What it brings

| Part | What it does |
|---|---|
| the MCP server `docket` | the service at `http://127.0.0.1:8741/mcp`; its tools are `search`, `read`, `verify`, `doc_put`, `run_open` and `ingest`, shown as `mcp__plugin_docket_docket__<tool>`; the token comes from `docket service token --header` on each connect |
| the session-start status | `docket hook session-start`: Claude learns whether the service runs, on which vault, what waits in staging, and whether a curate round is due, then your day: in the vault's repo the syncs due, the queue, the meetings, what is overdue, owed and awaited, and the streams, in at most 45 lines; in any other repo one line of counts. You see a line only when something needs you: a sync due, a status file you edited by hand |
| the capture hook | `docket hook post-tool-use` after every MCP call: during a sync run it hands Docket what the source's tools returned, and tells Claude which captures it kept; with no run open it reads one small file and returns |
| `/docket:recall` | finds what was said or written, reads the summary, checks every fact against its raw, and answers with locators; Claude also uses it on its own when you ask what someone said |
| `/docket:setup` | checks Docket is installed, asks which sources to set up, where the vault lives and your languages, creates the vault, and on your yes writes each source's skill and mapper with `docket source new` |
| `/docket:curate` | runs a curate round with you: Docket's cards for new people and terms and for spellings search misses, each with Claude's call and why, decided only on your answer for each card; and seeds entities from a people directory or glossary you point at, applied on your yes |
| `/docket:cuts` | for recordings you held back, turns your words for each left-out stretch into time ranges on the transcript, writes them to a proposals file outside any repo, shows you the cuts and runs `docket hold release` on your yes; it tells you first where the transcripts it reads stay in Claude Code's session files |
| `/docket:sync` | syncs your live sources, each in an agent of its own, then writes what they found into the ledger in one batch and goes through the list with you (below) |
| `/docket:tasks` | opens the page where you triage, then works your queue with you in chat, one item at a time, each outcome written to the ledger before the next (below) |

To sync a single source by hand, ask Claude to sync it: Claude calls `run_open`, follows the source's skill in your
project, and closes the run with `ingest`. Allow `mcp__plugin_docket_docket__run_open` and
`mcp__plugin_docket_docket__ingest` in your permission rules to skip the two prompts a run asks.

## The sync

`/docket:sync`, or `/docket:sync <source>...` for some of them, runs each live source in an agent of its own from a
sync folder, then writes what they found into the ledger in one `ledger_batch` by `sync`, and goes through the list
with you.

| Part | What |
|---|---|
| the sync folder | `docket sync start` writes it in Docket's state folder, never in the vault: `context.md`, the ledger as the agents read it, an item a line, and each agent's prompt; it goes 7 days after its start, or at your next forget |
| the agents | one per source, side by side, each from its prompt file: it opens a run, follows the source's `SKILL.md` and its `sync.md`, classifies the plainly work or plainly private records of a mixed source, promotes its own, reads its recordings whole, and writes a block of what you owe and await, what closed, invites, minutes and outcomes to the sync folder |
| the notes | `docket sync notes` writes a prompt per recording note the blocks name; an agent each writes the note in the background, and the sync never waits for them |
| the reconcile | one batch, dry run first: closes on a locator that shows it, asks logged or added (new ones untriaged), meetings set and linked, minutes, rollover, proposed points for the coming week's meetings, streams, archive |
| the list | in chat: meetings and prep, today, this week, whenever, invites, waiting on others; then what changed, the sources that didn't run, what waits on the Staging page, and the batch, to undo whole |

- Nothing is drafted or sent before you have gone through the list, and then only drafts: a chat's draft tool, a mail
  left as a draft unless you say send for that mail.
- A source's `sync.md`, beside its `SKILL.md` in your project, holds what you know about finding things there: what
  counts as an ask and as evidence, the names its transcripts mishear, your note layout. The sync adds what it learns
  there; copying a newer `SKILL.md` over yours leaves it alone. `docket source new` writes none.
- Your project's `CLAUDE.md` holds the rest: how you draft and in which language, the names the ledger uses for
  people, which stream a topic goes to, how to tell apart meetings that share a slot.
- The agents' runs, classifies, promotions and notes and the batch ask unless your permission rules allow them. The
  skill pre-approves only reads, `ledger`, `search`, `read`, `verify`, `briefs`, `decisions`, `docket ledger day`,
  `docket staging list` and `show`, and `docket sync start` and `notes`, which write only the sync folder.
- The sync folder is outside your project, so each agent's Read and Write there asks unless you allow them, and the
  note agents run in the background, where a prompt waits for nobody. Allow these, with your system's folder:

  | System | The rules |
  |---|---|
  | Linux | `Read(~/.local/state/docket/sync/**)`, `Write(~/.local/state/docket/sync/**)` |
  | macOS | `Read(~/Library/Application Support/docket/sync/**)`, `Write(~/Library/Application Support/docket/sync/**)` |
  | Windows | `Read(~/AppData/Local/docket/sync/**)`, `Write(~/AppData/Local/docket/sync/**)` |

  and `mcp__plugin_docket_docket__doc_put`, `mcp__plugin_docket_docket__read`, `mcp__plugin_docket_docket__search`,
  `mcp__plugin_docket_docket__ledger` and `mcp__plugin_docket_docket__verify` for the note agents. A `DOCKET_STATE_DIR`
  moves the folder to its `sync` folder.
- Moving from a sync of your own: keep its name and description, so your words still reach it, and make its body one
  line, "Run `/docket:sync` (the Skill tool) with the same arguments". Move its lore into `CLAUDE.md` and each
  source's `sync.md`, and delete it once the plugin's sync has run a working week.

## The tasks

`/docket:tasks`, or "let's do tasks", opens the Queue page with `docket open /queue`, where you triage: discuss,
claude, me or chase, or done, drop and snooze. Say "go" in chat, and Claude works the queue with you, one item at a
time.

| Verdict | What Claude does |
|---|---|
| discuss | the facts and a proposal, one short question round for a crisp choice, then the outcome you agree |
| claude | drafts it or does it; a message only you can send stays open, its draft on the item, and moves to me |
| me | lists it once, at the start |
| chase | drafts the nudge where the ask is, and clears the verdict |

- Each item's outcome is one `ledger_batch` before the next, each change saying whether you or Claude decided it, and
  the page shows it within seconds. Claude reads the queue and the item again before it writes.
- A prep item's agenda goes onto its meeting as talking points, those the sync proposed kept or dropped.
- Messages are drafts, by your project's `CLAUDE.md`: a chat's draft tool, one draft a channel and never over one
  already there; a mail left as a draft unless you say send for that mail.
- Without the service it reads and writes the same through `docket ledger queue`, `show`, `suggest` and `batch`, and
  you triage in chat.
- The skill pre-approves only reads: `ledger`, `search`, `read`, `verify`, `docket open`, and `docket ledger day`,
  `queue`, `show` and `suggest`. Each batch and each draft asks unless your permission rules allow it. In Git Bash on
  Windows, which turns a lone `/queue` into a folder, Claude runs `MSYS_NO_PATHCONV=1 docket open /queue`.
- Moving from a tasks skill of your own: as for a sync, keep its name and description, make its body "Run
  `/docket:tasks` (the Skill tool) with the same arguments", move its drafting rules into `CLAUDE.md`, and delete it
  after a working week.

## Curating and upgrading

To curate, allow `Bash(docket curate status *)`, `Bash(docket curate show *)` and `Bash(docket curate log *)` if you
like: they leave the vault as it is. Leave every other `docket curate` command to ask. In auto mode Claude Code may run
them without asking; the skill still decides a card only on your answer for it, and every change is logged and can be
undone with `docket curate undo`.

Upgrading: update Docket first (the install line above, with `--reinstall`), then the plugin. An older `docket`
doesn't know `docket hook post-tool-use`, and Claude Code would show its error after every MCP call. Then copy each
source's `SKILL.md` and `mapper.py` from the reference over your project's copies: a live run refuses a `SKILL.md`
from before live runs.

## When it doesn't connect

- `docket: not running`: start the service with `docket service install --vault <folder>`, then `/mcp` to reconnect.
- The service on another port than 8741 (`service.port` in Docket's config): set `DOCKET_PORT` to it in the
  environment Claude Code starts in. The status line says when the two differ.
- `docket` not found: the hook's notice and the failed server in `/mcp` both mean Docket isn't on PATH; install it as
  above.
- The token's store is gone: `docket service token --rotate`. A keyring chosen through `PYTHON_KEYRING_BACKEND` isn't
  seen by the token helper, whose environment drops names holding KEY; Docket's default store needs no variable.

A change under `plugin/` bumps the version in `.claude-plugin/plugin.json` (CV-0012): Claude Code keeps you on a
plugin's set version until it changes.
