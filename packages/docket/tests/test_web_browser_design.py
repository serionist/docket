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

import pytest
import ui_world
from browser_kit import AXE, BROWSER, axe, clean, dark, live, opened, sign_in
from docket_testkit import copied
from playwright.sync_api import Browser, expect, sync_playwright

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
    # The Queue joins the day pages in the app, with the week's untriaged count (DR-0708).
    queue = page.get_by_role("link", name=re.compile(r"^Queue ?, 9 untriaged$"))
    expect(queue).to_have_attribute("href", "/queue")
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


def test_enter_and_space_press_the_focused_link_or_control(browser: Browser, week: tuple[int, str]) -> None:
    port, token = week
    page, errors = opened(browser, port, token, "/design")
    # The skip link: Tab reaches it first, and Enter follows it rather than opening the selected row.
    page.keyboard.press("Tab")
    expect(page.get_by_role("link", name="Skip to the page")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#main")).to_be_focused()
    assert "open=" not in page.url
    # Space on a row's tick ticks that row, not the selected one.
    boxes = page.get_by_role("checkbox")
    boxes.nth(2).focus()
    page.keyboard.press(" ")
    expect(boxes.nth(2)).to_have_attribute("aria-checked", "true")
    expect(boxes.nth(0)).to_have_attribute("aria-checked", "false")
    # Enter on the Theme button opens its menu.
    page.get_by_role("button", name=re.compile(r"^Theme")).focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("menu")).to_be_visible()
    assert axe(page) == []
    page.keyboard.press("Escape")
    expect(page.get_by_role("menu")).to_have_count(0)
    # Space on the keys switch turns the keys off, and ticks nothing.
    switch = page.get_by_role("switch", name="Keys on")
    switch.focus()
    page.keyboard.press(" ")
    expect(switch).to_have_attribute("aria-checked", "false")
    expect(page.locator('[role="checkbox"][aria-checked="true"]')).to_have_count(1)
    # With the keys off, the header's button still opens the keys, and they say they are off.
    page.get_by_role("button", name="Keys (?)").click()
    expect(page.get_by_role("dialog", name="Keys")).to_contain_text("The keys are off.")
    page.keyboard.press("Escape")
    # The dialog gone and the focus back on its button, so the switch's focus isn't taken back by the dialog's.
    expect(page.get_by_role("dialog", name="Keys")).to_have_count(0)
    expect(page.get_by_role("button", name="Keys (?)")).to_be_focused()
    switch.focus()
    page.keyboard.press(" ")
    expect(switch).to_have_attribute("aria-checked", "true")
    # Enter on a sidebar link, with the keys on, follows the link.
    page.get_by_role("navigation", name="Pages").get_by_role("link", name="Search").focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**/search")
    assert errors == []
    assert page.evaluate("window.__csp") == []


def test_the_keys_rows_and_the_index_line_show_whole(browser: Browser, week: tuple[int, str]) -> None:
    port, token = week
    # At 390 px a row's words wrap, and the row grows to hold them.
    narrow = {"width": 390, "height": 844}
    page, errors = opened(browser, port, token, "/design", viewport=narrow, has_touch=True, is_mobile=True)
    # Through the header's button, as a finger opens it: a key pressed before the page took the focus can go astray.
    page.get_by_role("button", name="Keys (?)").click()
    expect(page.get_by_role("dialog", name="Keys")).to_be_visible()
    spilled = page.evaluate(
        """() => [...document.querySelectorAll('[role=dialog] dd, [role=dialog] dt')].filter(cell => {
          const box = cell.getBoundingClientRect(); const range = document.createRange();
          range.selectNodeContents(cell); const text = range.getBoundingClientRect();
          return text.top < box.top - 0.5 || text.bottom > box.bottom + 0.5;
        }).map(cell => cell.textContent)"""
    )
    assert spilled == []
    clean(page, errors)
    # At 1440 the sidebar is 224 px, and its index line wraps rather than clipping its time.
    page, errors = opened(browser, port, token, "/design")
    line = page.get_by_role("navigation", name="Pages").locator("p").last
    expect(line).to_be_visible()
    assert line.evaluate("p => p.scrollWidth <= p.clientWidth && p.scrollHeight <= p.clientHeight")
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
    # The same words whatever the URL holds: nothing from it reaches a command the owner pastes into a shell.
    expect(dialog.locator("code")).to_have_text("docket open")
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
