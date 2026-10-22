"""The Briefs page in a real browser (DR-0711, DR-0712, DR-0780, DR-0781): j walks the briefs and Enter opens one; a
line opens its raw beside the brief with the panel's heading taking the focus, j follows it with no new history entry
and Esc closes it, the focus back on the line; a pick confirms, the notice says so and the decision moves to Decided;
a pick on a decision another tab settled first is refused and the page shows it as it stands; at phone width the raw
takes the screen with the way back to the brief; axe in both themes; and on 60 briefs the page opened from the
sidebar shows its rows with the keys live within a quarter second.

It runs only with DOCKET_TEST_BROWSER set, as test_web_browser.py does, and reads axe-core from the web app's
node_modules: `npm ci` in packages/docket/web first. Invented people only: the index fixture's and decisions_kit's.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from briefs_kit import CALL, NORTHWIND, brief
from browser_kit import AXE, BROWSER, axe, clean, dark, live, opened
from decisions_kit import AGAIN, BRIEF, WEEKLY, decisions_template
from docket_testkit import build_index_vault, traced
from playwright.sync_api import Browser, expect, sync_playwright

from docket import index, recall

pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")

# DR-0668's budget for a page, held in Chrome from the sidebar's click to the rows with the keys live (DR-0712).
PAGE_SECONDS = 0.25
FLEET = f"/briefs?brief={BRIEF}"


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    assert AXE.is_file(), f"{AXE} is missing: run `npm ci` in packages/docket/web"
    with sync_playwright() as p:
        launched = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return decisions_template(tmp_path_factory)


@pytest.fixture
def service(template: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    shutil.copytree(template, tmp_path / "vault")
    yield from live(tmp_path / "vault")


def focused_row(page: Any) -> str:
    return str(page.evaluate("document.activeElement?.dataset?.row ?? ''"))


def test_the_keys_open_a_brief_and_a_line_s_raw_beside_it(browser: Browser, service: tuple[int, str]) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/briefs")
    sidebar = page.get_by_role("navigation", name="Pages")
    expect(sidebar.get_by_role("link", name="Briefs")).to_have_attribute("aria-current", "page")
    expect(page.get_by_role("region", name="Decisions waiting for you")).to_contain_text("Fleet management")
    assert axe(page) == []

    # j walks the briefs, Enter opens the one it is on.
    seen: list[str] = []
    while focused_row(page) != NORTHWIND and len(seen) < 6:
        page.keyboard.press("j")
        seen.append(focused_row(page))
    assert seen[-1] == NORTHWIND, seen
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/briefs\?brief=brief:northwind$"))
    expect(page.get_by_role("heading", level=1)).to_have_text("Northwind")

    # A line opens its raw beside the brief; j follows to the next line with no new history entry; Esc closes it.
    page.locator("a[data-row]", has_text="supplier call").click()
    expect(page).to_have_url(re.compile(r"&line=\d+#raw$"))
    panel = page.get_by_role("complementary")
    expect(panel.get_by_role("heading", name="Raw")).to_be_visible()
    expect(panel).to_contain_text("offer in writing")
    expect(page.locator("#panel-title")).to_be_focused()
    opened_at = page.url
    entries = page.evaluate("history.length")
    assert axe(page) == []
    page.keyboard.press("j")
    expect(page).not_to_have_url(opened_at)
    expect(panel.get_by_role("heading", name="Built from")).to_be_visible()
    assert page.evaluate("history.length") == entries
    line = re.search(r"&line=(\d+)#raw$", page.url)
    assert line is not None
    page.keyboard.press("Escape")
    expect(page.get_by_role("complementary")).to_have_count(0)
    expect(page.locator(f'a[data-row="{line.group(1)}"]')).to_be_focused()
    dark(page)
    assert axe(page) == []
    clean(page, errors)


def test_a_pick_confirms_and_one_another_tab_settled_first_is_refused(
    browser: Browser, service: tuple[int, str]
) -> None:
    port, token = service
    page, errors = opened(browser, port, token, FLEET)
    assert axe(page) == []
    weekly = page.locator(f'[data-decision="{WEEKLY}"]')
    weekly.get_by_role("button", name="Confirm").click()
    expect(page.get_by_role("status").filter(has_text="Confirmed: it is a decision now.")).to_be_visible()
    decided = page.locator('[aria-labelledby="decisions-current"]')
    expect(decided.locator(f'[data-decision="{WEEKLY}"]')).to_be_visible()
    expect(decided.get_by_role("button")).to_have_count(0)
    expect(page.locator("#decisions-title")).to_be_focused()
    assert axe(page) == []

    # Another tab of the owner's settles the contradiction first; this one's pick, at the rev it showed, writes nothing.
    other = page.context.new_page()
    other.goto(page.url)
    other.locator(f'[data-decision="{AGAIN}"]').get_by_role("button", name="The old one stands").click()
    expect(other.get_by_role("status").filter(has_text="The decision stands")).to_be_visible()
    other.close()
    page.locator(f'[data-decision="{AGAIN}"]').get_by_role("button", name="The new one replaces it").click()
    expect(page.get_by_role("alert")).to_have_text(f"{AGAIN} is rejected already")
    expect(page.locator('[aria-labelledby="decisions-waiting"]')).to_have_count(0)
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    # The 409 is the point of this test; nothing else may fail.
    assert [e for e in errors if "409" not in e] == []
    assert page.evaluate("window.__csp") == []


def test_at_phone_width_a_line_s_raw_takes_the_screen(browser: Browser, service: tuple[int, str]) -> None:
    port, token = service
    narrow: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    page, errors = opened(browser, port, token, f"/briefs?brief={NORTHWIND}", **narrow)
    page.locator("a[data-row]", has_text="supplier call").click()
    expect(page.get_by_role("complementary")).to_contain_text("offer in writing")
    expect(page.get_by_role("region", name="Northwind")).to_be_hidden()
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    page.get_by_role("button", name="Back to the brief").click()
    expect(page.get_by_role("region", name="Northwind")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/briefs\?brief=brief:northwind$"))
    assert axe(page) == []
    clean(page, errors)


# -- 60 briefs (DR-0668, DR-0712) -----------------------------------------------------------------------------------

BRIEFS = 60


@pytest.fixture(scope="module")
def perf_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The index fixture with 60 topics, each with a brief of a dozen lines on the supplier call, indexed: more briefs
    than an owner keeps on the people, customers and projects they work with."""
    vault = build_index_vault(tmp_path_factory.mktemp("briefs-perf"))
    topics = vault.root / "entities" / "topic"
    topics.mkdir(parents=True, exist_ok=True)
    names = [f"entities/topic/topic-{n:03d}.yaml" for n in range(BRIEFS)]
    for n in range(BRIEFS):
        (topics / f"topic-{n:03d}.yaml").write_text(
            f"id: topic-{n:03d}\ntype: topic\nname: {{en: Topic {n:03d}}}\n", encoding="utf-8"
        )
    vault.commit(names, "Topics")
    for n in range(BRIEFS):
        said = [f"- Point {k} on the call. [r:{CALL}@00:30-01:50]" for k in range(12)]
        recall.doc_put(vault, brief(f"topic-{n:03d}", f"Topic {n:03d}", [CALL], [said, [], []]))
    index.update(vault)
    return vault.root


@pytest.fixture
def perf_service(perf_root: Path) -> Iterator[tuple[int, str]]:
    yield from live(perf_root)


# From the sidebar's click to the first row in the page, timed in the page itself.
OPEN_FROM_SIDEBAR = """async () => {
  const link = document.querySelector('nav[aria-label="Pages"] a[href="/briefs"]');
  const started = performance.now();
  link.click();
  await new Promise((done) => {
    const look = () => (document.querySelector('[data-row]') ? done() : requestAnimationFrame(look));
    look();
  });
  return (performance.now() - started) / 1000;
}"""


# On 60 briefs: out of gate os's selection, while the browser jobs run it by file, as the others' (DR-0757).
@pytest.mark.nightly
@pytest.mark.perf
def test_the_list_opens_from_the_sidebar_within_its_budget(browser: Browser, perf_service: tuple[int, str]) -> None:
    """On 60 briefs, the list opened from the sidebar shows its rows with the keys live within a quarter second: once
    to warm the service's caches, then the best of three, each from a page loaded afresh."""
    port, token = perf_service
    page, errors = opened(browser, port, token, "/design")
    base = page.url.split("/design")[0]
    took: list[float] = []
    for _ in range(4):
        page.goto(f"{base}/design")
        page.wait_for_selector('nav[aria-label="Pages"] a[href="/briefs"]')
        page.wait_for_load_state("networkidle")
        took.append(float(page.evaluate(OPEN_FROM_SIDEBAR)))
        expect(page.locator("[data-row]")).to_have_count(BRIEFS)
        page.keyboard.press("j")
        expect(page.locator("[data-row]:focus")).to_have_count(1)
    best = min(took[1:])
    runs = ", ".join(f"{t * 1000:.0f}" for t in took[1:])
    print(f"the Briefs list from the sidebar on {BRIEFS} briefs: {best * 1000:.0f} ms ({runs} ms)")
    if not traced():
        assert best < PAGE_SECONDS, f"the list took {best:.3f} s from the sidebar, over {PAGE_SECONDS} s"
    clean(page, errors)
