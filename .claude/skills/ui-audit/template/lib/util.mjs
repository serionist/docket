// lib/util.mjs: small helpers shared by the capture core. Plain Node, no Playwright.

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** ISO-8601 time, one place so every ledger line stamps the same way. */
export const nowIso = () => new Date().toISOString();

/** A file-system-safe slug for state, journey and step names. */
export function slug(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '');
}

export function ensureDir(filePath) {
  mkdirSync(dirname(filePath), { recursive: true });
}

/** Write JSON through a temporary file and a rename, so a reader never sees half a file. */
export function writeJsonAtomic(path, obj) {
  ensureDir(path);
  const tmp = `${path}.tmp-${process.pid}-${Date.now().toString(36)}`;
  writeFileSync(tmp, JSON.stringify(obj, null, 2));
  renameSync(tmp, path);
  return path;
}

/** Same for text (journey files): temporary file, then rename. */
export function writeTextAtomic(path, text) {
  ensureDir(path);
  const tmp = `${path}.tmp-${process.pid}-${Date.now().toString(36)}`;
  writeFileSync(tmp, text);
  renameSync(tmp, path);
  return path;
}

/** Parse a JSON file; null on any read or parse error. */
export function readJsonSafe(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
}

/** The repo root: the nearest folder above `from` holding `.git` (a folder, or a file in a worktree). */
export function findRepoRoot(from = process.cwd()) {
  let dir = resolve(from);
  for (;;) {
    if (existsSync(join(dir, '.git'))) return dir;
    const parent = dirname(dir);
    if (parent === dir) throw new Error(`no repo root above ${from}`);
    dir = parent;
  }
}

/** Poll `fn` until it returns something truthy or the time runs out; returns that value or null. */
export async function until(fn, { timeoutMs = 15000, intervalMs = 500 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const v = await fn();
      if (v) return v;
    } catch {
      /* keep polling */
    }
    if (Date.now() > deadline) return null;
    await sleep(intervalMs);
  }
}

// Probes for the invalid-input battery (reality-lenses.md, Invalid input).
export const TEN_K = 'A'.repeat(10000);
export const MARKUP_ECHO = `<b>x</b>"'<script>alert(1)</script>`;
export const UNICODE_PROBE = 'Ẑoé 日本語 🔒 Ünüşöő';
