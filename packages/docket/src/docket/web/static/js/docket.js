/* Docket's own script: the keys dialog, the theme and keys switches, copying a command, and the reload into the
   sign-in page when a session ends. Loaded as a file under the page CSP: no inline script, no eval. The keys are the
   ones docket/web/pages.py lists; the page works without this script, by its links and forms alone. The day pages,
   Search, the Recording page, Briefs, Staging and Curate are the React app's (packages/docket/web). */
(() => {
  "use strict";

  const root = document.documentElement;
  const YEAR = 60 * 60 * 24 * 365;
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

  function openKeys() {
    const dialog = document.getElementById("keys-dialog");
    if (dialog && !dialog.open) dialog.showModal();
  }

  // -- every page --------------------------------------------------------------------------------------------------

  document.addEventListener("click", (event) => {
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
    const target = event.target;
    const typing = target.matches?.("input, textarea, select, [contenteditable]");
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Escape") {
      if (document.querySelector("dialog[open]")) return;
      if (typing) {
        // A search box clears itself on Escape; here Escape only leaves it, so what was typed stays.
        event.preventDefault();
        target.blur();
      }
      return;
    }
    if (typing || !keysOn() || document.querySelector("dialog[open]")) return;
    if (event.key !== "?") return;
    openKeys();
    event.preventDefault();
  });

  // A session that ended: htmx doesn't swap the 401 in, and the reload shows the whole sign-in page.
  document.addEventListener("htmx:response:error", (event) => {
    if (event.detail?.ctx?.response?.status === 401) location.reload();
  });
})();
