---
name: teams
description: Harvest Teams chats into Docket from Teams on the web, with two fixed scripts run in the user's own Chrome. Use when a sync runs the Teams source, or the user asks to fetch or catch up on Teams chats.
---

<!-- docket: steps from run_open -->

# Teams, a Docket example source

Reads Teams chats (one-on-one, group and meeting chats) from Teams on the web with two fixed scripts run through
Claude in Chrome's `javascript_tool`: `scripts/teams-chats.js` lists the chats, `scripts/teams-chat.js` reads the open
one. `mapper.py` next to this file makes one record per message. Channel posts and the Mentions view stay out. This
folder is an example to copy, not part of Docket: it suits a tenant where no Teams or Microsoft 365 MCP server is
allowed, and where one is, an MCP route is simpler.

Claude Code doesn't load this file as a skill: `run_open("teams")` names it and gives the run's steps. Keep the marker
line at the top, since Docket refuses a run of a `SKILL.md` without it.

## Set it up

`docket source new teams --example --vault <vault>` copies this folder to `.claude/skills/sources/teams/` in the repo
that holds the vault and adds the source to `vault.yaml`. By hand, the same two steps:

1. Copy this folder to `.claude/skills/sources/teams/` in the repo that holds the vault.
2. In `vault.yaml`, add the source, then commit `vault.yaml`: a source added by hand applies once it is committed.

```yaml
sources:
  teams:
    sensitivity: work             # every message goes straight to ready; make it mixed to classify each first
    mapper: .claude/skills/sources/teams/mapper.py
    tools: [javascript_tool]
    lookback_days: 1              # a week back would open every chat active that week on every run
```

Then run its tests where it was copied: `python -m pytest .claude/skills/sources/teams/tests`. The tests that go
through Docket's own checks run only where `docket` can be imported. The copy is the project's own from then on:
change the route as your tenant needs, and keep its tests passing.

`javascript_tool` is also what the Outlook and calendar examples capture, and a session's hook posts each capture to
the one open run that lists its tool: open one of their runs at a time in a session, or each in its own agent.

## Access

- Claude in Chrome, the user's own Chrome, signed in to Teams at https://teams.cloud.microsoft/. Load the Chrome tools
  with one ToolSearch, call `tabs_context_mcp`, work in a new tab of your own and close it at the end.
- Signed out: stop and say the user has to sign in. Never enter anything.
- Read only, with one exception. Never type, never press keys in Teams (Escape to close a menu is the only key), never
  click the compose box, a reaction, a call or meeting-join button, or anything in a message's action menu. Opening a
  chat may show its sender "Seen": agree that with the user before the first run. The exception: a chat that was
  unread before you opened it and is read afterwards gets marked unread again.
- A deep link (`/l/chat/<id>/0`) lands on a "Join conversation" launcher page that wants a click: never use one. A chat
  opens only through its row in the list.

## The reads

| Read | How |
|---|---|
| Teams | navigate your tab to `https://teams.cloud.microsoft/`. It opens the last chat viewed by itself, which can't be avoided |
| the chat list | `scripts/teams-chats.js`, pasted whole as `javascript_tool`'s text: the version line, a header and one string per chat row, newest activity first |
| more chats | the list holds about 20 chats. When its last row is still inside the window, click "See more" at its end with a real click (`find`, then `computer left_click` on the ref; a JS `click()` doesn't expand it) and read the list again |
| open a chat | the open snippet below, with the chat's id and the window's start: it clicks the chat's row (JS `click()`), waits until the pane holds that chat, and pages back until a message at or before the start is loaded |
| a chat | `scripts/teams-chat.js`, pasted whole, with the window's start in place of the `''` on its last line: the version line, a header and the messages people wrote since then |
| a later part | when the last string is `more<TAB><n>`, run the chat script again with `<n>` in place of the 0 on its last line. Seen once: a day's chats held 1 to 76 messages in the pane, under 12 KB, one part each |
| restore unread | the restore snippet below, once per chat that was unread before you opened it, after the run is closed |

The list's header is `chats<TAB><rows><TAB><from><TAB><rows read from data><TAB><unread rows>`; each row is `c`, the
chat id, kind (`one-on-one`, `group`, `meeting`), unread (1 or 0), last activity (UTC), muted and the name. The chat's
header is `chat`, the chat id, kind, name, messages in the pane, the oldest and newest loaded (UTC), the start you gave,
messages returned, from, messages read from data and system lines. Each message is `m`, how many strings it takes, then
its id, time (UTC), author, user id, edit time, flags, the quoted message's id, author and preview, attachments and
text. In every field `¤¤` is `¤`, `¤e` is `=` and `¤t` a tab; read them back that way when you quote.

The open snippet: the chat's id and the window's start (UTC ISO) on its last line.

```js
await (async (ID, SINCE) => {
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const pause = async (ms) => { const t = Date.now(); while (Date.now() - t < ms) await tick(); };
  const rowOf = () => [...document.querySelectorAll('[role="treeitem"][data-item-type="chat"]')].find((r) => (r.getAttribute('data-fui-tree-item-value') || '').endsWith('|' + ID));
  const unread = (r) => /\bchat_list_unread_text\b/.test(r.getAttribute('aria-labelledby') || '');
  const convOf = (el) => { const k = Object.keys(el).find((x) => x.startsWith('__reactFiber$')); let f = k ? el[k] : null; for (let n = 0; f && n < 12; n++, f = f.return) if (f.memoizedProps && typeof f.memoizedProps.convId === 'string') return f.memoizedProps.convId; return ''; };
  const times = () => [...document.querySelectorAll('[data-tid="chat-pane-message"][data-mid]')].map((b) => { const t = document.getElementById('timestamp-' + b.getAttribute('data-mid')); return t ? Date.parse(t.getAttribute('datetime')) : NaN; }).filter(Number.isFinite);
  const row = rowOf();
  if (!row) return ['no row', ID];
  const before = unread(row) ? 'unread' : 'read';
  const open = () => { const el = document.querySelector('[data-tid="chat-pane-item"] [data-mid]'); return !!el && convOf(el) === ID; };
  if (!open()) row.click();
  for (const t0 = Date.now(); !open() && Date.now() - t0 < 10000;) await tick();
  if (!open()) return ['not open', ID, 'was ' + before];
  await pause(1500);
  const since = SINCE ? Date.parse(SINCE) : 0;
  const vp = document.querySelector('[data-tid="message-pane-list-viewport"]');
  let n = times().length;
  let stalls = 0;
  for (const t0 = Date.now(); vp && since && stalls < 2 && Date.now() - t0 < 30000;) {
    const t = times();
    if (t.length && Math.min(...t) <= since) break;
    vp.scrollTop = 40; await pause(100); vp.scrollTop = 0; vp.dispatchEvent(new Event('scroll'));
    for (const t1 = Date.now(); times().length === n && Date.now() - t1 < 3000;) await tick();
    await pause(400);
    const m = times().length;
    stalls = m === n ? stalls + 1 : 0;
    n = m;
  }
  const t = times();
  const first = t.length ? Math.min(...t) : NaN;
  const r = rowOf();
  return ['opened ' + ID, 'was ' + before, 'now ' + (r && unread(r) ? 'unread' : 'read'), 'loaded ' + t.length,
    'oldest ' + (t.length ? new Date(first).toISOString() : ''),
    !since ? 'not paged' : first <= since ? 'reached the window start' : stalls >= 2 ? 'no older messages load' : 'stopped at 30 s'];
})('19:<chat id>@thread.v2', '2026-10-19T06:00:00Z');
```

`stopped at 30 s`: run it again; it goes on paging without clicking the row again. `no older messages load`: the
chat starts there.

The restore snippet: the chat's id on its last line. It marks the chat unread only when it is read now, never touches
another row, and says whether it took.

```js
await (async (ID) => {
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const pause = async (ms) => { const t = Date.now(); while (Date.now() - t < ms) await tick(); };
  const rowOf = () => [...document.querySelectorAll('[role="treeitem"][data-item-type="chat"]')].find((r) => (r.getAttribute('data-fui-tree-item-value') || '').endsWith('|' + ID));
  const unread = (r) => /\bchat_list_unread_text\b/.test(r.getAttribute('aria-labelledby') || '');
  const escape = () => (document.activeElement || document.body).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true }));
  if (!rowOf()) return ['no row', ID];
  await pause(1500); // the unread label can take 1-2 s to follow a change
  const row = rowOf();
  if (unread(row)) return ['still unread', ID];
  const box = row.getBoundingClientRect();
  row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: box.left + 20, clientY: box.top + box.height / 2 }));
  await pause(1000);
  const item = [...document.querySelectorAll('[role="menuitem"]')].find((m) => /^mark as unread$/i.test((m.innerText || '').trim()));
  if (!item) { escape(); await pause(400); return ['no Mark as unread in the menu', ID]; }
  item.click();
  for (const t0 = Date.now(); Date.now() - t0 < 3000 && !unread(rowOf());) await tick();
  return [unread(rowOf()) ? 'restored' : 'not restored', ID];
})('19:<chat id>@thread.v2');
```

### How the page behaves

Seen on one tenant's Teams on the web, on a few runs. Microsoft changes the page; the scripts' header counts show
when it has.

- Chat rows are `[role="treeitem"][data-item-type="chat"]`. Each carries its chat id after the last `|` of
  `data-fui-tree-item-value`, and its kind there (`OneGQL_OneOnOneChatConversation`, `GroupChat…`, `MeetingChat…`).
  Ids: a group's `19:<32 hex>@thread.v2`, a one-on-one's `19:<guid>_<guid>@unq.gbl.spaces`, a meeting chat's
  `19:meeting_<base64>@thread.v2`. Channels are `team` rows (`@thread.tacv2`), communities `engage-community`.
- In compact density the list shows names only, no time or preview. The conversation's own data behind the row has the
  last message's arrival time, so the chat script's header and the list need no open to know a chat's last activity.
- Unread: the row's `aria-labelledby` starts with `chat_list_unread_text`, and the conversation's data says
  `hasUnreadMessage`. Both matched the bold weight (700 unread, 400 read, on the first leaf `span` with text; the row's
  first `span` is always 600) on every row checked.
- The list is sorted by last activity, and meeting chats float up on system events too, so their newest message can be
  much older. Match a row by its id, never by its name: long meeting names are cut in the list, and a group chat
  without a name shows `<first name>, +2`.
- Messages are `[data-tid="chat-pane-message"][data-mid]`. `data-mid` is the message id (its first arrival in epoch
  ms). `#timestamp-<mid>` is a `time[datetime]` in UTC with milliseconds (its aria-label gives the local time),
  `#author-<mid>` the author's name, `#content-<mid>` the body. One run saw the author on every message, another only
  on the first of a run of messages, so the script carries it forward.
- The message's own data behind the element has the author's display name and `8:orgid:<guid>` user id, the edit and
  delete times, the files (name, GUID, SharePoint item id), the stored HTML, importance, forwarded, cards. A reaction
  changes its `version`, so `version` isn't an edit marker; `editTime` is.
- System lines (joined, added, named the meeting, transcription on, meeting started or ended, a recording) are
  `[data-tid="control-message-renderer"]` with no `chat-pane-message`, no time and no author. Date dividers have no
  `data-mid`. The script leaves both out.
- A reply quotes the message it answers: a `quoted-reply-card` inside the body, with the quoted message's id, author
  and a preview; the stored HTML has it as a `schema.skype.com/Reply` blockquote. A chat has no threads.
- Files sit in a `file-attachment-grid` outside the body's content; a file-only message has empty text. Images are
  `schema.skype.com/AMSImage` imgs (alt `image`, id `0-<region>-<shard>-<32 hex>`). Emoji are imgs whose alt is the
  character, so `innerText` drops them; the script walks the content and keeps them. Reactions sit outside the content.
- Teams enforces Trusted Types: `DOMParser`, `innerHTML` and `createContextualFragment` all throw, so nothing parses
  the stored HTML. The text comes from the rendered content, which matched `innerText` on every message compared.
- Opening a chat: a JS `click()` on its row; the pane is that chat once its items' data says that `convId`, about 0.6 s.
  A meeting chat with only system lines needs up to 4 s before its items exist.
- Older messages load when the viewport `[data-tid="message-pane-list-viewport"]` is scrolled to the top and gets a
  `scroll` event: about 11 more each time, in 0.5 to 1.5 s. One scroll in four loaded nothing and the next did, so the
  snippet stops after two empty tries. The pane keeps what it loaded; reopening a chat starts over.
- Whether opening a chat marks it read can't be predicted: in a hidden tab it often didn't, in a fresh tab with no
  screenshot taken yet it sometimes did, and after a screenshot brings the tab forward it does. So always note
  `was unread` and run the restore. The unread label can take 1-2 s to follow; the restore snippet waits before it
  decides.
- Mark unread: a `contextmenu` event on the row opens its menu; a read chat's menu has "Mark as unread", an unread
  chat's "Mark as read". Escape on the active element closes it.
- At most about 45 s a call, past which the result is lost: one chat per call, the open snippet capped at 30 s.
- Chrome's filters, on what a call returns: about 1,000 characters a string and 100 items an array; a string holding
  `=` together with `&` or `;` is blocked as a query string or cookie, even in prose (`a = b & c`); a whole string of
  three dot-separated words (`teams.cloud.microsoft`) is blocked as a JWT; a long run of base64 alone in a string is
  blocked. A single `?a=1`, URLs without query strings, a JWT inside a sentence and 80-character ids inside a line pass.
  Private-use characters are dropped. The scripts escape `=` (and the escape mark `¤` and tabs), cut lines at 900
  characters and return at most 95 strings and 30,000 characters. A blocked or cut string is set aside by the mapper.

## The window

`run_open` gives the window: the latest mark less the lookback (`lookback_days: 1` under `sources.teams` in
`vault.yaml`), to now. Every read in the run is captured and kept, inside Docket's window or not.

1. Read the chat list. Every row whose last activity is at or after the start has messages in the window; the list is
   newest first, so stop at the first row older than the start, and press "See more" while the last row is still
   inside it.
2. Open each of those chats with the open snippet and read it with the chat script, the start as `SINCE`, newest
   activity first, so the newest chats are captured before the budget runs out. Note each chat the snippet said
   `was unread`.
3. A chat whose header says no message was returned (only system lines, or activity that was a system event) is done.

## A run

Docket gives the run's steps: what to skip, what is owed, the budget and how to close. This is what is Teams' own.

| Part | What |
|---|---|
| Sign in | Microsoft's sign-in page or an account picker instead of Teams: stop without closing |
| List | the chat list, as above; the list itself makes no record |
| Each chat | the open snippet, then the chat script and its later parts. `not open`: run the snippet once more; still not open, skip the chat as `error` by its id |
| Data missing | the chat header's messages-read-from-data is 0 while messages returned is not, or its chat id is empty: Teams changed. Stop without closing, or every message would get a new version, and say so when you report the run |
| Held | a held chat isn't opened; a held message on a page is dropped by the mapper |
| Kept | nothing to skip: a kept message read again makes no new version |
| Other calls | while the run is open, `javascript_tool` runs only the two scripts and the open snippet: every call is captured. The Mentions view, screenshots and the unread restore wait until after the close |
| Too large | under the script's caps it can't happen; if it does, skip the chat as `too-large` by its id |
| Budget | past it, open no more chats; skip each one left as `later` by its chat id |
| By id | a retry id is a chat id: find its row in the list (press "See more" until it shows or the list ends), then open and read it from the window's start or the retry's `since` date, whichever is earlier. No row: skip it as `not-listed` |
| Close | `ingest(run, captures=<highest n>, skipped=[...])`; "another run is closing" means call it again a minute later. Then `run_open(source="teams", final=true)` answers that no final run is needed |
| Unread | after the close: open the Mentions view (a JS `click()` on its treeitem, `data-testid="slice-list-item-slice-activities-mentions"`), so no chat to restore is on screen; then the restore snippet for each chat noted `was unread`, and each must say `restored` or `still unread`. Close your tab last |

## What it makes

One record per message people wrote, kind `message`:

| Field | What |
|---|---|
| rid | `teams:<chat id>/<message id>` |
| container, thread | `teams:<chat id>`: a chat has no threads |
| ts | the message's time, UTC to the millisecond |
| author | `Name <Entra object id>` from the message's data (`8:orgid:<guid>`); another kind of user keeps Teams' id whole; the name alone when the page gave no data |
| text | what the page shows, emoji as their character, line breaks kept; never the quoted message, which is its own record |
| attachments | files by name with Teams' file id as ref; images as `image` with their id |
| native | chat id, kind and name, message id, the exact time, user id, edit time, flags (`mine`, `deleted`, `forwarded`, `cards:<n>`, `importance:<level>`, `shown`), and `reply_to` (the quoted message's id, author and preview) |
| fidelity | verbatim; `derived` when the page gave no chat id |

- An edit, a new file or a renamed author is a new version; a reaction, a reply, a chat rename or flags aren't.
- Without a chat id on the page, the container is `teams:d-<hash of the chat's name>`: a renamed group chat then
  splits in two.
- With `sensitivity: work`, the records go straight to ready. A chat on `never_store.containers` in `vault.yaml`
  (`19:…` or `teams:19:…`) is dropped by the gate before staging.
- The chat ids and message ids are the ones Microsoft Graph uses for chats and chat messages, so a later Graph route
  can keep the rids. Check that on one message before relying on it.

## Changing the mapper

Every record the lookback reads again gets a new version when the mapper's output changes.

- Keep the output stable: change it only for a real change in what Teams says.
- Pin golden versions in the tests, as `tests/test_with_docket.py` does, so a change shows before it runs.
- A change to what a script returns gets a new version line (`docket:teams-chat/2`), and the mapper learns it before the
  script ships.
- Pull the project on every machine before its next sync, so two machines never map one message two ways.
