"""The React app's shell on /design, in a real browser (DR-0706 to DR-0708, DR-0712): what only a browser shows, such
as a CSP violation from a style element without the nonce, a font that doesn't load, the widths the shell folds at, a
key that does nothing, or the lapsed session's dialog. Each state passes axe in both themes.

It runs only with DOCKET_TEST_BROWSER set, as test_web_browser.py does, and reads axe-core from the web app's
node_modules: `npm ci` in packages/docket/web first.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import ui_world
from browser_kit import AXE, BROWSER, WATCH_CSP, axe, live, sign_in, watched
from docket_testkit import copied
from playwright.sync_api import Browser, Page, expect, sync_playwright

pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")


@pytest.fixture
def week(tmp_path: Path) -> Iterator[tuple[int, str]]:
    vault = copied(tmp_path, lambda d: ui_world.build(d / "vault", "week").root, "ui-world-week")
    yield from live(vault)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    assert AXE.is_file(), f"{AXE} is missing: run `npm ci` in packages/docket/web"
    with sync_playwright() as p:
        launched = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        yield launched
        launched.close()


def opened(browser: Browser, port: int, token: str, after: str, **options: Any) -> tuple[Page, list[str]]:
    # Motion reduced, so a dialog fading in is checked as it stands once open, not half transparent.
    defaults: dict[str, Any] = {"viewport": {"width": 1440, "height": 900}, "reduced_motion": "reduce"}
    context = browser.new_context(**(defaults | options))
    context.add_init_script(WATCH_CSP)
    context.add_init_script(path=str(AXE))
    page = context.new_page()
    errors = watched(page)
    page.goto(sign_in(port, token, after))
    page.wait_for_url(f"**{after.split('?', maxsplit=1)[0]}*")
    page.wait_for_selector("#main")
    page.evaluate("document.fonts.ready")
    return page, errors


def dark(page: Page) -> None:
    """The page in the dark theme, as the system asks for it, once the browser has painted the change."""
    page.emulate_media(color_scheme="dark")
    page.evaluate("new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")


def clean(page: Page, errors: list[str]) -> None:
    assert errors == []
    assert page.evaluate("window.__csp") == []
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_the_shell_and_its_list_in_chrome(browser: Browser, week: tuple[int, str]) -> None:
    port, token = week
    page, errors = opened(browser, port, token, "/design")
    expect(page.get_by_role("heading", level=1, name="Design")).to_be_visible()
    assert page.evaluate(
        "document.fonts.check('14px \"Atkinson Hyperlegible Next Variable\"')"
        " && document.fonts.check('12px \"Atkinson Hyperlegible Mono Variable\"')"
    )
    # The sidebar's pages, with the counts the shell's read gave, each a link to the server's own page for now.
    # Chrome joins the label and the count's words with a space before the comma; a screen reader reads it as one.
    staging = page.get_by_role("link", name=re.compile(r"^Staging ?, 3 records wait$"))
    expect(staging).to_have_attribute("href", "/staging")
    expect(page.get_by_role("link", name=re.compile(r"^Sync ?, due: chat$"))).to_be_visible()
    expect(page.get_by_text("vault on test-box")).to_be_visible()
    # j moves the selection, Enter opens it beside the list with open= in the URL, Esc closes it.
    page.keyboard.press("j")
    expect(page.locator('li[aria-current="true"]')).to_have_attribute("data-ref", "A-0015")
    page.keyboard.press("Enter")
    page.wait_for_url("**/design?open=A-0015")
    panel = page.get_by_role("complementary")
    expect(panel.get_by_role("heading", name="Renew the fleet tracker licence before it lapses")).to_be_visible()
    assert panel.bounding_box()["width"] == 480  # type: ignore[index]
    page.keyboard.press("j")
    page.wait_for_url("**/design?open=A-0018")
    page.keyboard.press("Escape")
    page.wait_for_url("**/design")
    # x ticks, 1 calls both ticked rows discuss, and the notice's Undo puts the calls back.
    page.keyboard.press("x")
    page.keyboard.press("k")
    page.keyboard.press("x")
    page.keyboard.press("1")
    expect(page.get_by_role("status")).to_contain_text("call: discuss")
    page.keyboard.press("u")
    expect(page.get_by_role("status")).to_contain_text("Undone")
    # ? lists the keys the page registered.
    page.keyboard.press("Escape")
    page.keyboard.press("?")
    expect(page.get_by_role("dialog", name="Keys")).to_contain_text("tick the selected row")
    assert axe(page) == []
    page.keyboard.press("Escape")
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    clean(page, errors)


def test_a_select_under_the_csp_gets_the_nonce(browser: Browser, week: tuple[int, str]) -> None:
    port, token = week
    page, errors = opened(browser, port, token, "/design?part=components")
    nonce = page.get_attribute('meta[name="csp-nonce"]', "content")
    assert nonce and nonce != "docket-nonce"
    page.click("#stream")
    expect(page.get_by_role("listbox")).to_be_visible()
    # Radix styles the open list's viewport through a style element of its own, which carries the nonce.
    assert page.evaluate("[...document.querySelectorAll('style')].every(s => s.nonce)")
    assert axe(page) == []
    page.get_by_role("option", name="fleet").click()
    expect(page.locator("#stream")).to_contain_text("fleet")
    # A dialog locks the page's scroll through another style element, which carries it too.
    page.get_by_role("button", name="Drop A-0012").click()
    expect(page.get_by_role("dialog", name="Drop A-0012")).to_be_visible()
    assert page.evaluate("[...document.querySelectorAll('style')].every(s => s.nonce)")
    dark(page)
    assert axe(page) == []
    clean(page, errors)


@pytest.mark.parametrize(
    ("width", "sidebar", "panel"),
    [(1440, "open", "beside"), (1100, "icons", "beside"), (900, "sheet", "over"), (390, "sheet", "screen")],
)
def test_the_shell_folds_at_its_widths(
    browser: Browser, week: tuple[int, str], width: int, sidebar: str, panel: str
) -> None:
    port, token = week
    mobile = {"has_touch": True, "is_mobile": True} if width < 768 else {}
    size = {"width": width, "height": 844}
    page, errors = opened(browser, port, token, "/design?open=A-0012", viewport=size, **mobile)
    nav = page.get_by_role("navigation", name="Pages")
    if sidebar == "sheet":
        expect(nav).to_have_count(0)
        page.get_by_role("button", name="Open the pages").click()
        expect(page.get_by_role("dialog").get_by_role("navigation", name="Pages")).to_be_visible()
        page.keyboard.press("Escape")
    else:
        expect(nav).to_have_attribute("data-state", "expanded" if sidebar == "open" else "collapsed")
        assert nav.bounding_box()["width"] == (224 if sidebar == "open" else 48)  # type: ignore[index]
    aside = page.get_by_role("complementary")
    box = aside.bounding_box()
    assert box is not None
    if panel == "screen":
        expect(page.get_by_role("button", name="Back to the list")).to_be_visible()
        expect(page.get_by_role("region", name="Items")).to_be_hidden()
        page.get_by_role("button", name="Back to the list").click()
        page.wait_for_url("**/design")
        expect(page.get_by_role("region", name="Items")).to_be_visible()
    else:
        assert box["width"] == 480
        position = aside.evaluate("el => getComputedStyle(el).position")
        assert position == ("absolute" if panel == "over" else "static")
    clean(page, errors)


def test_a_lapsed_session_asks_for_a_new_link_and_retries(browser: Browser, week: tuple[int, str]) -> None:
    port, token = week
    page, errors = opened(browser, port, token, "/design?part=components")
    page.fill("#who", "typed before the lapse")
    page.context.clear_cookies()
    page.get_by_role("button", name="End the session here").click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_contain_text('docket open "/design?part=components"')
    # Retry without a session: /api/web answers 401 and the dialog comes back.
    dialog.get_by_role("button", name="Retry").click()
    expect(page.get_by_role("alertdialog")).to_be_visible()
    # Signed in again in another tab, Retry reads the shell and the dialog goes; what was typed stayed.
    other = page.context.new_page()
    other.goto(sign_in(port, token, "/design"))
    other.wait_for_url("**/design")
    other.close()
    page.get_by_role("alertdialog").get_by_role("button", name="Retry").click()
    expect(page.get_by_role("alertdialog")).to_have_count(0)
    assert page.input_value("#who") == "typed before the lapse"
    # The 401s are the point of this test; nothing else may fail.
    assert [e for e in errors if "401" not in e] == []
    assert page.evaluate("window.__csp") == []
