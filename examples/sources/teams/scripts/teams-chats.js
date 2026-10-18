// docket:teams-chats/1: the Teams chat list on screen, read only. No clicks, no scrolls, nothing typed.
// Paste it whole as javascript_tool's text. When the last string is "more<TAB><n>", run it again with <n> in place of
// the 0 on the last line. mapper.py knows this version and makes no record from it: the list says which chats to open.
//
// A header, then one string per chat row in the list's order (newest activity first), tab-separated:
//   c  <chat id>  <kind>  <unread 1|0>  <last activity, UTC>  <muted 1|0>  <name>
// The chat id is the row's own (19:...@thread.v2, 19:..._...@unq.gbl.spaces, 19:meeting_...@thread.v2); kind is
// one-on-one, group or meeting; unread and last activity come from the conversation's own data behind the row, unread
// falling back to the row's "unread" label, since the list in compact density shows no time. In the name, ¤ is ¤¤, =
// is ¤e and a tab ¤t, so Chrome's query-string filter never blocks a row.
await (async (FROM) => {
  const VERSION = 'docket:teams-chats/1';
  const MAX_STRINGS = 95; // Chrome keeps 100 items of an array
  const MAX_CHARS = 30000; // the capture hook keeps a result under 50,000 characters, JSON escapes included

  // A hidden tab throttles setTimeout, so short waits yield through MessageChannel.
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const pause = async (ms) => { const t = Date.now(); while (Date.now() - t < ms) await tick(); };
  const esc = (s) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim()
    .replace(/¤/g, '¤¤').replace(/=/g, '¤e').replace(/\t/g, '¤t');

  const ROWS = '[role="treeitem"][data-item-type="chat"]';
  // The list has settled when two looks 300 ms apart show the same rows.
  let count = -1;
  for (const t0 = Date.now(); Date.now() - t0 < 10000; await pause(300)) {
    const n = document.querySelectorAll(ROWS).length;
    if (n > 0 && n === count) break;
    count = n;
  }

  const conversationOf = (row) => {
    const key = Object.keys(row).find((k) => k.startsWith('__reactFiber$'));
    let f = key ? row[key] : null;
    for (let n = 0; f && n < 30; n++, f = f.return) {
      const c = f.memoizedProps && f.memoizedProps.conversation;
      if (c && typeof c.internalId === 'string') return c;
    }
    return null;
  };
  const kindOf = (value, id) => {
    const t = (value.match(/OneGQL_(\w+?)Conversation\|/) || [])[1] || '';
    if (/^OneOnOne/.test(t) || /@unq\.gbl\.spaces$/.test(id)) return 'one-on-one';
    if (/^Meeting/.test(t) || /^19:meeting_/.test(id)) return 'meeting';
    return t || /@thread\.v2$/.test(id) ? 'group' : '';
  };

  const rows = [];
  let fromData = 0;
  let unreadRows = 0;
  for (const row of document.querySelectorAll(ROWS)) {
    const value = row.getAttribute('data-fui-tree-item-value') || '';
    const id = value.slice(value.lastIndexOf('|') + 1);
    const c = conversationOf(row);
    const useData = !!c && c.internalId === id;
    if (useData) fromData++;
    const labelled = /\bchat_list_unread_text\b/.test(row.getAttribute('aria-labelledby') || '');
    const unread = useData && c.activityStatus ? !!c.activityStatus.hasUnreadMessage : labelled;
    if (unread) unreadRows++;
    const title = id ? document.getElementById('title-chat-list-item_' + id) : null;
    const name = (title && title.textContent) || (useData && c.title) || '';
    const last = useData && c.lastMessageArrivalTime ? new Date(c.lastMessageArrivalTime) : null;
    rows.push(['c', esc(id), kindOf(value, id), unread ? 1 : 0,
      last && Number.isFinite(last.getTime()) ? last.toISOString() : '', useData && c.isMuted ? 1 : 0, esc(name)].join('\t'));
  }

  const out = [VERSION, ['chats', rows.length, FROM, fromData, unreadRows].join('\t')];
  let used = out.join('').length;
  let i = FROM;
  for (; i < rows.length; i++) {
    if (out.length + 2 > MAX_STRINGS || used + rows[i].length + 12 > MAX_CHARS) break;
    out.push(rows[i]);
    used += rows[i].length + 12;
  }
  if (i < rows.length) out.push('more\t' + i);
  return out;
})(0);
