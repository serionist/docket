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
# state is checked inside the modal.
AXE_RUN = """async () => {
  const modal = document.querySelector(
    '[role=dialog][data-state=open], [role=alertdialog][data-state=open], [role=listbox][data-state=open]');
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
