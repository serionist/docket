// docket:calendar-week/1: the Outlook on the web week view on screen, read only. No clicks, nothing typed.
// Paste it whole as javascript_tool's text. When the last string is "more<TAB><n>", run it again with <n> in place of
// the 0 on the last line. mapper.py reads only results whose first string is the version line.
//
// One string per event, tab-separated (a tab never occurs inside a field: whitespace is collapsed):
//   ev  <strings it takes>  <id>  <start>  <end>  <response>  <organiser>  <organiser address>  <place>  <aria-label>
// The id is the event's data-calitemid. Start, end, response, organiser and place come from the event's own data
// behind the element, which the label leaves out (the response, the address, a past day's place); start and end
// fall back to the label's times in this browser's time zone. A line over 900 characters goes on in "+<TAB>..."
// strings, since Chrome cuts a string at about 1,000. Links, query strings and passcodes are taken out.
await (async (FROM) => {
  const VERSION = 'docket:calendar-week/1';
  const MAX_STRINGS = 95; // Chrome keeps 100 items of an array
  const MAX_CHARS = 30000; // the capture hook keeps a result under 50,000 characters, JSON escapes included
  const PIECE = 900;
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
    'November', 'December'];
  const DATE = '[A-Z][a-z]+, ([A-Z][a-z]+) (\\d{1,2}), (\\d{4})';
  const TIMED = new RegExp(', (\\d{1,2}):(\\d{2})(?: ?([AP]M))? to (\\d{1,2}):(\\d{2})(?: ?([AP]M))?, ' + DATE);
  const ALLDAY = new RegExp(', all day event, ' + DATE + '(?: to ' + DATE + ')?');
  const isLabel = (s) => !!s && (TIMED.test(s) || ALLDAY.test(s));

  // A hidden tab throttles setTimeout, so short waits yield through MessageChannel.
  const tick = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
  const pause = async (ms) => { const t = Date.now(); while (Date.now() - t < ms) await tick(); };

  const clean = (s) => String(s == null ? '' : s)
    .replace(/https?:\/\/\S+/gi, ' ')
    .replace(/[?&][\w.-]+=\S*/g, ' ')
    .replace(/\b(passcode|password|pwd|pin)\b\s*[:=]?\s*\S+/gi, '$1 (removed)')
    .replace(/\s+/g, ' ')
    .trim();

  const rangeText = () => {
    for (const e of document.querySelectorAll('button, span, div, h1, h2')) {
      if (e.childElementCount) continue;
      const t = (e.textContent || '').trim();
      if (/^\d{4}, [A-Z][a-z]+ \d{1,2}/.test(t)) return t;
    }
    return '';
  };

  // The view has settled when two looks 300 ms apart show the same week and the same number of events.
  let range = '';
  let count = -1;
  for (const t0 = Date.now(); Date.now() - t0 < 6000; await pause(300)) {
    const n = document.querySelectorAll('[data-calitemid]').length;
    const r = rangeText();
    if (r && r === range && n === count) break;
    range = r;
    count = n;
  }

  let els = [...document.querySelectorAll('[data-calitemid]')];
  const withIds = els.length > 0;
  if (!withIds) els = [...document.querySelectorAll('[role="button"][aria-label]')].filter((e) => isLabel(e.getAttribute('aria-label')));

  const labelOf = (el) => {
    for (const e of [el, ...el.querySelectorAll('[aria-label]')]) {
      const s = e.getAttribute('aria-label');
      if (isLabel(s)) return s;
    }
    return el.getAttribute('aria-label') || '';
  };
  const itemOf = (el, id) => {
    const key = Object.keys(el).find((k) => k.startsWith('__reactFiber$'));
    let f = key ? el[key] : null;
    for (let n = 0; f && n < 6; n++, f = f.return) {
      const it = f.memoizedProps && f.memoizedProps.item;
      if (it && it.ItemId && (!id || it.ItemId.Id === id)) return it;
    }
    return null;
  };
  const iso = (v) => {
    if (v == null || v === '') return '';
    const ms = typeof v === 'object' && typeof v.utcMs === 'number' ? v.utcMs : new Date(v).getTime();
    return Number.isFinite(ms) ? new Date(ms).toISOString().replace('.000Z', 'Z') : '';
  };
  const hour = (h, ap) => (ap ? (Number(h) % 12) + (ap === 'PM' ? 12 : 0) : Number(h));
  const shown = (label) => {
    let m = label.match(TIMED);
    if (m) {
      const [y, mo, d] = [Number(m[9]), MONTHS.indexOf(m[7]), Number(m[8])];
      const s = new Date(y, mo, d, hour(m[1], m[3]), Number(m[2]));
      let e = new Date(y, mo, d, hour(m[4], m[6]), Number(m[5]));
      if (e <= s) e = new Date(y, mo, d + 1, hour(m[4], m[6]), Number(m[5]));
      return [s, e];
    }
    m = label.match(ALLDAY);
    if (m) {
      const first = [Number(m[3]), MONTHS.indexOf(m[1]), Number(m[2])];
      const last = m[4] ? [Number(m[6]), MONTHS.indexOf(m[4]), Number(m[5])] : first;
      return [new Date(first[0], first[1], first[2]), new Date(last[0], last[1], last[2] + 1)];
    }
    return null;
  };

  const rows = [];
  const seen = new Set();
  let fromData = 0;
  for (const el of els) {
    const id = withIds ? el.getAttribute('data-calitemid') || '' : '';
    if (id && seen.has(id)) continue;
    seen.add(id);
    const label = clean(labelOf(el));
    const it = itemOf(el, id);
    if (it) fromData++;
    const times = shown(label);
    const box = (it && it.Organizer && it.Organizer.Mailbox) || {};
    const address = box.SmtpEmailAddress || (/@/.test(box.EmailAddress || '') ? box.EmailAddress : '');
    const loc = it ? (typeof it.Location === 'string' ? it.Location : (it.Location && it.Location.DisplayName) || '') : '';
    const body = [
      id,
      (it && iso(it.Start)) || (times ? iso(times[0]) : ''),
      (it && iso(it.End)) || (times ? iso(times[1]) : ''),
      it ? clean(it.ResponseType) : '',
      clean(box.Name),
      clean(address),
      clean(loc),
      label,
    ].join('\t');
    const k = Math.max(1, Math.ceil(body.length / PIECE));
    const pieces = [];
    for (let i = 0; i < k; i++) pieces.push((i ? '+\t' : 'ev\t' + k + '\t') + body.slice(i * PIECE, (i + 1) * PIECE));
    rows.push(pieces);
  }

  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  const out = [VERSION, ['week', clean(range), rows.length, FROM, fromData, zone].join('\t')];
  let used = out.join('').length;
  let i = FROM;
  for (; i < rows.length; i++) {
    const size = rows[i].join('').length + 12 * rows[i].length;
    if (out.length + rows[i].length + 1 > MAX_STRINGS || used + size > MAX_CHARS) break;
    out.push(...rows[i]);
    used += size;
  }
  if (i < rows.length) out.push('more\t' + i);
  return out;
})(0);
