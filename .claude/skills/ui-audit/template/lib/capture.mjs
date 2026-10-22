// lib/capture.mjs: shoot one STATE across the capture grid and record the Tier 1 checks the judges read.
//
// Per cell (viewport x theme x lang) two shots:
//   <state>-<width>-<theme>-<lang>-clip.png  the viewport: what the person sees, the judges' main surface
//   <state>-<width>-<theme>-<lang>.png       the full page: is anything below the fold broken or missing
// (<width>x<height> where a grid viewport has the same width.) config.captureGrid.viewports is what we shoot, desktop
// first; an opt-in cell (extraViewports: the short check, a phone with touch: true) only when a step names it.
// config.judgedCombos[0] is the canonical cell a judge reads. The variants reach a judge only as pixel deltas or
// through the sweeps, so capture cost stays apart from judging cost.
//
// Determinism: the clock fixed in connect(), reduced motion forced, a settle before every shot, config.maskRegions
// painted over. Geometry is read back per cell; a cell that is not what it claims is refused, never recorded.
// Everything app-specific (selectors, the theme cookie, the signed-in check) is config: see config.example.json.
// The axe rules, the target floors and their exemptions, the CSP watch and the sideways check are devkit ui's and
// browser_kit's, so a cell this passes is one `uv run devkit ui` would pass too.

import { join } from 'node:path';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { PNG } from 'pngjs';
import { ensureDir, slug } from './util.mjs';
import { applyViewport, clearViewport, newEmulatedContext } from './connect.mjs';

const require = createRequire(import.meta.url);

// Where the app shows errors, notices and status. Judges read these strings; the battery uses them to check the
// validation display contract. Extend through config.textSelectors.
// Docket's pages say a write's notice in role=status and a refusal in role=alert, the writer's words as they came.
export const DEFAULT_TEXT_SELECTORS = {
  alert: '[role="alert"]',
  status: '[role="status"]',
  invalid: '[aria-invalid="true"]',
};
// Visible on an untouched form, these break the rule that errors appear on blur or submit.
const DEFAULT_PREPAINT = '[aria-invalid="true"]';
// An open dialog, menu or popup listbox as Radix marks it; config.overlaySelector overrides it.
const DEFAULT_OVERLAY =
  '[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"], [role="menu"][data-state="open"], [role="listbox"][data-state="open"]';
// browser_kit's and devkit ui's rule set.
const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'];

// Playwright's page.screenshot shoots at the context viewport and ignores the CDP device metrics; CDP's own
// Page.captureScreenshot honours them, so the PNG has the cell's true width x height x DPR on one page (and an open
// overlay survives). Returns the PNG's {width, height}.
export async function cdpShot(cdp, page, { fullPage, vp, maskSelectors, path }) {
  // Masks are painted through the CSSOM, element by element: a style element of ours would be an inline style the
  // page's CSP refuses, and the refusal would read as the page's own CSP violation.
  if (maskSelectors?.length) {
    await page.evaluate((sels) => {
      for (const el of document.querySelectorAll(sels.join(','))) {
        el.dataset.uiauditMasked = el.style.cssText;
        el.style.setProperty('background', '#FF00FF', 'important');
        el.style.setProperty('color', '#FF00FF', 'important');
      }
    }, maskSelectors).catch(() => {});
  }
  let params = { format: 'png' };
  if (fullPage) {
    const h = await page.evaluate(() => Math.min(document.documentElement.scrollHeight, 24000)).catch(() => vp.height);
    params = { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width: vp.width, height: h, scale: 1 } };
  }
  const { data } = await cdp.send('Page.captureScreenshot', params);
  // captureBeyondViewport drops the context's touch emulation: every later phone cell would render `pointer: fine`.
  if (fullPage && vp.mobile) await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }).catch(() => {});
  const buf = Buffer.from(data, 'base64');
  writeFileSync(path, buf);
  if (maskSelectors?.length) {
    await page.evaluate(() => {
      for (const el of document.querySelectorAll('[data-uiaudit-masked]')) {
        el.style.cssText = el.dataset.uiauditMasked;
        delete el.dataset.uiauditMasked;
      }
    }).catch(() => {});
  }
  try {
    const p = PNG.sync.read(buf);
    return { width: p.width, height: p.height };
  } catch {
    return null;
  }
}

let AXE_SRC;
function axeSource() {
  if (AXE_SRC === undefined) {
    try {
      AXE_SRC = readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
    } catch {
      AXE_SRC = null;
    }
  }
  return AXE_SRC;
}

async function setEmulated(cdp, theme) {
  const features = [{ name: 'prefers-color-scheme', value: theme }, { name: 'prefers-reduced-motion', value: 'reduce' }];
  await cdp.send('Emulation.setEmulatedMedia', { features }).catch(() => {});
}

// Language: only switched when the grid has more than one (English only today). The app switches at run
// time; config.lang names how it stores the choice ({ storage: '<key>' } or { cookie: '<name>' }).
async function applyLang(page, lang, config, langs) {
  if (langs.length < 2) return;
  const mech = config.lang ?? {};
  if (mech.storage) {
    await page.evaluate(([k, v]) => localStorage.setItem(k, v), [mech.storage, lang]).catch(() => {});
  } else if (mech.cookie) {
    await page.context().addCookies([{ name: mech.cookie, value: lang, url: new URL(page.url()).origin }]);
  } else {
    return;
  }
  await page.reload({ waitUntil: 'domcontentloaded' });
}

// A stored theme choice would make every "dark" shot silently light: force the app's preference to follow the system
// so the emulated colour scheme drives it, and put the old value back afterwards. Docket keeps it in the docket_theme
// cookie, which the server writes into the document as data-theme ({ cookie, systemValue }); a localStorage key works
// too ({ key, systemValue }).
async function forceSystemTheme(page, config) {
  const pref = config.themePref;
  if (pref?.cookie) {
    const context = page.context();
    let url;
    try {
      url = new URL(page.url()).origin;
    } catch {
      return async () => {};
    }
    const value = pref.systemValue ?? 'system';
    const prev = (await context.cookies(url).catch(() => [])).find((c) => c.name === pref.cookie);
    const prevAttr = await page.evaluate(() => document.documentElement.dataset.theme ?? null).catch(() => null);
    await context.addCookies([{ name: pref.cookie, value, url }]).catch(() => {});
    await page.evaluate((v) => { document.documentElement.dataset.theme = v; }, value).catch(() => {});
    return async () => {
      if (prev) await context.addCookies([prev]).catch(() => {});
      else await context.clearCookies({ name: pref.cookie }).catch(() => {});
      await page
        .evaluate((v) => (v == null ? delete document.documentElement.dataset.theme : (document.documentElement.dataset.theme = v)), prevAttr)
        .catch(() => {});
    };
  }
  if (!pref?.key) return async () => {};
  const prev = await page.evaluate((k) => localStorage.getItem(k), pref.key).catch(() => null);
  await page.evaluate(([k, v]) => localStorage.setItem(k, v), [pref.key, pref.systemValue ?? 'system']).catch(() => {});
  return async () => {
    await page
      .evaluate(([k, v]) => (v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v)), [pref.key, prev])
      .catch(() => {});
  };
}

/** Network idle, two frames, then running animations finished (capped: an endless one must not hang the grid). */
export async function settle(page, { timeout = 8000 } = {}) {
  await page.waitForLoadState('networkidle', { timeout }).catch(() => {});
  await page
    .evaluate(async () => {
      const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
      await frame();
      await frame();
      const anims = document.getAnimations ? document.getAnimations() : [];
      await Promise.race([Promise.allSettled(anims.map((a) => a.finished)), new Promise((r) => setTimeout(r, 1500))]);
    })
    .catch(() => {});
}

export async function extractTexts(page, selectors) {
  return page.evaluate((sel) => {
    const out = {};
    const shown = (el) => {
      const r = el.getBoundingClientRect();
      const st = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    };
    for (const [key, selector] of Object.entries(sel)) {
      const texts = [];
      for (const el of document.querySelectorAll(selector)) {
        const t = shown(el) ? (el.textContent || '').replace(/\s+/g, ' ').trim() : '';
        if (t) texts.push(t.slice(0, 240));
      }
      if (texts.length) out[key] = texts;
    }
    return out;
  }, selectors);
}

async function prePaintError(page, selector) {
  return page.evaluate((s) => [...document.querySelectorAll(s)].some((el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  }), selector).catch(() => false);
}

// A modal hides the page behind it from assistive technology, as it should, and axe would read that page as missing
// its landmarks: a modal state is checked inside the modal on top, the last opened, as devkit ui checks it. The script
// goes in by evaluate, as the CSP allows no inline script tag.
async function runAxe(page, tags = AXE_TAGS) {
  const src = axeSource();
  if (!src) return { violations: [], blocked: 'axe-core not installed' };
  try {
    if (!(await page.evaluate(() => !!window.axe).catch(() => false))) await page.evaluate(src);
    return await page.evaluate(async (values) => {
      const modal = [...document.querySelectorAll(
        '[role=dialog][data-state=open], [role=alertdialog][data-state=open], [role=listbox][data-state=open], [role=menu][data-state=open]',
      )].pop();
      const res = await window.axe.run(modal ?? document, { resultTypes: ['violations'], runOnly: { type: 'tag', values } });
      return {
        violations: res.violations.map((v) => ({
          id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length,
          targets: v.nodes.slice(0, 5).map((n) => (n.target || []).join(' ')),
        })),
      };
    }, tags);
  } catch (err) {
    return { violations: [], blocked: `axe failed: ${String(err).slice(0, 120)}` };
  }
}

// Targets below the floor (24 px fine pointer, 44 px coarse), as devkit ui measures them: inline text is exempt
// (WCAG 2.5.8), and so is a control hidden from everyone, such as the native select a Select keeps for autofill.
async function hitTargets(page, minPx) {
  return page.evaluate((min) => {
    const sel = 'a[href],button,input:not([type="hidden"]),select,textarea,[role="button"],[role="checkbox"],[role="switch"],[role="tab"],[role="menuitem"],[role="menuitemradio"],[role="option"]';
    const small = [];
    for (const el of document.querySelectorAll(sel)) {
      if (el.closest('[aria-hidden="true"]')) continue;
      const r = el.getBoundingClientRect();
      const st = getComputedStyle(el);
      if (r.width === 0 || r.height === 0 || st.visibility === 'hidden' || st.display === 'none') continue;
      if (st.display === 'inline' || st.clipPath !== 'none') continue;
      if (r.width < min || r.height < min) {
        const label = (el.getAttribute('aria-label') || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40);
        small.push({ tag: el.tagName.toLowerCase(), w: Math.round(r.width), h: Math.round(r.height), label });
      }
      if (small.length >= 40) break;
    }
    return small;
  }, minPx);
}

// Visible text cut off: scrollWidth > clientWidth on a node with its own text, outside designed scrollers.
async function overflowNodes(page) {
  return page.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll('body *')) {
      if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
      const r = el.getBoundingClientRect();
      const st = getComputedStyle(el);
      if (r.width <= 0 || r.height <= 0 || st.visibility === 'hidden' || st.opacity === '0') continue;
      if (st.overflowX === 'auto' || st.overflowX === 'scroll') continue;
      const by = el.scrollWidth - el.clientWidth;
      if (by > 2 && st.textOverflow !== 'ellipsis') {
        out.push({ tag: el.tagName.toLowerCase(), text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60), by });
      }
      if (out.length >= 40) break;
    }
    return out;
  });
}

// Unrendered interpolations shown as text, and dictionary keys when the app has a dictionary (config.rawKeyPattern):
// Docket has none, and a material's path such as plan.md would read as a key.
async function rawKeys(page, pattern) {
  return page.evaluate((src) => {
    const keyRe = src ? new RegExp(src) : /(?!)/;
    const mustache = /\{\{[^}]+\}\}/;
    const out = new Set();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n && out.size < 30; n = walker.nextNode()) {
      const t = (n.textContent || '').trim();
      if (!t || !(keyRe.test(t) || mustache.test(t))) continue;
      const r = n.parentElement?.getBoundingClientRect();
      if (r && r.width > 0 && r.height > 0) out.add(t.slice(0, 80));
    }
    return [...out];
  }, pattern ?? null);
}

// Words a screen must never show (config.bannedText: the words the text of record bans, and retired names).
async function bannedText(page, patterns) {
  if (!patterns?.length) return [];
  const text = await page.evaluate(() => document.body.innerText).catch(() => '');
  const hits = [];
  for (const p of patterns) {
    const re = new RegExp(p, 'gi');
    for (const m of text.matchAll(re)) {
      hits.push({ pattern: p, match: m[0], near: text.slice(Math.max(0, m.index - 30), m.index + m[0].length + 30).replace(/\s+/g, ' ') });
      if (hits.length >= 20) return hits;
    }
  }
  return hits;
}

/** Signed-in check (config.authedAssert = { notPathPrefix, selector }); without config it always passes. */
export async function isPageAuthed(page, config) {
  const a = config.authedAssert;
  if (!a) return true;
  try {
    if (a.notPathPrefix && new URL(page.url()).pathname.startsWith(a.notPathPrefix)) return false;
  } catch {
    return false;
  }
  if (!a.selector) return true;
  return page.locator(a.selector).first().waitFor({ state: 'visible', timeout: 4000 }).then(() => true, () => false);
}

async function anchorOk(page, anchor) {
  if (!anchor) return true;
  try {
    if (anchor.path && !new URL(page.url()).pathname.startsWith(anchor.path)) return false;
  } catch {
    return false;
  }
  return anchor.selector ? page.locator(anchor.selector).first().isVisible().catch(() => false) : true;
}

// Inner scrollers (a virtual list, a side sheet) hide what is below their fold from a full-page shot: step through the
// largest one and clip each step. Canonical cell only.
async function scrollThrough(page, cdp, vpObj, maskSelectors, base, dir, cfg) {
  const extra = [];
  const s = await page.evaluate((minArea) => {
    const all = [...document.querySelectorAll('body *')].filter((e) => {
      const r = e.getBoundingClientRect();
      const st = getComputedStyle(e);
      return r.width * r.height >= minArea && e.scrollHeight - e.clientHeight > 40 && (st.overflowY === 'auto' || st.overflowY === 'scroll');
    }).sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
    if (!all.length) return null;
    all[0].setAttribute('data-uiaudit-scroller', '0');
    return { scrollHeight: all[0].scrollHeight, clientHeight: all[0].clientHeight };
  }, cfg.minArea ?? 60000).catch(() => null);
  if (!s) return extra;
  const steps = Math.min(cfg.maxSteps ?? 4, Math.ceil(s.scrollHeight / Math.max(1, s.clientHeight)));
  const scrollTo = (top) => page.evaluate((t) => {
    const e = document.querySelector('[data-uiaudit-scroller="0"]');
    if (e) e.scrollTop = t;
  }, top);
  for (let i = 1; i < steps; i++) {
    await scrollTo(s.clientHeight * i);
    await settle(page, { timeout: 3000 });
    const file = join(dir, `${base}-scroll${i + 1}-clip.png`);
    await cdpShot(cdp, page, { fullPage: false, vp: vpObj, maskSelectors, path: file });
    extra.push(file);
  }
  await scrollTo(0).catch(() => {});
  return extra;
}

// The checks that differ per cell (overflow, raw keys) and the geometry read-back. Returns false if the cell is refused.
async function shootCell(page, cdp, vpObj, ctx, sink) {
  const { base, dir, maskSelectors } = ctx;
  const clip = join(dir, `${base}-clip.png`);
  const full = join(dir, `${base}.png`);
  ensureDir(clip);
  const want = `${Math.round(vpObj.width * vpObj.dpr)}x${Math.round(vpObj.height * vpObj.dpr)}`;
  const got = await cdpShot(cdp, page, { fullPage: false, vp: vpObj, maskSelectors, path: clip });
  const gotS = got ? `${got.width}x${got.height}` : 'none';
  let ok = gotS === want;
  let pointer = null;
  if (ok && vpObj.mobile) {
    // The rendered consequence, not the emulation flag: the CSS must match `pointer: coarse`, and, when the config
    // names a control styled per pointer, that control must render at the coarse size.
    pointer = await page.evaluate((probe) => {
      const coarse = matchMedia('(pointer: coarse)').matches;
      const el = probe?.selector ? document.querySelector(probe.selector) : null;
      const h = el ? el.getBoundingClientRect().height : null;
      return { coarse, probeHeight: h, ok: coarse && (h == null || !probe?.minPx || h >= probe.minPx) };
    }, ctx.coarseProbe ?? null).catch(() => ({ ok: false }));
    ok = pointer.ok;
  }
  sink.geometry.push({ cell: base, want, got: gotS, pointer, ok });
  if (!ok) {
    sink.refused.push(base);
    rmSync(clip, { force: true });
    return false;
  }
  await cdpShot(cdp, page, { fullPage: true, vp: vpObj, maskSelectors, path: full });
  sink.clipShots.push(clip);
  sink.shots.push(full);
  for (const t of await overflowNodes(page)) sink.overflow.push({ cell: base, ...t });
  if (await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth).catch(() => false)) {
    sink.sideways.push(base);
  }
  for (const v of await page.evaluate(() => (window.__csp ?? []).splice(0)).catch(() => [])) sink.csp.push({ cell: base, violation: v });
  for (const k of await rawKeys(page, ctx.rawKeyPattern)) sink.raw.add(k);
  return true;
}

/**
 * capture(page, journey, state, opts) → the manifest record (also written when opts.manifest is given).
 * opts: config, cdp (required) · run · viewports/themes/langs (override the grid, e.g. the canonical cell only for an
 * open dialog) · anchor { path, selector } (checked per cell; a miss flags the cell suspect) · assertSelector (a
 * post-submit state's marker, required before any shot) · requireAuthed · secrets · noThemeForce · scroll (false to
 * skip the scroll-through) · expectedStatuses · manifest, part, step, recorder · flags.
 */
export async function capture(page, journey, state, opts = {}) {
  const t0 = Date.now();
  const config = opts.config ?? {};
  const cdp = opts.cdp;
  const run = opts.run ?? config.run ?? 'p1';
  const grid = config.captureGrid ?? {};
  const viewports = opts.viewports ?? grid.viewports ?? [{ name: 'desktop', width: 1440, height: 900, dpr: 1 }];
  const themes = opts.themes ?? grid.themes ?? ['light', 'dark'];
  const langs = opts.langs ?? grid.langs ?? ['en'];
  const [cvp, ctheme, clang] = config.judgedCombos?.[0] ?? [viewports[0].name, themes[0], langs[0]];
  const textSelectors = { ...DEFAULT_TEXT_SELECTORS, ...(config.textSelectors ?? {}) };
  const screensRoot = config.paths?.screensRoot ?? '../screens';
  const dir = join(screensRoot, run, slug(journey));
  const maskSelectors = (opts.maskRegions ?? config.maskRegions ?? []).filter(Boolean);
  const base0 = slug(state);
  const write = (rec) => (opts.manifest ? opts.manifest.write(rec) : rec);
  const common = { part: opts.part ?? '?', journey, step: opts.step ?? state, state, recorder: opts.recorder ?? null, expectedStatuses: opts.expectedStatuses ?? [] };

  // Never shoot the wrong screen as the state: signed out, or a post-submit marker missing ⇒ a skip line, no shots.
  const unauthed = opts.requireAuthed && !(await isPageAuthed(page, config));
  const noMarker = !unauthed && opts.assertSelector
    && !(await page.locator(opts.assertSelector).first().waitFor({ state: 'visible', timeout: 4000 }).then(() => true, () => false));
  if (unauthed || noMarker) {
    const reason = unauthed ? 'signed-out' : 'marker-absent';
    console.warn(`[capture] SKIP ${journey}/${state}: ${reason} (${page.url()})`);
    return { ...write({ ...common, url: page.url(), action: `capture-skip:${state}`, flags: { captureSkipReason: reason, ...(opts.flags ?? {}) } }), skipped: true };
  }

  // An open overlay closes on reload, so its cells never reload: it is shot in place, its other variants come from
  // the sweeps opening it again.
  const overlayOpen = await page.locator(config.overlaySelector ?? DEFAULT_OVERLAY).first().isVisible().catch(() => false);
  const inPlace = opts.inPlace || overlayOpen;
  const restoreTheme = opts.noThemeForce ? async () => {} : await forceSystemTheme(page, config);

  const sink = { shots: [], clipShots: [], geometry: [], refused: [], overflow: [], sideways: [], csp: [], raw: new Set() };
  const suspect = [];
  const errors = [];
  const axeByTheme = {};
  const hits = [];
  let texts = {};
  let prePaint = false;
  let vitals = null;
  let ariaRef = null;
  let banned = [];
  const minFine = config.hitTarget?.minPx ?? 24;
  const minCoarse = config.hitTarget?.minPxCoarse ?? 44;

  // The cell's size in its name is the width, or width x height where a grid viewport shares the width (the short
  // check, 1280x600, beside the laptop's 1280x720), so no cell overwrites another.
  const shared = (v) => (grid.viewports ?? []).some((g) => g.width === v.width && g.height !== v.height);
  const sizeTag = (v) => (shared(v) ? `${v.width}x${v.height}` : `${v.width}`);

  for (const vp of viewports) {
    // touch: a phone cell, opt-in per screen (desktop first; config.example.json, captureGrid.extraViewports).
    const vpObj = { name: vp.name, width: vp.width, height: vp.height, dpr: vp.dpr ?? 1, mobile: vp.touch === true };
    let env = null;
    let p = page;
    let c = cdp;
    try {
      if (vpObj.mobile) {
        // A fresh context with a real coarse pointer, navigated to the same URL: only page-level states get here
        // (interaction states are canonical, desktop only, and phone's own interaction states are driven in a part
        // that opens its page in a touch context).
        env = await newEmulatedContext(page.context().browser(), page.context(), config, vpObj);
        p = env.page;
        c = env.cdp;
        await p.goto(page.url(), { waitUntil: 'domcontentloaded' });
        if (!opts.noThemeForce) await forceSystemTheme(p, config);
      }
      await applyViewport(c, p, vpObj);
      for (const theme of themes) {
        for (const lang of langs) {
          const base = `${base0}-${sizeTag(vp)}-${theme}-${lang}`;
          try {
            if (!inPlace) await applyLang(p, lang, config, langs);
            await setEmulated(c, theme);
            await settle(p, { timeout: config.timeouts?.settle ?? 8000 });
            if (!(await anchorOk(p, opts.anchor))) suspect.push(base);
            if (!(await shootCell(p, c, vpObj, { base, dir, maskSelectors, coarseProbe: config.coarseProbe, rawKeyPattern: config.rawKeyPattern }, sink))) continue;
            if (vp.name === cvp && lang === clang) axeByTheme[theme] = await runAxe(p, config.axeTags ?? AXE_TAGS);
            if (theme === ctheme && lang === clang) {
              const floor = vpObj.mobile ? minCoarse : minFine;
              for (const t of await hitTargets(p, floor)) hits.push({ cell: base, floor, ...t });
            }
            if (vp.name === cvp && theme === ctheme && lang === clang) {
              texts = await extractTexts(p, textSelectors);
              prePaint = await prePaintError(p, config.prePaintSelectors ?? DEFAULT_PREPAINT);
              vitals = await p.evaluate(() => window.__uiAuditVitals || null).catch(() => null);
              banned = await bannedText(p, config.bannedText);
              const aria = await p.locator('body').ariaSnapshot().catch(() => null);
              if (aria != null) {
                ariaRef = join(dir, `${base0}-aria.txt`);
                writeFileSync(ariaRef, aria);
              }
              if (opts.scroll !== false && config.scrollCapture?.enabled !== false) {
                sink.clipShots.push(...(await scrollThrough(p, c, vpObj, maskSelectors, base, dir, config.scrollCapture ?? {})));
              }
            }
          } catch (err) {
            errors.push({ cell: base, error: String(err).slice(0, 200) });
          }
        }
      }
    } catch (err) {
      errors.push({ cell: `${base0}-${sizeTag(vp)}`, error: String(err).slice(0, 200) });
    } finally {
      await c.send('Emulation.setEmulatedMedia', { features: [] }).catch(() => {});
      if (env) await env.close();
      else await clearViewport(cdp);
    }
  }
  await restoreTheme();

  const axe = [];
  const contrast = [];
  for (const [theme, res] of Object.entries(axeByTheme)) {
    for (const v of res.violations ?? []) {
      axe.push({ theme, ...v });
      if (v.id === 'color-contrast') contrast.push({ theme, ...v });
    }
  }
  const axeBlocked = Object.values(axeByTheme).map((r) => r.blocked).filter(Boolean);
  const cvpSize = sizeTag(viewports.find((v) => v.name === cvp) ?? viewports[0]);
  const flags = {
    canonicalCombo: `${cvpSize}-${ctheme}-${clang}`,
    prePaintError: prePaint || undefined,
    suspect: suspect.length ? suspect : undefined,
    geometry: sink.geometry,
    refused: sink.refused.length ? sink.refused : undefined,
    errors: errors.length ? errors : undefined,
    axeBlocked: axeBlocked.length ? axeBlocked : undefined,
    inPlace: inPlace || undefined,
    secrets: opts.secrets || undefined,
    ...(opts.flags ?? {}),
  };
  return write({
    ...common, url: page.url(), action: `capture:${state}`, texts,
    shots: sink.shots, clipShots: sink.clipShots, axe, contrast, hitTargets: hits, overflow: sink.overflow,
    rawKeys: [...sink.raw], bannedText: banned, csp: sink.csp, sideways: sink.sideways, vitals, ariaSnapshotRef: ariaRef,
    ms: Date.now() - t0, flags,
  });
}
