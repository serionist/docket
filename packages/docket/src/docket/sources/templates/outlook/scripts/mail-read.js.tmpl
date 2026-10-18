// docket:mail-read/1: one conversation or message Outlook on the web has loaded, read from its own data, read only.
// No clicks, no scrolls, nothing typed. Paste it whole as javascript_tool's text, with the row's id (as mail-rows.js
// gives it, or '' for what the reading pane shows) and the window's start (UTC ISO, or '' for every message) in place
// of the two '' on the last line. When the last string is "more<TAB><n>", run it again with <n> in place of the 0
// there. mapper.py reads only results whose first string is the version line.
//
// OWA keeps every conversation it opened (and the ones it fetched ahead) in its own data: each message's ids, sender,
// To, Cc and Bcc with addresses, times, attachments and body, collapsed messages too. A conversation's body is the
// message's own part (Exchange's UniqueBody) followed by the quoted text Exchange holds apart for it, which is what a
// forward carries; a reply's quote of an earlier message of the same conversation is that message's own record. A
// message opened alone (a folder in message view, such as Junk Email) gives its whole body.
//
// A header, then one line per message, oldest first; drafts are left out. A message line's fields, tab-separated:
//   m  <strings it takes>  <id>  <conversation>  <internet message id>  <sent, UTC>  <received, UTC>  <from>
//      <from address>  <sender>  <to>  <cc>  <bcc>  <subject>  <folder>  <folder type>  <flags>  <attachments>  <text>
// Ids are in the URL-safe form (+ as -, / as _). Sender is name¤:address when it isn't From (sent on behalf). To, Cc,
// Bcc are name¤:address joined by ¤;. Attachments are kind¤:name¤:content type¤:size joined by ¤;, inline images left
// out (counted in flags). In every field ¤ is ¤¤, = is ¤e and a tab ¤t, since Chrome blocks a string holding = with &
// or ;. The lines go out in pieces of at most 900 characters, a line going on in "+<TAB>..." strings, since Chrome
// cuts a string at about 1,000; a long conversation pages by piece. Passcodes and secrets are taken out.
// Without OWA's data the expanded message in the reading pane is read as shown, with no id ("shown" in its flags).
await (async (ID, SINCE, FROM) => {
  const VERSION = 'docket:mail-read/1';
  const MAX_STRINGS = 95; // Chrome keeps 100 items of an array
  const MAX_CHARS = 30000; // the capture hook keeps a result under 50,000 characters, JSON escapes included
  const PIECE = 900;
  const since = SINCE ? Date.parse(SINCE) || 0 : 0;

  // A hidden tab throttles setTimeout, so short waits yield through MessageChannel.
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const pause = async (ms) => { const t = Date.now(); while (Date.now() - t < ms) await tick(); };

  const scrub = (s) => String(s == null ? '' : s).replace(/\r\n?/g, '\n')
    .replace(/\b(passcode|password|passwd|pwd|jelszó|jelszo|pin code)(\s*[:=]\s*)\S+/gi, '$1: (removed)')
    .replace(/\b(client secret|secret value|secret key|api key|access key)(\s*[:=-]\s*)\S+/gi, '$1: (removed)');
  const esc = (s) => scrub(s).replace(/¤/g, '¤¤').replace(/=/g, '¤e').replace(/\t/g, '¤t');
  const one = (s) => esc(String(s == null ? '' : s).replace(/\s+/g, ' ').trim());
  const safe = (id) => one(String(id || '').replace(/\+/g, '-').replace(/\//g, '_'));
  const plain = (id) => String(id || '').replace(/¤e/g, '=').replace(/-/g, '+').replace(/_/g, '/');
  const iso = (v) => {
    if (!v) return '';
    const ms = new Date(v).getTime();
    return Number.isFinite(ms) ? new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z') : '';
  };
  const list = (v) => (v ? Array.from(v) : []);

  // A body as the pane shows it: blocks on their own lines, list items with "- ", table cells joined by |, hidden
  // parts, styles and images left out. The HTML is parsed into an inert document: nothing in it runs or loads.
  const BLOCK = /^(P|DIV|LI|UL|OL|TR|TABLE|TBODY|THEAD|TFOOT|H[1-6]|PRE|BLOCKQUOTE|SECTION|ARTICLE|HEADER|FOOTER|HR|DL|DT|DD|ADDRESS|CENTER)$/;
  const SKIP = /^(STYLE|SCRIPT|HEAD|TITLE|META|LINK|IMG|SVG|OBJECT|IFRAME|NOSCRIPT|TEMPLATE|BUTTON)$/;
  const hidden = (el) => /display\s*:\s*none|visibility\s*:\s*hidden|mso-hide\s*:\s*all/i.test(el.getAttribute('style') || '') || el.hasAttribute('hidden');
  const walkText = (root) => {
    let s = '';
    const walk = (n, raw) => {
      if (n.nodeType === 3) { s += raw ? n.nodeValue : n.nodeValue.replace(/\s+/g, ' '); return; }
      if (n.nodeType !== 1) return;
      const tag = n.tagName.toUpperCase();
      if (SKIP.test(tag) || hidden(n)) return;
      if (tag === 'BR') { s += '\n'; return; }
      const block = BLOCK.test(tag);
      if (block && s && !s.endsWith('\n')) s += '\n';
      if (tag === 'LI') s += '- ';
      for (const c of n.childNodes) walk(c, raw || tag === 'PRE');
      if (tag === 'TD' || tag === 'TH') s += ' | ';
      if (block && !s.endsWith('\n')) s += '\n';
    };
    walk(root, false);
    return s.replace(/\xa0/g, ' ').replace(/ *\| *\n/g, '\n').replace(/[ \t]+\n/g, '\n').replace(/\n[ \t]+/g, '\n')
      .replace(/[ \t]{2,}/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  };
  const htmlText = (html) => walkText(new DOMParser().parseFromString(String(html || ''), 'text/html').body);
  const bodyText = (b) => (!b || b.Value == null ? '' : /^text$/i.test(b.BodyType || '') ? String(b.Value).replace(/\r\n?/g, '\n').trim() : htmlText(b.Value));

  const root = globalThis.__satchelGlobalContext && globalThis.__satchelGlobalContext.rootStore;
  const get = (k) => (root && root.get ? root.get(k) : null);
  const mail = get('mail');
  const folders = (get('folderStore') || {}).folderTable;
  const pane = get('readingpane');

  // What to read: the id given, else what the reading pane has loaded.
  let mode = '';
  let id = plain(ID);
  if (mail && id) mode = mail.conversations.has(id) ? 'conversation' : mail.items.has(id) ? 'message' : '';
  if (mail && !ID && pane) {
    const c = [...(pane.loadedConversationReadingPaneViewStates || new Map()).keys()][0];
    const m = [...(pane.loadedItemReadingPaneViewStates || new Map()).keys()][0];
    if (c && mail.conversations.has(c)) { mode = 'conversation'; id = c; } else if (m && mail.items.has(m)) { mode = 'message'; id = m; }
  }

  const nodesOf = (conv) => list(conv.conversationNodeIds).map((n) => mail.conversationNodes.get(n)).filter(Boolean);
  const loaded = () => {
    if (mode === 'message') { const x = mail.items.get(id); return !!(x && (x.NormalizedBody || x.UniqueBody)); }
    const conv = mail.conversations.get(id);
    if (!conv || (conv.loadingState && conv.loadingState.isLoading)) return false;
    const nodes = nodesOf(conv);
    return nodes.length > 0 && nodes.every((n) => list(n.itemIds).every((i) => { const x = mail.items.get(i); return x && (x.IsDraft || x.UniqueBody || x.NormalizedBody); }));
  };
  if (mode) for (const t0 = Date.now(); !loaded() && Date.now() - t0 < 8000;) await pause(250);

  const box = (r) => (r && (r.Mailbox || r)) || {};
  const person = (r) => { const b = box(r); return one(b.Name) + '¤:' + one(b.EmailAddress); };
  const people = (v) => list(v).map(person).join('¤;');
  const folderOf = (fid) => {
    const f = folders && fid ? folders.get(fid) : null;
    return f ? [one(f.displayName), one(f.distinguishedFolderType)] : ['', ''];
  };

  const lines = [];
  let inData = 0;
  let older = 0;
  let drafts = 0;
  let subject = '';
  let more = 0;
  const message = (x, node, copies) => {
    inData++;
    const when = x.DateTimeReceived || x.DateTimeSent;
    if (since && when && Date.parse(when) < since) { older++; return; }
    const flags = [];
    if (!x.IsRead) flags.push('unread');
    if (x.Importance && !/^normal$/i.test(x.Importance)) flags.push('importance:' + String(x.Importance).toLowerCase());
    if (x.ItemClass && x.ItemClass !== 'IPM.Note') flags.push('class:' + x.ItemClass);
    let text = '';
    if (mode === 'message' && x.NormalizedBody) {
      text = bodyText(x.NormalizedBody);
      flags.push('body:whole');
    } else if (x.UniqueBody) {
      const quoted = node ? list(node.quotedTextList).map(htmlText).filter(Boolean) : [];
      text = [bodyText(x.UniqueBody), ...quoted].filter(Boolean).join('\n\n');
      flags.push('body:own');
      if (node && node.hasQuotedText) flags.push(quoted.length ? 'quoted:kept' : 'quoted:thread');
      if (x.UniqueBody.IsTruncated) flags.push('truncated');
    } else if (x.NormalizedBody) {
      text = bodyText(x.NormalizedBody);
      flags.push('body:whole');
    } else {
      flags.push('body:none');
    }
    const atts = list(x.Attachments);
    const inline = atts.filter((a) => a.IsInline).length;
    if (inline) flags.push('inline:' + inline);
    if (copies) flags.push('copies:' + copies);
    const kind = (a) => (/ItemAttachment/i.test(a.OwsTypeName || a.__typename || '') ? 'item'
      : /ReferenceAttachment/i.test(a.OwsTypeName || a.__typename || '') ? 'reference' : 'file');
    const from = box(x.From);
    const sender = box(x.Sender);
    const onBehalf = sender.EmailAddress && from.EmailAddress && sender.EmailAddress.toLowerCase() !== from.EmailAddress.toLowerCase();
    lines.push([
      safe(x.ItemId && x.ItemId.Id), safe(x.ConversationId && x.ConversationId.Id), one(x.InternetMessageId),
      iso(x.DateTimeSent), iso(x.DateTimeReceived), one(from.Name), one(from.EmailAddress),
      onBehalf ? person(x.Sender) : '', people(x.ToRecipients), people(x.CcRecipients), people(x.BccRecipients),
      one(x.Subject), ...folderOf(x.ParentFolderId && x.ParentFolderId.Id), flags.join(','),
      atts.filter((a) => !a.IsInline).map((a) => [kind(a), one(a.Name), one(a.ContentType), a.Size || ''].join('¤:')).join('¤;'),
      esc(text),
    ].join('\t'));
  };

  if (mode === 'conversation') {
    const conv = mail.conversations.get(id);
    more = conv.canLoadMore ? 1 : 0;
    const msgs = [];
    for (const node of nodesOf(conv)) {
      const real = list(node.itemIds).map((i) => mail.items.get(i)).filter((x) => x && !x.IsDraft);
      if (!real.length) { drafts++; continue; }
      // One message can sit in two folders (Sent Items and the Inbox): one line, the smallest id, the copies counted.
      real.sort((a, b) => (a.ItemId.Id < b.ItemId.Id ? -1 : 1));
      msgs.push([real[0], node, real.length - 1]);
    }
    msgs.sort((a, b) => (Date.parse(a[0].DateTimeSent || a[0].DateTimeReceived) || 0) - (Date.parse(b[0].DateTimeSent || b[0].DateTimeReceived) || 0));
    for (const [x, node, copies] of msgs) message(x, node, copies);
    subject = msgs.length ? msgs[msgs.length - 1][0].Subject : '';
  } else if (mode === 'message') {
    const x = mail.items.get(id);
    let node = null;
    for (const n of mail.conversationNodes.values()) if (list(n.itemIds).includes(id)) { node = n; break; }
    if (x.IsDraft) drafts++; else message(x, node, 0);
    subject = x.Subject;
  } else if (!mail) {
    // No OWA data: the expanded messages in the reading pane, as shown, with no id. An id OWA hasn't loaded reads
    // nothing (open it first): the pane may show another conversation.
    const rp = document.querySelector('#ReadingPaneContainerId');
    const head = rp ? rp.querySelector('[id^="CONV_"][id$="_SUBJECT"]') : null;
    subject = head ? head.innerText : '';
    const bodies = rp ? [...rp.querySelectorAll('[id^="UniqueMessageBody"]')] : [];
    const stamps = rp ? [...rp.querySelectorAll('[id^="MSG_"][id$="_DATETIME"]')] : [];
    const path = decodeURIComponent((location.pathname.match(/\/id\/([^/]+)/) || [])[1] || '');
    if (bodies.length) mode = 'pane';
    bodies.forEach((body, n) => {
      const stamp = stamps[n];
      const key = stamp ? stamp.id.replace(/_DATETIME$/, '') : '';
      const fromEl = key ? document.getElementById(key + '_FROM') : null;
      const toEl = key ? document.getElementById(key + '_TO') : null;
      const labels = toEl ? [...toEl.querySelectorAll('[aria-label],[title]')].map((e) => e.getAttribute('aria-label') || e.getAttribute('title') || '') : [];
      const names = (p) => labels.filter((l) => l.startsWith(p)).map((l) => l.slice(p.length).split(/;\s*/).map((s) => one(s) + '¤:').join('¤;')).join('¤;');
      inData++;
      lines.push(['', /^AAQk/.test(path) ? safe(path) : '', '', iso(stamp ? stamp.innerText.trim() : ''), '',
        one(fromEl ? fromEl.innerText : ''), '', '', names('To: '), names('Cc: '), '', one(subject), '', '', 'shown', '',
        esc(walkText(body))].join('\t'));
    });
  }

  // Every line in pieces of at most PIECE characters, never cut inside an emoji's surrogate pair.
  const pieces = [];
  for (const fields of lines) {
    const parts = [];
    for (let at = 0; at < fields.length;) {
      let end = Math.min(at + PIECE, fields.length);
      if (end < fields.length && /[\uD800-\uDBFF]/.test(fields[end - 1])) end--;
      parts.push(fields.slice(at, end));
      at = end;
    }
    parts.forEach((part, n) => pieces.push((n ? '+\t' : 'm\t' + parts.length + '\t') + part));
  }
  // The read's mark: the same pieces give the same mark, so a later part joins only the read it continues.
  let h = 2166136261;
  for (const p of pieces) for (let n = 0; n < p.length; n++) { h ^= p.charCodeAt(n); h = Math.imul(h, 16777619) >>> 0; }

  const header = ['conv', mode, mode === 'pane' ? '' : safe(id), one(subject), inData, lines.length, older, drafts, one(SINCE),
    FROM, pieces.length, h.toString(16).padStart(8, '0'), mode === 'conversation' || mode === 'message' ? 1 : 0, more];
  const out = [VERSION, header.join('\t')];
  // JSON escapes count: a tab is two characters, anything outside ASCII up to six.
  const weight = (s) => s.length + 8 + (s.match(/[\t"\\]/g) || []).length + 5 * (s.match(/[^\x00-\x7f]/g) || []).length;
  let used = out.reduce((a, s) => a + weight(s), 0);
  let i = FROM;
  for (; i < pieces.length; i++) {
    const size = weight(pieces[i]);
    if (out.length + 2 > MAX_STRINGS || used + size > MAX_CHARS) break;
    out.push(pieces[i]);
    used += size;
  }
  if (i < pieces.length) out.push('more\t' + i);
  return out;
})('', '', 0);
