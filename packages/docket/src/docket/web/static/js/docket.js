/* Docket's own script: the keys, the theme and keys switches, focus around the panel, copying a locator, playing a
   recording from a line, and the reload into the sign-in page when a session ends. Loaded as a file under the page
   CSP: no inline script, no eval. The keys are the ones docket/web/pages.py lists; the page works without this
   script, by its links alone. */
(() => {
  "use strict";

  const root = document.documentElement;
  const YEAR = 60 * 60 * 24 * 365;
  let lastHit = null;
  let toastTimer = 0;

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
    const typing = target.matches?.("input, textarea, select, [contenteditable]");
    if (event.key === "Escape") {
      if (document.querySelector("dialog[open]")) return;
      if (typing) {
        // A search box clears itself on Escape; here Escape only leaves it, so the query stays.
        event.preventDefault();
        target.blur();
        return;
      }
      if (press("Escape")) event.preventDefault();
      return;
    }
    if (typing || !keysOn() || document.querySelector("dialog[open]")) return;
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

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", announce);
  else announce();
})();
