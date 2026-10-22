// lib/baseline-diff.mjs: the blessed baselines, kept at .local/ui-audit/baselines/<page>/ in the checkout, never committed:
// DR-0712 chose no pixel baselines in the repo while the pages change PR by PR, so they live on this machine only.
//
// pixeldiff.mjs owns the run-local layer (screens/baseline/, the loop inside one run). This file owns the layer across
// runs: a few canonical shots per key state of each screen that ended an audit clean. A re-run diffs its canonical
// shots against them and sends only changed cells to a judge, so an unchanged screen re-verifies for free.
// <page> is the page's route name, lowercase, with a part after a hyphen where one page has several: queue, today,
// staging-held.
//
// Rules (reference.md, Baselines): bless canonical cells only, only verified states, never a state marked
// `secrets: true`; a deliberate redesign re-blesses in the change that closes its finding. A stale blessed shot shows as
// a change to judge, never as a false pass.
//
// Fonts and anti-aliasing differ by platform and browser build, and pixelmatch's colour metric by major version, so
// `bless` writes baseline.json (Playwright, browser, platform, pixelmatch major) and `diff` anywhere else treats every
// cell as new instead of changed. The diff rules are config.json's (pixelThreshold, pixelDiffRatioMax, pixelWindowSize,
// pixelWindowMax), read through diffOptions() exactly as the run-local layer reads them.
//
// From e2e/:
//   node lib/baseline-diff.mjs diff  <screen> <shot.png...>   exit 1 when a cell changed
//   node lib/baseline-diff.mjs bless <screen> <shot.png...>   copies into .local/ui-audit/baselines/<page>/
//   node lib/baseline-diff.mjs list  <screen>

import { copyFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { basename, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { diffOptions, diffShot } from './pixeldiff.mjs';
import { findRepoRoot, readJsonSafe, writeJsonAtomic } from './util.mjs';

const require = createRequire(import.meta.url);
export const BLESSED_ROOT = ['.local', 'ui-audit', 'baselines'];

export function blessedDir(screen, repoRoot = findRepoRoot()) {
  if (!/^[a-z][a-z0-9-]*$/.test(screen)) throw new Error(`'${screen}' is not a page name like queue or staging-held`);
  return join(repoRoot, ...BLESSED_ROOT, screen);
}

const version = (pkg) => {
  try {
    return require(`${pkg}/package.json`).version;
  } catch {
    return null; // not installed
  }
};

/** Where these shots were made: blessed shots are only comparable on the same platform, browser build and metric. */
export function platform() {
  const pm = version('pixelmatch');
  return { playwright: version('playwright'), browser: 'chromium', platform: process.platform, pixelmatch: pm && pm.split('.')[0] };
}

const samePlatform = (a, b) =>
  !!a && a.playwright === b.playwright && a.browser === b.browser && a.platform === b.platform && a.pixelmatch === b.pixelmatch;

/**
 * → { changed, clean, newStates, platformMismatch } (each entry a pixeldiff result).
 * opts: the diff rules (diffOptions(config): threshold, ratioMax, windowSize, windowMax), repoRoot, diffOutDir.
 */
export function diffAgainstBlessed(screen, shots, opts = {}) {
  const dir = blessedDir(screen, opts.repoRoot);
  const meta = readJsonSafe(join(dir, 'baseline.json'));
  const out = { changed: [], clean: [], newStates: [], platformMismatch: null };
  if (meta && !samePlatform(meta, platform())) out.platformMismatch = { blessed: meta, here: platform() };
  for (const shot of shots) {
    if (out.platformMismatch) {
      out.newStates.push({ name: basename(shot), reason: 'platform-mismatch' });
      continue;
    }
    const r = diffShot(shot, dir, {
      ...diffOptions(),
      ...opts,
      diffOut: opts.diffOutDir ? join(opts.diffOutDir, `blessed-${basename(shot)}`) : undefined,
    });
    (r.reason === 'no-baseline' ? out.newStates : r.changed ? out.changed : out.clean).push(r);
  }
  return out;
}

export function bless(screen, shots, opts = {}) {
  const dir = blessedDir(screen, opts.repoRoot);
  mkdirSync(dir, { recursive: true });
  const meta = readJsonSafe(join(dir, 'baseline.json'));
  if (meta && !samePlatform(meta, platform()) && !opts.replacePlatform) {
    throw new Error(`blessed on ${JSON.stringify(meta)}, this is ${JSON.stringify(platform())}: re-bless the whole screen with --replace-platform`);
  }
  const out = shots.map((s) => {
    const dest = join(dir, basename(s));
    copyFileSync(s, dest);
    return dest;
  });
  writeJsonAtomic(join(dir, 'baseline.json'), platform());
  return out;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const args = process.argv.slice(2);
  const replacePlatform = args.includes('--replace-platform');
  const [cmd, screen, ...shots] = args.filter((a) => a !== '--replace-platform');
  if (!cmd || !screen || (cmd !== 'list' && shots.length === 0)) {
    console.error('usage: baseline-diff.mjs diff|bless <screen> <shot.png...> | list <screen>');
    process.exit(2);
  }
  try {
    if (cmd === 'list') {
      const dir = blessedDir(screen);
      const cells = existsSync(dir) ? readdirSync(dir).filter((f) => f.endsWith('.png')).sort() : [];
      console.log(cells.length ? cells.join('\n') : `(no blessed baselines for ${screen})`);
    } else if (cmd === 'diff') {
      const r = diffAgainstBlessed(screen, shots, diffOptions(readJsonSafe('config.json') ?? {}));
      if (r.platformMismatch) console.log(`PLATFORM ${JSON.stringify(r.platformMismatch)}: every cell treated as new`);
      const size = (x) =>
        x.windowPixels === undefined ? `${(x.diffRatio * 100).toFixed(2)} % of pixels` : `${x.windowPixels} px in the worst window`;
      for (const x of r.clean) console.log(`clean    ${x.name}`);
      for (const x of r.newStates) console.log(`new      ${x.name}`);
      for (const x of r.changed) console.log(`CHANGED  ${x.name} (${size(x)})`);
      process.exit(r.changed.length ? 1 : 0);
    } else if (cmd === 'bless') {
      for (const d of bless(screen, shots, { replacePlatform })) console.log(`blessed  ${d}`);
      console.log('Kept on this machine under .local/: canonical, verified, no secrets. Never commit them (DR-0712).');
    } else {
      console.error(`unknown command '${cmd}'`);
      process.exit(2);
    }
  } catch (err) {
    console.error(err.message);
    process.exit(2);
  }
}
