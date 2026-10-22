// lib/failures.mjs: when a journey step throws, keep the evidence the script doctor needs and let the part go on.
//
// Writes failures/<journey>/<step>/{screen.png, page.html, console.json, failure.json} and a FAILURE manifest line.
// Systemic failures (the app or the origin down) are the runner's watchdog's business, not this file's.

import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { ensureDir, nowIso, slug, writeJsonAtomic } from './util.mjs';

/** Never throws: the point is to keep the run alive. */
export async function recordFailure(page, { journey, step, error, recorder, manifest, part = '?', failuresRoot = 'failures' } = {}) {
  const dir = join(failuresRoot, slug(journey ?? 'unknown'), slug(step ?? 'step'));
  ensureDir(join(dir, '_'));
  let url = '';
  try {
    url = page.url();
  } catch {
    /* the page may be gone */
  }
  const screen = join(dir, 'screen.png');
  await page.screenshot({ path: screen, fullPage: true }).catch(() => {});
  try {
    writeFileSync(join(dir, 'page.html'), await page.content());
  } catch {
    /* ignore */
  }
  try {
    writeFileSync(join(dir, 'console.json'), JSON.stringify(recorder ? recorder.snapshot() : {}, null, 2));
  } catch {
    /* ignore */
  }
  const rec = { journey, step, part, url, error: String(error?.stack ?? error).slice(0, 2000), dir, ts: nowIso() };
  writeJsonAtomic(join(dir, 'failure.json'), rec);
  manifest?.write({
    part, journey, step, state: step, url, action: `FAILURE:${step}`, shots: [screen],
    flags: { failure: true, error: rec.error.slice(0, 300), failureDir: dir }, recorder,
  });
  return rec;
}
