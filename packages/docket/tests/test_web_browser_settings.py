"""The Settings page in a real browser (DR-0601, DR-0789, DR-0790): no never-store value until asked for; a value added
through its question, the address bar and the history never holding it; a refusal on the add's field, focused and
keeping what was typed; a source made private through its question; a rule turned on; another tab's change first,
so this page's is refused and the page reads itself again; an edit by hand not in force said at the top; Windows'
paths wrapping at phone width; axe in both themes; and from the sidebar's click the page shows its parts within a
quarter second.

It runs only with DOCKET_TEST_BROWSER set and reads axe-core from the web app's node_modules, as the other browser
files do. Invented values only: settings_kit's.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from browser_kit import AXE, BROWSER, axe, clean, dark, live, opened
from docket_testkit import traced
from playwright.sync_api import Browser, expect, sync_playwright
from settings_kit import settings_template

from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.web import settings as settings_routes
from docket.web.settings_view import Facts

pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")

PAGE_SECONDS = 0.25
NARROW: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    assert AXE.is_file(), f"{AXE} is missing: run `npm ci` in packages/docket/web"
    with sync_playwright() as p:
        launched = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return settings_template(tmp_path_factory)


@pytest.fixture
def vault_root(project: Path, tmp_path: Path) -> Path:
    shutil.copytree(project, tmp_path / "project")
    return tmp_path / "project" / "vault"


@pytest.fixture
def service(vault_root: Path) -> Iterator[tuple[int, str]]:
    yield from live(vault_root)


def config(vault_root: Path) -> VaultConfig:
    return VaultConfig.load(vault_root / CONFIG_FILE)


def test_a_value_is_added_through_its_question_and_never_reaches_the_address_bar(
    browser: Browser, service: tuple[int, str], vault_root: Path
) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/settings")
    sidebar = page.get_by_role("navigation", name="Pages")
    expect(sidebar.get_by_role("link", name=re.compile("^Settings"))).to_have_attribute("aria-current", "page")
    expect(page.locator("[data-lead]")).to_contain_text("4 sources, 4 on; 4 never-store values")
    expect(page.locator("main")).not_to_contain_text("Dora #2")
    assert axe(page) == []

    # Nothing typed: the service's refusal lands on the field, which takes the focus.
    page.locator("#list-people summary").click()
    page.locator("#list-people form button").click()
    field = page.locator("#add-people")
    expect(field).to_have_attribute("aria-invalid", "true")
    expect(field).to_be_focused()
    expect(page.locator("#add-people-error")).to_contain_text("never empty")

    field.fill("Ada Sample")
    page.locator("#list-people form button").click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_contain_text("Add to people?")
    expect(dialog.locator("[data-value]")).to_have_text("Ada Sample")
    expect(dialog.get_by_role("button", name="Back, adding nothing")).to_be_focused()
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    dialog.get_by_role("button", name="Add it to people").click()
    expect(page.get_by_role("status").filter(has_text="Added one value to people")).to_be_visible()
    expect(dialog).to_have_count(0)
    expect(field).to_have_value("")
    assert config(vault_root).never_store.people[-1] == "Ada Sample"
    history = page.context.new_cdp_session(page).send("Page.getNavigationHistory")
    assert not [e["url"] for e in history["entries"] if "Ada" in e["url"] or "Sample" in e["url"]]
    assert [e for e in errors if "400" not in e] == []
    assert page.evaluate("window.__csp") == []

    page.get_by_role("link", name="Show the values").click()
    expect(page).to_have_url(re.compile(r"/settings\?show=lists$"))
    expect(page.locator("#list-people [data-value]")).to_have_text(["Cleo Test", "Dora #2", "Ada Sample"])


def test_a_source_is_made_private_through_its_question_and_a_rule_turned_on(
    browser: Browser, service: tuple[int, str], vault_root: Path
) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/settings")
    page.locator("#source-memo summary").click()
    page.get_by_role("button", name="Make it private…: memo").click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_contain_text("Make memo private?")
    dialog.get_by_role("button", name="Make memo private").click()
    expect(page.get_by_role("status").filter(has_text="memo is private")).to_be_visible()
    page.get_by_role("button", name="Turn it on: phone").click()
    expect(page.get_by_role("status").filter(has_text="Phone numbers are redacted")).to_be_visible()
    expect(page.get_by_role("button", name="Turn it off: phone")).to_be_visible()
    found = config(vault_root)
    assert (found.sources["memo"].sensitivity, found.redaction.phone) == ("private", True)
    # Credentials off is a command for the owner's console, never a button.
    expect(page.get_by_role("button", name=re.compile("^Turn it (on|off): credentials"))).to_have_count(0)
    clean(page, errors)


def test_another_tab_s_change_first_refuses_this_page_s_and_it_reads_itself_again(
    browser: Browser, service: tuple[int, str], vault_root: Path
) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/settings")
    other = page.context.new_page()
    other.goto(page.url)
    other.get_by_role("button", name="Turn it on: phone").click()
    expect(other.get_by_role("status").filter(has_text="Phone numbers")).to_be_visible()
    other.close()
    page.locator("#source-calls summary").click()
    page.get_by_role("button", name="Make it work: calls").click()
    expect(page.get_by_role("alert")).to_contain_text("vault.yaml changed since the page showed it")
    expect(page.get_by_role("button", name="Turn it off: phone")).to_be_visible()
    assert config(vault_root).sources["calls"].sensitivity == "mixed"
    assert axe(page) == []
    assert [e for e in errors if "409" not in e] == []


def test_an_edit_not_in_force_is_said_at_the_top_at_both_widths(
    browser: Browser, service: tuple[int, str], vault_root: Path
) -> None:
    port, token = service
    path = vault_root / CONFIG_FILE
    edited = VaultConfig.load(path)
    edited.redaction.credentials = False
    path.write_text(edited.dump(), encoding="utf-8", newline="\n")
    page, errors = opened(browser, port, token, "/settings", **NARROW)
    expect(page.get_by_role("status").filter(has_text="vault.yaml has an edit that isn't in force")).to_be_visible()
    expect(page.locator("main")).to_contain_text("Credentials are not redacted")
    expect(page.locator("main")).not_to_contain_text("Dora #2")
    dark(page)
    assert axe(page) == []
    clean(page, errors)


WINDOWS = r"C:\Users\AdaExampleSampleName"
CONFIG = WINDOWS + r"\AppData\Local\docket\config.yaml"
HERE = WINDOWS + r"\Documents\SharedProjectDocuments"


def test_windows_paths_wrap_at_phone_width(
    browser: Browser, service: tuple[int, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each part that shows a path wraps it anywhere, so the page never scrolls sideways at 390 px (#193). CI runs on
    Linux, whose paths have slashes to break at, so the paths are made Windows-shaped here."""
    port, token = service
    real = settings_routes.gather

    def windows(service: Any) -> Facts:
        facts = real(service)
        sources = [replace(s, here=HERE) if s.entry.folder else s for s in facts.sources]
        root = Path(WINDOWS + r"\Projects\AdaExampleNotes\vault")
        return replace(facts, root=root, config_path=Path(CONFIG), sources=sources)

    monkeypatch.setattr(settings_routes, "gather", windows)
    page, errors = opened(browser, port, token, "/settings", **NARROW)
    page.evaluate("document.querySelectorAll('details').forEach(d => { d.open = true; })")
    expect(page.locator("main")).to_contain_text(CONFIG)
    expect(page.locator("#source-documents")).to_contain_text(HERE)
    clean(page, errors)


OPEN_FROM_SIDEBAR = """async () => {
  const link = document.querySelector('nav[aria-label="Pages"] a[href="/settings"]');
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
    """From the sidebar's click to the lead line within a quarter second: once to warm the service and load the
    page's script, then the best of three, each from a page loaded afresh."""
    port, token = service
    page, errors = opened(browser, port, token, "/design")
    base = page.url.split("/design")[0]
    took: list[float] = []
    for _ in range(4):
        page.goto(f"{base}/design")
        page.wait_for_selector('nav[aria-label="Pages"] a[href="/settings"]')
        page.wait_for_load_state("networkidle")
        took.append(float(page.evaluate(OPEN_FROM_SIDEBAR)))
    best = min(took[1:])
    runs = ", ".join(f"{t * 1000:.0f}" for t in took[1:])
    print(f"the Settings page from the sidebar: {best * 1000:.0f} ms ({runs} ms)")
    if not traced():
        assert best < PAGE_SECONDS, f"the page took {best:.3f} s from the sidebar, over {PAGE_SECONDS} s"
    clean(page, errors)
