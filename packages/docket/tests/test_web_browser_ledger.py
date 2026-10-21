"""The Ledger page of the React app in a real browser (DR-0710 to DR-0712): the keys and the one selection the panel
follows, the focus kept with the selection, the calls and their notice with Undo, the bulk rule over ticked rows, the
saves on change and the leave guard, the poll that holds and then refetches in place, the drop's why and the snooze's
day, the materials and the viewer, the new item and its look-alike, the phone width, axe in both themes; and on the
perf ledger, the page opened from the sidebar shows its rows with its keys live within a quarter second.

It runs only with DOCKET_TEST_BROWSER set, as test_web_browser.py does, and reads axe-core from the web app's
node_modules: `npm ci` in packages/docket/web first. Invented people only.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from browser_kit import AXE, BROWSER, axe, clean, dark, live, opened
from day_kit import day_vault, today
from docket_testkit import empty_vault, make_config, traced
from ledger_kit import ITEMS, build
from playwright.sync_api import Browser, Page, expect, sync_playwright

from docket.index import build as index_build
from docket.ledger import ops, store
from docket.vault.vault import Vault
from docket.web import day as day_routes
from docket.web import day_view

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
    """The invented day vault: seven items, a meeting with its points, two streams, the files its materials name."""
    return day_vault(tmp_path).root


@pytest.fixture
def day_service(day_root: Path) -> Iterator[tuple[int, str]]:
    yield from live(day_root)


def row(page: Page, ref: str) -> Any:
    return page.locator(f'[data-row="{ref}"]')


def selected(page: Page) -> Any:
    return page.locator('li[aria-current="true"]')


def notice(page: Page) -> Any:
    return page.get_by_role("status")


def panel(page: Page) -> Any:
    return page.get_by_role("complementary")


def shots() -> Path:
    found = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or Path.cwd() / ".local" / "screenshots")
    found.mkdir(parents=True, exist_ok=True)
    return found


def test_the_keys_move_the_selection_open_it_and_make_the_calls(browser: Browser, day_service: tuple[int, str]) -> None:
    """j selects and Enter opens beside the list; j carries the panel; a verdict's key writes and moves on, the focus
    with the selection; u undoes; a click on the panel's verdict leaves the focus on the selected row, so Enter never
    presses it again; a verdict over ticked rows sets it on all, then clears it once all hold it (DR-0710, DR-0711)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/ledger")
    expect(page.get_by_role("heading", level=1, name="Ledger")).to_be_visible()
    expect(page.get_by_role("heading", level=2, name=re.compile("^Overdue"))).to_be_visible()
    assert axe(page) == []
    page.keyboard.press("j")
    expect(selected(page)).to_have_attribute("data-row", "A-0001")
    expect(row(page, "A-0001")).to_be_focused()
    page.keyboard.press("Enter")
    page.wait_for_url("**/ledger?open=A-0001")
    expect(panel(page)).to_have_accessible_name("A-0001, Send Ada the dates")
    page.keyboard.press("j")
    page.wait_for_url("**/ledger?open=A-0002")
    expect(row(page, "A-0002")).to_be_focused()
    page.screenshot(path=str(shots() / "ledger.png"))

    page.keyboard.press("2")
    expect(notice(page)).to_contain_text("A-0002: triage: claude")
    page.wait_for_url("**/ledger?open=A-0003")
    expect(row(page, "A-0002")).to_have_attribute("data-triage", "claude")
    expect(row(page, "A-0003")).to_be_focused()
    page.keyboard.press("u")
    expect(notice(page)).to_contain_text("Undone.")
    expect(notice(page).get_by_role("button", name=re.compile("^Redo"))).to_be_visible()
    expect(row(page, "A-0002")).to_have_attribute("data-triage", "")

    # A click on the panel's call: the focus goes with the selection to the next row, so Enter opens that row.
    panel(page).get_by_role("group", name="Call").get_by_role("button", name=re.compile("^Me")).click()
    expect(notice(page)).to_contain_text("A-0003: triage: me")
    page.wait_for_url("**/ledger?open=A-0004")
    expect(row(page, "A-0004")).to_be_focused()
    page.keyboard.press("Enter")
    expect(notice(page)).to_contain_text("A-0003: triage: me")
    assert page.url.endswith("/ledger?open=A-0004")
    page.keyboard.press("Escape")
    page.wait_for_url("**/ledger?at=A-0004")

    # Two rows ticked, one already chase: 4 sets chase on both; ticked again, 4 clears it on both.
    page.get_by_role("checkbox", name="Tick A-0001").click()
    page.get_by_role("checkbox", name="Tick A-0003").click()
    bar = page.get_by_role("region", name="The ticked rows")
    expect(bar).to_contain_text("2 ticked")
    page.screenshot(path=str(shots() / "ledger-ticked.png"))
    assert axe(page) == []
    page.keyboard.press("4")
    expect(notice(page)).to_contain_text("A-0003: triage: chase")
    expect(bar).to_have_count(0)
    expect(row(page, "A-0003")).to_have_attribute("data-triage", "chase")
    page.get_by_role("checkbox", name="Tick A-0001").click()
    page.get_by_role("checkbox", name="Tick A-0003").click()
    expect(bar.get_by_role("button", name=re.compile("^Chase"))).to_have_attribute("aria-pressed", "true")
    page.keyboard.press("4")
    expect(notice(page)).to_contain_text("A-0001: triage cleared")
    expect(row(page, "A-0001")).to_have_attribute("data-triage", "")
    dark(page)
    assert axe(page) == []
    clean(page, errors)


def test_at_phone_width_an_open_item_takes_the_screen(browser: Browser, day_service: tuple[int, str]) -> None:
    port, token = day_service
    narrow: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    page, errors = opened(browser, port, token, "/ledger?open=A-0003", **narrow)
    expect(page.get_by_role("region", name="Items")).to_be_hidden()
    expect(page.get_by_role("button", name="Back to the list")).to_be_visible()
    expect(page.get_by_label("Title")).to_have_value("Pick the venue")
    assert axe(page) == []
    page.screenshot(path=str(shots() / "ledger-390.png"))
    page.get_by_role("button", name="Back to the list").click()
    page.wait_for_url("**/ledger?at=A-0003")
    expect(page.get_by_role("region", name="Items")).to_be_visible()
    # The filters fold behind a button, and the selected row shows its calls on a line of their own.
    expect(page.get_by_role("button", name="Filters")).to_have_attribute("aria-expanded", "false")
    expect(page.get_by_role("group", name="Calls on A-0003")).to_be_visible()
    dark(page)
    assert axe(page) == []
    clean(page, errors)


def test_a_field_saves_on_change_and_a_refused_one_keeps_its_text(
    browser: Browser, day_service: tuple[int, str]
) -> None:
    """Notes typed and left by a click on another row are saved first, the panel waiting for the save; a select saves
    as it changes; a save the writer refuses keeps its text, marked unsaved, with the writer's words (DR-0710)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/ledger?open=A-0003")
    page.get_by_label("Working notes").fill("Call the venue on Monday")
    row(page, "A-0001").get_by_role("link").click()
    page.wait_for_url("**/ledger?open=A-0001")
    row(page, "A-0003").get_by_role("link").click()
    page.wait_for_url("**/ledger?open=A-0003")
    expect(page.get_by_label("Working notes")).to_have_value("Call the venue on Monday")
    history = page.locator('[data-history="A-0003"]')
    expect(history).to_contain_text("notes edited")

    panel(page).get_by_label("Kind").click()
    page.get_by_role("option", name="task").click()
    expect(history).to_contain_text("kind: task")
    expect(row(page, "A-0003")).to_contain_text("task")

    title = page.get_by_label("Title")
    title.fill("")
    title.press("Enter")
    expect(panel(page).get_by_role("alert")).to_have_text("title can't be cleared")
    expect(page.locator('[data-field="title"]')).to_have_attribute("data-unsaved", "")
    expect(title).to_have_value("")
    expect(row(page, "A-0003")).to_contain_text("Pick the venue")
    # The refused save's 400, as the console logs it, is the one failure the page expects.
    assert [e for e in errors if "400" not in e] == []
    assert page.evaluate("window.__csp") == []


def test_ticked_rows_hold_the_poll_and_a_write_from_the_cli_shows_in_place_once_cleared(
    browser: Browser, day_service: tuple[int, str], day_root: Path
) -> None:
    """While a row is ticked the poll holds, so a write from the CLI changes nothing past the 5 seconds; once the tick
    is cleared, the next poll refetches the page in place, with no reload (DR-0711)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/ledger")
    page.get_by_role("checkbox", name="Tick A-0001").click()
    expect(page.get_by_role("region", name="The ticked rows")).to_be_visible()
    page.evaluate("window.notReloaded = true")
    change = {"op": "set", "item": "A-0005", "fields": {"title": "Quote from the new caterer"}}
    store.write(Vault.open(day_root), [change], by="user", via="cli")
    page.wait_for_timeout(6_500)
    expect(row(page, "A-0005")).to_contain_text("Quote from the caterer")
    expect(page.get_by_role("checkbox", name="Tick A-0001")).to_have_attribute("aria-checked", "true")

    page.keyboard.press("Escape")
    expect(page.get_by_role("checkbox", name="Tick A-0001")).to_have_attribute("aria-checked", "false")
    expect(row(page, "A-0005")).to_contain_text("Quote from the new caterer", timeout=12_000)
    assert page.evaluate("window.notReloaded === true"), "refetched in place, never reloaded"
    clean(page, errors)


def test_a_write_from_the_cli_while_a_field_is_typed_in_shows_once_it_is_saved(
    browser: Browser, day_service: tuple[int, str], day_root: Path
) -> None:
    """Typing in a field holds the poll; a write from the CLI to another row lands meanwhile; once the field is left,
    its save tells the page its read missed a write, and the list shows it within one poll, in place (DR-0710,
    DR-0711)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/ledger?open=A-0003")
    page.evaluate("window.notReloaded = true")
    notes = page.get_by_label("Working notes")
    notes.click()
    notes.press_sequentially("typing while the CLI writes")
    change = {"op": "set", "item": "A-0004", "fields": {"title": "Plan the spring visit, from the CLI"}}
    store.write(Vault.open(day_root), [change], by="user", via="cli")
    page.wait_for_timeout(6_500)
    expect(row(page, "A-0004")).not_to_contain_text("from the CLI")
    page.locator("#panel-title").click()
    expect(page.locator('[data-history="A-0003"]')).to_contain_text("notes edited")
    expect(row(page, "A-0004")).to_contain_text("from the CLI", timeout=5_000)
    expect(page.get_by_label("Working notes")).to_have_value("typing while the CLI writes")
    assert page.evaluate("window.notReloaded === true"), "refetched in place, never reloaded"
    clean(page, errors)


def test_a_drop_s_why_and_a_snooze_s_day_take_the_focus(
    browser: Browser, day_service: tuple[int, str], day_root: Path
) -> None:
    """Delete or Backspace on a row, and a click on its drop, open the drop form with its why focused, so a why typed
    there never presses a key; s focuses the next working day, and Enter snoozes to it (DR-0711)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/ledger")
    page.keyboard.press("j")
    page.keyboard.press("Backspace")
    page.wait_for_url("**/ledger?open=A-0001&drop=1")
    expect(page.locator("#why-A-0001")).to_be_focused()

    # The page loaded afresh, so its selection starts again at the first row.
    base = page.url.split("/ledger")[0]
    page.goto(f"{base}/ledger")
    page.wait_for_load_state("networkidle")
    page.keyboard.press("j")
    page.keyboard.press("s")
    page.wait_for_url("**/ledger?open=A-0001")
    working = day_view.next_working_day(today())
    expect(page.locator("#snooze-A-0001")).to_be_focused()
    expect(page.locator("#snooze-A-0001")).to_have_attribute("title", re.compile(working.strftime("%a")))
    page.keyboard.press("Enter")
    expect(notice(page)).to_contain_text("A-0001: snooze")
    page.wait_for_url("**/ledger?open=A-0002")

    page.keyboard.press("Escape")
    before = day_routes.version(day_root)
    page.keyboard.press("j")
    page.keyboard.press("Delete")
    why = page.locator("#why-A-0003")
    expect(why).to_be_focused()
    page.keyboard.type("replied, see the thread")
    expect(why).to_have_value("replied, see the thread")
    assert day_routes.version(day_root) == before
    page.keyboard.press("Enter")
    expect(notice(page)).to_contain_text("A-0003: dropped")

    # A click on a row's drop, which shows on its hover, focuses its why too.
    before = day_routes.version(day_root)
    row(page, "A-0004").hover()
    row(page, "A-0004").get_by_role("button", name="Drop A-0004").click()
    expect(page.locator("#why-A-0004")).to_be_focused()
    page.keyboard.type("we met")
    expect(page.locator("#why-A-0004")).to_have_value("we met")
    assert day_routes.version(day_root) == before
    clean(page, errors)


def focused_day(page: Page) -> str:
    return str(page.evaluate("document.activeElement.closest('[data-day]')?.dataset.day ?? ''"))


def test_a_day_is_picked_from_a_month_or_typed_and_the_keys_in_it_stay_its_own(
    browser: Browser, day_service: tuple[int, str], day_root: Path
) -> None:
    """A date field shows its day as the app writes it and opens a month whose weeks start on Monday, the day
    focused; the page's keys do nothing in it and Esc closes it alone; a day picked saves at once, as does one typed
    as ISO writes it; the snooze's own day snoozes to the day typed; the new item's due takes one too (DR-0710,
    DR-0711)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/ledger?open=A-0003")
    due = page.locator("#f-A-0003-due")
    history = page.locator('[data-history="A-0003"]')
    due.click()
    month = page.get_by_role("dialog", name="Due")
    expect(month.locator("thead th").first).to_have_attribute("aria-label", "Monday")
    expect(month.locator("thead th").last).to_have_attribute("aria-label", "Sunday")
    assert focused_day(page) != ""
    assert axe(page) == []
    before = day_routes.version(day_root)
    for key in ("e", "j", "1", "x", "s", "Delete", "u", "n"):
        page.keyboard.press(key)
    expect(month).to_be_visible()
    expect(page.get_by_role("dialog", name="New item")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(month).to_have_count(0)
    expect(due).to_be_focused()
    assert page.url.endswith("/ledger?open=A-0003")
    assert day_routes.version(day_root) == before

    due.click()
    page.keyboard.press("ArrowRight")
    picked = focused_day(page)
    page.keyboard.press("Enter")
    expect(month).to_have_count(0)
    expect(history).to_contain_text(f"due: {picked}")
    shown = day_view.day_text(today().fromisoformat(picked), today())
    expect(due).to_have_text(shown)

    typed = (today() + timedelta(days=30)).isoformat()
    due.click()
    month.get_by_label("Or type the day").fill(typed)
    month.get_by_label("Or type the day").press("Enter")
    expect(history).to_contain_text(f"due: {typed}")
    expect(due).to_have_accessible_name(f"Due, {day_view.day_text(today().fromisoformat(typed), today())}")

    # The snooze's own day: a day typed there snoozes to it.
    until = (today() + timedelta(days=10)).isoformat()
    page.get_by_role("button", name="Snooze to a day, Another day").click()
    snooze = page.get_by_role("dialog", name="Snooze to a day")
    snooze.get_by_label("Or type the day").fill(until)
    snooze.get_by_label("Or type the day").press("Enter")
    expect(notice(page)).to_contain_text("A-0003: snooze")
    expect(notice(page)).to_contain_text(until)

    # The new item's due: Esc in its month closes the month, never the form.
    page.keyboard.press("Escape")
    page.keyboard.press("n")
    form = page.get_by_role("dialog", name="New item")
    form.get_by_role("button", name=re.compile("^Due")).click()
    page.get_by_role("dialog", name="Due").get_by_label("Or type the day").fill(typed)
    page.keyboard.press("Enter")
    expect(form.get_by_role("button", name=re.compile("^Due"))).to_have_text(re.compile(r"\d+ \w{3}"))
    form.get_by_role("button", name=re.compile("^Due")).click()
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog", name="Due")).to_have_count(0)
    expect(form).to_be_visible()
    assert axe(page) == []
    clean(page, errors)


def inside(box: Any, height: int) -> bool:
    return box is not None and box["y"] >= 0 and box["y"] + box["height"] <= height + 0.5


def test_the_date_picker_stays_on_screen_a_sheet_on_a_phone_and_a_capped_popover_above(
    browser: Browser, day_service: tuple[int, str], day_root: Path
) -> None:
    """Below 768 px the month opens as a sheet from the screen's foot, its caption and arrows in sight; the page's
    keys stay out of it and Esc closes it alone, the item still open; a day picked there saves. Above, on a short
    window, the popover is no taller than the room it has, and scrolls (DR-0710, DR-0711)."""
    port, token = day_service
    narrow: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    page, errors = opened(browser, port, token, "/ledger?open=A-0003", **narrow)
    due = page.locator("#f-A-0003-due")
    due.click()
    sheet = page.get_by_role("dialog", name="Due")
    expect(sheet).to_have_attribute("data-side", "bottom")
    expect(sheet.get_by_role("button", name=re.compile("previous month", re.IGNORECASE))).to_be_in_viewport()
    expect(sheet.get_by_role("grid")).to_be_in_viewport()
    assert inside(sheet.bounding_box(), 844)
    assert focused_day(page) != ""
    assert axe(page) == []
    before = day_routes.version(day_root)
    for key in ("e", "j", "1", "x", "s", "Delete", "n"):
        page.keyboard.press(key)
    expect(sheet).to_be_visible()
    page.keyboard.press("Escape")
    expect(sheet).to_have_count(0)
    expect(page.get_by_label("Title")).to_have_value("Pick the venue")
    assert page.url.endswith("/ledger?open=A-0003")
    assert day_routes.version(day_root) == before
    due.click()
    page.keyboard.press("ArrowRight")
    picked = focused_day(page)
    page.keyboard.press("Enter")
    expect(sheet).to_have_count(0)
    expect(page.locator('[data-history="A-0003"]')).to_contain_text(f"due: {picked}")
    dark(page)
    due.click()
    assert axe(page) == []
    page.keyboard.press("Escape")
    clean(page, errors)

    short, errors = opened(browser, port, token, "/ledger?open=A-0003", viewport={"width": 1440, "height": 380})
    short.locator("#f-A-0003-due").click()
    month = short.get_by_role("dialog", name="Due")
    expect(month).to_have_attribute("data-slot", "popover-content")
    # Radix draws the popover off screen until it has measured where it goes, so the box is read once it has.
    expect(month).to_be_in_viewport(ratio=1)
    assert inside(month.bounding_box(), 380)
    # The month is taller than the room it has here, so it scrolls within the window rather than past it.
    scrolls = "(el => el.scrollHeight > el.clientHeight && getComputedStyle(el).overflowY === 'auto')"
    assert short.evaluate(f"{scrolls}(document.querySelector('[data-slot=popover-content]'))")
    clean(short, errors)


def test_space_ticks_and_ctrl_z_undoes(browser: Browser, day_service: tuple[int, str]) -> None:
    """The old tasks app's other keys: Space ticks the selected row, and Ctrl+Z undoes as u does (DR-0711)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/ledger")
    page.keyboard.press("j")
    page.keyboard.press(" ")
    expect(page.get_by_role("checkbox", name="Tick A-0001")).to_have_attribute("aria-checked", "true")
    expect(page.get_by_role("region", name="The ticked rows")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("checkbox", name="Tick A-0001")).to_have_attribute("aria-checked", "false")
    page.keyboard.press("2")
    expect(notice(page)).to_contain_text("A-0001: triage: claude")
    page.keyboard.press("Control+z")
    expect(notice(page)).to_contain_text("Undone.")
    expect(row(page, "A-0001")).to_have_attribute("data-triage", "chase")
    clean(page, errors)


def test_staying_on_the_leave_guard_writes_nothing_and_leaving_writes(
    browser: Browser, day_service: tuple[int, str], day_root: Path
) -> None:
    """A call that would carry the panel off text typed in it asks first; Stay writes nothing and leaves its buttons
    live, and Leave writes it with no second question (DR-0710)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/ledger?open=A-0003")
    answers, seen = [False, True], []

    def answer(dialog: Any) -> None:
        seen.append(dialog.type)
        if answers.pop(0) if answers else True:
            dialog.accept()
        else:
            dialog.dismiss()

    page.on("dialog", answer)
    page.get_by_label("Add a line").fill("a line not yet added")
    before = day_routes.version(day_root)
    claude = panel(page).get_by_role("group", name="Call").get_by_role("button", name=re.compile("^Claude"))
    claude.click()
    expect(claude).to_be_enabled()
    assert seen == ["confirm"]
    assert page.url.endswith("/ledger?open=A-0003")
    assert day_routes.version(day_root) == before
    claude.click()
    expect(notice(page)).to_contain_text("A-0003: triage: claude")
    assert seen == ["confirm", "confirm"]
    page.wait_for_url("**/ledger?open=A-0004")
    clean(page, errors)


def test_a_material_opens_shows_in_its_folder_and_reads_in_the_viewer(
    browser: Browser, day_service: tuple[int, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A material opened or shown in its folder says so, the panel scrolled where it was; a text material reads in
    the panel's viewer (DR-0664). The launcher is patched, so nothing opens."""
    launched: list[tuple[str, bool]] = []
    monkeypatch.setattr(day_routes, "launch", lambda path, *, reveal: launched.append((Path(path).name, reveal)))
    port, token = day_service
    page, errors = opened(browser, port, token, "/ledger?open=A-0005", viewport={"width": 1440, "height": 520})
    scrolled = "document.querySelector('[data-slot=panel] .overflow-y-auto').scrollTop"
    plan = panel(page).get_by_role("listitem").filter(has_text="The plan")
    pressed = (("Open The plan", "Opened it with its app."), ("Show in folder: The plan", "Shown in its folder."))
    for button, said in pressed:
        plan.get_by_role("button", name=button).scroll_into_view_if_needed()
        kept = page.evaluate(scrolled)
        assert kept > 0
        plan.get_by_role("button", name=button).click()
        expect(notice(page)).to_have_text(re.compile(said))
        assert abs(page.evaluate(scrolled) - kept) <= 2
    assert launched == [("plan.md", False), ("plan.md", True)]
    plan.get_by_role("button", name="View The plan").click()
    page.wait_for_url("**/ledger?open=A-0005&text=notes/plan.md")
    viewer = page.get_by_role("region", name="The text of notes/plan.md")
    expect(viewer).to_contain_text("Book the room first.")
    assert axe(page) == []
    clean(page, errors)


def test_a_new_item_adds_and_a_look_alike_asks_first(browser: Browser, day_service: tuple[int, str]) -> None:
    """n opens the new item; what is typed stays while it is closed; a look-alike is refused with the items it looks
    like, and Add anyway adds it and opens it (DR-0710)."""
    port, token = day_service
    page, errors = opened(browser, port, token, "/ledger")
    page.keyboard.press("n")
    dialog = page.get_by_role("dialog", name="New item")
    dialog.get_by_label("Title").fill("Send Ada the dates again")
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    page.keyboard.press("n")
    expect(dialog.get_by_label("Title")).to_have_value("Send Ada the dates again")
    dialog.get_by_label("Who").fill("Ada Example")
    dialog.get_by_label("Stream").fill("pilot")
    dialog.get_by_role("button", name="Add", exact=True).click()
    expect(dialog.get_by_role("alert")).to_contain_text("looks like an open item already: A-0001")
    expect(dialog.get_by_role("link", name=re.compile("A-0001"))).to_be_visible()
    assert axe(page) == []
    dialog.get_by_role("button", name="Add anyway").click()
    page.wait_for_url("**/ledger?open=A-0008")
    # The form closes, emptied, and the new item opens beside the list.
    expect(dialog).to_have_count(0)
    expect(panel(page).get_by_label("Title")).to_have_value("Send Ada the dates again")
    page.keyboard.press("Escape")
    page.keyboard.press("n")
    expect(dialog.get_by_label("Title")).to_have_value("")
    # The look-alike's 400, as the console logs it, is the one failure the page expects.
    assert [e for e in errors if "400" not in e] == []


# -- the page on the perf ledger (DR-0668, DR-0712) -----------------------------------------------------------------


@pytest.fixture(scope="module")
def perf_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """#201's perf ledger in a vault, its files an hour old, and the index rebuilt over it."""
    vault = empty_vault(tmp_path_factory.mktemp("ledger-perf"), make_config())
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
  const link = document.querySelector('nav[aria-label="Pages"] a[href="/ledger"]');
  const started = performance.now();
  link.click();
  await new Promise((done) => {
    const look = () => (document.querySelector('[data-row]') ? done() : requestAnimationFrame(look));
    look();
  });
  return (performance.now() - started) / 1000;
}"""


# On the perf ledger: out of gate os's selection, while the browser jobs run it by file, as before (DR-0757).
@pytest.mark.nightly
@pytest.mark.perf
def test_the_ledger_opens_from_the_sidebar_within_its_budget(browser: Browser, perf_service: tuple[int, str]) -> None:
    """On 5,000 items, the Ledger opened from the sidebar shows its 100 rows with its keys live within a quarter
    second: once to warm the service's caches, then the best of three, each from a page loaded afresh, so no read
    comes from the app's own cache."""
    port, token = perf_service
    page, errors = opened(browser, port, token, "/design")
    base = page.url.split("/design")[0]
    took: list[float] = []
    for _ in range(4):
        page.goto(f"{base}/design")
        page.wait_for_selector('nav[aria-label="Pages"] a[href="/ledger"]')
        page.wait_for_load_state("networkidle")
        took.append(float(page.evaluate(OPEN_FROM_SIDEBAR)))
        expect(page.locator("[data-row]")).to_have_count(100)
        # The keys live with the rows: j selects the first.
        page.keyboard.press("j")
        expect(page.locator('li[aria-current="true"]')).to_have_count(1)
    best = min(took[1:])
    runs = ", ".join(f"{t * 1000:.0f}" for t in took[1:])
    print(f"the Ledger from the sidebar on {ITEMS:,} items: {best * 1000:.0f} ms ({runs} ms)")
    if not traced():
        assert best < PAGE_SECONDS, f"the Ledger took {best:.3f} s from the sidebar, over {PAGE_SECONDS} s"
    clean(page, errors)
