#!/usr/bin/env node
// runner.mjs: one segment of a UI audit on one invented world. Run it from the run's e2e/.
//
//   node runner.mjs check                          config, packages, uv and Chrome, then a service on checkWorld:
//                                                  started, identified, the owner signed in, the shell shown, a
//                                                  stranger refused, stopped
//   node runner.mjs run --world <w> [--parts 01,03] [--journeys J-07,J-08] [--run p2]
//
// run: serve.py (this folder) builds the world and starts devkit ui's own throwaway service on a free port, with
// config, state and models folders, a token and a keyring of its own, and writes state/service.json. Before any part
// the runner identifies that service (lib/docket.mjs: loopback, never port 8741, /api/status answering the token the
// run's world wrote with the recorded port, pid, vault and machine), then signs the owner in through a one-time link
// on localhost (state/owner.json). Each part (parts/<nn>-<name>.mjs, in order; 00-* is the smoke and runs only when
// named) is started with node; exit 0 writes parts/<nn>.done, anything else parts/<nn>.failed. Every watch interval
// the runner touches parts/.heartbeat and probes the service; envFailThreshold misses in a row write ENV-DOWN, stop the
// part and exit. Environment failures never become findings. The service stops when the segment ends, its sessions
// and folders with it, so a world that a write changed for good is rebuilt by the next segment. Liveness for a
// resuming session: .runner.pid and the heartbeat's mtime.

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, openSync, readdirSync, readFileSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { launch } from './lib/connect.mjs';
import { identify, readService, signIn } from './lib/docket.mjs';
import { findRepoRoot, nowIso, sleep } from './lib/util.mjs';

const E2E = resolve('.');
const CONFIG_PATH = join(E2E, 'config.json');
const PARTS = join(E2E, 'parts');
const STATE = join(E2E, 'state');
const RECORD = join(STATE, 'service.json');
const HEARTBEAT = join(PARTS, '.heartbeat');
const ENV_DOWN = join(E2E, 'ENV-DOWN');
const PID_FILE = join(E2E, '.runner.pid');

const config = JSON.parse(readFileSync(CONFIG_PATH, 'utf8'));
const watchMs = (config.runner?.watchIntervalS ?? 15) * 1000;
const failThreshold = config.runner?.envFailThreshold ?? 3;
const startS = config.runner?.serviceStartS ?? 240;

function args(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const [k, v] = a.includes('=') ? a.slice(2).split('=') : [a.slice(2), argv[++i]];
      out[k] = v;
    } else out._.push(a);
  }
  return out;
}

/** The throwaway service for one world, held by serve.py until its stdin closes. */
async function startService(world) {
  if (!/^[a-z]+$/.test(world ?? '')) throw new Error(`'${world}' is not a world of packages/docket/tests/ui_world.py`);
  rmSync(RECORD, { force: true });
  mkdirSync(STATE, { recursive: true });
  const log = openSync(join(E2E, 'service.log'), 'a');
  const child = spawn('uv', ['run', 'python', join(E2E, 'serve.py'), world, '--record', RECORD, '--stdin'], {
    cwd: findRepoRoot(E2E), stdio: ['pipe', log, log], shell: false, windowsHide: true, detached: process.platform !== 'win32',
  });
  let exited = null;
  const gone = new Promise((r) => child.on('exit', (code) => r((exited = code ?? 1))));
  child.on('error', () => (exited = exited ?? 1));
  const deadline = Date.now() + startS * 1000;
  while (!existsSync(RECORD)) {
    if (exited !== null) throw new Error(`serve.py ended (${exited}) before the service started: see service.log`);
    if (Date.now() > deadline) {
      child.stdin.end();
      throw new Error(`the ${world} world's service didn't start in ${startS} s: see service.log`);
    }
    await sleep(500);
  }
  const rec = readService(RECORD);
  const stop = async () => {
    child.stdin.end();
    await Promise.race([gone, sleep(60000)]);
    if (exited === null) killTree(child.pid);
  };
  return { rec, child, stop, alive: () => exited === null };
}

async function health(rec, timeoutMs = 5000) {
  try {
    const res = await fetch(`http://127.0.0.1:${rec.port}/api/health`, { signal: AbortSignal.timeout(timeoutMs) });
    return res.status === 200 ? null : `the service's health answered ${res.status}`;
  } catch (err) {
    return `the service's health answered nothing (${String(err.message ?? err).slice(0, 80)})`;
  }
}

function killTree(pid) {
  if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(pid), '/T', '/F'], { stdio: 'ignore' });
  else {
    try {
      process.kill(-pid, 'SIGTERM'); // the part runs in its own process group (detached), browser included
    } catch {
      try {
        process.kill(pid, 'SIGTERM');
      } catch {
        /* already gone */
      }
    }
  }
}

const touch = (f) => {
  try {
    utimesSync(f, new Date(), new Date());
  } catch {
    writeFileSync(f, '');
  }
};

function partFiles(select) {
  const all = existsSync(PARTS) ? readdirSync(PARTS).filter((f) => /^\d{2}-.+\.mjs$/.test(f)).sort() : [];
  if (!select) return all.filter((f) => !f.startsWith('00-'));
  return select.split(',').map((id) => {
    const hits = all.filter((f) => f.startsWith(`${id.trim()}-`));
    if (hits.length !== 1) throw new Error(`part id '${id}' matches ${hits.length} files (ids are two digits, like 03)`);
    return hits[0];
  });
}

async function runPart(file, a, svc) {
  const id = file.slice(0, 2);
  rmSync(join(PARTS, `${id}.done`), { force: true });
  rmSync(join(PARTS, `${id}.failed`), { force: true });
  const env = {
    ...process.env, UI_AUDIT_E2E: E2E, UI_AUDIT_RUN: a.run ?? config.run ?? 'p1', UI_AUDIT_JOURNEYS: a.journeys ?? '', UI_AUDIT_SERVICE: RECORD,
  };
  console.error(`[part ${id}] start ${file} on the ${svc.rec.world} world`);
  const child = spawn(process.execPath, [join(PARTS, file)], { cwd: E2E, env, stdio: 'inherit', detached: process.platform !== 'win32' });
  let exitCode = null;
  const exited = new Promise((r) => child.on('exit', (code) => r((exitCode = code ?? 1))));
  let misses = 0;
  while (exitCode === null) {
    await Promise.race([exited, sleep(watchMs)]);
    if (exitCode !== null) break;
    touch(HEARTBEAT);
    const why = svc.alive() ? await health(svc.rec) : 'serve.py ended';
    misses = why ? misses + 1 : 0;
    if (why) console.error(`[watchdog] ${why} (${misses}/${failThreshold})`);
    if (misses >= failThreshold) {
      writeFileSync(ENV_DOWN, JSON.stringify({ ts: nowIso(), part: id, reason: why }) + '\n');
      killTree(child.pid);
      await exited;
      return 'env-down';
    }
  }
  writeFileSync(join(PARTS, `${id}.${exitCode === 0 ? 'done' : 'failed'}`), '');
  console.error(`[part ${id}] ${exitCode === 0 ? 'done' : `FAILED (exit ${exitCode})`}`);
  return exitCode === 0 ? 'done' : 'failed';
}

async function signInOwner(rec) {
  const owner = config.personas?.owner?.storageState;
  if (!owner) return;
  const browser = await launch(config);
  try {
    await signIn(browser, rec, config, join(E2E, owner));
  } finally {
    await browser.close().catch(() => {});
  }
}

async function run(a) {
  if (!a.world) throw new Error('usage: node runner.mjs run --world <world> [--parts 01,03] [--journeys J-07] [--run p2]');
  const files = partFiles(a.parts);
  rmSync(ENV_DOWN, { force: true });
  writeFileSync(PID_FILE, String(process.pid));
  touch(HEARTBEAT);
  const svc = await startService(a.world);
  try {
    await identify(svc.rec);
    console.error(`[run] identified the throwaway service on port ${svc.rec.port}, the ${svc.rec.world} world`);
    await signInOwner(svc.rec);
    for (const file of files) {
      const why = await health(svc.rec);
      if (why) {
        writeFileSync(ENV_DOWN, JSON.stringify({ ts: nowIso(), part: file.slice(0, 2), reason: why }) + '\n');
        console.error(`[run] environment down before ${file}: ${why}`);
        return 3;
      }
      if ((await runPart(file, a, svc)) === 'env-down') return 3;
      touch(HEARTBEAT);
    }
    return 0;
  } finally {
    await svc.stop();
    rmSync(join(E2E, config.personas?.owner?.storageState ?? join('state', 'owner.json')), { force: true });
    rmSync(PID_FILE, { force: true });
  }
}

function unfilled(obj, path = '') {
  if (typeof obj === 'string') return obj.startsWith('FILL') ? [path] : [];
  if (obj && typeof obj === 'object') {
    return Object.entries(obj).flatMap(([k, v]) => (k.startsWith('//') ? [] : unfilled(v, path ? `${path}.${k}` : k)));
  }
  return [];
}

/** The pixel-diff rules lib/pixeldiff.mjs reads (diffOptions) and the opt-in cells: a wrong value fails no cell loudly. */
function gridAndDiffProblems(c) {
  const out = [];
  const t = c.pixelThreshold ?? 0.1;
  if (!(t >= 0 && t <= 1)) out.push(`pixelThreshold ${t} is outside 0 to 1 (pixelmatch 8: OKLab HyAB, 1 = black against white)`);
  const r = c.pixelDiffRatioMax ?? 0;
  if (!(r >= 0 && r < 1)) out.push(`pixelDiffRatioMax ${r} is not a share of the image (0 to below 1)`);
  const n = c.pixelWindowSize ?? null;
  if (n !== null && !(Number.isInteger(n) && n >= 1)) out.push(`pixelWindowSize ${n} is not a whole number of pixels, 1 or more, or null`);
  const m = c.pixelWindowMax ?? 0;
  if (!(Number.isInteger(m) && m >= 0)) out.push(`pixelWindowMax ${m} is not a pixel count, 0 or more`);
  for (const [name, v] of Object.entries(c.captureGrid?.extraViewports ?? {})) {
    if (!name.startsWith('//') && !(v?.width > 0 && v?.height > 0)) out.push(`captureGrid.extraViewports.${name} needs a width and a height`);
  }
  return out;
}

/** The plumbing, end to end on the check world: what a drive would trip on, found before any drive. */
async function check() {
  const problems = [];
  const blocked = [
    'no fault hook in the service: route interception stands in (lib/part.mjs, faults)',
    'one owner: the personas are the owner, signed in, and a stranger with no session',
    'one locale: English',
  ];
  problems.push(...unfilled(config).map((p) => `config.${p} is still FILL`));
  for (const pkg of ['playwright', 'axe-core', 'pixelmatch', 'pngjs']) {
    if (!existsSync(join(E2E, 'node_modules', pkg))) problems.push(`${pkg} not installed (npm install --ignore-scripts)`);
  }
  problems.push(...gridAndDiffProblems(config));
  if (spawnSync('uv', ['--version'], { stdio: 'ignore', shell: false }).status !== 0) problems.push('uv is not on PATH');
  if (!existsSync(join(E2E, 'serve.py'))) problems.push('serve.py is missing: copy the whole template');
  if (problems.length) return report(problems, blocked);

  const world = config.checkWorld ?? 'empty';
  let svc;
  let browser;
  try {
    browser = await launch(config);
    console.log(`browser: ${config.browser?.channel ?? 'chrome'} ${browser.version()}, headless`);
    svc = await startService(world);
    const said = await identify(svc.rec);
    console.log(`identified: port ${said.port}, pid ${said.pid}, machine ${said.machine}, the ${world} world in ${said.vault}`);
    const owner = join(E2E, config.personas?.owner?.storageState ?? join('state', 'owner.json'));
    await signIn(browser, svc.rec, config, owner);
    console.log('signed in: the owner, through a one-time link on localhost');
    const context = await browser.newContext({ storageState: owner });
    const page = await context.newPage();
    await page.goto(`${svc.rec.origin}/`, { waitUntil: 'networkidle' });
    if (!(await page.locator(config.authedAssert.selector).first().isVisible())) problems.push(`the signed-in shell (${config.authedAssert.selector}) did not show at /`);
    for (const family of config.fonts ?? []) {
      const ok = await page.evaluate(async (f) => {
        // A face loads when text first needs it: ask for it, then check it resolved to the file, not a fallback.
        await document.fonts.load(`16px "${f}"`).catch(() => {});
        return document.fonts.check(`16px "${f}"`);
      }, family);
      if (!ok) problems.push(`the font ${family} did not load`);
      else console.log(`font: ${family}`);
    }
    await context.close();
    const stranger = await fetch(`${svc.rec.origin}/queue`, { redirect: 'manual' });
    const text = await stranger.text();
    if (stranger.status !== 401 || !text.includes('docket open')) problems.push(`a stranger at /queue got ${stranger.status}, not the 401 sign-in page`);
    if (text.includes(String(svc.rec.vault))) problems.push("the stranger's page names the vault");
    if (stranger.status === 401 && text.includes('docket open') && !text.includes(String(svc.rec.vault))) {
      console.log('a stranger: 401, the sign-in page naming docket open and no vault');
    }
  } catch (err) {
    problems.push(String(err.message ?? err));
  } finally {
    if (browser) await browser.close().catch(() => {});
    if (svc) {
      await svc.stop();
      if (!(await health(svc.rec, 3000))) problems.push(`the service on port ${svc.rec.port} still answers after the stop`);
      else console.log('stopped: the port no longer answers, the folders removed');
    }
    rmSync(join(E2E, config.personas?.owner?.storageState ?? join('state', 'owner.json')), { force: true });
  }
  return report(problems, blocked);
}

function report(problems, blocked) {
  for (const b of blocked) console.log(`BLOCKED(env): ${b}`);
  for (const p of problems) console.log(`PROBLEM: ${p}`);
  console.log(problems.length ? 'check: FAILED' : 'check: ok');
  return problems.length ? 1 : 0;
}

const a = args(process.argv.slice(2));
const [cmd = 'run'] = a._;
try {
  let code = 0;
  if (cmd === 'check') code = await check();
  else if (cmd === 'run') code = await run(a);
  else throw new Error(`unknown command '${cmd}': check, or run --world <world>`);
  process.exit(code);
} catch (err) {
  console.error(String(err.message ?? err));
  process.exit(2);
}
