// lib/part.mjs: what every generated part shares, so a part is only its world and its journeys.
//
//   import { openPart } from '../lib/part.mjs';
//   const part = await openPart('03-queue', { world: 'week' });
//   await part.journey('J-07', async ({ page, shoot, battery, faults }) => {
//     await page.goto(part.url('/queue'));
//     await shoot('queue-list', { tier: 'grid', requireAuthed: true });
//   });
//   await part.close();
//
// The runner started the segment's throwaway service (serve.py), identified it and signed the owner in; the part reads
// its record (UI_AUDIT_SERVICE), checks the world is the one the part was written for, and identifies the service
// again before any journey. part.journey() runs one journey module; when it throws, the evidence goes to failures/ and
// the part goes on to the next journey; at the end, passlog writes the journey's shots block. The runner passes
// --journeys through UI_AUDIT_JOURNEYS, and journeys not listed are skipped (their world prologue still runs).

import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { connect } from './connect.mjs';
import { capture } from './capture.mjs';
import { runBattery } from './battery.mjs';
import { identify, readService, SESSION_COOKIE, sessionLinks } from './docket.mjs';
import { Manifest, manifestPath } from './manifest.mjs';
import { recordFailure } from './failures.mjs';
import { logJourneyPass } from './passlog.mjs';
import { sleep } from './util.mjs';

const E2E = resolve(process.env.UI_AUDIT_E2E ?? '.');
export const config = JSON.parse(readFileSync(join(E2E, 'config.json'), 'utf8'));
config.run = process.env.UI_AUDIT_RUN || config.run;
const SERVICE = resolve(E2E, process.env.UI_AUDIT_SERVICE ?? join('state', 'service.json'));

// A viewport by name: one of the grid's, or an opt-in cell from captureGrid.extraViewports. Objects pass through.
function viewport(v) {
  if (typeof v !== 'string') return v;
  const found = (config.captureGrid?.viewports ?? []).find((x) => x.name === v) ?? config.captureGrid?.extraViewports?.[v];
  if (!found) throw new Error(`viewport '${v}' is in neither captureGrid.viewports nor captureGrid.extraViewports`);
  return { ...found, name: v };
}

function tierOpts(tier, add = []) {
  const vps = config.captureGrid?.viewports ?? [];
  const [vp, theme, lang] = config.judgedCombos?.[0] ?? ['desktop', 'light', 'en'];
  const extra = add.map(viewport);
  if (tier === 'canonical') return { viewports: [...vps.filter((v) => v.name === vp), ...extra], themes: [theme], langs: [lang] };
  return extra.length ? { viewports: [...vps, ...extra] } : {}; // 'grid': the whole configured grid, plus opt-in cells
}

// Route interception stands in for a fault hook, which the service doesn't have: a read failing, a slow network, the
// service gone, a lapsed session. Each is named in the flags of every capture taken while it is on.
function faultsFor(page, context, active) {
  const routes = []; // [url, handler]: each fault's route, so a touch cell's fresh page gets it as well (prepare)
  const route = async (url, handler) => {
    routes.push([url, handler]);
    await page.route(url, handler);
  };
  return {
    async fail(url, status = 500, body = '{"error":"failed on purpose by the audit"}') {
      active.add(`fail ${status} ${url}`);
      await route(url, (r) => r.fulfill({ status, contentType: 'application/json', body }));
    },
    async slow(url = '**/api/web/**', ms = 2000) {
      active.add(`slow ${ms} ms ${url}`);
      await route(url, async (r) => {
        await sleep(ms);
        await r.continue().catch(() => {});
      });
    },
    async down(url = '**/api/**') {
      active.add(`down ${url}`);
      await route(url, (r) => r.abort('connectionrefused'));
    },
    async lapse() {
      active.add('session lapsed');
      await context.clearCookies({ name: SESSION_COOKIE });
    },
    async clear() {
      active.clear();
      routes.length = 0;
      await page.unrouteAll({ behavior: 'ignoreErrors' });
    },
    async prepare(other) {
      for (const [url, handler] of routes) await other.route(url, handler);
    },
  };
}

/**
 * openPart(name, { persona, world }) → { page, context, cdp, recorder, manifest, config, service, url(path), journey(id, fn), close() }
 * persona: 'owner' (signed in, state/owner.json) or 'stranger' (no session); world: the world the part is written
 * for, which must be the segment's.
 */
export async function openPart(name, { persona = 'owner', world } = {}) {
  const service = readService(SERVICE);
  if (world && service.world !== world) {
    throw new Error(`part ${name} is written for the ${world} world, and this segment serves ${service.world}`);
  }
  await identify(service);
  const p = config.personas?.[persona];
  if (!p) throw new Error(`persona '${persona}' is not in config.personas`);
  const cfg = { ...config, origin: service.origin };
  const c = await connect(cfg, { storageState: p.storageState ? join(E2E, p.storageState) : undefined });
  const manifest = new Manifest(manifestPath(E2E, config.run), config.run, cfg);
  const only = (process.env.UI_AUDIT_JOURNEYS ?? '').split(',').map((s) => s.trim()).filter(Boolean);
  const active = new Set();
  const faults = faultsFor(c.page, c.context, active);

  async function journey(id, fn) {
    if (only.length && !only.includes(id)) return;
    // shoot(state, { tier, add: ['tablet'], viewports: ['short'], ... }): add appends opt-in cells to the tier's;
    // viewports replaces them. Names resolve through viewport().
    const shoot = (state, { add = [], ...o } = {}) => {
      const vps = o.viewports ? { viewports: o.viewports.map(viewport) } : {};
      const flags = active.size ? { faults: [...active], ...(o.flags ?? {}) } : o.flags;
      return capture(c.page, id, state, {
        ...tierOpts(o.tier ?? 'canonical', add), ...o, ...vps, flags, prepare: faults.prepare, config: cfg, cdp: c.cdp, manifest, part: name,
        recorder: c.recorder,
      });
    };
    const battery = (form, o = {}) =>
      runBattery(c.page, form, {
        seed: sessionLinks(service), ...o, journey: id, config: cfg, cdp: c.cdp, manifest, part: name, recorder: c.recorder,
      });
    let step = 'start';
    const at = (s) => {
      step = s;
    };
    try {
      await fn({ ...c, manifest, config: cfg, service, shoot, battery, faults, at });
    } catch (error) {
      await recordFailure(c.page, { journey: id, step, error, recorder: c.recorder, manifest, part: name, failuresRoot: join(E2E, 'failures') });
    } finally {
      await faults.clear().catch(() => {});
    }
    await logJourneyPass({ ...config, paths: { ...config.paths, e2eRoot: E2E } }, id).catch((e) => console.warn(`[passlog] ${id}: ${e}`));
  }

  return { ...c, manifest, config: cfg, service, url: (path) => new URL(path, service.origin).href, journey };
}
