"""Meetings in a real browser (DR-0666, DR-0667, DR-0710 to DR-0712, DR-0768 to DR-0770): the days from today, then the
earlier ones; j and Enter open a meeting, a point's mark answers it with its notice, and u undoes it; an answer being
typed holds the poll, and a write from the CLI shows in place once it is sent; at phone width an open meeting takes
the screen and a write's notice shows at its foot; axe in both themes; and on the perf ledger, the page opened from
the sidebar shows its rows with its keys live within a quarter second.

It runs only with DOCKET_TEST_BROWSER set, as test_web_browser.py does, and reads axe-core from the web app's
node_modules: `npm ci` in packages/docket/web first. Invented people only.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from browser_kit import AXE, BROWSER, axe, clean, dark, live, opened
from day_kit import day_vault, meetings_week
from docket_testkit import empty_vault, make_config, traced
from ledger_kit import MEETINGS, build
from playwright.sync_api import Browser, Page, expect, sync_playwright

from docket.index import build as index_build
from docket.ledger import ops, store
from docket.vault.vault import Vault

pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")

# DR-0668's budget for a page, held in Chrome from the sidebar's click to the rows with the keys live (DR-0712).
PAGE_SECONDS = 0.25
# seed()'s Pilot sync and meetings_week() in the list's order: today's two, tomorrow's, the two ahead, then yesterday's
# and the retro twenty days back; the kick-off forty days back is out of the window.
ORDER = ["M-0002", "M-0003", "M-0001", "M-0005", "M-0006", "M-0004", "M-0007"]


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    assert AXE.is_file(), f"{AXE} is missing: run `npm ci` in packages/docket/web"
    with sync_playwright() as p:
        launched = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        yield launched
        launched.close()


@pytest.fixture
def day_root(tmp_path: Path) -> Path:
    vault = day_vault(tmp_path)
    store.write(vault, meetings_week(), by="claude", via="cli")
    return vault.root


@pytest.fixture
def day_service(day_root: Path) -> Iterator[tuple[int, str]]:
    yield from live(day_root)


def rows(page: Page) -> list[str]:
    return [str(r) for r in page.locator("[data-row]").evaluate_all("els => els.map(e => e.dataset.row)")]


def point(page: Page, text: str) -> Any:
    return page.locator("[data-point]", has_text=text)


def test_meetings_go_by_day_and_a_point_s_mark_answers_it_with_undo(
    browser: Browser, day_service: tuple[int, str]
) -> None:
    """The days in order, the sidebar's Meetings current; j and Enter open a meeting; its mark answers a point, the
    notice says so and u undoes it (DR-0666, DR-0667, DR-0711)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/meetings")
    expect(page.get_by_role("heading", level=1, name="Meetings")).to_be_visible()
    expect(page.locator("[data-row]")).to_have_count(len(ORDER))
    assert rows(page) == ORDER
    expect(page.get_by_text("Earlier days, newest first")).to_be_visible()
    sidebar = page.get_by_role("navigation", name="Pages")
    expect(sidebar.get_by_role("link", name="Meetings")).to_have_attribute("aria-current", "page")
    assert axe(page) == []

    for _ in range(3):
        page.keyboard.press("j")
    expect(page.locator('li[aria-current="true"]')).to_have_attribute("data-row", "M-0001")
    page.keyboard.press("Enter")
    panel = page.get_by_role("complementary", name=re.compile("^M-0001, Pilot sync"))
    expect(panel).to_be_visible()
    # The item keys never act on a meeting: 2 writes nothing here.
    page.keyboard.press("2")
    panel.get_by_role("button", name="Answered: The venue's dates").click()
    expect(page.get_by_role("status")).to_contain_text("M-0001: point answered")
    expect(point(page, "The venue's dates")).to_have_attribute("data-state", "answered")
    page.keyboard.press("u")
    expect(page.get_by_role("status")).to_contain_text("Undone.")
    expect(point(page, "The venue's dates")).to_have_attribute("data-state", "open")
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    clean(page, errors)


def test_an_answer_being_typed_holds_the_poll_and_a_cli_write_shows_once_it_is_sent(
    browser: Browser, day_service: tuple[int, str], day_root: Path
) -> None:
    """Another session's point shows on the open meeting: held while an answer is typed, then refetched in place once
    the answer is sent, with no reload (DR-0711)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/meetings?open=M-0001")
    page.get_by_role("button", name="Answer: The venue's dates").click()
    answer = page.get_by_label("The answer")
    answer.fill("the 12th")
    page.evaluate("window.notReloaded = true")
    point_from_cli = {"op": "point", "item": "M-0001", "fields": {"text": "Raised from the CLI"}}
    store.write(Vault.open(day_root), [point_from_cli], by="user", via="cli")
    page.wait_for_timeout(6_500)
    expect(point(page, "Raised from the CLI")).to_have_count(0)
    answer.press("Enter")
    expect(point(page, "The venue's dates")).to_have_attribute("data-state", "answered", timeout=12_000)
    expect(point(page, "Raised from the CLI")).to_have_count(1, timeout=12_000)
    assert page.evaluate("window.notReloaded === true"), "refetched in place, never reloaded"
    clean(page, errors)


def test_at_phone_width_an_open_meeting_takes_the_screen_and_its_notice_shows_at_its_foot(
    browser: Browser, day_service: tuple[int, str]
) -> None:
    port, token = day_service
    narrow: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    page, errors = opened(browser, port, token, "/meetings?open=M-0001", **narrow)
    expect(page.get_by_role("region", name="Meetings")).to_be_hidden()
    page.get_by_role("button", name="Keep: Who books the room").click()
    expect(page.get_by_role("status")).to_contain_text("M-0001: point kept")
    expect(page.get_by_role("status").get_by_role("button", name="Undo")).to_be_visible()
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    page.get_by_role("button", name="Back to the list").click()
    expect(page.locator("[data-row]")).to_have_count(len(ORDER))
    assert axe(page) == []
    clean(page, errors)


# -- the page on the perf ledger (DR-0668, DR-0712) -----------------------------------------------------------------


@pytest.fixture(scope="module")
def perf_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """#201's perf ledger in a vault, its files an hour old, and the index rebuilt over it."""
    vault = empty_vault(tmp_path_factory.mktemp("meetings-perf"), make_config())
    build(vault.root)
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
  const link = document.querySelector('nav[aria-label="Pages"] a[href="/meetings"]');
  const started = performance.now();
  link.click();
  await new Promise((done) => {
    const look = () => (document.querySelector('[data-row]') ? done() : requestAnimationFrame(look));
    look();
  });
  return (performance.now() - started) / 1000;
}"""


# On the perf ledger: out of gate os's selection, while the browser jobs run it by file, as the Queue's (DR-0757).
@pytest.mark.nightly
@pytest.mark.perf
def test_meetings_open_from_the_sidebar_within_their_budget(browser: Browser, perf_service: tuple[int, str]) -> None:
    """On 2,000 meetings with 20,000 point ops, Meetings opened from the sidebar shows the four weeks' rows with its
    keys live within a quarter second: once to warm the service's caches, then the best of three, each from a page
    loaded afresh."""
    port, token = perf_service
    page, errors = opened(browser, port, token, "/design")
    base = page.url.split("/design")[0]
    took: list[float] = []
    for _ in range(4):
        page.goto(f"{base}/design")
        page.wait_for_selector('nav[aria-label="Pages"] a[href="/meetings"]')
        page.wait_for_load_state("networkidle")
        took.append(float(page.evaluate(OPEN_FROM_SIDEBAR)))
        expect(page.locator("[data-row]").first).to_be_visible()
        page.keyboard.press("j")
        expect(page.locator('li[aria-current="true"]')).to_have_count(1)
    best = min(took[1:])
    runs = ", ".join(f"{t * 1000:.0f}" for t in took[1:])
    print(f"Meetings from the sidebar on {MEETINGS:,} meetings: {best * 1000:.0f} ms ({runs} ms)")
    if not traced():
        assert best < PAGE_SECONDS, f"Meetings took {best:.3f} s from the sidebar, over {PAGE_SECONDS} s"
    clean(page, errors)
