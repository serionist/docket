"""The Staging page in a real browser (DR-0711, DR-0712, DR-0784, DR-0785): j walks the records and Enter opens one
beside the list with the panel's heading taking the focus; ticks, Preview and Work through the gate, a refusal kept
beside the ticks; private through its own question; Promote all, the sidebar's count following; a record another tab
classified first is refused and shown as it stands; at phone width the record takes the screen with the way back; axe
in both themes; and on 300 records the page opened from the sidebar shows its rows with the keys live within a quarter
second.

It runs only with DOCKET_TEST_BROWSER set, as test_web_browser.py does, and reads axe-core from the web app's
node_modules: `npm ci` in packages/docket/web first. Invented people only: staging_kit's.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from browser_kit import AXE, BROWSER, axe, clean, dark, live, opened
from docket_testkit import empty_vault, make_record, traced
from playwright.sync_api import Browser, expect, sync_playwright
from staging_kit import config, staging_vault

from docket.vault.gate import Gate

pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")

# DR-0668's budget for a page, held in Chrome from the sidebar's click to the rows with the keys live (DR-0712).
PAGE_SECONDS = 0.25
LEAD = "3 records wait at the gate: 2 for you to classify, 1 ready to promote"


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    assert AXE.is_file(), f"{AXE} is missing: run `npm ci` in packages/docket/web"
    with sync_playwright() as p:
        launched = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        yield launched
        launched.close()


@pytest.fixture
def service(tmp_path: Path) -> Iterator[tuple[int, str]]:
    yield from live(staging_vault(tmp_path).root)


def focused_row(page: Any) -> str:
    return str(page.evaluate("document.activeElement?.dataset?.row ?? ''"))


def test_the_keys_open_a_record_and_preview_and_work_cut_it(browser: Browser, service: tuple[int, str]) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/staging")
    sidebar = page.get_by_role("navigation", name="Pages")
    expect(sidebar.get_by_role("link", name=re.compile("^Staging"))).to_have_attribute("aria-current", "page")
    expect(page.locator("[data-lead]")).to_have_text(LEAD)
    assert axe(page) == []

    # j to the first record, Enter opens it beside the list with its heading focused; nothing reloads.
    page.keyboard.press("j")
    first = focused_row(page)
    assert first
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(rf"/staging\?open={first}$"))
    expect(page.locator("#panel-title")).to_have_text("Supplier call")
    expect(page.locator("#panel-title")).to_be_focused()

    # Two utterances overlap: the second ticked and previewed, the third goes too.
    page.get_by_role("checkbox", name="Cut 00:12-00:31").click()
    page.get_by_role("button", name="Preview").click()
    expect(page.locator("[data-preview]")).to_contain_text("Removes 2 utterances: 00:12-00:31.")
    expect(page).to_have_url(re.compile(r"&u=2$"))
    expect(page.locator('[data-utterance="3"][data-going]')).to_have_count(1)
    assert axe(page) == []
    dark(page)
    assert axe(page) == []

    # A typed cut that doesn't read is refused beside the ticks, which stay.
    page.get_by_placeholder("12:30-14:05").fill("12:30")
    page.get_by_role("button", name="Work, with these cuts").click()
    expect(page.get_by_role("alert")).to_contain_text("cut 12:30: a cut is <start>-<end>")
    expect(page.get_by_role("checkbox", name="Cut 00:12-00:31")).to_be_checked()
    page.get_by_placeholder("12:30-14:05").fill("")

    # Work writes the cut through the gate: the panel closes, the notice says so, the row is ready.
    page.get_by_role("button", name="Work, with 1 cut").click()
    expect(page.get_by_role("status").filter(has_text="classified work")).to_be_visible()
    expect(page.get_by_role("complementary")).to_have_count(0)
    expect(page.locator(f'[data-row="{first}"] [data-state="ready"]')).to_be_visible()
    expect(page.locator(f'[data-row="{first}"]')).to_be_focused()

    # Esc closes an open record, the focus back on its row.
    page.locator('[data-row] a:has-text("The offer")').click()
    expect(page.locator("#panel-title")).to_have_text("The offer")
    page.keyboard.press("Escape")
    expect(page.get_by_role("complementary")).to_have_count(0)
    expect(page.locator('[data-row]:has-text("The offer")')).to_be_focused()
    assert [e for e in errors if "400" not in e] == []
    assert page.evaluate("window.__csp") == []


def test_private_asks_then_promote_all_empties_the_gate(browser: Browser, service: tuple[int, str]) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/staging")
    badge = page.get_by_role("navigation", name="Pages").get_by_role("link", name=re.compile("^Staging"))
    expect(badge).to_have_accessible_name(re.compile(r"^Staging ?, 3 records wait$"))
    page.locator('[data-row] a:has-text("The offer")').click()
    page.get_by_role("button", name="Private: drop it").click()
    expect(page.locator("[data-confirm]")).to_contain_text("Drop this mail for good?")
    expect(page).to_have_url(re.compile(r"&confirm=private$"))
    assert axe(page) == []
    page.get_by_role("button", name="Drop it as private").click()
    expect(page.get_by_role("status").filter(has_text="dropped as private")).to_be_visible()
    expect(page.locator("[data-row]")).to_have_count(2)
    expect(badge).to_have_accessible_name(re.compile(r"^Staging ?, 2 records wait$"))

    page.locator('[data-row] a:has-text("Supplier call")').click()
    page.get_by_role("button", name="Work, whole").click()
    expect(page.get_by_role("status").filter(has_text="classified work")).to_be_visible()
    page.get_by_role("button", name="Promote all").click()
    expect(page.get_by_role("status")).to_contain_text("Promoted: 2 records written to raw and committed.")
    expect(page.locator("[data-lead]")).to_have_text("Nothing waits at the gate")
    expect(badge).to_have_accessible_name("Staging")
    dark(page)
    assert axe(page) == []
    clean(page, errors)


def test_a_record_another_tab_classified_first_is_refused_as_it_stands(
    browser: Browser, service: tuple[int, str]
) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/staging")
    page.locator('[data-row] a:has-text("Supplier call")').click()
    expect(page.locator("[data-actions]")).to_be_visible()
    other = page.context.new_page()
    other.goto(page.url)
    other.get_by_role("checkbox", name="Cut 00:46-01:00").click()
    other.get_by_role("button", name="Work, with these cuts").click()
    expect(other.get_by_role("status").filter(has_text="classified work")).to_be_visible()
    other.close()
    page.get_by_role("button", name="Work, whole").click()
    expect(page.get_by_role("status").filter(has_text="This record changed since you opened it")).to_be_visible()
    # Read again: the other tab's cut, ticked.
    expect(page.get_by_role("checkbox", name="Cut 00:46-01:00")).to_be_checked()
    expect(page.locator("[data-reason]")).to_have_text(
        "classified work by you, with 1 cut: it goes at the next promotion"
    )
    assert axe(page) == []
    # The 409 is the point of this test; nothing else may fail.
    assert [e for e in errors if "409" not in e] == []
    assert page.evaluate("window.__csp") == []


def test_at_phone_width_a_record_takes_the_screen(browser: Browser, service: tuple[int, str]) -> None:
    port, token = service
    narrow: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    page, errors = opened(browser, port, token, "/staging", **narrow)
    page.locator('[data-row] a:has-text("Supplier call")').click()
    expect(page.get_by_role("complementary")).to_contain_text("Tick the utterances to cut")
    expect(page.get_by_role("region", name="Records at the gate")).to_be_hidden()
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    page.get_by_role("button", name="Back to the list").click()
    expect(page.get_by_role("region", name="Records at the gate")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/staging$"))
    clean(page, errors)


# -- 300 records (DR-0668, DR-0712) ---------------------------------------------------------------------------------

RECORDS = 300


@pytest.fixture(scope="module")
def perf_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """300 mails waiting at the gate, more than a backfill leaves for a morning: the page lists the first 50."""
    vault = empty_vault(tmp_path_factory.mktemp("staging-perf"), config())
    mails = [
        make_record(n, rid=f"calls:mail-{n:04d}", source="calls", kind="mail", title=f"Mail {n:04d}", text="Hello.")
        for n in range(RECORDS)
    ]
    Gate(vault).stage(mails)
    return vault.root


@pytest.fixture
def perf_service(perf_root: Path) -> Iterator[tuple[int, str]]:
    yield from live(perf_root)


# From the sidebar's click to the first row in the page, timed in the page itself.
OPEN_FROM_SIDEBAR = """async () => {
  const link = document.querySelector('nav[aria-label="Pages"] a[href="/staging"]');
  const started = performance.now();
  link.click();
  await new Promise((done) => {
    const look = () => (document.querySelector('[data-row]') ? done() : requestAnimationFrame(look));
    look();
  });
  return (performance.now() - started) / 1000;
}"""


# On 300 records: out of gate os's selection, while the browser jobs run it by file, as the others' (DR-0757).
@pytest.mark.nightly
@pytest.mark.perf
def test_the_list_opens_from_the_sidebar_within_its_budget(browser: Browser, perf_service: tuple[int, str]) -> None:
    """On 300 records, the list opened from the sidebar shows its rows with the keys live within a quarter second:
    once to warm the service and load the page's script, then the best of three, each from a page loaded afresh."""
    port, token = perf_service
    page, errors = opened(browser, port, token, "/design")
    base = page.url.split("/design")[0]
    took: list[float] = []
    for _ in range(4):
        page.goto(f"{base}/design")
        page.wait_for_selector('nav[aria-label="Pages"] a[href="/staging"]')
        page.wait_for_load_state("networkidle")
        took.append(float(page.evaluate(OPEN_FROM_SIDEBAR)))
        expect(page.locator("[data-row]")).to_have_count(50)
        page.keyboard.press("j")
        expect(page.locator("[data-row]:focus")).to_have_count(1)
    best = min(took[1:])
    runs = ", ".join(f"{t * 1000:.0f}" for t in took[1:])
    print(f"the Staging list from the sidebar on {RECORDS} records: {best * 1000:.0f} ms ({runs} ms)")
    if not traced():
        assert best < PAGE_SECONDS, f"the list took {best:.3f} s from the sidebar, over {PAGE_SECONDS} s"
    clean(page, errors)
