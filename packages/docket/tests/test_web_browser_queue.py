"""The Queue page of the React app in a real browser (DR-0666, DR-0710 to DR-0712, DR-0714, DR-0715): the untriaged
first and the calls after, the one selection moving down the queue as the calls are made, the notice's undo, a write
from the CLI shown in place once the ticks are cleared, the who field's names taken by the arrows and Enter, the kinds'
tabs, axe in both themes at both widths; and on the perf ledger, the page opened from the sidebar shows its rows with
its keys live within a quarter second.

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
from day_kit import day_vault
from docket_testkit import empty_vault, make_config, traced
from ledger_kit import ITEMS, build
from playwright.sync_api import Browser, Page, expect, sync_playwright

from docket.index import build as index_build
from docket.ledger import ops, store
from docket.vault.vault import Vault

pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")

# DR-0668's budget for a page, held in Chrome from the sidebar's click to the rows with the keys live (DR-0712).
PAGE_SECONDS = 0.25
# day_kit's open items in the Queue's order: four untriaged by due, none last, then the one call, chase.
ORDER = ["A-0002", "A-0003", "A-0004", "A-0005", "A-0001"]


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    assert AXE.is_file(), f"{AXE} is missing: run `npm ci` in packages/docket/web"
    with sync_playwright() as p:
        launched = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        yield launched
        launched.close()


@pytest.fixture
def day_root(tmp_path: Path) -> Path:
    return day_vault(tmp_path).root


@pytest.fixture
def day_service(day_root: Path) -> Iterator[tuple[int, str]]:
    yield from live(day_root)


def row(page: Page, ref: str) -> Any:
    return page.locator(f'[data-row="{ref}"]')


def rows(page: Page) -> list[str]:
    return [str(r) for r in page.locator("[data-row]").evaluate_all("els => els.map(e => e.dataset.row)")]


def selected(page: Page) -> Any:
    return page.locator('li[aria-current="true"]')


def notice(page: Page) -> Any:
    return page.get_by_role("status")


def panel(page: Page) -> Any:
    return page.get_by_role("complementary")


def test_the_queue_lists_the_untriaged_first_and_the_calls_move_down_it(
    browser: Browser, day_service: tuple[int, str]
) -> None:
    """The untriaged by due, then the calls under their stamps; a verdict's key writes and the selection moves to the
    next untriaged row, the focus with it, while the row joins its call's group; u undoes it (DR-0666, DR-0711)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/queue")
    expect(page.get_by_role("heading", level=1, name="Queue")).to_be_visible()
    expect(page.locator("[data-row]")).to_have_count(5)
    assert rows(page) == ORDER
    heads = page.get_by_role("heading", level=2)
    expect(heads).to_have_text([re.compile("^Untriaged"), re.compile("^chase")])
    # Each row's kind is written on its tab, in the kind's own hue.
    expect(row(page, "A-0003").locator('[data-slot="kind"]:visible')).to_have_text("decision")
    sidebar = page.get_by_role("navigation", name="Pages")
    expect(sidebar.get_by_role("link", name=re.compile(r"^Queue ?, 4 untriaged$"))).to_have_attribute(
        "aria-current", "page"
    )
    assert axe(page) == []

    page.keyboard.press("j")
    expect(selected(page)).to_have_attribute("data-row", "A-0002")
    page.keyboard.press("2")
    expect(notice(page)).to_contain_text("A-0002: triage: claude")
    expect(selected(page)).to_have_attribute("data-row", "A-0003")
    expect(row(page, "A-0003")).to_be_focused()
    expect(heads).to_have_text([re.compile("^Untriaged"), re.compile("^claude"), re.compile("^chase")])
    expect(row(page, "A-0002")).to_have_attribute("data-triage", "claude")
    expect(sidebar.get_by_role("link", name=re.compile(r"^Queue ?, 3 untriaged$"))).to_be_visible()
    page.keyboard.press("3")
    expect(notice(page)).to_contain_text("A-0003: triage: me")
    expect(selected(page)).to_have_attribute("data-row", "A-0004")
    page.keyboard.press("u")
    expect(notice(page)).to_contain_text("Undone.")
    expect(row(page, "A-0003")).to_have_attribute("data-triage", "")
    assert rows(page)[:3] == ["A-0003", "A-0004", "A-0005"]
    dark(page)
    assert axe(page) == []
    clean(page, errors)


def test_ticked_rows_hold_the_poll_and_a_write_from_the_cli_shows_once_cleared(
    browser: Browser, day_service: tuple[int, str], day_root: Path
) -> None:
    """Another session's write shows in the Queue: held while a row is ticked, then refetched in place, the row moved
    to its call's group, with no reload (DR-0711)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/queue")
    page.get_by_role("checkbox", name="Tick A-0004").click()
    expect(page.get_by_role("region", name="The ticked rows")).to_be_visible()
    page.evaluate("window.notReloaded = true")
    store.write(Vault.open(day_root), [{"op": "triage", "item": "A-0005", "verdict": "discuss"}], by="user", via="cli")
    page.wait_for_timeout(6_500)
    expect(row(page, "A-0005")).to_have_attribute("data-triage", "")
    page.keyboard.press("Escape")
    expect(row(page, "A-0005")).to_have_attribute("data-triage", "discuss", timeout=12_000)
    assert rows(page) == ["A-0002", "A-0003", "A-0004", "A-0005", "A-0001"]
    expect(page.get_by_role("heading", level=2).nth(1)).to_have_text(re.compile("^discuss"))
    assert page.evaluate("window.notReloaded === true"), "refetched in place, never reloaded"
    clean(page, errors)


def test_the_who_field_offers_the_ledger_s_names_and_saves_one_taken_from_its_list(
    browser: Browser, day_service: tuple[int, str]
) -> None:
    """Typing in the panel's who opens its names, the first marked; the arrows move the mark and Enter saves the name;
    Esc closes the list and leaves the panel open; the page's keys stay out of the field (DR-0714)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/queue?open=A-0003")
    who = panel(page).get_by_role("combobox", name="Who")
    who.fill("e")
    names = page.get_by_role("listbox", name="Names")
    expect(names.get_by_role("option")).to_have_text(
        [re.compile("^Ada Example"), re.compile("^Ben Sample"), re.compile("^Use “e”")]
    )
    expect(names.get_by_role("option").first).to_have_attribute("aria-selected", "true")
    assert axe(page) == []
    page.keyboard.press("ArrowDown")
    expect(names.get_by_role("option").nth(1)).to_have_attribute("aria-selected", "true")
    marked = names.get_by_role("option").nth(1).get_attribute("id")
    expect(who).to_have_attribute("aria-activedescendant", marked or "")
    page.keyboard.press("Enter")
    expect(names).to_have_count(0)
    expect(who).to_have_value("Ben Sample")
    expect(page.locator('[data-history="A-0003"]')).to_contain_text("who: Ben Sample")
    expect(row(page, "A-0003")).to_contain_text("Ben Sample")
    # Esc closes the list alone: the panel stays, and j typed in the field is text, not the next row.
    who.fill("Cleo j")
    expect(page.get_by_role("listbox", name="Names").get_by_role("option")).to_have_text([re.compile("^Use “Cleo j”")])
    page.keyboard.press("Escape")
    expect(page.get_by_role("listbox", name="Names")).to_have_count(0)
    expect(panel(page)).to_be_visible()
    expect(who).to_be_focused()
    assert page.url.endswith("/queue?open=A-0003")
    # The second Esc leaves the field, which saves the text as typed.
    page.keyboard.press("Escape")
    expect(page.locator('[data-history="A-0003"]')).to_contain_text("who: Cleo j")
    dark(page)
    who.fill("a")
    assert axe(page) == []
    clean(page, errors)


def test_at_phone_width_the_queue_reads_in_one_column(browser: Browser, day_service: tuple[int, str]) -> None:
    port, token = day_service
    narrow: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    page, errors = opened(browser, port, token, "/queue", **narrow)
    expect(page.locator("[data-row]")).to_have_count(5)
    expect(row(page, "A-0003").locator('[data-slot="kind"]:visible')).to_be_visible()
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    clean(page, errors)


# -- the page on the perf ledger (DR-0668, DR-0712) -----------------------------------------------------------------


@pytest.fixture(scope="module")
def perf_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """#201's perf ledger in a vault, its files an hour old, and the index rebuilt over it."""
    vault = empty_vault(tmp_path_factory.mktemp("queue-perf"), make_config())
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
  const link = document.querySelector('nav[aria-label="Pages"] a[href="/queue"]');
  const started = performance.now();
  link.click();
  await new Promise((done) => {
    const look = () => (document.querySelector('[data-row]') ? done() : requestAnimationFrame(look));
    look();
  });
  return (performance.now() - started) / 1000;
}"""


@pytest.mark.perf
def test_the_queue_opens_from_the_sidebar_within_its_budget(browser: Browser, perf_service: tuple[int, str]) -> None:
    """On 5,000 items, the Queue opened from the sidebar shows its 100 rows with its keys live within a quarter second:
    once to warm the service's caches, then the best of three, each from a page loaded afresh."""
    port, token = perf_service
    page, errors = opened(browser, port, token, "/design")
    base = page.url.split("/design")[0]
    took: list[float] = []
    for _ in range(4):
        page.goto(f"{base}/design")
        page.wait_for_selector('nav[aria-label="Pages"] a[href="/queue"]')
        page.wait_for_load_state("networkidle")
        took.append(float(page.evaluate(OPEN_FROM_SIDEBAR)))
        expect(page.locator("[data-row]")).to_have_count(100)
        page.keyboard.press("j")
        expect(page.locator('li[aria-current="true"]')).to_have_count(1)
    best = min(took[1:])
    runs = ", ".join(f"{t * 1000:.0f}" for t in took[1:])
    print(f"the Queue from the sidebar on {ITEMS:,} items: {best * 1000:.0f} ms ({runs} ms)")
    if not traced():
        assert best < PAGE_SECONDS, f"the Queue took {best:.3f} s from the sidebar, over {PAGE_SECONDS} s"
    clean(page, errors)
