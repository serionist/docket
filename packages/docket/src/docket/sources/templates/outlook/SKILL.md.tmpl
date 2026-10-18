---
name: outlook
description: Harvest Outlook mail into Docket from Outlook on the web, with two fixed scripts run in the user's own Chrome. Use when a sync runs the outlook source, or the user asks to fetch or catch up on mail.
---

<!-- docket: steps from run_open -->

# Outlook mail, a Docket example source

Reads mail from Outlook on the web (OWA) with two fixed scripts run through Claude in Chrome's `javascript_tool`:
`scripts/mail-rows.js` lists the rows of a search or a folder, `scripts/mail-read.js` reads one conversation or
message OWA has loaded. Both read OWA's own data behind the page, not the painted list or reading pane. `mapper.py`
next to this file makes one record per message. Every mail in the window becomes a record, newsletters included; a
sender on `never_store.people` is dropped by the gate. The calendar is a source of its own, the `calendar` example.

This folder is an example to copy, not part of Docket: it suits a mailbox no mail MCP server can reach, and where one
can, an MCP route is simpler. Claude Code doesn't load this file as a skill: `run_open("outlook")` names it and gives
the run's steps. Keep the marker line at the top, since Docket refuses a run of a `SKILL.md` without it.

## Set it up

`docket source new outlook --example --vault <vault>` copies this folder to `.claude/skills/sources/outlook/` in the
repo that holds the vault and adds the source to `vault.yaml`. By hand, the same two steps:

1. Copy this folder to `.claude/skills/sources/outlook/` in the repo that holds the vault.
2. In `vault.yaml`, add the source, then commit `vault.yaml`: a source added by hand applies once it is committed.

```yaml
sources:
  outlook:
    sensitivity: work             # every message goes straight to ready; make it mixed if private mail lands here
    mapper: .claude/skills/sources/outlook/mapper.py
    tools: [javascript_tool]
    lookback_days: 1              # a week back would open every conversation of the week on every run
```

Then run its tests where it was copied: `python -m pytest .claude/skills/sources/outlook/tests`. The tests that go
through Docket's own checks run only where `docket` can be imported. The copy is the project's own from then on:
change the route as your mailbox needs, and keep its tests passing.

`javascript_tool` is also what the Teams and calendar examples capture, and a session's hook posts each capture to the
one open run that lists its tool: open one of their runs at a time in a session, or each in its own agent.

## Access

- Claude in Chrome, the user's own Chrome, signed in to OWA at https://outlook.cloud.microsoft/mail/. Load the Chrome
  tools with one ToolSearch, call `tabs_context_mcp`, work in a new tab of your own and close it at the end.
- Signed out: stop and say the user has to sign in. Never enter anything.
- Read only. Never type or press keys in OWA (seen once: a stray "n" opened New Mail, which saved a draft). Never send,
  reply, forward, move, flag, delete or download. The search box is filled by the search snippet, never by keys.
- Unread stays as it was. Opening a conversation can mark it read (with OWA's "mark as read when the selection
  changes", a mail turns read when the selection moves off it), so every row the rows script lists as unread is noted
  before anything is opened, and after the run is closed each of those that is read now gets "Mark as unread" again,
  with the restore snippet below. Conversations OWA fetched ahead (the top of the Inbox) are read from its data with no
  click and stay as they were. Nothing that was read before the run is ever marked unread, and nothing but the read
  flag changes.
- A Drafts row is never opened: it opens compose.
- Chrome can drop mid-run ("Browser extension is not connected", or "Couldn't determine which page this action
  targets"): `tabs_context_mcp`, then the same call again; seen once, that recovered both. A failed call still takes a
  capture number, so the highest n counts it.

## The reads

| Read | How |
|---|---|
| OWA | navigate your tab to `https://outlook.cloud.microsoft/mail/` (the Inbox). A full navigate clears OWA's data, so the reads after it start over |
| define | right after every navigate, the define call below: it puts the two scripts and the open and folders snippets on `window` as functions, so each later read is a one-line call. A navigate clears `window`: define again after each one |
| the search | the search snippet below with `received>=<date>`, the local date of the window's start. From the Inbox it searches All folders: the Inbox, Sent Items and every folder rules file mail into. It leaves out Junk Email, Deleted Items and the online archive |
| the rows | `await window.__dkRows('<start>', 0)`, the window's start in UTC ISO: `scripts/mail-rows.js`'s result, the version line, a header and one string per row in the window, newest first |
| more rows | when the rows header says more wait on the server and the oldest loaded row is still inside the window: the more snippet below, then the rows call again |
| a later part | when the last string is `more<TAB><n>`, the same call with `<n>` in place of the 0 (`__dkRows`, or `__dkRead('<id>', '<start>', <n>)` for a read) |
| open | the open snippet below, with a row's id: it scrolls the list to the row, clicks it (a JS `click()`), and waits until OWA has every message of it, about 1 to 2 s. A conversation OWA already holds whole isn't clicked: `loaded already` |
| a conversation | `await window.__dkOR('<id>', '<start>')`: opens the row (the open snippet, once more on `not loaded`) and returns `scripts/mail-read.js`'s result for it, the version line, a header and the messages since the start. When the open doesn't come back `opened` or `loaded already`, it returns the open's answer (a draft, `no row`), which makes no record. Long ones page by part, as above |
| the folders apart | `await window.__dkFolders('')`, the folders snippet below: from OWA's folder data, the folders the search leaves out (Junk Email, Deleted Items, the Archive folder to be safe, and any folder under them), each with its id, kind, total and unread, and whether there is an online archive. For each with mail, the folders snippet with its id opens it in the list (a click in the folder pane); then the rows script, the open snippet and the read script per row, as above. Junk Email is in message view, so each of its rows is one message |
| the online archive | the folders snippet with `archive` selects the archive mailbox; check that it did (below the search snippet), then the search snippet searches all of it (scope `archive mailbox`), and its rows are read as above. Back to the Inbox: the folders snippet with `inbox`, or navigate to `/mail/` |
| restore unread | after the run is closed: show each list again (the same search, or the folder), and run the restore snippet below with the ids of that list's rows that were unread when listed. Then run it again with the same ids as the check: each must say `still unread` |

The rows header is `rows`, the view (`search` or `folder`), the query or folder, the list (`conversations` or
`messages`), rows loaded, rows in the view, more on the server (1 or 0), the oldest loaded row (UTC), the start you
gave, rows returned, from and rows read from data. Each row is `r`, kind (`c` a conversation, `i` a message), its id,
the conversation id, the last delivery (UTC), unread, messages in the conversation (all folders), folder, folder type,
flags (`att`, `drafts:<n>`, `high`, `meeting`, `flagged`), sender, address and subject.

The read header is `conv`, the mode (`conversation`, `message`, `pane`, or empty when nothing is loaded for that id),
the id, the subject, messages in data, returned, older than the start, drafts left out, the start, from, pieces, the
read's mark, from data (1 or 0) and more to load in the conversation. Each message is `m`, how many strings it takes,
then its id, conversation id, internet message id, sent and received (UTC), from, address, sender (on behalf), To, Cc,
Bcc, subject, folder, folder type, flags, attachments and text. In every field `¤¤` is `¤`, `¤e` is `=` and `¤t` a
tab; read them back that way when you quote. Ids come in the URL-safe form (`+` as `-`, `/` as `_`) and go into the
snippets and the read script as printed.

The define call: one `javascript_tool` call, right after the navigate. It holds, in this order, `window.__dkRows = `
then `scripts/mail-rows.js` whole, with `await (async (SINCE, FROM) => {` cut to `async (SINCE, FROM) => {` and its
last line `})('', 0);` cut to `};`; the same for `window.__dkRead = ` with `scripts/mail-read.js` (`(ID, SINCE,
FROM)`, last line `})('', '', 0);`), `window.__dkOpen = ` with the open snippet and `window.__dkFolders = ` with the
folders snippet (each `(ID)` / `(OPEN)`, last line `})('...');` cut to `};`); then this tail. The comment headers can
stay or go. The restore snippet can be defined the same way as `window.__dkRestore` after the close.

```js
window.__dkOR = async (ID, SINCE) => {
  let o = await window.__dkOpen(ID);
  if (o[0] === 'not loaded') o = await window.__dkOpen(ID);
  if (o[0] !== 'opened' && o[0] !== 'loaded already') return o;
  return await window.__dkRead(ID, SINCE, 0);
};
['defined', typeof window.__dkRows, typeof window.__dkRead, typeof window.__dkOpen, typeof window.__dkFolders, typeof window.__dkOR];
```

Every type must say `function`. Calls are then `await window.__dkOR('<id>', '<start>')` per row,
`await window.__dkRows('<start>', 0)` per list and `await window.__dkFolders('<id>')` per folder. Why: pasted whole,
each conversation took two calls and about 17 KB of script text, and writing that text was most of a run's time; now
it is one call of about 150 characters. Checked on a full watched run: the results are what the scripts return,
version line included (it comes from each function's `VERSION`), and Docket's mapper reads only a call's result,
never its text. The define call is captured too and makes no record.

The search snippet: the query on its last line.

```js
await (async (QUERY) => {
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const pause = async (ms) => { const t = Date.now(); while (Date.now() - t < ms) await tick(); };
  const store = (k) => { const g = globalThis.__satchelGlobalContext; return g && g.rootStore.get(k); };
  const latest = () => { const lv = store('listview'); let best = null; if (lv) for (const tv of lv.tableViews.values()) if (tv.tableQuery && tv.tableQuery.type === 1 && (!best || tv.tableQuery.searchNumber > best.tableQuery.searchNumber)) best = tv; return best; };
  for (const t0 = Date.now(); !(document.querySelector('#topSearchInput') && store('listview')) && Date.now() - t0 < 15000;) await pause(250);
  const input = document.querySelector('#topSearchInput');
  if (!input) return ['no search box'];
  const before = latest();
  for (let n = 0; n < 2 && latest() === before; n++) {
    input.click(); input.focus(); await pause(400);
    document.execCommand('selectAll', false, null);
    document.execCommand('insertText', false, QUERY);
    await pause(300);
    for (const type of ['keydown', 'keypress', 'keyup']) input.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }));
    for (const t0 = Date.now(); latest() === before && Date.now() - t0 < 5000;) await pause(250);
  }
  const tv = latest();
  if (!tv || tv === before) return ['search did not start'];
  for (const t0 = Date.now(); (tv.isLoading || !tv.isInitialLoadComplete) && Date.now() - t0 < 15000;) await pause(250);
  const scope = tv.tableQuery.searchScope || {};
  return ['searched', 'scope ' + (scope.kind === 'PrimaryMailbox' ? 'all folders' : scope.kind === 'ArchiveMailbox' ? 'archive mailbox' : String(scope.kind)), 'rows ' + Array.from(tv.rowKeys).length + (tv.totalRowsInView > Array.from(tv.rowKeys).length ? ', more on the server' : '')];
})('received>=2026-10-19');
```

`scope` must say `all folders` for the main search and `archive mailbox` for the archive's. Anything else means the
search started from another folder (a search follows the open folder): navigate to `/mail/` and search again. It
doesn't prove the converse: seen once, a search started from Deleted Items also said `all folders` (and gave Deleted
Items' rows). So run the main search only right after a navigate to `/mail/`, and before the archive's search check
that the archive really is the open folder. Seen once: the folders snippet's `archive` answered `archive mailbox
selected` while OWA stayed on the folder before, since the archive node was below the fold of a short window and the
click didn't switch the list. Check (not yet tried): the open folder view's `tableQuery.folderId` must be a folder
whose `mailboxInfo.type` is `ArchiveMailbox`; a screenshot shows it too (the search box names the open folder). A row
count equal to the last folder's is the tell after the fact. Recover: close the search (the ribbon's "Close search"
button), scroll the folder pane until the archive node is in view, select it again, check, then search. If it still
won't switch, skip the archive and say so when you report the run; on a daily window it is almost always empty.

The more snippet: nothing to fill in. It scrolls the list to its end until more rows load.

```js
await (async () => {
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const pause = async (ms) => { const t = Date.now(); while (Date.now() - t < ms) await tick(); };
  const g = globalThis.__satchelGlobalContext;
  const lv = g && g.rootStore.get('listview');
  if (!lv) return ['no OWA data'];
  const viewOf = () => { for (const row of document.querySelectorAll('[role="option"]')) { const k = Object.keys(row).find((x) => x.startsWith('__reactFiber$')); let f = k ? row[k] : null; for (let n = 0; f && n < 6; n++, f = f.return) { const p = f.memoizedProps && f.memoizedProps.mailListTableProps; if (p && lv.tableViews.get(p.tableViewId)) return lv.tableViews.get(p.tableViewId); } } return null; };
  let tv = null;
  for (const t0 = Date.now(); !(tv = viewOf()) && Date.now() - t0 < 6000;) await pause(250);
  const sc = document.querySelector('[data-virtuoso-scroller]');
  if (!tv || !sc) return ['no rows painted: take a screenshot at 0.1, then run this again'];
  const count = () => Array.from(tv.rowKeys).length;
  const before = count();
  for (let n = 0; n < 3 && count() === before && tv.totalRowsInView > before; n++) {
    sc.scrollTop = sc.scrollHeight; sc.dispatchEvent(new Event('scroll'));
    for (const t0 = Date.now(); count() === before && Date.now() - t0 < 4000;) await pause(200);
  }
  return ['rows ' + before + ' to ' + count(), tv.totalRowsInView > count() ? 'more on the server' : 'all loaded'];
})();
```

The open snippet: a row's id, as the rows script printed it, on its last line. It never opens a Drafts row.

```js
await (async (ID) => {
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const pause = async (ms) => { const t = Date.now(); while (Date.now() - t < ms) await tick(); };
  const id = String(ID || '').replace(/¤e/g, '=').replace(/-/g, '+').replace(/_/g, '/');
  const g = globalThis.__satchelGlobalContext;
  const lv = g && g.rootStore.get('listview');
  const mail = g && g.rootStore.get('mail');
  const folders = g && g.rootStore.get('folderStore').folderTable;
  if (!lv || !mail) return ['no OWA data', ID];
  const list = (v) => (v ? Array.from(v) : []);
  const rels = [...lv.tableViewConversationRelations.values()].filter((r) => r.id === id);
  const isItem = !rels.length;
  const nodes = () => { const c = mail.conversations.get(id); return c ? list(c.conversationNodeIds).map((n) => mail.conversationNodes.get(n)).filter(Boolean) : []; };
  const items = () => nodes().flatMap((n) => list(n.itemIds)).map((i) => mail.items.get(i)).filter(Boolean);
  const loaded = () => {
    if (isItem) { const x = mail.items.get(id); return !!(x && (x.NormalizedBody || x.UniqueBody)); }
    const c = mail.conversations.get(id);
    return !!c && !(c.loadingState && c.loadingState.isLoading) && nodes().length > 0 && items().every((x) => x.IsDraft || x.UniqueBody || x.NormalizedBody);
  };
  const last = Math.max(0, ...rels.map((r) => Date.parse(r.lastDeliveryTimeStamp) || 0));
  const newest = () => Math.max(0, ...items().map((x) => Date.parse(x.DateTimeReceived) || 0));
  if (loaded() && (isItem || newest() >= last - 2000)) return ['loaded already', ID, 'messages ' + (isItem ? 1 : nodes().length)];
  const type = (fid) => { const f = folders && fid ? folders.get(fid) : null; return f ? f.distinguishedFolderType || '' : ''; };
  const x = mail.items.get(id);
  if (rels.some((r) => type(r.parentFolderId) === 'drafts') || (isItem && x && x.IsDraft)) return ['a draft: not opened', ID];
  const unread = isItem ? (x && !x.IsRead ? 1 : 0) : Math.max(0, ...rels.map((r) => r.unreadCount || 0));
  const propsOf = (row) => { const k = Object.keys(row).find((y) => y.startsWith('__reactFiber$')); let f = k ? row[k] : null; for (let n = 0; f && n < 6; n++, f = f.return) if (f.memoizedProps && f.memoizedProps.mailListItemDataProps) return f.memoizedProps.mailListItemDataProps; return null; };
  const rowOf = () => [...document.querySelectorAll('[role="option"]')].find((r) => { const p = propsOf(r); return p && p.rowId === id; });
  for (const t0 = Date.now(); !document.querySelector('[role="option"]') && Date.now() - t0 < 6000;) await pause(250);
  const sc = document.querySelector('[data-virtuoso-scroller]');
  if (!sc || !document.querySelector('[role="option"]')) return ['no rows painted: take a screenshot at 0.1, then run this again', ID];
  let row = rowOf();
  if (!row) {
    sc.scrollTop = 0; sc.dispatchEvent(new Event('scroll')); await pause(300);
    for (const t0 = Date.now(); !(row = rowOf()) && Date.now() - t0 < 25000;) {
      const end = sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 2;
      sc.scrollTop += 300; sc.dispatchEvent(new Event('scroll'));
      await pause(end ? 1500 : 250);
      if (end && sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 2 && !rowOf()) break;
    }
  }
  if (!row) return ['no row', ID];
  row.click();
  for (const t0 = Date.now(); !loaded() && Date.now() - t0 < 12000;) await pause(250);
  return [loaded() ? 'opened' : 'not loaded', ID, unread ? 'was unread' : 'was read', 'messages ' + (isItem ? 1 : nodes().length)];
})('<row id>');
```

`not loaded`: run the snippet once more. `no row`: the row isn't in this list; the search or folder changed, so list
again.

The folders snippet: `''` lists the folders the search leaves out; a folder id (as listed) opens that folder in the
list; `archive` selects the online archive for a search; `inbox` goes back to the Inbox. It reads folders by their
kind, never by name, so a folder added later is listed without a change here.

```js
await (async (OPEN) => {
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const pause = async (ms) => { const t = Date.now(); while (Date.now() - t < ms) await tick(); };
  const g = globalThis.__satchelGlobalContext;
  const ft = g && g.rootStore.get('folderStore').folderTable;
  const lv = g && g.rootStore.get('listview');
  if (!ft || !lv) return ['no OWA data'];
  const list = (v) => (v ? Array.from(v) : []);
  const safe = (id) => String(id).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '¤e');
  const plain = (id) => String(id || '').replace(/¤e/g, '=').replace(/-/g, '+').replace(/_/g, '/');
  const kind = (f) => f.distinguishedFolderType || 'custom';
  const box = (f) => (f.mailboxInfo && f.mailboxInfo.type) || '';
  const fiberProp = (el, test) => { const k = Object.keys(el).find((x) => x.startsWith('__reactFiber$')); let f = k ? el[k] : null; for (let n = 0; f && n < 12; n++, f = f.return) { const p = f.memoizedProps || {}; for (const v of Object.values(p)) if (test(v)) return v; } return null; };
  const nodeOf = (id) => [...document.querySelectorAll('[role="treeitem"]')].find((e) => fiberProp(e, (v) => v === id));
  const archiveNode = () => [...document.querySelectorAll('[role="treeitem"][aria-level="1"]')].find((e) => fiberProp(e, (v) => v && typeof v === 'object' && v.type === 'ArchiveMailbox' && 'mailboxSmtpAddress' in v));
  // What the All-folders search leaves out: Junk Email and Deleted Items; the Archive folder is read
  // apart to be safe; and every folder under them.
  const APART = new Set(['junkemail', 'deleteditems', 'archive']);
  const apart = [];
  const under = (id, parent) => { const f = ft.get(id); if (!f) return; apart.push([id, f, parent]); for (const c of list(f.childFolderIds)) under(c, kind(f)); };
  for (const [id, f] of ft) if (box(f) !== 'ArchiveMailbox' && APART.has(kind(f))) under(id, 'msgfolderroot');
  if (!OPEN) return ['folders apart ' + apart.length, ...apart.map(([id, f, parent]) => ['f', safe(id), kind(f), parent, f.totalMessageCount || 0, f.UnreadCount || 0].join('\t')), 'archive mailbox ' + (archiveNode() ? 'yes' : 'no')];
  if (OPEN === 'archive') {
    const node = archiveNode();
    if (!node) return ['no archive mailbox'];
    node.click();
    await pause(2000);
    return ['archive mailbox selected: search now'];
  }
  const id = OPEN === 'inbox' ? ([...ft.entries()].find(([, f]) => kind(f) === 'inbox' && box(f) !== 'ArchiveMailbox') || [])[0] : plain(OPEN);
  const f = id && ft.get(id);
  if (!f) return ['no such folder', OPEN];
  let node = nodeOf(id);
  if (!node) {
    // A folder under a collapsed one: expand the folders above it first.
    const chain = [];
    for (let at = id; ;) { const up = [...ft.entries()].find(([, x]) => list(x.childFolderIds).includes(at)); if (!up) break; chain.push(up[0]); at = up[0]; }
    for (const pid of chain.reverse()) { const pn = nodeOf(pid); const b = pn && pn.getAttribute('aria-expanded') === 'false' ? pn.querySelector('button') : null; if (b) { b.click(); await pause(800); } }
    node = nodeOf(id);
  }
  if (!node) return ['no folder in the pane', OPEN];
  node.click();
  let tv = null;
  for (const t0 = Date.now(); Date.now() - t0 < 10000; await pause(250)) {
    tv = [...lv.tableViews.values()].find((t) => t.tableQuery && t.tableQuery.type === 0 && t.tableQuery.folderId === id && t.isInitialLoadComplete && !t.isLoading);
    if (tv) break;
  }
  return [tv ? 'opened folder' : 'not loaded', OPEN, kind(f), tv ? 'rows ' + list(tv.rowKeys).length + ' of ' + tv.totalRowsInView : ''];
})('');
```

The restore snippet: the ids of the list's rows that were unread when the rows script listed them, as printed, on its
last line. Per row it marks unread only a row that is read now, with the row's own "Mark as unread" button (shown on
hover; it doesn't select the row), and reads the row's state again after. It answers with counts, and the ids of any
row that didn't take.

```js
await (async (IDS) => {
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const pause = async (ms) => { const t = Date.now(); while (Date.now() - t < ms) await tick(); };
  const plain = (id) => String(id || '').replace(/¤e/g, '=').replace(/-/g, '+').replace(/_/g, '/');
  const propsOf = (row) => { const k = Object.keys(row).find((y) => y.startsWith('__reactFiber$')); let f = k ? row[k] : null; for (let n = 0; f && n < 6; n++, f = f.return) if (f.memoizedProps && f.memoizedProps.mailListItemDataProps) return f.memoizedProps.mailListItemDataProps; return null; };
  const rowOf = (id) => [...document.querySelectorAll('[role="option"]')].find((r) => { const p = propsOf(r); return p && p.rowId === id; });
  const unread = (id) => { const r = rowOf(id); const p = r && propsOf(r); return p ? p.unreadCount || 0 : -1; };
  for (const t0 = Date.now(); !document.querySelector('[role="option"]') && Date.now() - t0 < 6000;) await pause(250);
  const sc = document.querySelector('[data-virtuoso-scroller]');
  if (!sc || !document.querySelector('[role="option"]')) return ['no rows painted: take a screenshot at 0.1, then run this again'];
  const find = async (id) => {
    let row = rowOf(id);
    if (row) return row;
    sc.scrollTop = 0; sc.dispatchEvent(new Event('scroll')); await pause(300);
    for (const t0 = Date.now(); !(row = rowOf(id)) && Date.now() - t0 < 20000;) {
      const end = sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 2;
      sc.scrollTop += 300; sc.dispatchEvent(new Event('scroll'));
      await pause(end ? 1500 : 250);
      if (end && sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 2 && !rowOf(id)) break;
    }
    return row;
  };
  const count = { restored: 0, 'still unread': 0, 'still read': 0, 'no row': 0 };
  const failed = [];
  for (const ID of IDS) {
    const id = plain(ID);
    const row = await find(id);
    if (!row) { count['no row']++; failed.push('no row ' + ID); continue; }
    if (unread(id) > 0) { count['still unread']++; continue; }
    const box = row.getBoundingClientRect();
    row.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, clientX: box.left + 100, clientY: box.top + box.height / 2 }));
    let button = null;
    for (const t0 = Date.now(); !button && Date.now() - t0 < 2000; await pause(150)) button = [...row.querySelectorAll('button, [role="button"]')].find((b) => /^mark as unread$/i.test((b.getAttribute('aria-label') || b.getAttribute('title') || '').trim()));
    if (button) button.click();
    for (const t0 = Date.now(); button && unread(id) <= 0 && Date.now() - t0 < 4000;) await pause(200);
    row.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }));
    if (unread(id) > 0) count.restored++; else { count['still read']++; failed.push('still read ' + ID); }
    await pause(300);
  }
  return [...Object.entries(count).map(([k, v]) => k + ' ' + v), ...failed];
})(['<row id>']);
```

`still read`: run it once more for those ids; still read, say so with the count when you report the run. `no row`:
the conversation left this list (moved, or the search changed): look for it in the folder its row named.

### How the page behaves

Seen on one tenant's OWA, read only, in a tab of its own, opening only mail already read. Microsoft changes the page;
the scripts' header counts show when it has.

- OWA keeps its data in `globalThis.__satchelGlobalContext.rootStore`: `listview` (the lists and their rows), `mail`
  (conversations, their nodes and messages), `folderStore.folderTable` (folder names and types) and `readingpane`.
  The scripts read only these. Every loaded row is there, painted or not, so nothing depends on the list's paint.
- Ids: a message's id is an Exchange immutable id (`AAkA…`, 68 characters, with `+` and `/`), the layout the calendar
  example finds too; a move between folders keeps it. A conversation id is 80 characters ending in `=`. Search rows
  carry the message's ordinary EWS id (`AAMk…`, 152 characters), never the immutable one, so a search row can't be
  matched to a kept message; Junk Email and Inbox folder rows carry the immutable id.
- The Inbox and the All-folders search show conversations; Junk Email shows messages. A row has the sender with
  address, subject, last delivery, unread count, messages in the conversation, folder, attachment flag, importance,
  draft ids and a 255-character preview, but no body.
- Bodies: OWA fetches the top ten or so Inbox conversations ahead, bodies included, without marking them read. Any
  other conversation loads when its row is clicked: every message of it from every folder (Inbox, Sent Items,
  subfolders), collapsed ones too, with To, Cc and Bcc with addresses, attachments and the body, in 1 to 2 s. It stays
  loaded until a navigate. The reading pane's DOM shows only the expanded message, its time to the minute and its
  sender without an address, so the scripts don't read it.
- A conversation's message holds its own part (Exchange's UniqueBody). The quoted text Exchange keeps apart only when it
  isn't another message of the conversation, chiefly a forward's original, as HTML parts each starting with
  `From:`/`Sent:`; the read script adds them after the own part. A reply's quote of an earlier message has none: that
  message is its own record. A message opened alone (Junk Email) has its whole body.
- One message can sit in two folders (Sent Items and the Inbox, when you are on the list it went to): one node, two
  items with the same internet message id. The read script gives one line, the smaller id, with `copies:1` in its
  flags.
- Mark as read: with "when the selection changes", a clicked conversation turns read when the next one is clicked. The
  open snippet says `was unread`.
- Mark as unread: every row, in a search, a folder in conversation view and Junk Email's message view, has its own
  "Mark as unread" (when read) or "Mark as read" (when unread) button, shown on hover; the restore snippet uses it. A
  row's right-click menu isn't reliable: a dispatched `contextmenu` opened no menu, or one late enough to catch the
  next event, and a real right-click selects and opens the row. The shortcut needs a selected row. Neither is used.
- Search: `received>=<date>` from the Inbox searches All folders (`PrimaryMailbox`). Checked once against every folder
  with mail in nine days: every conversation of the Inbox, Sent Items and the custom folders rules file mail into was
  in it. Junk Email's weren't. Deleted Items' messages weren't either: their conversations showed only by other
  folders' messages, with an older last delivery, though opening such a conversation does load its deleted messages.
  A conversation only in Deleted Items never shows, so Junk Email and Deleted Items are read by folder. The Archive
  folder, Conversation History and Scheduled were empty when checked, so untested; the Archive folder is read apart to
  be safe, the other two aren't mail.
- Results are newest first, 25 at first; scrolling the list to its end loads the rest, about 125 at a time. While more
  wait, the view's total is one more than the rows loaded. KQL `OR` returns nothing; one term per search. A search
  follows the open folder: from a folder it searches only that folder, so search from the Inbox.
- The online archive, where a mailbox has one, is a mailbox of its own: only archiving moves mail there, and inbox
  rules can't. The All-folders search leaves it out; selecting its node in the folder pane and searching searches all
  of it (scope `ArchiveMailbox`). Its folders load into OWA's folder data only when its node is expanded. The node
  sits at the bottom of the folder pane, and a click on it below the fold may not switch the list: check before
  searching, as under the search snippet.
- The list paints a moment after OWA has the rows; in a hidden tab it can stay unpainted until a screenshot. The open
  and more snippets say `no rows painted` then.
- A Drafts row opens compose: never open one. Drafts inside a conversation are left out by the read script.
- `DOMParser` works in OWA (Teams enforces Trusted Types, OWA doesn't); the parsed document is inert, nothing in it runs
  or loads.
- Chrome's filters, on what a call returns: about 1,000 characters a string, 100 items an array; a string with `=` and
  `&` or `;` is blocked as a query string, a whole string of three dot-separated words as a JWT, a whole string of
  base64-looking letters as base64 (even 995 `x`). Every script string starts with a tag and a tab, which the JWT and
  base64 checks pass, and `=` goes out as `¤e`: Safe Links, a link with `?a=1&b=2`, a Teams join link and
  `mailto:...?subject=` all came back whole. The scripts return at most 95 strings and 30,000 characters (JSON escapes
  counted), lines in 900-character pieces. A blocked or cut piece sets its message aside in the mapper.
- At most about 45 s a call. A full navigate clears OWA's data and anything defined on `window`.

## The window

`run_open` gives the window: the latest mark less the lookback (`lookback_days: 1` under `sources.outlook` in
`vault.yaml`), to now. Every read in the run is captured and kept, inside Docket's window or not.

1. Search `received>=<the local date of the start>`, read the rows from the start, and load more until the oldest
   loaded row is older than the start or none are left. Note each row whose unread count is above 0.
2. Open and read each row in the window, newest first, so the newest mail is captured before the budget runs out.
   Give the read script the start too: it returns the messages received since then, and counts the older ones of the
   thread without returning them.
3. Then each folder apart that has mail (the folders snippet lists them), the same way: open it, its rows, its rows
   in the window opened and read, unread ones noted. A conversation read twice (by the search and from Deleted Items)
   makes one record.
4. Then the online archive, if there is one: select it, search it with the same query, and read its rows the same
   way. On a daily window it is almost always empty.
5. After the close, restore unread, list by list: the same search, each folder apart, the archive's search.

## A run

Docket gives the run's steps: what to skip, what is owed, the budget and how to close. This is what is Outlook's own.

| Part | What |
|---|---|
| Sign in | Microsoft's sign-in page or an account picker instead of OWA: stop without closing |
| List | the search's rows, each folder apart's and the archive's, as above; the rows make no record. Note the ids of the rows listed as unread, per list |
| Each row | `__dkOR` (the open snippet, then the read script) and its later parts. `not loaded` twice: skip the row as `error` by its id |
| Data missing | the rows header's rows-read-from-data is 0 while rows are loaded, or a read comes back with mode `pane`: OWA changed. Stop without closing, or every message would get a new rid, and say so when you report the run |
| Held | a row whose id is held isn't opened; a held message or conversation in a read is dropped by the mapper |
| Kept | search rows carry conversation ids and no message ids, so a kept message is read again and makes no new version. A Junk Email row whose id is kept needn't be opened |
| Other calls | while the run is open, `javascript_tool` runs only the define call, the functions it defines, the two scripts and the search, more, open and folders snippets: every call is captured. Screenshots aren't, and are fine. The restore waits until after the close |
| Too large | under the scripts' caps it can't happen; if it does, skip the row as `too-large` by its id |
| Budget | past it, open no more rows; skip each one left as `later` by its id |
| By id | a retry id is a row's id (a conversation, or a Junk Email message): search from the retry's `since` date, or list the folders apart, and open and read its row. No row: skip it as `not-listed` |
| Close | `ingest(run, captures=<highest n>, skipped=[...])`; "another run is closing" means call it again a minute later. Then `run_open(source="outlook", final=true)` answers that no final run is needed |
| Unread | after the close, before any other run opens: show each list again (navigate to `/mail/` and the same search; the folders snippet for each folder apart; `archive` and the search for the archive), run the restore snippet with that list's noted ids, then once more with the same ids as the check. Say `restored <n>, still read <n>` across all lists. Close your tab last |

## What it makes

One record per message, kind `mail`:

| Field | What |
|---|---|
| rid | `outlook:<message id>`, the immutable id in the URL-safe form |
| thread | `outlook:<conversation id>`, the same form |
| ts | when it was sent, in UTC |
| author | `Name <address>` of From |
| to | To, then Cc, each `Name <address>` |
| title | the subject |
| text | the body as the pane shows it: its own part, then any quoted text Exchange kept apart for it (a forward's original); passcodes and secrets taken out |
| attachments | by name only: `file`, `item` (an attached mail or event) or `reference` (a cloud link); inline images left out |
| native | id, conversation, internet message id, sent, received, on-behalf sender, Bcc, folder and folder type, flags (`unread`, `importance:<level>`, `class:<item class>`, `body:own` or `body:whole`, `quoted:kept` or `quoted:thread`, `inline:<n>`, `copies:<n>`, `truncated`), each file's type and size, and how it was read |
| fidelity | verbatim; `derived` when the page gave no id |

- An edited body (a recall and resend is another message), a new attachment or a new recipient is a new version; a move
  between folders, the read state, flags and copies aren't.
- Without an id (the pane read as shown), the rid is `outlook:d-<hash of sender, sent second, subject and body
  start>`, `fidelity: derived`, and the run stops before closing, as above.
- With `sensitivity: work`, the records go straight to ready. A conversation on `never_store.containers` in
  `vault.yaml` (its id as the rows print it with `¤e` read as `=`, with or without `outlook:`) is dropped by the gate
  before staging, since the gate matches the thread too; a sender on `never_store.people` is dropped the same way.
- The ids are the ones Microsoft Graph gives with `Prefer: IdType="ImmutableId"`, in its URL-safe form, so a later Graph
  route can keep the rids. Check that on one message before relying on it.

## Changing the mapper

Every record the lookback reads again gets a new version when the mapper's output changes.

- Keep the output stable: change it only for a real change in what the mail says.
- Pin golden versions in the tests, as `tests/test_with_docket.py` does, so a change shows before it runs.
- A change to what a script returns gets a new version line (`docket:mail-read/2`), and the mapper learns it before the
  script ships.
- Pull the project on every machine before its next sync, so two machines never map one message two ways.
