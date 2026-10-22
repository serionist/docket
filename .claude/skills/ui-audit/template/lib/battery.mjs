// lib/battery.mjs: the invalid-input battery, B1 to B10 (reality-lenses.md, Invalid input), driven by a form's spec
// from its journey's frontmatter.
//
// B1 to B7 and B10 only drive inputs, from each field's role, required flag, maximum length and trim policy. B8 (codes)
// and B9 (links with a token) need expired and used states, which only the app can make: the battery never touches
// the app's data itself, it calls the seed interface the part passes in:
//   seed.expiredCode(ctx)  seed.usedCode(ctx)                                  → { code }
//   seed.expiredLink(ctx)  seed.usedLink(ctx)  seed.tamperedLink(ctx)          → { url }
// A missing method makes that case BLOCKED(env), never a pass.
// In Docket, lib/docket.mjs's sessionLinks(service) gives B9 the sign-in link's tampered, used and expired states; no
// form takes a code, so B8 is n/a. Docket's dates are picked in its DatePicker, so a date field's fill is a click there.
//
// Each case drives the inputs, shoots one clip at the canonical cell, reads the error texts, checks that a rejected
// submit kept the valid input, and records the timing of the validation display (appears on blur, clears on fix).
// The expected copy is the journey's `expect:`; the battery records what it saw and the judge rates it.

import { join } from 'node:path';
import { ensureDir, MARKUP_ECHO, slug, TEN_K, UNICODE_PROBE } from './util.mjs';
import { cdpShot, DEFAULT_TEXT_SELECTORS, extractTexts, settle } from './capture.mjs';
import { applyViewport, clearViewport } from './connect.mjs';

const VALID = { normal: 'Valid', email: 'valid@example.com', key: 'valid_key', code: '000000', number: '1', password: 'Aa1!aaaaaaaa' };
const INVALID = { normal: '', email: 'not-an-email', key: 'Bad Key!', code: 'abc', number: 'abc', password: 'weak' };

function desktopVp(config) {
  const v = (config.captureGrid?.viewports ?? []).find((x) => x.name === 'desktop') ?? { width: 1440, height: 900, dpr: 1 };
  return { name: 'desktop', width: v.width, height: v.height, dpr: v.dpr ?? 1, mobile: false };
}
const roleOf = (f) => (String(f.role ?? 'normal').startsWith('confirm-of') ? 'password' : f.role ?? 'normal');
const validValue = (role, config) => config.batteryValues?.[role] ?? VALID[role] ?? VALID.normal;
const loc = (page, f) => page.locator(f.selector).first();

async function fill(page, f, v) {
  const el = loc(page, f);
  if (f.role === 'select') {
    // "A valid choice": the option at f.validOption (default: the first after a native select's placeholder, the
    // first in a custom select's listbox).
    if (!v) return;
    const native = (await el.evaluate((n) => n.tagName).catch(() => '')) === 'SELECT';
    const ok = native
      ? await el.selectOption({ index: f.validOption ?? 1 }).then(() => true, () => false)
      : await el.click().then(() => page.locator('[role="listbox"] [role="option"]').nth(f.validOption ?? 0).click()).then(() => true, () => false);
    if (!ok) throw new Error(`BLOCKED(env): select ${f.name} has no option to choose`);
    return;
  }
  if (f.role === 'date') {
    if (!(await el.fill(v ? (f.validDate ?? '2026-10-20') : '').then(() => true, () => false))) {
      throw new Error(`BLOCKED(env): date ${f.name} not fillable`);
    }
    return;
  }
  await el.fill('').catch(() => {});
  if (v) await el.fill(v).catch(() => {});
}

async function fillAllValid(page, form, config, blocked) {
  for (const f of form.fields || []) {
    try {
      await fill(page, f, validValue(roleOf(f), config));
    } catch (err) {
      blocked.push({ field: f.name, role: f.role, err: String(err).slice(0, 80) });
    }
  }
}

async function readValues(page, fields) {
  const out = {};
  for (const f of fields) out[f.name] = await loc(page, f).inputValue().catch(() => null);
  return out;
}

/** True when every valid value not in `changed` survived the rejected submit. */
function preserved(before, after, changed) {
  const skip = new Set(changed.map((f) => f.name));
  return Object.entries(before).every(([k, v]) => skip.has(k) || !v || after[k] === v);
}

async function submit(page, form) {
  if (form.submit) await page.locator(form.submit).first().click().catch(() => {});
  else await page.locator(`${form.selector} input`).first().press('Enter').catch(() => {});
  await settle(page, { timeout: 4000 });
}

const invalidNow = (page, f) => loc(page, f).getAttribute('aria-invalid').then((v) => v === 'true', () => false);

// The validation display contract, on one required field: no error while typing, an error on blur, none once fixed.
async function timing(page, field, config, selectors) {
  await fill(page, field, INVALID[roleOf(field)] ?? 'x');
  const early = (await invalidNow(page, field)) || Object.keys(await extractTexts(page, selectors)).length > 0;
  await loc(page, field).blur().catch(() => {});
  await settle(page, { timeout: 2000 });
  const onBlur = await invalidNow(page, field);
  await fill(page, field, validValue(roleOf(field), config));
  await loc(page, field).blur().catch(() => {});
  await settle(page, { timeout: 2000 });
  return { lazyOk: !early, appearsOnBlur: onBlur, clearsOnFix: !(await invalidNow(page, field)) };
}

const CASES = {
  async B1(page, form) {
    const req = form.fields.filter((f) => f.required && !['select', 'date'].includes(f.role));
    for (const f of req) await fill(page, f, '   ');
    await submit(page, form);
    return { invalid: await Promise.all(req.map((f) => invalidNow(page, f))) };
  },
  async B2(page, form, ctx) {
    const targets = form.fields.filter((f) => ['email', 'key', 'number'].includes(f.role));
    if (!targets.length) return { note: 'no formatted field' };
    await fillAllValid(page, form, ctx.config, ctx.blocked);
    for (const f of targets) await fill(page, f, INVALID[f.role]);
    const before = await readValues(page, form.fields);
    await submit(page, form);
    return { invalid: await Promise.all(targets.map((f) => invalidNow(page, f))), inputPreserved: preserved(before, await readValues(page, form.fields), targets) };
  },
  async B3(page, form) {
    const f = form.fields.find((x) => ['normal', 'email'].includes(x.role ?? 'normal'));
    if (!f) return { note: 'no text field' };
    const max = f.maxlength ?? 255;
    const lengths = {};
    for (const [label, v] of [['one', 'a'], ['max', 'a'.repeat(max)], ['over', 'a'.repeat(max + 1)], ['ten-k', TEN_K]]) {
      await fill(page, f, v);
      lengths[label] = (await loc(page, f).inputValue().catch(() => '')).length;
    }
    await submit(page, form); // a 5xx here shows in the line's network budget
    return { max, lengths };
  },
  async B4(page, form, ctx) {
    const f = form.fields.find((x) => (x.role ?? 'normal') === 'normal') ?? form.fields[0];
    await fill(page, f, `  ${validValue(roleOf(f), ctx.config)}  `);
    await submit(page, form);
    return { trimPolicy: f.trimPolicy ?? 'unknown' };
  },
  async B5(page, form) {
    for (const f of form.fields.filter((x) => ['normal', 'email'].includes(x.role ?? 'normal'))) await fill(page, f, UNICODE_PROBE);
    await submit(page, form);
    return {};
  },
  async B6(page, form, ctx) {
    for (const f of form.fields.filter((x) => (x.role ?? 'normal') === 'normal')) await fill(page, f, MARKUP_ECHO);
    await submit(page, form);
    return { executed: ctx.executed(), severity: ctx.executed() ? 'blocker' : undefined };
  },
  async B7(page, form, ctx) {
    const pw = form.fields.find((f) => f.role === 'password');
    if (!pw) return { note: 'no secret field' };
    const confirm = form.fields.find((f) => String(f.role ?? '').startsWith('confirm-of'));
    await fillAllValid(page, form, ctx.config, ctx.blocked);
    await fill(page, pw, INVALID.password);
    if (confirm) await fill(page, confirm, 'different');
    const before = await readValues(page, form.fields);
    await submit(page, form);
    const changed = confirm ? [pw, confirm] : [pw];
    return {
      weakRejected: await invalidNow(page, pw),
      mismatchShown: confirm ? await invalidNow(page, confirm) : null,
      inputPreserved: preserved(before, await readValues(page, form.fields), changed),
    };
  },
  async B8(page, form, ctx) {
    const f = form.fields.find((x) => x.role === 'code');
    if (!f) return { note: 'no code field' };
    const byCase = {};
    for (const [label, v] of [['wrong', '000000'], ['short', '000'], ['letters', 'abcdef']]) {
      await fill(page, f, v);
      await submit(page, form);
      byCase[label] = await extractTexts(page, ctx.selectors);
    }
    for (const [label, make] of [['expired', ctx.seed.expiredCode], ['used', ctx.seed.usedCode]]) {
      if (typeof make !== 'function') {
        byCase[label] = { blocked: 'BLOCKED(env): no seed state' };
        continue;
      }
      const { code } = (await make({ journey: ctx.journey })) ?? {};
      await fill(page, f, code ?? '');
      await submit(page, form);
      byCase[label] = await extractTexts(page, ctx.selectors);
    }
    return { byCase };
  },
  async B9(page, form, ctx) {
    const byCase = {};
    for (const [label, make] of [['tampered', ctx.seed.tamperedLink], ['expired', ctx.seed.expiredLink], ['used', ctx.seed.usedLink]]) {
      if (typeof make !== 'function') {
        byCase[label] = { blocked: 'BLOCKED(env): no seed state' };
        continue;
      }
      const { url } = (await make({ journey: ctx.journey })) ?? {};
      if (!url) {
        byCase[label] = { blocked: 'BLOCKED(env): the seed returned no url' };
        continue;
      }
      await page.goto(url, { waitUntil: 'domcontentloaded' }).catch(() => {});
      await settle(page, { timeout: 4000 });
      byCase[label] = { texts: await extractTexts(page, ctx.selectors) };
    }
    return { byCase };
  },
  async B10(page, form, ctx) {
    await page.goto(new URL('/__ui_audit_no_such_route__', ctx.config.origin).href, { waitUntil: 'domcontentloaded' }).catch(() => {});
    await settle(page, { timeout: 4000 });
    return { texts: await extractTexts(page, ctx.selectors) };
  },
};

/**
 * runBattery(page, form, opts) → { form, results, timing, blocked }
 * opts: journey, config, cdp, seed, manifest, part, recorder, cases (default form.battery).
 * Never throws: a case that fails is recorded and the rest go on.
 */
export async function runBattery(page, form, opts = {}) {
  const config = opts.config ?? {};
  const selectors = { ...DEFAULT_TEXT_SELECTORS, ...(config.textSelectors ?? {}) };
  const vp = desktopVp(config);
  const [, theme, lang] = config.judgedCombos?.[0] ?? ['desktop', 'light', 'en'];
  const dir = join(config.paths?.screensRoot ?? '../screens', config.run ?? 'p1', slug(opts.journey));
  if (opts.cdp) await applyViewport(opts.cdp, page, vp);

  let executed = false;
  const onDialog = (d) => {
    executed = true; // markup became a live alert(): B6 failed
    d.dismiss().catch(() => {});
  };
  page.on('dialog', onDialog);
  const ctx = { journey: opts.journey, config, selectors, seed: opts.seed ?? {}, executed: () => executed, blocked: [] };

  const first = (form.fields || []).find((f) => f.required && ['normal', 'email', 'password', 'key', 'number'].includes(f.role ?? 'normal'));
  const timingResult = first ? await timing(page, first, config, selectors).catch(() => null) : null;

  const results = [];
  for (const c of opts.cases ?? form.battery ?? []) {
    if (!CASES[c]) {
      results.push({ case: c, ok: false, note: 'unknown case' });
      continue;
    }
    try {
      const r = await CASES[c](page, form, ctx);
      const texts = await extractTexts(page, selectors);
      const clip = join(dir, `battery-${slug(c)}-${vp.width}-${theme}-${lang}-clip.png`);
      ensureDir(clip);
      if (opts.cdp) await cdpShot(opts.cdp, page, { fullPage: false, vp, maskSelectors: config.maskRegions ?? [], path: clip }).catch(() => {});
      results.push({ case: c, ok: r.severity !== 'blocker', texts, clip, ...r });
      opts.manifest?.write({
        part: opts.part ?? '?', journey: opts.journey, step: `battery:${c}`, state: `battery-${c}`, url: page.url(),
        action: `battery:${c}`, texts, clipShots: [clip], recorder: opts.recorder ?? null,
        flags: { battery: c, result: r, timing: timingResult, blocked: ctx.blocked.length ? ctx.blocked : undefined },
      });
    } catch (err) {
      results.push({ case: c, ok: false, note: `case error: ${String(err).slice(0, 160)}` });
    }
  }
  // Clear the text fields, so the battery's leftovers don't leak into the next capture of this form.
  for (const f of form.fields || []) {
    if (!['select', 'date'].includes(f.role)) await loc(page, f).fill('').catch(() => {});
  }
  if (opts.cdp) await clearViewport(opts.cdp);
  page.off('dialog', onDialog);
  return { form: form.selector, results, timing: timingResult, blocked: ctx.blocked };
}

export { CASES };
