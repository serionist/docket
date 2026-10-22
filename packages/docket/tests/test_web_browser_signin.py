"""The pages the service writes itself, and the way in, in a real browser (DR-0792, DR-0793): a stranger's page names
the command for the page asked for, runs no script and passes axe in both themes; the one-time link signs in and lands
on that page, its query kept; the link used again gives the used-link page and no session; a page path the app has no
route for is its not-found page in the shell; and the page a failure answers carries no detail.

It runs only with DOCKET_TEST_BROWSER set and reads axe-core from the web app's node_modules, as the other browser
files do. Invented data only: a copy of the index fixture.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from browser_kit import AXE, AXE_RUN, BROWSER, WATCH_CSP, axe, clean, dark, live, opened, watched
from docket_testkit import index_template
from playwright.sync_api import Browser, expect, sync_playwright

from docket.service import client

pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")

WANTED = "/settings?show=lists"


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    assert AXE.is_file(), f"{AXE} is missing: run `npm ci` in packages/docket/web"
    with sync_playwright() as p:
        launched = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        yield launched
        launched.close()


@pytest.fixture
def service(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Iterator[tuple[int, str]]:
    shutil.copytree(index_template(tmp_path_factory), tmp_path / "vault")
    yield from live(tmp_path / "vault")


def test_a_stranger_gets_the_command_and_the_link_lands_on_the_page_asked_for(
    browser: Browser, service: tuple[int, str]
) -> None:
    port, token = service
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.add_init_script(WATCH_CSP)
    context.add_init_script(path=str(AXE))
    page = context.new_page()
    errors = watched(page)
    scripts: list[str] = []
    page.on("request", lambda r: scripts.append(r.url) if r.resource_type == "script" else None)
    base = f"http://127.0.0.1:{port}"
    page.goto(f"{base}{WANTED}")
    expect(page.locator("h1")).to_have_text("Open Docket from a terminal on this machine")
    expect(page.locator(".bare-command")).to_have_text(f'docket open "{WANTED}"')
    assert scripts == [], "a stranger's page runs no script"
    assert page.evaluate(AXE_RUN) == []
    dark(page)
    assert page.evaluate(AXE_RUN) == []

    # The one-time link: signed in, on the page asked for, its query kept.
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    url = f"{link.body['url']}&next=%2Fsettings%3Fshow%3Dlists"
    page.goto(url)
    page.wait_for_url(f"{base}{WANTED}")
    expect(page.locator("[data-lead]")).to_be_visible()
    # Used again: the used-link page, and the session the first use started still stands.
    page.goto(url)
    expect(page.locator("h1")).to_have_text("This link was used, or is over a minute old")
    expect(page.locator(".bare-command")).to_have_text(f'docket open "{WANTED}"')
    assert page.evaluate(AXE_RUN) == []
    assert [e for e in errors if "401" not in e] == []
    assert page.evaluate("window.__csp") == []
    context.close()


def test_a_page_path_with_no_route_is_the_not_found_page_in_the_shell(
    browser: Browser, service: tuple[int, str]
) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/nowhere-at-all")
    expect(page.get_by_role("heading", name="Not found")).to_be_visible()
    expect(page.get_by_role("navigation", name="Pages")).to_be_visible()
    expect(page.locator("main")).not_to_contain_text("nowhere")
    assert axe(page) == []
    page.get_by_role("navigation", name="Pages").get_by_role("link", name=re.compile("^Settings")).click()
    expect(page.locator("[data-lead]")).to_be_visible()
    assert [e for e in errors if "404" not in e] == []
    assert page.evaluate("window.__csp") == []


def test_the_failure_page_says_so_with_no_detail(browser: Browser, service: tuple[int, str]) -> None:
    """The page a failure on a page path answers, as the service writes it, in place of the document."""
    from docket.web import bare

    port, token = service
    page, errors = opened(browser, port, token, "/design")
    headers = {"Content-Security-Policy": bare.CSP, "Cache-Control": "no-store"}
    page.route(
        re.compile(r".*/today$"),
        lambda route: route.fulfill(status=500, content_type="text/html", headers=headers, body=bare.failed_page()),
    )
    page.goto(page.url.replace("/design", "/today"))
    expect(page.locator("h1")).to_have_text("Something failed in Docket")
    expect(page.get_by_role("link", name="Back to Today")).to_be_visible()
    assert axe(page) == []
    assert [e for e in errors if "500" not in e] == []
    clean(page, [])
