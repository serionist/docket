"""What the tests that drive a real browser share: the real service on a free port in a thread, the browser named by
DOCKET_TEST_BROWSER, and the checks every page passes there (DR-0712): no console error, no CSP violation, and axe's
rules in each theme."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import uvicorn

from docket.service import client, server
from docket.service.token import Tokens

BROWSER = os.environ.get("DOCKET_TEST_BROWSER", "")
AXE = Path(__file__).parents[1] / "web" / "node_modules" / "axe-core" / "axe.min.js"
# CSP violations as they happen, recorded from before the page's first script.
WATCH_CSP = (
    "window.__csp = [];"
    "document.addEventListener('securitypolicyviolation', e => "
    "window.__csp.push(`${e.violatedDirective} ${e.blockedURI || 'inline'}`));"
)
# A modal hides the page behind it, as it should, and axe would read that page as missing its landmarks: a modal
# state is checked inside the modal on top, the last opened, a menu included.
AXE_RUN = """async () => {
  const modal = [...document.querySelectorAll(
    '[role=dialog][data-state=open], [role=alertdialog][data-state=open], [role=listbox][data-state=open], ' +
    '[role=menu][data-state=open]')].pop();
  const found = await axe.run(modal ?? document, {
    runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']},
  });
  return found.violations.map(v => `${v.id}: ${v.nodes.map(n => n.target.join(' ')).join(' | ')}`);
}"""


def live(vault: Path) -> Iterator[tuple[int, str]]:
    """The real service over the vault on a free port, in a thread: its port and its token."""
    token = Tokens().ensure().value
    servers: list[uvicorn.Server] = []
    runner = threading.Thread(
        target=server.serve, args=(vault, 0), kwargs={"said": lambda _: None, "running": servers.append}
    )
    runner.start()
    deadline = time.monotonic() + 60
    record = client.recorded()
    while record is None and time.monotonic() < deadline:
        time.sleep(0.05)
        record = client.recorded()
    assert record is not None, "the service didn't start"
    try:
        yield int(record["port"]), token
    finally:
        while not servers:
            time.sleep(0.05)
        servers[0].should_exit = True
        runner.join(30)


def sign_in(port: int, token: str, after: str) -> str:
    """A one-time link that opens `after`."""
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    return f"{link.body['url']}&next={quote(after, safe='')}"


def watched(page: Any) -> list[str]:
    """The page's console errors and page errors, as they come, and its failed requests with their URLs."""
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("requestfailed", lambda r: errors.append(f"request failed: {r.method} {r.url} ({r.failure})"))
    return errors


def axe(page: Any) -> list[str]:
    return list(page.evaluate(AXE_RUN))


def opened(browser: Any, port: int, token: str, after: str, **options: Any) -> tuple[Any, list[str]]:
    """A page of the app in a context of its own, signed in, its fonts loaded and its reads answered; with what goes
    wrong on it, as it comes."""
    # Motion reduced, so a dialog fading in is checked as it stands once open, not half transparent.
    defaults: dict[str, Any] = {"viewport": {"width": 1440, "height": 900}, "reduced_motion": "reduce"}
    context = browser.new_context(**(defaults | options))
    context.add_init_script(WATCH_CSP)
    context.add_init_script(path=str(AXE))
    page = context.new_page()
    errors = watched(page)
    page.goto(sign_in(port, token, after))
    page.wait_for_url(f"**{after.split('?', maxsplit=1)[0]}**")
    page.wait_for_selector("#main")
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready")
    return page, errors


def dark(page: Any) -> None:
    """The page in the dark theme, as the system asks for it, once its ground has turned and the browser has painted
    the change twice more: a colour that eases, even reduced, can still be on its way a frame after the switch."""
    light = page.evaluate("getComputedStyle(document.body).backgroundColor")
    page.emulate_media(color_scheme="dark")
    page.wait_for_function("(light) => getComputedStyle(document.body).backgroundColor !== light", arg=light)
    page.evaluate("new Promise(done => setTimeout(() => requestAnimationFrame(() => requestAnimationFrame(done)), 50))")


def clean(page: Any, errors: list[str]) -> None:
    """No console error, no CSP violation, and nothing scrolls sideways."""
    assert errors == []
    assert page.evaluate("window.__csp") == []
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
