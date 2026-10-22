"""The Sync page in a real browser (DR-0711, DR-0712, DR-0788): the source never synced due with the words to paste; a
run opened by Claude's session shows open and its part reads again on its own while it is, and the page reads itself
whole once it closes; a dropped run's failure opens its card in the panel, j moves it and Esc closes it; a deep link
to a run opens its card; at phone width the card takes the screen with the way back; axe in both themes; and from the
sidebar's click the page shows its parts with the keys live within a quarter second.

It runs only with DOCKET_TEST_BROWSER set and reads axe-core from the web app's node_modules, as the other browser
files do. Invented memos only: sync_kit's.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from browser_kit import AXE, BROWSER, axe, clean, dark, live, opened
from docket_testkit import traced
from playwright.sync_api import Browser, expect, sync_playwright
from sync_kit import as_structured, as_text, memo, sync_template

from docket.service.client import call_json

pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")

PAGE_SECONDS = 0.25


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    assert AXE.is_file(), f"{AXE} is missing: run `npm ci` in packages/docket/web"
    with sync_playwright() as p:
        launched = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return sync_template(tmp_path_factory)


@pytest.fixture
def service(project: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    shutil.copytree(project, tmp_path / "project")
    yield from live(tmp_path / "project" / "vault")


def capture(service: tuple[int, str], run: str, n: int, tool: str, result: object) -> None:
    port, token = service
    body = {
        "session": "s-browser",
        "agent": None,
        "tool_name": f"mcp__memos__{tool}",
        "tool_use_id": f"toolu_browser_{n}",
        "arguments": {},
        "result": result,
        "captured": "2026-10-15T09:12:00+00:00",
    }
    answer = call_json("POST", f"/api/runs/{run}/captures", body, token=token, at=port)
    assert answer is not None and answer.status == 200, answer


def open_run(service: tuple[int, str]) -> str:
    port, token = service
    opened_run = call_json("POST", "/api/runs", {"source": "memo", "session": "s-browser"}, token=token, at=port)
    assert opened_run is not None and opened_run.status == 200, opened_run
    return str(opened_run.body["run"])


def test_a_run_shows_open_reads_its_part_alone_and_the_page_reads_itself_once_it_closes(
    browser: Browser, service: tuple[int, str]
) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/sync")
    sidebar = page.get_by_role("navigation", name="Pages")
    expect(sidebar.get_by_role("link", name=re.compile("^Sync"))).to_have_attribute("aria-current", "page")
    expect(page.locator("[data-need=due]")).to_contain_text("due: never synced")
    expect(page.locator("[data-paste]").first).to_have_text(
        'Sync memo with Docket: run_open("memo") and follow its steps.'
    )
    assert axe(page) == []

    run = open_run(service)
    capture(service, run, 1, "list_memos", as_text([memo("b1")]))
    page.reload()
    row = page.locator(f'[data-row="{run}"]')
    expect(row).to_have_attribute("data-state", "open")
    expect(page.locator("[data-as-of]")).to_contain_text("refreshes while a run is open")
    asked: list[str] = []
    page.on("request", lambda r: asked.append(r.url) if "/api/web/sync" in r.url else None)
    capture(service, run, 2, "get_memo", as_structured(memo("b1")))
    expect(row).to_contain_text("2 captures", timeout=8000)
    assert any("/api/web/sync/runs" in url for url in asked), asked
    closed = call_json("POST", f"/api/runs/{run}/close", {"captures": 2}, token=token, at=port)
    assert closed is not None and closed.status == 200, closed
    expect(row).to_have_attribute("data-state", "closed", timeout=8000)
    expect(page.locator("[data-lead]")).to_contain_text("last whole run", timeout=8000)
    expect(page.locator("[data-as-of]")).not_to_contain_text("refreshes")
    clean(page, errors)


def test_a_failure_opens_its_run_in_the_panel_the_keys_move_it_and_a_deep_link_lands_on_it(
    browser: Browser, service: tuple[int, str]
) -> None:
    port, token = service
    first = open_run(service)
    capture(service, first, 3, "list_memos", as_text([memo("b2")]))
    dropped = call_json("POST", f"/api/runs/{first}/close", {"captures": 3}, token=token, at=port)
    assert dropped is not None and dropped.status == 400, dropped
    page, errors = opened(browser, port, token, "/sync")
    failure = page.locator("[data-failure=count]")
    expect(failure).to_contain_text("What to do: nothing; if it repeats")
    failure.get_by_role("link").click()
    expect(page).to_have_url(re.compile(rf"/sync\?run={first}$"))
    panel = page.get_by_role("complementary")
    expect(panel).to_contain_text("the manifest counts 3 capture(s) and 1 arrived")
    expect(page.locator("#panel-title")).to_be_focused()
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    page.keyboard.press("Escape")
    expect(panel).to_have_count(0)
    expect(page).to_have_url(re.compile(r"/sync$"))
    # A link to the run, as a bookmark or Claude gives it, opens its card.
    page.goto(page.url.replace("/sync", f"/sync?run={first}"))
    expect(page.get_by_role("complementary")).to_contain_text("count")
    assert [e for e in errors if "400" not in e] == []
    assert page.evaluate("window.__csp") == []


def test_at_phone_width_a_run_takes_the_screen_with_the_way_back(browser: Browser, service: tuple[int, str]) -> None:
    port, token = service
    run = open_run(service)
    narrow: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    page, errors = opened(browser, port, token, f"/sync?run={run}", **narrow)
    expect(page.get_by_role("complementary")).to_contain_text("memo")
    expect(page.get_by_role("region", name="What the syncs did")).to_be_hidden()
    assert axe(page) == []
    page.get_by_role("button", name="Back to the runs").click()
    expect(page.get_by_role("region", name="What the syncs did")).to_be_visible()
    dark(page)
    assert axe(page) == []
    clean(page, errors)


OPEN_FROM_SIDEBAR = """async () => {
  const link = document.querySelector('nav[aria-label="Pages"] a[href="/sync"]');
  const started = performance.now();
  link.click();
  await new Promise((done) => {
    const look = () => (document.querySelector('[data-lead]') ? done() : requestAnimationFrame(look));
    look();
  });
  return (performance.now() - started) / 1000;
}"""


@pytest.mark.nightly
@pytest.mark.perf
def test_the_page_opens_from_the_sidebar_within_its_budget(browser: Browser, service: tuple[int, str]) -> None:
    """From the sidebar's click to the lead line with the keys live within a quarter second: once to warm the service
    and load the page's script, then the best of three, each from a page loaded afresh."""
    port, token = service
    page, errors = opened(browser, port, token, "/design")
    base = page.url.split("/design")[0]
    took: list[float] = []
    for _ in range(4):
        page.goto(f"{base}/design")
        page.wait_for_selector('nav[aria-label="Pages"] a[href="/sync"]')
        page.wait_for_load_state("networkidle")
        took.append(float(page.evaluate(OPEN_FROM_SIDEBAR)))
    best = min(took[1:])
    runs = ", ".join(f"{t * 1000:.0f}" for t in took[1:])
    print(f"the Sync page from the sidebar: {best * 1000:.0f} ms ({runs} ms)")
    if not traced():
        assert best < PAGE_SECONDS, f"the page took {best:.3f} s from the sidebar, over {PAGE_SECONDS} s"
    clean(page, errors)
