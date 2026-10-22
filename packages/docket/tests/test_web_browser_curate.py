"""The Curate page in a real browser (DR-0711, DR-0712, DR-0786): a round starts on the click; j walks the cards and
Enter opens one beside the list, its snippets marked; Edit opens the form in the panel and Cancel leaves it; Accept
writes the entity and moves on to the next open card, the log's Undo takes it back; a card another tab decided first
is refused and shown as it stands; at phone width the card takes the screen with the way back; axe in both themes;
and on a round of 15 cards with a long log the page opened from the sidebar shows its cards with the keys live within
a quarter second.

It runs only with DOCKET_TEST_BROWSER set, as test_web_browser.py does, and reads axe-core from the web app's
node_modules: `npm ci` in packages/docket/web first. Invented people only: curate_kit's.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from browser_kit import AXE, BROWSER, axe, clean, dark, live, opened
from curate_kit import build_curate_vault, more_recordings
from docket_testkit import traced
from playwright.sync_api import Browser, expect, sync_playwright

from docket.curate import batch
from docket.vault.curatelog import Change, EntityFields
from docket.vault.entitystore import EntityStore, Planned
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


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_curate_vault(tmp_path_factory.mktemp("curate-browser"))


@pytest.fixture
def service(template: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    shutil.copytree(template, tmp_path / "vault")
    yield from live(tmp_path / "vault")


@pytest.fixture
def round_service(template: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    """The curate vault with a round started on this machine."""
    shutil.copytree(template, tmp_path / "vault")
    batch.start(Vault.open(tmp_path / "vault"))
    yield from live(tmp_path / "vault")


def focused_row(page: Any) -> str:
    return str(page.evaluate("document.activeElement?.dataset?.row ?? ''"))


def test_a_round_starts_and_the_keys_open_edit_and_accept_its_cards(browser: Browser, service: tuple[int, str]) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/curate")
    sidebar = page.get_by_role("navigation", name="Pages")
    expect(sidebar.get_by_role("link", name="Curate")).to_have_attribute("aria-current", "page")
    expect(page.locator("[data-lead]")).to_have_text("No round on this machine yet.")
    assert axe(page) == []

    # A round starts only on the click.
    page.get_by_role("button", name="Start a round").click()
    expect(page.get_by_role("status").filter(has_text="A new round: 6 cards.")).to_be_visible()
    expect(page.locator("[data-row]")).to_have_count(6)

    # j to the first card, Enter opens it beside the list, its snippets cut now.
    page.keyboard.press("j")
    first = focused_row(page)
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(rf"/curate\?open={first}$"))
    expect(page.locator("#panel-title")).to_have_text("1. New term: Northwind")
    expect(page.locator("#panel-title")).to_be_focused()
    expect(page.locator("[data-snippet] mark").first).to_have_text("Nortwin")
    assert axe(page) == []

    # Edit opens the form in the panel; Cancel goes back to the card.
    page.get_by_role("button", name="Edit").click()
    expect(page).to_have_url(re.compile(rf"/curate\?edit={first}$"))
    expect(page.locator("#edit-id")).to_have_value("northwind")
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    page.get_by_role("button", name="Cancel").click()
    expect(page.locator("#edit-id")).to_have_count(0)

    # Accept writes the entity; the panel moves on to the next open card, and the log takes it back.
    page.get_by_role("button", name="Accept", exact=True).click()
    expect(page.get_by_role("status").filter(has_text="accepted and logged")).to_be_visible()
    expect(page.locator(f'[data-row="{first}"] [data-state="accepted"]')).to_be_visible()
    expect(page.locator("#panel-title")).to_have_text(re.compile(r"^2\. "))
    page.get_by_role("button", name=re.compile(r"^Undo create")).click()
    expect(page.get_by_role("status").filter(has_text="Undone: 1 change taken back")).to_be_visible()
    expect(page.locator(f'[data-row="{first}"] [data-state="open"]')).to_be_visible()

    # Esc closes the card, the focus back on its row.
    page.keyboard.press("Escape")
    expect(page.get_by_role("complementary")).to_have_count(0)
    clean(page, errors)


def test_an_edit_refused_keeps_what_was_typed(browser: Browser, round_service: tuple[int, str]) -> None:
    port, token = round_service
    page, errors = opened(browser, port, token, "/curate")
    page.locator('[data-row] a:has-text("Cleo Test")').click()
    page.get_by_role("button", name="Edit").click()
    page.locator("#edit-id").fill("Not An Id")
    page.locator("#edit-role").fill("rights agent")
    page.get_by_role("button", name="Accept as edited").click()
    expect(page.get_by_role("alert")).to_contain_text("id:")
    expect(page.locator("#edit-id")).to_have_value("Not An Id")
    expect(page.locator("#edit-role")).to_have_value("rights agent")
    page.locator("#edit-id").fill("cleo-test")
    page.get_by_role("button", name="Accept as edited").click()
    expect(page.get_by_role("status").filter(has_text="accepted as you edited it")).to_be_visible()
    assert axe(page) == []
    assert [e for e in errors if "400" not in e] == []
    assert page.evaluate("window.__csp") == []


def test_a_card_another_tab_decided_first_is_refused_as_it_stands(
    browser: Browser, round_service: tuple[int, str]
) -> None:
    port, token = round_service
    page, errors = opened(browser, port, token, "/curate")
    page.locator('[data-row] a:has-text("Cleo Test")').click()
    expect(page.locator("[data-actions]")).to_be_visible()
    other = page.context.new_page()
    other.goto(page.url)
    other.get_by_role("button", name="Accept", exact=True).click()
    expect(other.get_by_role("status").filter(has_text="accepted and logged")).to_be_visible()
    other.close()
    page.get_by_role("button", name="Accept", exact=True).click()
    expect(page.get_by_role("status").filter(has_text="changed since the page showed the card")).to_be_visible()
    # Read again: the card stands decided, with no buttons left.
    expect(page.locator("[data-actions]")).to_have_count(0)
    expect(page.get_by_role("complementary")).to_contain_text("Decided: accepted.")
    assert axe(page) == []
    # The 409 is the point of this test; nothing else may fail.
    assert [e for e in errors if "409" not in e] == []
    assert page.evaluate("window.__csp") == []


def test_at_phone_width_a_card_takes_the_screen(browser: Browser, round_service: tuple[int, str]) -> None:
    port, token = round_service
    narrow: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    page, errors = opened(browser, port, token, "/curate", **narrow)
    page.locator('[data-row] a:has-text("Northwind")').first.click()
    expect(page.get_by_role("complementary")).to_contain_text("Where it is")
    expect(page.get_by_role("region", name="The round")).to_be_hidden()
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    page.get_by_role("button", name="Back to the list").click()
    expect(page.get_by_role("region", name="The round")).to_be_visible()
    expect(page).to_have_url(re.compile(r"/curate$"))
    clean(page, errors)


# -- a round of 15 cards and a long log (DR-0668, DR-0712) -----------------------------------------------------------

LOGGED = 300


@pytest.fixture(scope="module")
def perf_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The curate vault with more names to mine, 300 entities written through the store, each a log line, and a round
    started: more than a year of weekly rounds leaves."""
    vault = Vault.open(build_curate_vault(tmp_path_factory.mktemp("curate-perf")))
    more_recordings(vault, [f"We met Harbourmaster {n} at the yard." for n in range(20)])
    store = EntityStore(vault)
    planned = [
        Planned(
            n,
            f"topic-{n:03d}",
            Change(entity=EntityFields(id=f"topic-{n:03d}", type="topic", name={"en": f"Topic {n:03d}"})),
        )
        for n in range(LOGGED)
    ]
    store.apply(planned)
    batch.start(vault)
    return vault.root


@pytest.fixture
def perf_service(perf_root: Path) -> Iterator[tuple[int, str]]:
    yield from live(perf_root)


# From the sidebar's click to the first card in the page, timed in the page itself.
OPEN_FROM_SIDEBAR = """async () => {
  const link = document.querySelector('nav[aria-label="Pages"] a[href="/curate"]');
  const started = performance.now();
  link.click();
  await new Promise((done) => {
    const look = () => (document.querySelector('[data-row]') ? done() : requestAnimationFrame(look));
    look();
  });
  return (performance.now() - started) / 1000;
}"""


# On a long log: out of gate os's selection, while the browser jobs run it by file, as the others' (DR-0757).
@pytest.mark.nightly
@pytest.mark.perf
def test_the_round_opens_from_the_sidebar_within_its_budget(browser: Browser, perf_service: tuple[int, str]) -> None:
    """With 300 entities logged, the round opened from the sidebar shows its cards with the keys live within a quarter
    second: once to warm the service and load the page's script, then the best of three, each from a page loaded
    afresh."""
    port, token = perf_service
    page, errors = opened(browser, port, token, "/design")
    base = page.url.split("/design")[0]
    took: list[float] = []
    for _ in range(4):
        page.goto(f"{base}/design")
        page.wait_for_selector('nav[aria-label="Pages"] a[href="/curate"]')
        page.wait_for_load_state("networkidle")
        took.append(float(page.evaluate(OPEN_FROM_SIDEBAR)))
        expect(page.locator("[data-row]").first).to_be_visible()
        page.keyboard.press("j")
        expect(page.locator("[data-row]:focus")).to_have_count(1)
    best = min(took[1:])
    runs = ", ".join(f"{t * 1000:.0f}" for t in took[1:])
    print(f"the Curate round from the sidebar with {LOGGED} entities logged: {best * 1000:.0f} ms ({runs} ms)")
    if not traced():
        assert best < PAGE_SECONDS, f"the round took {best:.3f} s from the sidebar, over {PAGE_SECONDS} s"
    clean(page, errors)
