// lib/connect.mjs: a headless Chrome page instrumented for capture, and the per-cell viewport mechanics.
//
// The browser is the machine's own Chrome (config.browser.channel), driven in a profile of Playwright's own, as
// devkit ui and the browser tests drive it: nothing is downloaded and the owner's own browser is never touched. Every
// page this hands out has the Web Vitals collector and the CSP watch installed before any script, the clock fixed at
// the world's "now", a Recorder attached, and its own CDP session (theme, motion and device-metrics emulation ride on
// it). Touch cells need a fresh context made with isMobile and hasTouch: CDP touch emulation alone flips matchMedia's
// answer but not the rendered `pointer` media (newEmulatedContext).

import { chromium } from 'playwright';
import { Recorder } from './manifest.mjs';

// Installed before any page script, so the vitals count from the first paint. capture() reads window.__uiAuditVitals.
const VITALS_INIT = `(() => {
  if (window.__uiAuditVitals) return;
  const v = { lcp: 0, cls: 0, inp: 0 };
  window.__uiAuditVitals = v;
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) v.lcp = Math.max(v.lcp, e.startTime); })
    .observe({ type: 'largest-contentful-paint', buffered: true }); } catch {}
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) v.cls += e.value; })
    .observe({ type: 'layout-shift', buffered: true }); } catch {}
  try { new PerformanceObserver((l) => { for (const e of l.getEntries()) v.inp = Math.max(v.inp, e.duration); })
    .observe({ type: 'event', buffered: true, durationThreshold: 40 }); } catch {}
})();`;

// CSP violations as they happen, from before the app's first script, as devkit ui and browser_kit record them.
const WATCH_CSP = `(() => {
  if (window.__csp) return;
  window.__csp = [];
  document.addEventListener('securitypolicyviolation', (e) =>
    window.__csp.push(\`\${e.violatedDirective} \${e.blockedURI || 'inline'}\`));
})();`;

async function instrument(page, config) {
  page.setDefaultTimeout(config.timeouts?.action ?? 15000);
  page.setDefaultNavigationTimeout(config.timeouts?.navigation ?? 30000);
  await page.addInitScript(VITALS_INIT);
  await page.addInitScript(WATCH_CSP);
  // setFixedTime pins Date.now() and new Date() while timers keep running. clock.install() would freeze the timer
  // queue and hang the 5 s poll and any settle that waits for a frame.
  if (config.clockEpoch) await page.clock.setFixedTime(new Date(config.clockEpoch));
}

export function launch(config) {
  return chromium.launch({ headless: true, channel: config.browser?.channel ?? 'chrome' });
}

/**
 * Set the viewport AND the device pixel ratio through CDP. setViewportSize alone never changes the DPR, so DPR 3 phone
 * bugs would never show. vp = { width, height, dpr, mobile }.
 */
export async function applyViewport(cdp, page, vp) {
  const { width, height, dpr = 1, mobile = false } = vp;
  try {
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width, height, deviceScaleFactor: dpr, mobile, screenWidth: width, screenHeight: height,
    });
    return true;
  } catch {
    await page.setViewportSize({ width, height }).catch(() => {}); // no DPR: capture() will refuse the cell
    return false;
  }
}

export async function clearViewport(cdp) {
  await cdp?.send('Emulation.clearDeviceMetricsOverride').catch(() => {});
}

/**
 * A fresh context and page for a touch cell, with a real coarse pointer. The session's cookie and the origin's storage
 * come from `baseContext`, so signed-in pages still render. Caller navigates, shoots, closes.
 */
export async function newEmulatedContext(browser, baseContext, config, vp) {
  let storageState;
  try {
    storageState = await baseContext.storageState();
  } catch {
    storageState = undefined;
  }
  const context = await browser.newContext({
    storageState,
    locale: config.browserLocale ?? 'en-GB',
    timezoneId: config.browserTimezone ?? 'UTC',
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: vp.dpr ?? 1,
    hasTouch: true,
    isMobile: true,
  });
  const page = await context.newPage();
  await instrument(page, config);
  const cdp = await context.newCDPSession(page);
  return {
    context, page, cdp,
    close: async () => {
      await cdp.detach().catch(() => {});
      await context.close().catch(() => {});
    },
  };
}

/**
 * connect(config, { storageState }) → { browser, context, page, cdp, recorder, applyViewport, clearViewport, close }
 * storageState: the owner's saved session (e2e/state/owner.json), or none for a stranger's page.
 */
export async function connect(config, opts = {}) {
  const browser = await launch(config);
  try {
    const context = await browser.newContext({
      storageState: opts.storageState,
      locale: config.browserLocale ?? 'en-GB',
      timezoneId: config.browserTimezone ?? 'UTC',
    });
    const page = await context.newPage();
    await instrument(page, config);
    const recorder = new Recorder().attach(page);
    const cdp = await context.newCDPSession(page);
    return {
      browser, context, page, cdp, recorder,
      applyViewport: (vp) => applyViewport(cdp, page, vp),
      clearViewport: () => clearViewport(cdp),
      close: async () => {
        await clearViewport(cdp);
        await browser.close().catch(() => {});
      },
    };
  } catch (err) {
    await browser.close().catch(() => {});
    throw err;
  }
}
