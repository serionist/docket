// lib/pixeldiff.mjs: the run-local visual regression check.
//
// A shot is diffed against screens/baseline/<journey>/<same name>. Zero delta is verified without a judge; a delta or a
// state never seen before goes to a judge. A shot becomes the baseline when its journey's findings are verified
// (promoteBaseline). The file name encodes the cell, so an after-shot always diffs the identical cell.
// capture() already paints config.maskRegions, so dynamic content is stable in both images; opts.maskRects excludes
// rectangles a selector can't reach (pixelmatch's ignoreMask: not compared, drawn unchanged in the diff image).
//
// Callers pass diffOptions(config), so this layer and lib/baseline-diff.mjs decide alike:
//   threshold     pixelmatch 8's per-pixel colour difference, OKLab with the HyAB distance, 0 to 1 (1 = black against
//                 white). Not pixelmatch 7's YIQ metric: baselines blessed under 7 are re-blessed once.
//   ratio rule    (the default) changed when the share of differing pixels exceeds ratioMax.
//   window rule   (windowSize set) changed when the most differing pixels in any N x N square exceed windowMax.
//                 Scattered GPU dithering and anti-aliasing never fill a square; a real change is compact.

import { PNG } from 'pngjs';
import pixelmatch from 'pixelmatch';
import { copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { basename, join } from 'node:path';
import { ensureDir } from './util.mjs';

/** The diff options from config.json (config.example.json documents each key). */
export function diffOptions(config = {}) {
  return {
    threshold: config.pixelThreshold ?? 0.1,
    ratioMax: config.pixelDiffRatioMax ?? 0,
    windowSize: config.pixelWindowSize ?? null,
    windowMax: config.pixelWindowMax ?? 0,
  };
}

/** One byte per pixel, non-zero inside any { x, y, w, h } (in the PNG's own pixels); undefined when there are none. */
function ignoreMask(width, height, rects) {
  if (!rects?.length) return undefined;
  const mask = new Uint8Array(width * height);
  for (const { x, y, w, h } of rects) {
    const x0 = Math.max(0, Math.floor(x));
    const x1 = Math.min(width, Math.ceil(x + w));
    for (let j = Math.max(0, Math.floor(y)); j < Math.min(height, Math.ceil(y + h)); j++) mask.fill(1, j * width + x0, j * width + x1);
  }
  return mask;
}

/**
 * diffShot(shot, baselineDir, { threshold, ratioMax, windowSize, windowMax, maskRects, diffOut })
 *   → { changed, reason: 'clean' | 'delta' | 'dimension' | 'no-baseline', diffPixels, diffRatio, windowPixels, name }
 * diffPixels is the total; windowPixels (the window rule only) the most in any N x N square. A different size is a
 * change (a reflow); pixelmatch can't compare unequal images.
 */
export function diffShot(shotPath, baselineDir, opts = {}) {
  const name = basename(shotPath);
  const basePath = join(baselineDir, name);
  if (!existsSync(basePath)) return { changed: true, reason: 'no-baseline', diffPixels: 0, diffRatio: 1, name };
  const a = PNG.sync.read(readFileSync(basePath));
  const b = PNG.sync.read(readFileSync(shotPath));
  if (a.width !== b.width || a.height !== b.height) {
    return { changed: true, reason: 'dimension', diffPixels: 0, diffRatio: 1, name, dims: { base: [a.width, a.height], shot: [b.width, b.height] } };
  }
  const { width, height } = a;
  const pm = { threshold: opts.threshold ?? 0.1, includeAA: false, ignoreMask: ignoreMask(width, height, opts.maskRects) };
  const diff = new PNG({ width, height });
  const diffPixels = pixelmatch(a.data, b.data, diff.data, width, height, pm);
  const diffRatio = diffPixels / (width * height);
  const win = Number.isFinite(opts.windowSize) && opts.windowSize >= 1 ? Math.floor(opts.windowSize) : null;
  let windowPixels;
  if (win) windowPixels = diffPixels ? pixelmatch(a.data, b.data, null, width, height, { ...pm, windowSize: win }) : 0;
  const changed = win ? windowPixels > (opts.windowMax ?? 0) : diffRatio > (opts.ratioMax ?? 0);
  if (changed && opts.diffOut) {
    ensureDir(opts.diffOut);
    writeFileSync(opts.diffOut, PNG.sync.write(diff));
  }
  return { changed, reason: changed ? 'delta' : 'clean', diffPixels, diffRatio, windowPixels, name };
}

export function diffAll(shotPaths, baselineDir, opts = {}) {
  const results = shotPaths.map((p) => diffShot(p, baselineDir, opts));
  return { changed: results.some((r) => r.changed), results };
}

/** Copy a verified shot into the run-local baseline. */
export function promoteBaseline(shotPath, baselineDir) {
  const dest = join(baselineDir, basename(shotPath));
  ensureDir(dest);
  copyFileSync(shotPath, dest);
  return dest;
}
