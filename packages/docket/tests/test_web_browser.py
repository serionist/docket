"""The web app in a real browser, once: what render tests can't see, such as a CSP violation, a swap that doesn't
happen, a key that does nothing, or a font that doesn't load.

It runs only with `DOCKET_TEST_BROWSER` set to the browser to drive: `chrome` or `msedge` (the system's own, nothing
downloaded) or `chromium` (Playwright's, after `playwright install chromium`). With the variable set, a browser that
can't start fails the test. CI runs it in its own Linux job; `DOCKET_TEST_SCREENSHOTS` names a folder for the shots.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
import uvicorn
from docket_testkit import index_template
from playwright.sync_api import Page, expect, sync_playwright
from sync_kit import as_structured, as_text, memo, sync_template

from docket.service import client, server
from docket.service.client import call_json
from docket.service.token import Tokens

BROWSER = os.environ.get("DOCKET_TEST_BROWSER", "")
pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def service(template: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    """The real service on a free port, in a thread: its port and its token."""
    shutil.copytree(template, tmp_path / "vault")
    yield from _serve(tmp_path / "vault")


@pytest.fixture(scope="module")
def sync_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return sync_template(tmp_path_factory)


@pytest.fixture
def sync_service(sync_project: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    """The real service over a consuming project with the invented memo source."""
    shutil.copytree(sync_project, tmp_path / "project")
    yield from _serve(tmp_path / "project" / "vault")


def _serve(vault: Path) -> Iterator[tuple[int, str]]:
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


def test_the_search_page_in_a_browser(service: tuple[int, str], tmp_path: Path) -> None:
    port, token = service
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        # A console error names no URL; these do, so a failure says which request it was.
        page.on("requestfailed", lambda r: errors.append(f"request failed: {r.method} {r.url} ({r.failure})"))
        page.on(
            "response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None
        )
        base = f"http://127.0.0.1:{port}"
        page.goto(f"{link.body['url']}&next=/search")
        page.wait_for_url(f"{base}/search")
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.fonts.check('13px Inter') && document.fonts.check('13px \"JetBrains Mono\"')")

        # A query typed and sent: the results swap in, the address bar holds the canonical URL.
        page.fill("#query", "Nortwin")
        page.press("#query", "Enter")
        page.wait_for_selector(".groups")
        assert page.url.endswith("/search?query=Nortwin")
        expect(page.locator("#announce")).to_contain_text("hits in")

        # Escape leaves the box with the query kept; j moves through the hits; Enter opens one beside the results.
        page.keyboard.press("Escape")
        assert page.input_value("#query") == "Nortwin"
        # Titles that name Northwind lead (DR-0231), so the call isn't the first group: j walks on to it, a new hit each
        # time.
        page.keyboard.press("j")
        seen = [page.evaluate("document.activeElement.dataset.locator")]
        while not seen[-1].startswith("r:calls:") and len(seen) < 8:
            page.keyboard.press("j")
            seen.append(page.evaluate("document.activeElement.dataset.locator"))
        hit = seen[-1]
        assert hit.startswith("r:calls:") and len(seen) > 1 and len(set(seen)) == len(seen)
        page.keyboard.press("Enter")
        page.wait_for_selector("#panel .raw-column .segment")
        expect(page.locator("#panel .summary-column")).to_be_visible()
        assert page.evaluate("document.activeElement.id") == "panel-title"
        page.screenshot(path=str(shots / "search-panel.png"))

        # v verifies the summary; Escape closes the panel and gives the focus back to the hit.
        page.keyboard.press("v")
        page.wait_for_selector("#panel .pairs")
        page.keyboard.press("Escape")
        expect(page.locator("#panel .panel")).to_have_count(0)
        assert page.evaluate("document.activeElement.dataset.locator") == hit

        # Back restores the search before.
        page.fill("#query", "fleet")
        page.press("#query", "Enter")
        page.wait_for_url(f"{base}/search?query=fleet")
        page.go_back()
        page.wait_for_url(f"{base}/search?query=Nortwin")
        expect(page.locator("#query")).to_have_value("Nortwin")
        page.screenshot(path=str(shots / "search-results.png"))

        # The theme switch, light then dark, survives a reload.
        page.click('[data-action="theme"]')
        page.click('[data-action="theme"]')
        page.reload()
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        page.screenshot(path=str(shots / "search-dark.png"))

        # A narrow window wraps and never scrolls sideways: the close button and the switches stay in view.
        for width, height in ((800, 900), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            page.goto(f"{base}/search?query=Nortwin&hit={quote(hit)}")
            page.wait_for_selector("#panel .panel")
            wide = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
            assert wide[0] == wide[1], (width, wide)
            for control in ("#panel .close", '[data-action="theme"]', '[data-action="keys"]'):
                box = page.locator(control).bounding_box()
                assert box is not None
                assert box["x"] >= 0 and box["x"] + box["width"] <= wide[1], (width, control, box)
            if width == 800:
                page.screenshot(path=str(shots / "search-800.png"))
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{base}/search?query=Nortwin")
        page.wait_for_selector(".groups")
        assert not errors, errors

        # With the single keys switched off, j moves nothing.
        page.keyboard.press("?")
        page.click('[data-action="keys-switch"]')
        page.click('[data-action="close-dialog"]')
        page.locator("body").click(position={"x": 5, "y": 500})
        page.keyboard.press("j")
        assert page.evaluate("document.activeElement.classList.contains('hit-link')") is False

        # A session that ended: the next search reloads into the whole sign-in page, naming the page to come back to.
        page.context.clear_cookies()
        page.fill("#query", "pallet labels")
        page.press("#query", "Enter")
        page.wait_for_selector("text=Open Docket from a terminal on this machine")
        assert page.locator("code").inner_text() == 'docket open "/search?query=Nortwin"'
        browser.close()


def test_the_sync_page_in_a_browser(sync_service: tuple[int, str], tmp_path: Path) -> None:
    port, token = sync_service
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: errors.append(f"request failed: {r.method} {r.url} ({r.failure})"))
        page.on(
            "response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None
        )
        base = f"http://127.0.0.1:{port}"
        page.goto(f"{link.body['url']}&next=/sync")
        page.wait_for_url(f"{base}/sync")

        # On a phone the source never synced is due, and the words to paste fit the screen.
        expect(page.locator(".need-line")).to_have_text("due: never synced")
        _fits(page, ".paste-text", ".paste .button")
        page.screenshot(path=str(shots / "sync-390-due.png"), full_page=True)

        # A run opened and bound as the capture hook does, with one capture: the page shows it, and its runs part
        # refreshes itself until the run closes.
        opened = call_json("POST", "/api/runs", {"source": "memo", "session": "s-browser"}, token=token, at=port)
        assert opened is not None and opened.status == 200, opened
        run = opened.body["run"]
        _capture(sync_service, run, 1, "list_memos", as_text([memo("b1")]))
        page.set_viewport_size({"width": 1440, "height": 900})
        page.reload()
        card = page.locator(f"li.state-open[id='{run}']")
        expect(card).to_contain_text("1 capture,")
        _capture(sync_service, run, 2, "get_memo", as_structured(memo("b1")))
        expect(card).to_contain_text("2 captures", timeout=8000)
        assert page.url == f"{base}/sync"
        page.screenshot(path=str(shots / "sync-open-run.png"), full_page=True)
        closed = call_json("POST", f"/api/runs/{run}/close", {"captures": 2}, token=token, at=port)
        assert closed is not None and closed.status == 200, closed
        expect(page.locator(f"li.state-closed[id='{run}']")).to_be_visible(timeout=8000)
        expect(page.locator("#runs")).to_contain_text("No run is open now")
        # A second run whose count doesn't match is dropped: a failure, with what to do about it.
        again = call_json("POST", "/api/runs", {"source": "memo", "session": "s-browser"}, token=token, at=port)
        assert again is not None and again.status == 200, again
        _capture(sync_service, again.body["run"], 3, "list_memos", as_text([memo("b2")]))
        dropped = call_json("POST", f"/api/runs/{again.body['run']}/close", {"captures": 3}, token=token, at=port)
        assert dropped is not None and dropped.status == 400, dropped
        page.reload()
        expect(page.locator(".lead")).to_contain_text("nothing needs a session; 0 runs open; last whole run")
        expect(page.locator("li.failure")).to_contain_text("What to do: nothing; if it repeats")
        page.screenshot(path=str(shots / "sync.png"), full_page=True)

        # At phone width the runs and cursors stack, and nothing scrolls sideways.
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload()
        _fits(page, f"li.run[id='{run}']", "li.failure", ".machine")
        page.screenshot(path=str(shots / "sync-390.png"), full_page=True)
        assert not errors, errors
        browser.close()


def _capture(service: tuple[int, str], run: str, n: int, tool: str, result: object) -> None:
    port, token = service
    body = {
        "session": "s-browser",
        "agent": None,
        "tool_name": f"mcp__memos__{tool}",
        "tool_use_id": f"toolu_browser_{n}",
        "arguments": {},
        "result": result,
        "captured": "2026-10-15T09:12:00+00:00",
    }
    answer = call_json("POST", f"/api/runs/{run}/captures", body, token=token, at=port)
    assert answer is not None and answer.status == 200, answer


def _fits(page: Page, *selectors: str) -> None:
    """Nothing scrolls sideways, and each of these sits inside the window."""
    wide = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert wide[0] == wide[1], wide
    for selector in selectors:
        box = page.locator(selector).first.bounding_box()
        assert box is not None, selector
        assert box["x"] >= 0 and box["x"] + box["width"] <= wide[1], (selector, box)
