"""Search in a real browser (DR-0711, DR-0712, DR-0775): a query sent from the box lists its results with the live
region saying what came back; Esc leaves the box with the words kept; j walks the hits, Enter opens one beside them
with the panel's heading taking the focus, v verifies its summary, Esc closes it and gives the focus back to its row,
and back restores the search before; a facet narrows; a lapsed session keeps the words typed; at phone width an open
hit takes the screen; axe in both themes; and on 500 messages a query answers with its hits and the keys live within
a quarter second.

It runs only with DOCKET_TEST_BROWSER set, as test_web_browser.py does, and reads axe-core from the web app's
node_modules: `npm ci` in packages/docket/web first. Invented people only: the index fixture's.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from browser_kit import AXE, BROWSER, axe, clean, dark, live, opened
from docket_testkit import index_template, make_config, make_record, traced
from playwright.sync_api import Browser, Page, expect, sync_playwright

from docket import index
from docket.vault.gate import Gate
from docket.vault.vault import Vault

pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")

# DR-0668's budget for a page, held in Chrome from Enter in the box to the hits with the keys live (DR-0712).
PAGE_SECONDS = 0.25
CALL = "r:calls:rec-0100@01:50-03:20"


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    assert AXE.is_file(), f"{AXE} is missing: run `npm ci` in packages/docket/web"
    with sync_playwright() as p:
        launched = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def service(template: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    shutil.copytree(template, tmp_path / "vault")
    yield from live(tmp_path / "vault")


def focused(page: Page) -> str:
    return str(page.evaluate("document.activeElement.dataset.locator || document.activeElement.id || ''"))


def test_a_query_lists_its_hits_and_the_keys_open_verify_and_close_one(
    browser: Browser, service: tuple[int, str]
) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/search")
    sidebar = page.get_by_role("navigation", name="Pages")
    expect(sidebar.get_by_role("link", name="Search")).to_have_attribute("aria-current", "page")
    page.fill("#query", "Nortwin")
    page.press("#query", "Enter")
    page.wait_for_selector("[data-row]")
    expect(page).to_have_url(re.compile(r"/search\?query=Nortwin$"))
    expect(page.locator("[data-announce]")).to_have_text(re.compile(r"^\d+ hits in \d+ groups$"))
    assert axe(page) == []

    # Esc leaves the box with the query kept; j walks the hits to the call's, a new row each time.
    page.keyboard.press("Escape")
    assert page.input_value("#query") == "Nortwin"
    seen: list[str] = []
    while not seen or (not seen[-1].startswith("r:calls:") and len(seen) < 12):
        page.keyboard.press("j")
        seen.append(focused(page))
    hit = seen[-1]
    assert hit.startswith("r:calls:") and len(set(seen)) == len(seen), seen
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Raw")).to_be_visible()
    expect(page.locator("#panel-title")).to_be_focused()
    assert "hit=" in page.url
    assert axe(page) == []

    # v verifies the summary; Esc closes the panel and gives the focus back to the hit's row.
    page.keyboard.press("v")
    expect(page.get_by_role("heading", name="Each line above its raw")).to_be_visible()
    assert page.url.endswith("&verify=1")
    page.keyboard.press("Escape")
    expect(page.locator("#panel-title")).to_have_count(0)
    expect(page.locator(f'[data-locator="{hit}"]')).to_be_focused()

    # Back restores the search before.
    page.fill("#query", "fleet")
    page.press("#query", "Enter")
    expect(page).to_have_url(re.compile(r"/search\?query=fleet$"))
    page.go_back()
    expect(page).to_have_url(re.compile(r"/search\?query=Nortwin"))
    expect(page.locator("#query")).to_have_value("Nortwin")
    dark(page)
    assert axe(page) == []
    clean(page, errors)


def test_a_facet_narrows_and_its_chip_takes_it_off(browser: Browser, service: tuple[int, str]) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/search?query=Nortwin")
    page.locator("[data-facet=sources]").click()
    menu = page.get_by_role("dialog", name="Sources")
    assert axe(page) == []
    menu.get_by_role("link", name=re.compile("^calls")).click()
    expect(page).to_have_url(re.compile(r"/search\?query=Nortwin&sources=calls$"))
    page.keyboard.press("Escape")
    chip = page.get_by_role("link", name="source: calls: take it off")
    expect(chip).to_be_visible()
    chip.click()
    expect(page).to_have_url(re.compile(r"/search\?query=Nortwin$"))
    clean(page, errors)


def test_a_lapsed_session_keeps_the_words_typed(browser: Browser, service: tuple[int, str]) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/search?query=Nortwin")
    page.context.clear_cookies()
    page.fill("#query", "pallet labels")
    page.press("#query", "Enter")
    dialog = page.get_by_role("alertdialog")
    expect(dialog.locator("code")).to_have_text("docket open")
    assert page.input_value("#query") == "pallet labels"
    # The 401 is the point of this test; nothing else may fail.
    assert [e for e in errors if "401" not in e] == []


def test_at_phone_width_an_open_hit_takes_the_screen(browser: Browser, service: tuple[int, str]) -> None:
    port, token = service
    narrow: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    page, errors = opened(browser, port, token, f"/search?query=Nortwin&hit={quote(CALL)}", **narrow)
    expect(page.get_by_role("heading", name="Raw")).to_be_visible()
    expect(page.get_by_role("region", name="Results")).to_be_hidden()
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    page.get_by_role("button", name="Back to the list").click()
    expect(page.get_by_role("region", name="Results")).to_be_visible()
    assert axe(page) == []
    clean(page, errors)


# -- a query on 500 messages (DR-0668, DR-0712) ----------------------------------------------------------------------

WORDS = ["crane", "order", "offer", "supplier", "budget", "plan", "route", "lifting", "site", "visit", "review"]


@pytest.fixture(scope="module")
def perf_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """500 messages over a few months, as test_service_search_perf builds them, indexed."""
    vault = Vault.init(tmp_path_factory.mktemp("search-perf") / "vault", make_config(), "test-box")
    start = datetime(2026, 7, 1, tzinfo=UTC)
    records = [
        make_record(
            n,
            text=" ".join(WORDS[(n * step) % len(WORDS)] for step in range(1, 30)) + f" message {n}",
            ts=start + timedelta(minutes=47 * n),
        )
        for n in range(500)
    ]
    gate = Gate(vault)
    gate.stage(records)
    gate.promote()
    index.update(vault)
    return vault.root


@pytest.fixture
def perf_service(perf_root: Path) -> Iterator[tuple[int, str]]:
    yield from live(perf_root)


# From Enter in the box to the first hit in the page, timed in the page itself.
SEARCH_FROM_THE_BOX = """async (words) => {
  const box = document.getElementById('query');
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(box, words);
  box.dispatchEvent(new Event('input', { bubbles: true }));
  const started = performance.now();
  box.form.requestSubmit();
  await new Promise((done) => {
    const look = () => (document.querySelector('[data-row]') ? done() : requestAnimationFrame(look));
    look();
  });
  return (performance.now() - started) / 1000;
}"""


# On a big vault: out of gate os's selection, while the browser jobs run it by file, as the others' (DR-0757).
@pytest.mark.nightly
@pytest.mark.perf
def test_a_query_answers_with_its_hits_within_its_budget(browser: Browser, perf_service: tuple[int, str]) -> None:
    """On 500 messages, a query sent from the box shows its hits with the keys live within a quarter second: once to
    warm the service's caches, then the best of three, each a query of its own from a page loaded afresh."""
    port, token = perf_service
    page, errors = opened(browser, port, token, "/search")
    base = page.url.split("/search")[0]
    took: list[float] = []
    for words in ("crane offer", "supplier budget", "route plan", "site visit"):
        page.goto(f"{base}/search")
        page.wait_for_selector("#query")
        page.wait_for_load_state("networkidle")
        took.append(float(page.evaluate(SEARCH_FROM_THE_BOX, words)))
        page.keyboard.press("j")
        expect(page.locator('li[aria-current="true"]')).to_have_count(1)
    best = min(took[1:])
    runs = ", ".join(f"{t * 1000:.0f}" for t in took[1:])
    print(f"a query from the box on 500 messages: {best * 1000:.0f} ms ({runs} ms)")
    if not traced():
        assert best < PAGE_SECONDS, f"the hits took {best:.3f} s, over {PAGE_SECONDS} s"
    clean(page, errors)
