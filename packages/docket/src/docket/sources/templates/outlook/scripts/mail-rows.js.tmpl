// docket:mail-rows/1: the mail list in Outlook on the web (a search's results or a folder), read only. No clicks,
// no scrolls, nothing typed. Paste it whole as javascript_tool's text, with the window's start (UTC ISO, or '' for
// every loaded row) in place of the '' on the last line. When the last string is "more<TAB><n>", run it again with
// <n> in place of the 0 there. mapper.py knows the version line and makes no record of it: rows only say what to open.
//
// The rows come from OWA's own data behind the list, so every loaded row is read, painted or not. A header, then one
// string per row, newest first, tab-separated:
//   r  <kind>  <id>  <conversation>  <time, UTC>  <unread>  <messages>  <folder>  <folder type>  <flags>  <sender>
//      <sender address>  <subject>
// kind is c for a conversation row (the All-folders search, a folder in conversation view) and i for a message row
// (a folder in message view, such as Junk Email). The id is what the open snippet and mail-read.js take: the
// conversation's id for c, the message's immutable id for i, in the URL-safe form (+ as -, / as _). The time is the
// row's last delivery; messages is how many the conversation holds in all folders. In every field ¤ is ¤¤, = is ¤e
// and a tab ¤t, since Chrome blocks a string holding = with & or ;.
await (async (SINCE, FROM) => {
  const VERSION = 'docket:mail-rows/1';
  const MAX_STRINGS = 95; // Chrome keeps 100 items of an array
  const MAX_CHARS = 30000; // the capture hook keeps a result under 50,000 characters, JSON escapes included
  const since = SINCE ? Date.parse(SINCE) || 0 : 0;

  // A hidden tab throttles setTimeout, so short waits yield through MessageChannel.
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const pause = async (ms) => { const t = Date.now(); while (Date.now() - t < ms) await tick(); };

  const one = (s, max) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim().slice(0, max || 300)
    .replace(/¤/g, '¤¤').replace(/=/g, '¤e').replace(/\t/g, '¤t');
  const safe = (id) => one(String(id || '').replace(/\+/g, '-').replace(/\//g, '_'), 600);
  const iso = (v) => {
    if (!v) return '';
    const ms = new Date(v).getTime();
    return Number.isFinite(ms) ? new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z') : '';
  };
  const list = (v) => (v ? Array.from(v) : []);

  const root = globalThis.__satchelGlobalContext && globalThis.__satchelGlobalContext.rootStore;
  const get = (k) => (root && root.get ? root.get(k) : null);
  const lv = get('listview');
  const mail = get('mail');
  const folders = (get('folderStore') || {}).folderTable;

  // The table view on screen: the one a painted row belongs to, else the latest search's.
  const viewOf = () => {
    if (!lv) return null;
    for (const row of document.querySelectorAll('[role="option"]')) {
      const key = Object.keys(row).find((k) => k.startsWith('__reactFiber$'));
      let f = key ? row[key] : null;
      for (let n = 0; f && n < 6; n++, f = f.return) {
        const p = f.memoizedProps && f.memoizedProps.mailListTableProps;
        if (p && p.tableViewId && lv.tableViews.get(p.tableViewId)) return lv.tableViews.get(p.tableViewId);
      }
    }
    let best = null;
    for (const tv of lv.tableViews.values()) {
      if (tv.tableQuery && tv.tableQuery.type === 1 && (!best || tv.tableQuery.searchNumber > best.tableQuery.searchNumber)) best = tv;
    }
    return best;
  };

  // The list has settled when two looks 300 ms apart show the same view, not loading, with the same rows.
  let tv = null;
  let count = -1;
  for (const t0 = Date.now(); Date.now() - t0 < 6000; await pause(300)) {
    const v = viewOf();
    const n = v ? list(v.rowKeys).length : -1;
    if (v && v === tv && n === count && !v.isLoading) break;
    tv = v;
    count = n;
  }

  const folderOf = (id) => {
    const f = folders && id ? folders.get(id) : null;
    return f ? [f.displayName || '', f.distinguishedFolderType || ''] : ['', ''];
  };
  const rows = [];
  let fromData = 0;
  let older = 0;
  let oldest = '';
  const items = tv && tv.tableQuery && tv.tableQuery.listViewType === 1;
  for (const key of tv ? list(tv.rowKeys) : []) {
    let row = null;
    if (items) {
      const rel = lv.tableViewItemRelations && lv.tableViewItemRelations.get(tv.id + key);
      const it = rel && rel.clientId && mail ? mail.items.get(rel.clientId.Id) : null;
      if (it && it.ItemId) {
        const box = (it.From && it.From.Mailbox) || (it.Sender && it.Sender.Mailbox) || {};
        const flags = [];
        if (it.HasAttachments) flags.push('att');
        if (it.IsDraft) flags.push('draft');
        if (/^high$/i.test(it.Importance || '')) flags.push('high');
        if (/Schedule\.Meeting/i.test(it.ItemClass || '')) flags.push('meeting');
        row = ['i', safe(it.ItemId.Id), safe(it.ConversationId && it.ConversationId.Id), iso(it.DateTimeReceived || it.DateTimeSent),
          it.IsRead ? 0 : 1, 1, ...folderOf(it.ParentFolderId && it.ParentFolderId.Id), flags.join(','), box.Name, box.EmailAddress,
          it.Subject];
      }
    } else {
      const rel = lv.tableViewConversationRelations.get(tv.id + key);
      if (rel && rel.id) {
        const conv = lv.conversationItems.get(rel.id);
        const box = (rel.lastSender && rel.lastSender.Mailbox) || {};
        const flags = [];
        if (rel.hasAttachments) flags.push('att');
        if (list(rel.draftItemIds).length) flags.push('drafts:' + list(rel.draftItemIds).length);
        if (/^high$/i.test(rel.importance || '')) flags.push('high');
        if (list(rel.itemClasses).some((c) => /Schedule\.Meeting/i.test(c))) flags.push('meeting');
        if (/^flagged$/i.test(rel.flagStatus || '')) flags.push('flagged');
        row = ['c', safe(rel.id), safe(rel.id), iso(rel.lastDeliveryTimeStamp), rel.unreadCount || 0,
          conv ? conv.globalMessageCount : '', ...folderOf(rel.parentFolderId), flags.join(','), box.Name, box.EmailAddress,
          conv ? conv.subject : ''];
      }
    }
    if (!row) continue;
    fromData++;
    if (row[3] && (!oldest || row[3] < oldest)) oldest = row[3];
    if (since && row[3] && Date.parse(row[3]) < since) { older++; continue; }
    rows.push('r\t' + row.map((v, n) => (n >= 6 ? one(v, n === 11 ? 300 : 160) : String(v))).join('\t'));
  }

  const q = tv ? tv.tableQuery || {} : {};
  const view = !tv ? '' : q.type === 1 ? 'search' : 'folder';
  const name = !tv ? '' : q.type === 1 ? q.queryString || '' : folderOf(q.folderId)[0];
  const loaded = tv ? list(tv.rowKeys).length : 0;
  const total = tv ? tv.totalRowsInView || 0 : 0;
  const header = ['rows', view, one(name), !tv ? '' : items ? 'messages' : 'conversations', loaded, total,
    total > loaded ? 1 : 0, oldest, one(SINCE), rows.length, FROM, fromData];
  const out = [VERSION, header.join('\t')];
  let used = out.join('').length;
  let i = FROM;
  for (; i < rows.length; i++) {
    const size = rows[i].length + 12;
    if (out.length + 2 > MAX_STRINGS || used + size > MAX_CHARS) break;
    out.push(rows[i]);
    used += size;
  }
  if (i < rows.length) out.push('more\t' + i);
  return out;
})('', 0);
