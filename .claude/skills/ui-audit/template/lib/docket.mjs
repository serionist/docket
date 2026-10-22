// lib/docket.mjs: the run's throwaway Docket service, as serve.py recorded it, and the ways into it.
//
// serve.py runs devkit ui's own service: an invented world, `docket serve` on port 0 with config, state and models
// folders of its own in a `docket-ui-` temp folder, a file token and the failing keyring. identify() is the guard every
// step passes first: the record's port is never 8741 and its origin is localhost on that port, and /api/status
// answers the token the run's world wrote with the record's port, pid, vault and devkit ui's machine name. A name or a
// port alone is not a guard. Signing in goes through a one-time link on localhost, never 127.0.0.1, where the owner's
// session cookie lives, into a browser context of Playwright's own.

import { readFileSync } from 'node:fs';
import { basename, dirname } from 'node:path';
import { sleep } from './util.mjs';

export const OWNERS_PORT = 8741;
// What devkit ui gives every service it starts: its DOCKET_MACHINE, and the prefix of its temp folder.
export const MACHINE = 'ui-box';
export const WORK_PREFIX = 'docket-ui-';
// The session cookie (docket/service/sessions.py): a lapsed session is this cookie gone.
export const SESSION_COOKIE = 'docket_session';
// How long a one-time code is good for, in seconds (sessions.py's CODE_SECONDS).
export const CODE_SECONDS = 60;

/** The record serve.py wrote: { port, origin, world, vault, pid, machine, started, holder, token }. */
export function readService(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

async function call(rec, method, path, timeoutMs = 60000) {
  return fetch(`http://127.0.0.1:${rec.port}${path}`, {
    method,
    headers: { Authorization: `Bearer ${rec.token}` },
    signal: AbortSignal.timeout(timeoutMs),
    redirect: 'manual',
  });
}

/** Positive identification of the run's own service. Throws REFUSING to stop the step; returns /api/status's answer. */
export async function identify(rec) {
  const refuse = (why) => {
    throw new Error(`REFUSING: ${why}`);
  };
  if (!rec || !Number.isInteger(rec.port) || !rec.token) refuse('no service record from serve.py');
  if (rec.port === OWNERS_PORT) refuse(`port ${OWNERS_PORT} is the owner's service`);
  let origin;
  try {
    origin = new URL(rec.origin);
  } catch {
    refuse(`the record's origin ${rec.origin} is not a URL`);
  }
  if (origin.hostname !== 'localhost' || Number(origin.port) !== rec.port) refuse(`origin ${rec.origin} is not localhost:${rec.port}`);
  if (!basename(dirname(String(rec.vault ?? ''))).startsWith(WORK_PREFIX)) refuse(`vault ${rec.vault} is not in a folder devkit ui made`);
  let res;
  try {
    res = await call(rec, 'GET', '/api/status');
  } catch (err) {
    refuse(`/api/status on port ${rec.port} answered nothing (${err.message})`);
  }
  if (res.status !== 200) refuse(`/api/status on port ${rec.port} refused the run's token (${res.status})`);
  const said = await res.json();
  if (said.service !== 'docket') refuse(`port ${rec.port} is not a Docket service`);
  for (const key of ['port', 'pid', 'vault', 'machine']) {
    if (said[key] !== rec[key]) refuse(`/api/status says ${key} ${said[key]}, the record ${rec[key]}`);
  }
  if (said.machine !== MACHINE) refuse(`the service's machine is ${said.machine}, not devkit ui's ${MACHINE}`);
  return said;
}

/** A one-time sign-in link that lands on `next`, on localhost. Good once, for CODE_SECONDS. */
export async function signInLink(rec, next = '/') {
  const res = await call(rec, 'POST', '/api/session');
  if (res.status !== 200) throw new Error(`/api/session answered ${res.status}`);
  const { url } = await res.json();
  const code = new URL(url).searchParams.get('code');
  return `${rec.origin}/session?code=${encodeURIComponent(code)}&next=${encodeURIComponent(next)}`;
}

/**
 * Sign the owner in, in a browser context of its own, and save the session to `file` (state/owner.json) for the parts.
 * The session lives in the service's memory and ends when the service stops, so each segment signs in again.
 */
export async function signIn(browser, rec, config, file) {
  const context = await browser.newContext({ locale: config.browserLocale ?? 'en-GB', timezoneId: config.browserTimezone ?? 'UTC' });
  try {
    const page = await context.newPage();
    await page.goto(await signInLink(rec, '/'), { waitUntil: 'domcontentloaded' });
    const selector = config.authedAssert?.selector;
    if (selector) await page.locator(selector).first().waitFor({ state: 'visible', timeout: 30000 });
    if (new URL(page.url()).pathname.startsWith(config.authedAssert?.notPathPrefix ?? '/session')) {
      throw new Error(`signing in landed on ${page.url()}`);
    }
    await context.storageState({ path: file });
  } finally {
    await context.close().catch(() => {});
  }
}

/**
 * The invalid-input battery's B9 states for Docket's one link with a token, the sign-in link: tampered, used and
 * expired. Pass it to runBattery as `seed`. expiredLink waits out the code, a minute.
 */
export function sessionLinks(rec) {
  return {
    async tamperedLink() {
      const url = new URL(await signInLink(rec, '/'));
      const code = url.searchParams.get('code');
      url.searchParams.set('code', `${code.slice(0, -1)}${code.endsWith('A') ? 'B' : 'A'}`);
      return { url: url.href };
    },
    async usedLink() {
      const url = await signInLink(rec, '/');
      await fetch(url, { redirect: 'manual', signal: AbortSignal.timeout(30000) });
      return { url };
    },
    async expiredLink() {
      const url = await signInLink(rec, '/');
      await sleep((CODE_SECONDS + 2) * 1000);
      return { url };
    },
  };
}
