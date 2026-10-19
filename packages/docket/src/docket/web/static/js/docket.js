/* Docket's own script: the keys, the theme and keys switches, focus around the panel, copying a locator, playing a
   recording from a line, the reload into the sign-in page when a session ends, and on the day pages the selection,
   the ticks, the saves on change, the guard on unsaved text and the poll's hold. Loaded as a file under the page
   CSP: no inline script, no eval. The keys are the ones docket/web/pages.py lists; the page works without this
   script, by its links and forms alone. */
(() => {
  "use strict";

  const root = document.documentElement;
  const YEAR = 60 * 60 * 24 * 365;
  let lastHit = null;
  let toastTimer = 0;

  // A page knows the script runs: the day pages' fields save on change, so their Save buttons hide.
  root.dataset.script = "on";

  const keysOn = () => root.dataset.keys !== "off";

  function setCookie(name, value) {
    const age = value ? YEAR : 0;
    document.cookie = `${name}=${value}; Path=/; Max-Age=${age}; SameSite=Strict`;
  }

  function toast(text) {
    const box = document.getElementById("toast");
    if (!box) return;
    box.textContent = text;
    box.classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove("on"), 1600);
  }

  function announce() {
    const results = document.querySelector("[data-announce]");
    const region = document.getElementById("announce");
    if (results && region && results.dataset.announce) region.textContent = results.dataset.announce;
  }

  function cycleTheme() {
    const order = ["system", "light", "dark"];
    const now = root.dataset.theme || "system";
    const next = order[(order.indexOf(now) + 1) % order.length];
    if (next === "system") {
      delete root.dataset.theme;
      setCookie("docket_theme", "");
    } else {
      root.dataset.theme = next;
      setCookie("docket_theme", next);
    }
    toast(`Theme: ${next === "system" ? "follows the system" : next}`);
  }

  function copy(locator) {
    if (!locator) return;
    const done = () => toast(`Copied ${locator}`);
    if (navigator.clipboard) {
      navigator.clipboard.writeText(locator).then(done, () => toast("Copying needs the page in focus"));
    }
  }

  const hits = () => [...document.querySelectorAll("a.hit-link")];

  function move(step) {
    const all = hits();
    if (!all.length) return;
    const at = all.indexOf(document.activeElement);
    const next = at < 0 ? (step > 0 ? 0 : all.length - 1) : Math.min(all.length - 1, Math.max(0, at + step));
    all[next].focus();
    all[next].scrollIntoView({ block: "nearest" });
  }

  function press(key) {
    const target = document.querySelector(`#panel [data-key="${CSS.escape(key)}"]`);
    if (target) target.click();
    return Boolean(target);
  }

  function openKeys() {
    const dialog = document.getElementById("keys-dialog");
    if (dialog && !dialog.open) dialog.showModal();
  }

  // -- the day pages (DR-0661 to DR-0663) ------------------------------------------------------------------------

  const RESTORE = "docket-day-restore";
  const day = () => document.querySelector("[data-day]");
  const rows = () => [...document.querySelectorAll(".day-list .row")];
  const ticks = () => [...document.querySelectorAll(".day-list .tick:checked")];
  const itemPanel = () => document.querySelector("#panel [data-item]");
  let saving = 0;
  let waiting = null;
  let following = false;
  let focusAfter = null;

  // The selection: the row whose link has the focus, else the one the panel shows, else the one last selected.
  function selected() {
    const focused = document.activeElement?.closest?.(".day-list .row");
    return focused || document.querySelector(".day-list .row.open") || document.querySelector(".day-list .row.selected");
  }

  function select(row, { open = false } = {}) {
    if (!row) return;
    for (const other of document.querySelectorAll(".day-list .row.selected")) other.classList.remove("selected");
    row.classList.add("selected");
    const link = row.querySelector(".row-link");
    link?.focus({ preventScroll: true });
    row.scrollIntoView({ block: "nearest" });
    if (open && link) {
      following = true;
      link.click();
    }
  }

  function step(by) {
    const all = rows();
    if (!all.length) return;
    const here = selected();
    const at = all.indexOf(here);
    const next = at < 0 ? (by > 0 ? 0 : all.length - 1) : Math.min(all.length - 1, Math.max(0, at + by));
    // While a panel is open it follows the selection to the next row's item (DR-0662).
    select(all[next], { open: Boolean(itemPanel()) && all[next] !== here });
  }

  // What a verdict button posts, by the old tasks app's bulk rule: it clears when every ticked row holds it (DR-0661).
  function verdicts() {
    const held = ticks().map((box) => box.closest(".row")?.dataset.triage || "");
    for (const button of document.querySelectorAll("#act .verdict")) {
      const verdict = button.dataset.verdict;
      const every = held.length > 0 && held.every((t) => t === verdict);
      button.value = every ? "triage:" : `triage:${verdict}`;
      button.setAttribute("aria-pressed", every ? "true" : "false");
    }
  }

  function tick(row) {
    const box = row?.querySelector(".tick");
    if (!box) return;
    box.checked = !box.checked;
    verdicts();
  }

  function clearTicks() {
    for (const box of ticks()) box.checked = false;
    verdicts();
  }

  // Where the selection goes after a call: the row after the acted ones, or the one before, when the selection was
  // among them; otherwise it stays (DR-0662).
  function after(acted) {
    const all = rows();
    const here = selected() || (acted.length === 1 ? acted[0] : null);
    if (!here || !acted.includes(here)) return "";
    const at = all.indexOf(here);
    const later = all.slice(at + 1).find((row) => !acted.includes(row));
    const earlier = all.slice(0, at).reverse().find((row) => !acted.includes(row));
    return (later || earlier)?.dataset.ref || "";
  }

  function acted(form) {
    if (form.id === "act") return ticks().map((box) => box.closest(".row"));
    const row = form.closest(".row");
    if (row) return [row];
    const item = form.closest("[data-item]")?.dataset.item;
    return rows().filter((r) => r.dataset.ref === item);
  }

  // The scope an item key presses in: the act bar over ticked rows, else an item's panel, else the selected item row;
  // a meeting's row or panel takes none, so a key never lands on an item behind it (DR-0662).
  function scope() {
    if (ticks().length) return document.getElementById("act");
    const panel = document.getElementById("panel");
    if (itemPanel()) return panel;
    if (panel?.querySelector(".panel")) return null;
    const row = selected();
    return row?.hasAttribute("data-item") ? row : null;
  }

  function pressIn(where, key) {
    const target = where?.querySelector(`[data-key="${CSS.escape(key)}"]`);
    target?.click();
    return Boolean(target);
  }

  function drop() {
    const where = scope();
    if (!where) return;
    if (where.id === "act") {
      where.querySelector('input[name="note"]')?.focus();
    } else if (where.id === "panel") {
      const form = where.querySelector("details.close-form");
      if (form) form.open = true;
      form?.querySelector('input[name="note"]')?.focus();
    } else {
      pressIn(where, "Delete");
    }
  }

  function snooze() {
    const where = scope();
    if (!where) return;
    if (where.id === "act" || where.id === "panel") {
      where.querySelector("[data-snooze] button, [data-snooze] input")?.focus();
      return;
    }
    focusAfter = "[data-snooze] button";
    select(where, { open: true });
  }

  function newItem() {
    const details = document.querySelector("details.new-item");
    if (!details) return;
    details.open = true;
    details.querySelector('input[name="fields.title"]')?.focus();
  }

  // The keys of a day page; true when one was handled. A key held down repeats nothing.
  function dayKey(event) {
    const key = event.key;
    if (key === "j" || key === "k") {
      step(key === "j" ? 1 : -1);
      return true;
    }
    if (!["x", "1", "2", "3", "4", "0", "e", "Delete", "s", "u", "n"].includes(key)) return false;
    if (event.repeat) return true;
    switch (key) {
      case "x":
        tick(selected());
        break;
      case "Delete":
        drop();
        break;
      case "s":
        snooze();
        break;
      case "u":
        pressIn(document.querySelector(".day-notice"), "u");
        break;
      case "n":
        newItem();
        break;
      default:
        pressIn(scope(), key);
    }
    return true;
  }

  const unsaved = (where) => Boolean(where?.querySelector("form[data-unsaved]"));
  // A field the owner types or picks in; a row's tick isn't one, so the keys go on working after a click on it.
  const FIELDS = "input:not([type=checkbox]):not([type=radio]):not([type=hidden]), textarea, select, [contenteditable]";
  const field = () => Boolean(document.activeElement?.matches?.(FIELDS));

  // The poll holds back while a reload would lose what the owner is doing (DR-0663).
  function held() {
    return (
      saving > 0 ||
      document.hidden ||
      Boolean(document.querySelector("dialog[open], details.new-item[open]")) ||
      unsaved(day()) ||
      ticks().length > 0 ||
      field()
    );
  }

  const isSave = (ctx) => ctx?.request?.action?.startsWith("/ledger/save");
  const isPoll = (ctx) => ctx?.sourceElement?.matches?.("[data-poll]");

  document.addEventListener("htmx:before:request", (event) => {
    const ctx = event.detail?.ctx;
    if (!day() || !ctx) return;
    if (isPoll(ctx)) {
      if (held()) event.preventDefault();
      return;
    }
    if (isSave(ctx)) {
      saving += 1;
      const form = ctx.sourceElement?.closest("form");
      if (form) delete form.dataset.unsaved;
      return;
    }
    if (ctx.target?.id === "panel") {
      // A panel swap waits for a save in flight, then asks before it leaves unsaved text behind.
      if (saving > 0) {
        event.preventDefault();
        waiting = ctx.sourceElement;
        return;
      }
      if (unsaved(document.getElementById("panel")) && !window.confirm("Leave the text you typed unsaved?")) {
        event.preventDefault();
      }
    }
  });

  document.addEventListener("htmx:after:request", (event) => {
    const ctx = event.detail?.ctx;
    if (!day() || !ctx) return;
    if (isPoll(ctx) && ctx.hx?.refresh === "true") {
      // The poll's own reload: the selection and the panel's scroll come back after it, and only then.
      const slot = document.getElementById("panel");
      const keep = { url: location.href, ref: selected()?.dataset.ref || "", panel: slot?.scrollTop || 0, y: scrollY };
      try {
        sessionStorage.setItem(RESTORE, JSON.stringify(keep));
      } catch {
        // No session storage: the page reloads where its URL says.
      }
    }
    if (isSave(ctx) && ctx.response?.status >= 400) {
      // A refused save's field answers marked unsaved; keep the mark if the swap is held up.
      ctx.sourceElement?.closest("form")?.setAttribute("data-unsaved", "");
    }
  });

  document.addEventListener("htmx:finally:request", (event) => {
    if (!isSave(event.detail?.ctx)) return;
    saving = Math.max(0, saving - 1);
    if (saving === 0 && waiting) {
      const source = waiting;
      waiting = null;
      if (source.isConnected) source.click();
    }
  });

  function restore() {
    if (!day()) return;
    let kept = null;
    try {
      kept = JSON.parse(sessionStorage.getItem(RESTORE) || "null");
      sessionStorage.removeItem(RESTORE);
    } catch {
      kept = null;
    }
    const find = (ref) => rows().find((row) => row.dataset.ref === ref);
    if (kept && kept.url === location.href) {
      if (kept.ref) select(find(kept.ref));
      const slot = document.getElementById("panel");
      if (slot) slot.scrollTop = kept.panel;
      window.scrollTo(0, kept.y);
      return;
    }
    const at = new URLSearchParams(location.search).get("at");
    if (at) select(find(at));
  }

  document.addEventListener("input", (event) => {
    const form = event.target.closest?.("[data-day] form");
    if (form && form.method === "post") form.dataset.unsaved = "";
  });

  document.addEventListener("change", (event) => {
    if (event.target.matches?.(".day-list .tick")) verdicts();
  });

  document.addEventListener("focusin", (event) => {
    const row = event.target.closest?.(".day-list .row");
    if (!row) return;
    for (const other of document.querySelectorAll(".day-list .row.selected")) other.classList.remove("selected");
    row.classList.add("selected");
  });

  // A post names where the selection goes, and once it is sent its buttons are spent: a double click posts once.
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!day() || !(form instanceof HTMLFormElement) || form.method !== "post") return;
    const at = form.querySelector('input[name="at"]');
    if (at) at.value = after(acted(form));
    delete form.dataset.unsaved;
    setTimeout(() => {
      for (const button of form.querySelectorAll("button")) button.disabled = true;
    });
  });

  window.addEventListener("pageshow", () => {
    for (const button of document.querySelectorAll("[data-day] form button:disabled")) button.disabled = false;
  });

  window.addEventListener("beforeunload", (event) => {
    if (unsaved(day())) event.preventDefault();
  });

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    if (!day() || event.key !== "Enter" || event.ctrlKey || event.metaKey || event.altKey) return;
    // Enter in a field that saves on change saves it, as leaving it does; in the act bar's why it drops, and in its
    // day it snoozes, rather than pressing the bar's first button.
    if (target.matches?.("input[data-save]")) {
      event.preventDefault();
      target.blur();
    } else if (target.matches?.('#act input[name="note"]')) {
      event.preventDefault();
      document.querySelector('#act button[value="drop"]')?.click();
    } else if (target.matches?.('#act input[name="until"]')) {
      event.preventDefault();
      document.querySelector('#act button[value="snooze"]')?.click();
    }
  });

  // -- every page --------------------------------------------------------------------------------------------------

  document.addEventListener("click", (event) => {
    const hit = event.target.closest("a.hit-link");
    if (hit) lastHit = hit.dataset.locator || null;
    const action = event.target.closest("[data-action]");
    if (!action) return;
    switch (action.dataset.action) {
      case "theme":
        cycleTheme();
        break;
      case "keys":
        openKeys();
        break;
      case "close-dialog":
        action.closest("dialog")?.close();
        break;
      case "copy":
        copy(action.dataset.locator);
        break;
      case "select-all":
        for (const box of action.closest(".day-group")?.querySelectorAll(".tick") || []) box.checked = true;
        verdicts();
        break;
      case "clear-ticks":
        clearTicks();
        break;
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target.matches?.('[data-action="keys-switch"]')) {
      const on = event.target.checked;
      root.dataset.keys = on ? "on" : "off";
      setCookie("docket_keys", on ? "" : "off");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    const typing = target.matches?.("input, textarea, select, [contenteditable]") && !(day() && target.matches(".tick"));
    if (event.key === "Escape") {
      if (document.querySelector("dialog[open]")) return;
      if (typing) {
        // A search box clears itself on Escape; here Escape only leaves it, so the query stays.
        event.preventDefault();
        target.blur();
        return;
      }
      if (day() && ticks().length) {
        clearTicks();
        event.preventDefault();
        return;
      }
      if (press("Escape")) event.preventDefault();
      return;
    }
    if (typing || !keysOn() || document.querySelector("dialog[open]")) return;
    if (day() && dayKey(event)) {
      event.preventDefault();
      return;
    }
    switch (event.key) {
      case "/":
        document.getElementById("query")?.focus();
        break;
      case "j":
        move(1);
        break;
      case "k":
        move(-1);
        break;
      case "o":
        if (document.activeElement?.matches("a.hit-link")) document.activeElement.click();
        break;
      case "v":
      case "[":
      case "]":
        press(event.key);
        break;
      case "c": {
        const own = document.activeElement?.closest?.("[data-locator]");
        const panel = document.querySelector("#panel [data-locator]");
        copy((own || panel)?.dataset.locator);
        break;
      }
      case "?":
        openKeys();
        break;
      default:
        return;
    }
    event.preventDefault();
  });

  document.addEventListener("htmx:after:swap", (event) => {
    const target = event.detail?.ctx?.target;
    if (!target) return;
    if (target.id === "panel") {
      const item = itemPanel()?.dataset.item;
      for (const row of rows()) row.classList.toggle("open", Boolean(item) && row.dataset.ref === item);
      if (following || focusAfter) {
        // The selection moved by key: the focus stays on its row, or goes where the key asked.
        following = false;
        const asked = focusAfter && target.querySelector(focusAfter);
        focusAfter = null;
        if (asked) asked.focus();
        else selected()?.querySelector(".row-link")?.focus({ preventScroll: true });
        return;
      }
      const heading = target.querySelector("#panel-title");
      if (heading) {
        heading.focus({ preventScroll: true });
        target.querySelector(".line.cited")?.scrollIntoView({ block: "center" });
      } else if (lastHit) {
        const back = hits().find((a) => a.dataset.locator === lastHit);
        back?.focus();
      }
    } else {
      announce();
    }
  });

  // On the Recording page a line's time plays the audio from there. Without this script the same link opens the page
  // at the line, and the player starts there by itself (#t=).
  function play(link) {
    const player = document.getElementById("player");
    const line = link.closest(".segment[data-start]");
    if (!player || !line) return false;
    player.currentTime = Number(line.dataset.start);
    player.play().catch(() => toast("The audio didn't start"));
    for (const other of document.querySelectorAll(".segment.playing")) other.classList.remove("playing");
    line.classList.add("playing");
    history.replaceState(null, "", link.href);
    return true;
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest(".segment a.time");
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (play(link)) event.preventDefault();
  });

  // A session that ended: htmx doesn't swap the 401 in, and the reload shows the whole sign-in page.
  document.addEventListener("htmx:response:error", (event) => {
    if (event.detail?.ctx?.response?.status === 401) location.reload();
  });

  function ready() {
    announce();
    restore();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
})();
