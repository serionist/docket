---
name: slack
description: Harvest Slack into Docket with the Slack plugin's read tools. Use when a sync runs the Slack source, or the user asks to fetch or catch up on Slack.
---

<!-- docket: steps from run_open -->

# Slack, a Docket example source

Reads Slack with the Slack plugin's MCP tools and hands what `slack_read_channel` and `slack_read_thread` returned to
Docket untouched; `mapper.py` next to this file turns a run's captures into one record per message. Search finds what
to read and is never captured: only what a read returned is kept. This folder is an example to copy, not part of
Docket.

Claude Code doesn't load this file as a skill: `run_open("slack")` names it and gives the run's steps. Keep the marker
line at the top, since Docket refuses a run of a `SKILL.md` without it.

## Set it up

`docket source new slack --example --vault <vault>` copies this folder to `.claude/skills/sources/slack/` in the repo
that holds the vault and adds the source to `vault.yaml`. By hand, the same two steps:

1. Copy this folder to `.claude/skills/sources/slack/` in the repo that holds the vault.
2. In `vault.yaml`, add the source, then commit `vault.yaml`: a source added by hand applies once it is committed.

```yaml
sources:
  slack:
    sensitivity: work             # every message goes straight to ready; make it mixed to classify each first
    mapper: .claude/skills/sources/slack/mapper.py
    tools: [slack_read_channel, slack_read_thread]
```

Then run its tests where it was copied: `python -m pytest .claude/skills/sources/slack/tests`. The tests that go
through Docket's own checks run only where `docket` can be imported. The copy is the project's own from then on:
change the route as your workspace needs, and keep its tests passing.

## Access

- The Slack plugin's tools, `mcp__plugin_slack_slack__*`, their schemas loaded with one ToolSearch. Your own user id
  (`U…`) and the ids of the channels and DMs you read come from `slack_search_users`, `slack_search_channels` and a
  detailed search; note them in your copy of this file.
- A private channel shows in search only when you are in it.
- Search takes unix seconds in `after`/`before`.

## The reads

| Read | How |
|---|---|
| a conversation | `slack_read_channel(channel_id, oldest=<window start, unix seconds>, limit=50, response_format="detailed")`, following `next_cursor` until there is none; with `oldest` set, the cursor pages newer |
| a thread | `slack_read_thread(channel_id, message_ts=<the parent's ts>, limit=50, response_format="detailed")`, following the cursor. It ignores `oldest` and returns the whole thread; a page that repeats the one before ends it. Every message a read returns with a `Thread: <n> replies (latest: ...)` line gets one: a channel read gives only the parent, never its replies |
| a DM | by its D id (a group DM by its G or C id), never by a user id: a user id opens the same DM under another id, which would give its messages a second rid, so the mapper sets such a read aside. A detailed search hit names the D id |

Always detailed, always `limit` 50: a page stays under the 50,000 characters past which Claude Code hands the hook a
file note instead of the result. A concise read reaches the hook too, so a run makes none.

## The window

`run_open` gives the window: the latest mark less the lookback, to now. On the first run that is a week.

1. Retry ids first, by id (below): threads and conversations an earlier run skipped as `later`.
2. Find every conversation with activity since the window's start, with the search methods below.
3. Read each conversation from the window's start, newest activity first, so the newest is captured before the budget
   runs out.
4. Read the thread of every message those reads showed with a `Thread:` line, newest `latest` first, and of every
   older parent whose replies search shows in the window. Keep a list as you go: each parent is either read or
   skipped, never left out.

Keep the week's lookback (no `lookback_days` under `sources.slack` in `vault.yaml`) until a run closes with no thread
or conversation skipped as `later`. Then set `lookback_days: 1` there and commit it. With 1, the window starts a day
back, and a parent older than that is reached only through a retry id. Seen once: most of a first run's thread parents
were older than the window.

## A run

Docket gives the run's steps: what to skip, what is owed, the budget and how to close. This is what is Slack's own.

| Part | What |
|---|---|
| Sign in | an auth error from the plugin (`invalid_auth`, `not_authed`, a sign-in prompt) means re-authorising it in `/mcp`; stop without closing |
| List | the search methods below, from the window's start |
| Held | a held channel or DM isn't read, nor a held `<channel>/<ts>` thread; a held message on a page is dropped by the mapper |
| Kept | nothing to skip: a page holds what it holds, and a kept message read again makes no new version. A kept parent says nothing of its replies, so its thread is read like any other |
| By id | a retry id `C…`, `G…` or `D…`: read the conversation from the window's start or from 7 days before the retry's `since` date, whichever is earlier, then the threads its Thread lines name; `<channel>/<ts>`: read that thread. `channel_not_found`, `thread_not_found` or `message_not_found` means gone: skip it as `gone` |
| Too large | "not kept: ... (too large)": read the same page again with `limit` 10; still too large, skip the conversation or thread as `too-large`, by its id |
| Budget | past it, read nothing new for the run: skip each conversation left as `later` by its id, and each thread left as `later` by `<channel>/<parent ts>`, so the next run reads it first |
| Rate limits | `ratelimited`: pause and retry with growing pauses (30 s, 1 min, 2 min); still limited while searching, stop without closing; on one conversation, skip it as `rate-limited` |
| Errors | `ECONNRESET` on a detailed read (seen once, on a DM): retry once, then `limit` 10; still failing, skip it as `error` |
| Close | `ingest(run, captures=<highest n>, skipped=[...])`; "another run is closing" means another source's close is still mapping: call `ingest` again a minute later |

### Finding what to read

Each of these was seen on one workspace; check them on yours.

- `<@U…>` (your user id) filters only inside `query` ("<@U…> after:YYYY-MM-DD") or as a `keywords` element with
  `filters: after:YYYY-MM-DD`; on its own in the `filters` field it returns all traffic.
- Cheapest "is the window empty?" check: `filters: on:<today>`, sort by timestamp, limit 5. `after` (unix) with no
  keywords returns nothing, so it can't prove a window empty.
- Walking a window: `filters: on:<day>` with `channel_types` split into im,mpim and
  public_channel,private_channel, sorted by timestamp desc, paged with the cursor; stop once a page passes the window
  start. `-in:<#Dxxx>` in filters drops a chatty DM so the others surface.
- `filters: on:<day>` also returns thread replies (their permalinks carry thread_ts), so on a short window it covers
  the `is:thread` pass too.
- Seen once: `filters: on:<day>` combined with `after` (unix) dropped several in-window messages (a DM, a group DM, a
  thread reply). Use `on:<day>` without `after`, sorted desc, and page down to the window start.
- `after:YYYY-MM-DD` in `filters` starts at the next local midnight. For the late part of the previous day, run
  `on:<that day>` sorted desc, limit 5: one call.
- `before` (unix) combines with `filters: on:<day>` and `-in:` exclusions, so a midday re-run can skip the part of
  the day already paged.
- Seen once: `is:thread` with an `after:` date filter returned top-level messages too; the `thread_ts` in a detailed
  result's permalink is the reliable marker.
- For a channel whose window traffic is all thread replies, a detailed `in:<#C…> on:<day>` search gives each reply's
  thread_ts in one call.
- A group DM that concise results show only by names, or a DM's D id: search one distinctive phrase in `keywords`,
  detailed format, limit 3.
- To find the channel or ts of one message, search with `include_context: false`: with context, a `from:` keyword
  search can pull a whole DM thread from outside the window.
- `-with:<@U…>` with a wrong user id is silently ignored; drop a DM with `-in:<#Dxxx>`. Long exclusion lists work.
- A busy DM can fill a whole 20-result page by itself; exclude it with `-in:<#Dxxx>` and search again to reach the
  window start.
- A long thread under one DM parent (60+ short replies) eats several search pages: exclude it with `-in:` early and
  read it once with `slack_read_thread`.
- With the same filters, concise said "End of results" while detailed gave a cursor whose next page was empty:
  concise's "End of results" can be trusted.

### How the reads behave

- `slack_read_channel` returns only top-level messages. A channel whose traffic in the window is all thread replies
  comes back empty: find those threads with search, then read each with `slack_read_thread`.
- Seen once: on a long DM thread the cursor repeated the first page. Stop at a repeated page; the mapper keeps one
  copy of each message.
- To prove a known DM empty, a read with `oldest` = the window start is cheaper than more exclusion searches. A
  weekend DM that runs to several pages is cheaper read that way too.
- Seen once: a reply landed top level instead of in the thread it answered. Read the channel history around each
  `from:` hit, not only the thread.
- An empty-text message is usually a file post; the detailed read names the file.
- A forwarded message reads back as "Forwarded message from ... (permalink)" with empty text; the permalink names the
  source conversation and ts.
- Slack drafts don't show in search or reads.
- A Slack ts can't be rebuilt from a clock time (the fraction is unknown): cite a message as channel plus time unless
  the exact ts was read.

## What it makes

One record per message, kind `message`:

| Field | What |
|---|---|
| rid | `slack:<channel id>/<ts>` |
| container | `slack:<channel id>`: a channel, group DM or DM |
| thread | `slack:<channel id>/<parent ts>`; a top-level message is its own parent, and a reply, read by its thread, hangs from the parent |
| author | `Name <user id>`, from the message's header or its `From:` line |
| attachments | its files, by name, with Slack's file id as `ref` |
| native | channel, ts, the local time shown, the tool that read it, user id, address, the lines under the text, and from a `Thread:` line the reply count and the latest reply's time |

The two layouts, as a watched run returned them:

| Read | Layout |
|---|---|
| channel | `Channel: #<name> (<id>)`; per message `=== Message from <name> <<address>> (<user id>) at <local time> ===`, `Message TS: <ts>`, the text |
| thread | `=== THREAD PARENT MESSAGE ===`, then `=== THREAD REPLIES (<n> total) ===` and `--- Reply <i> of <n> ---` per reply; each with `From:`, `Time:` and `Message TS:` lines, the text, a blank line |
| under the text | `Thread: <n> replies (latest: <local time>)` (channel reads only), `Reactions: <name> (<n>), ...`, `Files: <name> (ID: <file id>, <type>, <size>), ...`, in that order |

- A message read twice in a run (by its channel and its thread, or on a repeated page) gives one record, from the
  latest read.
- An edit, a new file or a renamed author is a new version; a reaction or a reply isn't.
- With `sensitivity: work`, the records go straight to ready. A conversation on `never_store.containers` in
  `vault.yaml` (`D0…` or `slack:D0…`) is dropped by the gate before staging.
- A line that only looks like those (`Files: on the drive`) stays in the text. A header that starts like one and
  doesn't read is set aside as `bad-header`: the layout changed, and the mapper and its tests change with it.
- A thread past 50 replies (seen once): the first page is the newest 50 replies and the cursor pages toward older
  ones. The parent repeats on every page, `Reply <i> of <n>` restarts on each page, and `(<n> total)` is that page's
  count, not the thread's. The mapper only matches the header shapes, so it reads every page as it is; without a
  parent block, replies would hang from the ts the read asked for.

## Changing the mapper

Every record the lookback reads again gets a new version when the mapper's output changes.

- Keep the output stable: change it only for a real change in what Slack says.
- Pin golden versions in the tests, as `tests/test_with_docket.py` does, so a change shows before it runs.
- Pull the project on every machine before its next sync, so two machines never map one message two ways.
