// docket:teams-chat/1: the chat open in Teams, read only. No clicks, no scrolls, nothing typed.
// Paste it whole as javascript_tool's text, with the window's start (UTC ISO, or '' for every loaded message) in place
// of the '' on the last line. When the last string is "more<TAB><n>", run it again with <n> in place of the 0 there.
// mapper.py reads only results whose first string is the version line.
//
// A header, then one line per message people wrote, in the pane's order (oldest first); system lines (joined, added,
// renamed, meeting started or ended, recordings) are left out. A message line's fields, tab-separated:
//   m  <strings it takes>  <id>  <time, UTC>  <author>  <user id>  <edited, UTC>  <flags>  <quoted id>
//      <quoted author>  <quoted preview>  <attachments>  <text>
// The id is the message's data-mid, the time its time[datetime], the text what the page shows, emoji as their
// character. Author, user id, edit time, files and image ids come from the message's own data behind the element;
// without it the flags say `shown` and the author is the one shown, carried forward from the message before. A quote
// is the message it replies to: its id, author and the preview Teams shows. Attachments are kind¤:ref¤:name, joined by
// ¤;. In every field ¤ is ¤¤, = is ¤e and a tab ¤t, since Chrome blocks a string holding = with & or ;. A line over
// 900 characters goes on in "+<TAB>..." strings, since Chrome cuts a string at about 1,000. Passcodes are taken out.
// Teams enforces Trusted Types, so no HTML is parsed here: the text is walked from the rendered message.
await (async (SINCE, FROM) => {
  const VERSION = 'docket:teams-chat/1';
  const MAX_STRINGS = 95; // Chrome keeps 100 items of an array
  const MAX_CHARS = 30000; // the capture hook keeps a result under 50,000 characters, JSON escapes included
  const PIECE = 900;
  const since = SINCE ? Date.parse(SINCE) || 0 : 0;

  // A hidden tab throttles setTimeout, so short waits yield through MessageChannel.
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const pause = async (ms) => { const t = Date.now(); while (Date.now() - t < ms) await tick(); };

  const esc = (s) => String(s == null ? '' : s).replace(/\r\n?/g, '\n')
    .replace(/\b(passcode|password|passwd|pwd|jelszó|jelszo)(\s*[:=]\s*)\S+/gi, '$1: (removed)')
    .replace(/¤/g, '¤¤').replace(/=/g, '¤e').replace(/\t/g, '¤t');
  const one = (s) => esc(String(s == null ? '' : s).replace(/\s+/g, ' ').trim());
  const iso = (v) => {
    if (!v) return '';
    const d = new Date(v);
    return Number.isFinite(d.getTime()) ? d.toISOString() : '';
  };
  const fiberProps = (el, test, depth) => {
    const key = Object.keys(el).find((k) => k.startsWith('__reactFiber$'));
    let f = key ? el[key] : null;
    for (let n = 0; f && n < depth; n++, f = f.return) {
      const p = f.memoizedProps;
      if (p && typeof p === 'object' && test(p)) return p;
    }
    return null;
  };
  const convOf = (el) => { const p = fiberProps(el, (q) => typeof q.convId === 'string', 12); return p ? p.convId : ''; };
  const dataOf = (el) => fiberProps(el, (q) => q.message && typeof q.message.id === 'string' && 'convId' in q, 10);

  const BODIES = '[data-tid="chat-pane-message"][data-mid]';
  // The pane has settled when two looks 300 ms apart show the same chat and the same number of items.
  let count = -1;
  let conv = '';
  for (const t0 = Date.now(); Date.now() - t0 < 6000; await pause(300)) {
    const any = document.querySelector('[data-tid="chat-pane-item"] [data-mid]');
    const c = any ? convOf(any) : '';
    const n = document.querySelectorAll(BODIES).length + document.querySelectorAll('[data-tid="control-message-renderer"]').length;
    if (any && n === count && c === conv) break;
    count = n;
    conv = c;
  }

  // The chat: its id from the pane's own data, else the selected row's; its name from the header.
  const rows = [...document.querySelectorAll('[role="treeitem"][data-item-type="chat"]')];
  const valueOf = (r) => r.getAttribute('data-fui-tree-item-value') || '';
  let chatId = conv;
  if (!chatId) {
    const sel = rows.find((r) => r.getAttribute('aria-selected') === 'true');
    if (sel) chatId = valueOf(sel).slice(valueOf(sel).lastIndexOf('|') + 1);
  }
  const row = chatId ? rows.find((r) => valueOf(r).endsWith('|' + chatId)) : null;
  const typename = row ? (valueOf(row).match(/OneGQL_(\w+?)Conversation\|/) || [])[1] || '' : '';
  const kind = /^OneOnOne/.test(typename) || /@unq\.gbl\.spaces$/.test(chatId) ? 'one-on-one'
    : /^Meeting/.test(typename) || /^19:meeting_/.test(chatId) ? 'meeting'
      : typename || /@thread\.v2$/.test(chatId) ? 'group' : '';
  const title = document.querySelector('h2[data-tid="chat-title"]');
  const rowTitle = chatId ? document.getElementById('title-chat-list-item_' + chatId) : null;
  // A group chat without a name shows its people's first names in spans of their own.
  const shown = title ? (title.innerText || title.textContent || '').split('\n').map((s) => s.trim()).filter(Boolean).join(', ') : '';
  const name = shown || (rowTitle && rowTitle.textContent) || '';

  // The text as rendered: emoji as their character, line breaks kept, the quote card and images left out (they are
  // fields of their own), table cells joined by |.
  const BLOCK = /^(P|DIV|LI|UL|OL|TR|TABLE|TBODY|THEAD|H[1-6]|PRE|BLOCKQUOTE|SECTION|CODEBLOCK)$/;
  const textOf = (root) => {
    let s = '';
    const walk = (n, raw) => {
      if (n.nodeType === 3) { s += raw ? n.nodeValue : n.nodeValue.replace(/\s+/g, ' '); return; }
      if (n.nodeType !== 1) return;
      const tag = n.tagName.toUpperCase();
      if (n.getAttribute('data-tid') === 'quoted-reply-card') return;
      if (tag === 'IMG') {
        if (/Emoji/i.test(n.getAttribute('itemtype') || '') || n.closest('[data-tid="emoticon-renderer"]')) s += n.getAttribute('alt') || '';
        return;
      }
      if (tag === 'BR') { s += '\n'; return; }
      if (tag === 'STYLE' || tag === 'SCRIPT' || tag === 'BUTTON' || tag === 'SVG') return;
      const block = BLOCK.test(tag);
      if (block && s && !s.endsWith('\n')) s += '\n';
      if (tag === 'LI') s += '- ';
      const keep = raw || tag === 'PRE' || tag === 'CODE' || tag === 'CODEBLOCK';
      for (const c of n.childNodes) walk(c, keep);
      if (tag === 'TD' || tag === 'TH') s += ' | ';
      if (block && !s.endsWith('\n')) s += '\n';
    };
    walk(root, false);
    return s.replace(/\xa0/g, ' ').replace(/ *\| *\n/g, '\n').replace(/[ \t]+\n/g, '\n').replace(/\n[ \t]+/g, '\n')
      .replace(/\n{3,}/g, '\n\n').trim();
  };
  // An attribute of a tag in the stored HTML, read by pattern: nothing is parsed.
  const attr = (tag, name) => { const m = tag.match(new RegExp('\\s' + name + '="([^"]*)"', 'i')); return m ? m[1] : ''; };

  const lines = [];
  let inPane = 0;
  let fromData = 0;
  let oldest = '';
  let newest = '';
  let author = '';
  for (const body of document.querySelectorAll(BODIES)) {
    const mid = body.getAttribute('data-mid') || '';
    const props = dataOf(body);
    if (chatId && props && props.convId && props.convId !== chatId) continue; // another chat's, still in the pane
    const m = props ? props.message : null;
    if (m && m.id !== mid) continue;
    inPane++;
    const timeEl = document.getElementById('timestamp-' + mid);
    const time = iso(timeEl && timeEl.getAttribute('datetime')) || iso(m && m.originalArrivalTime);
    if (time && (!oldest || time < oldest)) oldest = time;
    if (time && time > newest) newest = time;
    if (since && time && Date.parse(time) < since) continue;
    const authorEl = document.getElementById('author-' + mid);
    author = (m && m.imDisplayName) || (authorEl && authorEl.textContent) || author;
    const content = document.getElementById('content-' + mid);
    const flags = [];
    const atts = [];
    let quote = ['', '', ''];
    const card = content ? content.querySelector('[data-tid="quoted-reply-card"]') : null;
    if (card) {
      const qp = fiberProps(card, (p) => p.messageId != null && 'userName' in p, 8);
      const preview = card.querySelector('[data-tid="quoted-reply-preview-content"]');
      quote = [qp ? String(qp.messageId) : '', qp && qp.userName ? qp.userName : '', preview ? textOf(preview) : ''];
    }
    if (m) {
      fromData++;
      const html = typeof m.content === 'string' ? m.content : '';
      if (!quote[0]) {
        const q = html.match(/<blockquote[^>]*schema\.skype\.com\/Reply[^>]*>/i);
        if (q) quote[0] = attr(q[0], 'itemid');
      }
      for (const tag of html.match(/<img\b[^>]*>/gi) || []) {
        if (/schema\.skype\.com\/AMSImage/i.test(tag)) atts.push(['image', attr(tag, 'itemid'), attr(tag, 'alt') || 'image']);
      }
      for (const f of m.files || []) atts.push(['file', f.id || f.itemId || '', f.title || f.fileName || '']);
      if (props.mine) flags.push('mine');
      if (props.isDeleted || m.deleteTime) flags.push('deleted');
      if (m.isForwardedMessage) flags.push('forwarded');
      if (m.cards && m.cards.length) flags.push('cards:' + m.cards.length);
      if (m.importance && !/^(normal|standard)$/i.test(m.importance)) flags.push('importance:' + String(m.importance).toLowerCase());
    } else {
      flags.push('shown');
      for (const img of content ? content.querySelectorAll('img') : []) {
        if (!img.closest('[data-tid="emoticon-renderer"]') && !/Emoji/i.test(img.getAttribute('itemtype') || '')) {
          atts.push(['image', img.getAttribute('itemid') || '', img.getAttribute('alt') || 'image']);
        }
      }
    }
    const fields = [
      one(mid), time, one(author), one(m ? m.fromUserId || m.from : ''), iso(m && m.editTime), flags.join(','),
      one(quote[0]), one(quote[1]), one(quote[2]),
      atts.map((a) => a.map(one).join('¤:')).join('¤;'),
      esc(content ? textOf(content) : ''),
    ].join('\t');
    // Pieces of at most PIECE characters, never cut inside an emoji's surrogate pair.
    const parts = [];
    for (let at = 0; at < fields.length;) {
      let end = Math.min(at + PIECE, fields.length);
      if (end < fields.length && /[\uD800-\uDBFF]/.test(fields[end - 1])) end--;
      parts.push(fields.slice(at, end));
      at = end;
    }
    lines.push(parts.map((part, n) => (n ? '+\t' : 'm\t' + parts.length + '\t') + part));
  }

  const system = document.querySelectorAll('[data-tid="control-message-renderer"]').length;
  const header = ['chat', one(chatId), kind, one(name), inPane, oldest, newest, one(SINCE), lines.length, FROM, fromData, system];
  const out = [VERSION, header.join('\t')];
  let used = out.join('').length;
  let i = FROM;
  for (; i < lines.length; i++) {
    const size = lines[i].join('').length + 12 * lines[i].length;
    if (out.length + lines[i].length + 1 > MAX_STRINGS || used + size > MAX_CHARS) break;
    out.push(...lines[i]);
    used += size;
  }
  if (i < lines.length) out.push('more\t' + i);
  return out;
})('', 0);
