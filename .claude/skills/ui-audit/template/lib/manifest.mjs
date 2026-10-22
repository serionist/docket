// lib/manifest.mjs: the per-pass event log (manifest.<pass>.jsonl) the judges read.
//
// One JSON line per step. A line carries the console and network noise its step produced (drained from the Recorder)
// and the Tier 1 rows capture() computed, so a judge reads mechanical facts instead of hunting them. Budgets flag only
// noise the config does not expect: the allow-lists, the expected-status map, foreign origins, the vitals ceilings.

import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { nowIso } from './util.mjs';

export function manifestPath(e2eRoot, run) {
  return join(e2eRoot, `manifest.${run}.jsonl`);
}

/** Collects console errors and warnings, page errors, and failed or >=400 responses for one page. */
export class Recorder {
  constructor() {
    this.consoleErrors = [];
    this.failedRequests = [];
    this.requests = [];
  }

  attach(page) {
    page.on('console', (msg) => {
      const type = msg.type();
      if (type === 'error' || type === 'warning') this.consoleErrors.push({ type, text: msg.text().slice(0, 500) });
    });
    page.on('pageerror', (err) => this.consoleErrors.push({ type: 'pageerror', text: String(err).slice(0, 500) }));
    page.on('request', (req) => this.requests.push(req.url()));
    page.on('requestfailed', (req) =>
      this.failedRequests.push({ url: req.url(), method: req.method(), failure: req.failure()?.errorText ?? null }));
    page.on('response', (res) => {
      if (res.status() >= 400) this.failedRequests.push({ url: res.url(), method: res.request().method(), status: res.status() });
    });
    return this;
  }

  drain() {
    const out = { consoleErrors: this.consoleErrors, failedRequests: this.failedRequests, requests: this.requests };
    this.consoleErrors = [];
    this.failedRequests = [];
    this.requests = [];
    return out;
  }

  snapshot() {
    return { consoleErrors: [...this.consoleErrors], failedRequests: [...this.failedRequests] };
  }
}

function compile(patterns) {
  return (patterns || []).map((p) => {
    try {
      return new RegExp(p);
    } catch {
      return { test: (s) => String(s).includes(p) };
    }
  });
}
const anyMatch = (list, s) => list.some((re) => re.test(s));

/**
 * Only the noise the config does not expect.
 *   consoleBudget  { allow: [regex] }
 *   networkBudget  { allow: [regex over url], expectStatus: { "<url regex>": [403, 404] }, foreignAllow: [regex] }
 *   vitalsBudget   { cls, lcp, inp }
 * `expected` adds the statuses a journey step declares (expectedStatuses) to the map for this line only.
 */
export function evaluateBudget(config, { consoleErrors = [], failedRequests = [], requests = [], vitals = null }, expected = []) {
  const cb = config.consoleBudget ?? {};
  const nb = config.networkBudget ?? {};
  const cAllow = compile(cb.allow);
  const nAllow = compile(nb.allow);
  const foreignAllow = compile(nb.foreignAllow);
  const expect = Object.entries(nb.expectStatus ?? {}).map(([pat, codes]) => ({ re: compile([pat])[0], codes }));
  let origin = null;
  try {
    origin = new URL(config.origin).origin;
  } catch {
    /* no origin configured: foreign-origin check off */
  }

  const consoleNoise = consoleErrors.filter((e) => !anyMatch(cAllow, e.text));
  const networkNoise = failedRequests.filter((r) => {
    if (anyMatch(nAllow, r.url)) return false;
    if (r.status == null) return true; // a transport failure is noise unless allowed
    if (expected.includes(r.status)) return false;
    const hit = expect.find((x) => x.re.test(r.url));
    return !(hit && hit.codes.includes(r.status));
  });
  // Nothing loads from another origin unless the config allows it: any such request is noise.
  const foreignRequests = origin
    ? [...new Set(requests)].filter((u) => {
        try {
          const o = new URL(u);
          if (o.protocol === 'data:' || o.protocol === 'blob:') return false;
          return o.origin !== origin && !anyMatch(foreignAllow, u);
        } catch {
          return false;
        }
      })
    : [];
  const vitalsHits = [];
  const vb = config.vitalsBudget;
  if (vb && vitals) {
    for (const k of ['cls', 'lcp', 'inp']) {
      if (vb[k] != null && vitals[k] != null && vitals[k] > vb[k]) vitalsHits.push({ metric: k, value: vitals[k], budget: vb[k] });
    }
  }
  return { consoleNoise, networkNoise, foreignRequests, vitalsHits };
}

export class Manifest {
  constructor(path, run, config = {}) {
    this.path = path;
    this.run = run;
    this.config = config;
    mkdirSync(dirname(path), { recursive: true });
  }

  /** Append one step. `recorder` is drained into the line; Tier 1 fields pass through from capture(). */
  write(rec) {
    const {
      part = '?', journey, step, state, url, action, texts = {}, shots = [], clipShots = [], ms = 0, flags = {},
      axe = [], contrast = [], hitTargets = [], overflow = [], rawKeys = [], bannedText = [], vitals = null,
      ariaSnapshotRef = null, recorder = null, expectedStatuses = [],
    } = rec;
    const noise = recorder ? recorder.drain() : { consoleErrors: [], failedRequests: [], requests: [] };
    const budget = evaluateBudget(this.config, { ...noise, vitals }, expectedStatuses);
    // A state that shows a secret never exports its texts.
    const safeTexts = flags?.secrets ? { redacted: ['<secrets state: texts suppressed>'] } : texts;
    const line = {
      run: this.run, part, journey, step, state, url, action,
      consoleErrors: noise.consoleErrors, failedRequests: noise.failedRequests,
      texts: safeTexts, shots, clipShots,
      axe, contrast, hitTargets, overflow, rawKeys, bannedText, vitals, ariaSnapshotRef,
      budget, ms, flags, ts: nowIso(),
    };
    appendFileSync(this.path, JSON.stringify(line) + '\n');
    return line;
  }
}
