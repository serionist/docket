"""Today in a real browser (DR-0669, DR-0710 to DR-0712, DR-0773): / lands on it with Today first in the sidebar; the
next meeting leads, j moves into the items, 2 makes a call with its notice and u undoes it; a sync from elsewhere
shows in the bar in place, with no reload; at phone width an open item takes the screen and a write's notice shows at
its foot; axe in both themes; and on the perf ledger, the page opened from the sidebar shows its rows with its keys
live within a quarter second.

It runs only with DOCKET_TEST_BROWSER set, as test_web_browser.py does, and reads axe-core from the web app's
node_modules: `npm ci` in packages/docket/web first. Invented people only.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from browser_kit import AXE, BROWSER, axe, clean, dark, live, opened
from day_kit import add, day, day_vault, meetings_week
from docket_testkit import empty_vault, make_config, traced
from ledger_kit import MEETINGS, build
from playwright.sync_api import Browser, Page, expect, sync_playwright

from docket.index import build as index_build
from docket.ledger import ops, store
from docket.vault import cursors
from docket.vault.config import SourceConfig
from docket.vault.vault import Vault

pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")

# DR-0668's budget for a page, held in Chrome from the sidebar's click to the rows with the keys live (DR-0712).
PAGE_SECONDS = 0.25


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    assert AXE.is_file(), f"{AXE} is missing: run `npm ci` in packages/docket/web"
    with sync_playwright() as p:
        launched = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        yield launched
        launched.close()


@pytest.fixture
def day_root(tmp_path: Path) -> Path:
    """day_kit's ledger and meetings_week's, with chat a live source synced an hour ago."""
    config = make_config()
    config.sources["chat"] = SourceConfig(tools=["read_channel"])
    vault = day_vault(tmp_path, config)
    store.write(vault, meetings_week(), by="claude", via="cli")
    cursors.save(vault, "chat", cursors.Cursor(mark=datetime.now(UTC) - timedelta(hours=1)))
    return vault.root


@pytest.fixture
def day_service(day_root: Path) -> Iterator[tuple[int, str]]:
    yield from live(day_root)


def rows(page: Page) -> list[str]:
    return [str(r) for r in page.locator("[data-row]").evaluate_all("els => els.map(e => e.dataset.row)")]


def test_slash_lands_on_today_and_a_call_from_it_undoes(browser: Browser, day_service: tuple[int, str]) -> None:
    """/ opens Today, first in the sidebar and current; the day's rows, the next meeting first when one is left today;
    j moves into the items, 2 makes the call with its notice, and u undoes it (DR-0669, DR-0711)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/")
    expect(page).to_have_url(re.compile(r"/today$"))
    expect(page.get_by_role("heading", level=1, name="Today")).to_be_visible()
    sidebar = page.get_by_role("navigation", name="Pages")
    first = sidebar.get_by_role("link").nth(1)
    expect(first).to_have_attribute("href", "/today")
    expect(first).to_have_attribute("aria-current", "page")
    # day_kit's A-0001 is two days late; A-0002 is due today.
    expect(page.locator('[data-row="A-0001"]')).to_be_visible()
    expect(page.get_by_role("list", name="Last sync per source")).to_contain_text("chat")
    assert axe(page) == []

    order = rows(page)
    late = order.index("A-0001")
    for _ in range(late + 1):
        page.keyboard.press("j")
    expect(page.locator('li[aria-current="true"]')).to_have_attribute("data-row", "A-0001")
    page.keyboard.press("2")
    expect(page.get_by_role("status")).to_contain_text("A-0001: triage: claude")
    expect(page.locator('[data-row="A-0001"]')).to_have_attribute("data-triage", "claude")
    page.keyboard.press("u")
    expect(page.get_by_role("status")).to_contain_text("Undone.")
    expect(page.locator('[data-row="A-0001"]')).to_have_attribute("data-triage", "chase")
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    clean(page, errors)


def test_a_sync_from_elsewhere_shows_in_the_bar_in_place(
    browser: Browser, day_service: tuple[int, str], day_root: Path
) -> None:
    """Another clone's sync moves only a cursor file, which Today's poll hashes: the bar shows it with no reload
    (DR-0711)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/today")
    syncs = page.get_by_role("list", name="Last sync per source")
    page.evaluate("window.notReloaded = true")
    vault = Vault.open(day_root)
    mark = datetime.now(UTC) - timedelta(days=3)
    cursors.save(vault, "chat", cursors.Cursor(mark=mark))
    expect(syncs.get_by_role("link", name=re.compile("chat"))).to_have_attribute("data-due", "1", timeout=12_000)
    expect(page.get_by_text("Ask Claude to sync it.")).to_be_visible()
    assert page.evaluate("window.notReloaded === true"), "refetched in place, never reloaded"
    clean(page, errors)


def test_at_phone_width_an_open_item_takes_the_screen_and_its_notice_shows_at_its_foot(
    browser: Browser, day_service: tuple[int, str]
) -> None:
    port, token = day_service
    narrow: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    page, errors = opened(browser, port, token, "/today?open=A-0002", **narrow)
    expect(page.get_by_role("region", name="The day")).to_be_hidden()
    panel = page.get_by_role("complementary", name=re.compile("^A-0002, Book the room"))
    panel.get_by_role("button", name=re.compile("^Me")).first.click()
    # The call moves the selection, and the panel with it, to the row after; the notice stays at the panel's foot.
    expect(page.get_by_role("status")).to_contain_text("A-0002: triage: me")
    expect(page.get_by_role("complementary").get_by_role("status").get_by_role("button", name="Undo")).to_be_visible()
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    page.get_by_role("button", name="Back to the list").click()
    expect(page.get_by_role("region", name="The day")).to_be_visible()
    assert axe(page) == []
    clean(page, errors)


# -- the page on the perf ledger (DR-0668, DR-0712) -----------------------------------------------------------------


@pytest.fixture(scope="module")
def perf_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """#201's perf ledger in a vault, with a few items due today so the day has rows whatever the date, its files an
    hour old, and the index rebuilt over it."""
    vault = empty_vault(tmp_path_factory.mktemp("today-perf"), make_config())
    build(vault.root)
    due = [add(f"due{n}", "task", f"Due today {n}", "Ada Example", "pilot", due=day(0)) for n in range(5)]
    store.write(vault, due, by="claude", via="cli")
    hour_ago = time.time_ns() - 3600 * 10**9
    for path in (vault.root / ops.OPS_DIR).iterdir():
        os.utime(path, ns=(hour_ago, hour_ago))
    index_build.rebuild(vault)
    return vault.root


@pytest.fixture
def perf_service(perf_root: Path) -> Iterator[tuple[int, str]]:
    yield from live(perf_root)


# From the sidebar's click to the first row in the page, timed in the page itself.
OPEN_FROM_SIDEBAR = """async () => {
  const link = document.querySelector('nav[aria-label="Pages"] a[href="/today"]');
  const started = performance.now();
  link.click();
  await new Promise((done) => {
    const look = () => (document.querySelector('[data-row]') ? done() : requestAnimationFrame(look));
    look();
  });
  return (performance.now() - started) / 1000;
}"""


# On the perf ledger: out of gate os's selection, while the browser jobs run it by file, as the others' (DR-0757).
@pytest.mark.nightly
@pytest.mark.perf
def test_today_opens_from_the_sidebar_within_its_budget(browser: Browser, perf_service: tuple[int, str]) -> None:
    """On 5,000 items and 2,000 meetings, Today opened from the sidebar shows its rows with its keys live within a
    quarter second: once to warm the service's caches, then the best of three, each from a page loaded afresh."""
    port, token = perf_service
    page, errors = opened(browser, port, token, "/design")
    base = page.url.split("/design")[0]
    took: list[float] = []
    for _ in range(4):
        page.goto(f"{base}/design")
        page.wait_for_selector('nav[aria-label="Pages"] a[href="/today"]')
        page.wait_for_load_state("networkidle")
        took.append(float(page.evaluate(OPEN_FROM_SIDEBAR)))
        expect(page.locator("[data-row]").first).to_be_visible()
        page.keyboard.press("j")
        expect(page.locator('li[aria-current="true"]')).to_have_count(1)
    best = min(took[1:])
    runs = ", ".join(f"{t * 1000:.0f}" for t in took[1:])
    print(f"Today from the sidebar on {MEETINGS:,} meetings: {best * 1000:.0f} ms ({runs} ms)")
    if not traced():
        assert best < PAGE_SECONDS, f"Today took {best:.3f} s from the sidebar, over {PAGE_SECONDS} s"
    clean(page, errors)
