"""The smoke set's pages (DR-0754): every page of both apps once in the system's own browser, failing on a console
error, a failed request or a CSP violation. The server's pages are the navigation's built ones, the React app's the
routes its build serves, both read from the code, so a new page is loaded here without a line of its own. Windows and
macOS run it on every PR with the smoke set, Linux in its browser shares; the other browser files test each page's
states and their accessibility.

It runs only where `DOCKET_TEST_BROWSER` names the browser to drive (`chrome` or `msedge`, the system's own, or
`chromium`, Playwright's), as the browser files do, so `gate os` never starts a browser on a developer's machine.
Invented data only: a copy of the index fixture."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from browser_kit import WATCH_CSP, live, sign_in, watched
from docket_testkit import index_template
from playwright.sync_api import sync_playwright

from docket.web import frontend, pages

BROWSER = os.environ.get("DOCKET_TEST_BROWSER", "")
pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome, msedge or chromium"),
]


@pytest.fixture
def service(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Iterator[tuple[int, str]]:
    shutil.copytree(index_template(tmp_path_factory), tmp_path / "vault")
    yield from live(tmp_path / "vault")


def every_page() -> list[str]:
    """The server's built pages, then the React app's routes, each once."""
    found = [page.path for page in pages.PAGES if page.built]
    return [*found, *(route for route in frontend.BUILD.routes if route not in found)]


def problems(browser: Any, port: int, token: str, path: str) -> list[str]:
    """What went wrong on one page, opened signed in in a context of its own, once it settled."""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.add_init_script(WATCH_CSP)
    try:
        page = context.new_page()
        errors = watched(page)
        page.goto(sign_in(port, token, path))
        page.wait_for_url(f"**{path}**")
        page.wait_for_selector("#main")
        page.wait_for_load_state("networkidle")
        violations: list[str] = page.evaluate("window.__csp")
        return [*errors, *(f"CSP: {v}" for v in violations)]
    finally:
        context.close()


def test_every_page_of_both_apps_loads_clean_in_the_system_s_browser(service: tuple[int, str]) -> None:
    port, token = service
    paths = every_page()
    assert {"/search", "/ledger", "/queue", "/design"} <= set(paths)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        try:
            found = {path: problems(browser, port, token, path) for path in paths}
        finally:
            browser.close()
    assert {path: said for path, said in found.items() if said} == {}
