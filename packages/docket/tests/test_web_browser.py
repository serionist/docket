"""The web app in a real browser, once: what render tests can't see, such as a CSP violation, a swap that doesn't
happen, a key that does nothing, or a font that doesn't load.

It runs only with `DOCKET_TEST_BROWSER` set to the browser to drive: `chrome` or `msedge` (the system's own, nothing
downloaded) or `chromium` (Playwright's, after `playwright install chromium`). With the variable set, a browser that
can't start fails the test. CI runs it in its own Linux job; `DOCKET_TEST_SCREENSHOTS` names a folder for the shots.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import uvicorn
from briefs_kit import NORTHWIND, briefs_template
from curate_kit import curate_vault
from day_kit import day_vault, today
from decisions_kit import BRIEF, decisions_template
from docket_testkit import index_template
from playwright.sync_api import Page, expect, sync_playwright
from recording_kit import CRANE, NOTE, TOUR, recording_template, with_audio
from settings_kit import settings_template
from staging_kit import staging_vault
from sync_kit import as_structured, as_text, memo, sync_template

from docket.ledger import store
from docket.service import client, server
from docket.service.client import call_json
from docket.service.token import Tokens
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.vault import Vault
from docket.web import day as day_routes
from docket.web import day_view
from docket.web import settings as settings_routes
from docket.web.settings_view import Facts

BROWSER = os.environ.get("DOCKET_TEST_BROWSER", "")
pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def service(template: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    """The real service on a free port, in a thread: its port and its token."""
    shutil.copytree(template, tmp_path / "vault")
    yield from _serve(tmp_path / "vault")


@pytest.fixture(scope="module")
def sync_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return sync_template(tmp_path_factory)


@pytest.fixture
def sync_service(sync_project: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    """The real service over a consuming project with the invented memo source."""
    shutil.copytree(sync_project, tmp_path / "project")
    yield from _serve(tmp_path / "project" / "vault")


@pytest.fixture
def staging_service(tmp_path: Path) -> Iterator[tuple[int, str]]:
    """The real service over a vault with a transcript and a mail waiting, and a memo ready."""
    yield from _serve(staging_vault(tmp_path).root)


@pytest.fixture(scope="module")
def settings_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return settings_template(tmp_path_factory)


@pytest.fixture
def settings_service(settings_project: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    """The real service over a consuming project with a mixed, a work and a folder source, and never-store values."""
    shutil.copytree(settings_project, tmp_path / "project")
    yield from _serve(tmp_path / "project" / "vault")


@pytest.fixture
def curate_service(tmp_path: Path) -> Iterator[tuple[int, str]]:
    """The real service over the invented curate vault: recordings, notes and mail, no entity yet."""
    yield from _serve(curate_vault(tmp_path).root)


@pytest.fixture(scope="module")
def recording_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return recording_template(tmp_path_factory)


@pytest.fixture
def recording_service(recording_project: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    """The real service over the invented recording vault: calls kept as Plaud keeps them, a note on one."""
    shutil.copytree(recording_project, tmp_path / "vault")
    yield from _serve(tmp_path / "vault")


@pytest.fixture(scope="module")
def briefs_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return briefs_template(tmp_path_factory)


@pytest.fixture
def briefs_service(briefs_project: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    """The real service over the invented briefs vault: four briefs, stale, current and orphaned."""
    shutil.copytree(briefs_project, tmp_path / "vault")
    yield from _serve(tmp_path / "vault")


@pytest.fixture(scope="module")
def decisions_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return decisions_template(tmp_path_factory)


@pytest.fixture
def decisions_service(decisions_project: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    """The real service over the invented decisions vault: decided, superseded, rejected and waiting decisions."""
    shutil.copytree(decisions_project, tmp_path / "vault")
    yield from _serve(tmp_path / "vault")


def _serve(vault: Path) -> Iterator[tuple[int, str]]:
    token = Tokens().ensure().value
    servers: list[uvicorn.Server] = []
    runner = threading.Thread(
        target=server.serve, args=(vault, 0), kwargs={"said": lambda _: None, "running": servers.append}
    )
    runner.start()
    deadline = time.monotonic() + 60
    record = client.recorded()
    while record is None and time.monotonic() < deadline:
        time.sleep(0.05)
        record = client.recorded()
    assert record is not None, "the service didn't start"
    try:
        yield int(record["port"]), token
    finally:
        while not servers:
            time.sleep(0.05)
        servers[0].should_exit = True
        runner.join(30)


def test_the_search_page_in_a_browser(service: tuple[int, str], tmp_path: Path) -> None:
    port, token = service
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        # A console error names no URL; these do, so a failure says which request it was.
        page.on("requestfailed", lambda r: errors.append(f"request failed: {r.method} {r.url} ({r.failure})"))
        page.on(
            "response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None
        )
        base = f"http://127.0.0.1:{port}"
        page.goto(f"{link.body['url']}&next=/search")
        page.wait_for_url(f"{base}/search")
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.fonts.check('13px Inter') && document.fonts.check('13px \"JetBrains Mono\"')")

        # A query typed and sent: the results swap in, the address bar holds the canonical URL.
        page.fill("#query", "Nortwin")
        page.press("#query", "Enter")
        page.wait_for_selector(".groups")
        assert page.url.endswith("/search?query=Nortwin")
        expect(page.locator("#announce")).to_contain_text("hits in")

        # Escape leaves the box with the query kept; j moves through the hits; Enter opens one beside the results.
        page.keyboard.press("Escape")
        assert page.input_value("#query") == "Nortwin"
        # Titles that name Northwind lead (DR-0231), so the call isn't the first group: j walks on to it, a new hit each
        # time.
        page.keyboard.press("j")
        seen = [page.evaluate("document.activeElement.dataset.locator")]
        while not seen[-1].startswith("r:calls:") and len(seen) < 8:
            page.keyboard.press("j")
            seen.append(page.evaluate("document.activeElement.dataset.locator"))
        hit = seen[-1]
        assert hit.startswith("r:calls:") and len(seen) > 1 and len(set(seen)) == len(seen)
        page.keyboard.press("Enter")
        page.wait_for_selector("#panel .raw-column .segment")
        expect(page.locator("#panel .summary-column")).to_be_visible()
        assert page.evaluate("document.activeElement.id") == "panel-title"
        page.screenshot(path=str(shots / "search-panel.png"))

        # v verifies the summary; Escape closes the panel and gives the focus back to the hit.
        page.keyboard.press("v")
        page.wait_for_selector("#panel .pairs")
        page.keyboard.press("Escape")
        expect(page.locator("#panel .panel")).to_have_count(0)
        assert page.evaluate("document.activeElement.dataset.locator") == hit

        # Back restores the search before.
        page.fill("#query", "fleet")
        page.press("#query", "Enter")
        page.wait_for_url(f"{base}/search?query=fleet")
        page.go_back()
        page.wait_for_url(f"{base}/search?query=Nortwin")
        expect(page.locator("#query")).to_have_value("Nortwin")
        page.screenshot(path=str(shots / "search-results.png"))

        # The theme switch, light then dark, survives a reload.
        page.click('[data-action="theme"]')
        page.click('[data-action="theme"]')
        page.reload()
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        page.screenshot(path=str(shots / "search-dark.png"))

        # A narrow window wraps and never scrolls sideways: the close button and the switches stay in view.
        for width, height in ((800, 900), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            page.goto(f"{base}/search?query=Nortwin&hit={quote(hit)}")
            page.wait_for_selector("#panel .panel")
            wide = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
            assert wide[0] == wide[1], (width, wide)
            for control in ("#panel .close", '[data-action="theme"]', '[data-action="keys"]'):
                box = page.locator(control).bounding_box()
                assert box is not None
                assert box["x"] >= 0 and box["x"] + box["width"] <= wide[1], (width, control, box)
            if width == 800:
                page.screenshot(path=str(shots / "search-800.png"))
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{base}/search?query=Nortwin")
        page.wait_for_selector(".groups")
        assert not errors, errors

        # With the single keys switched off, j moves nothing.
        page.keyboard.press("?")
        page.click('[data-action="keys-switch"]')
        page.click('[data-action="close-dialog"]')
        page.locator("body").click(position={"x": 5, "y": 500})
        page.keyboard.press("j")
        assert page.evaluate("document.activeElement.classList.contains('hit-link')") is False

        # A session that ended: the next search reloads into the whole sign-in page, naming the page to come back to.
        page.context.clear_cookies()
        page.fill("#query", "pallet labels")
        page.press("#query", "Enter")
        page.wait_for_selector("text=Open Docket from a terminal on this machine")
        assert page.locator("code").inner_text() == 'docket open "/search?query=Nortwin"'
        browser.close()


def test_the_sync_page_in_a_browser(sync_service: tuple[int, str], tmp_path: Path) -> None:
    port, token = sync_service
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: errors.append(f"request failed: {r.method} {r.url} ({r.failure})"))
        page.on(
            "response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None
        )
        base = f"http://127.0.0.1:{port}"
        page.goto(f"{link.body['url']}&next=/sync")
        page.wait_for_url(f"{base}/sync")

        # On a phone the source never synced is due, and the words to paste fit the screen.
        expect(page.locator(".need-line")).to_have_text("due: never synced")
        _fits(page, ".paste-text", ".paste .button")
        page.screenshot(path=str(shots / "sync-390-due.png"), full_page=True)

        # A run opened and bound as the capture hook does, with one capture: the page shows it, and its runs part
        # refreshes itself until the run closes.
        opened = call_json("POST", "/api/runs", {"source": "memo", "session": "s-browser"}, token=token, at=port)
        assert opened is not None and opened.status == 200, opened
        run = opened.body["run"]
        _capture(sync_service, run, 1, "list_memos", as_text([memo("b1")]))
        page.set_viewport_size({"width": 1440, "height": 900})
        page.reload()
        card = page.locator(f"li.state-open[id='{run}']")
        expect(card).to_contain_text("1 capture,")
        _capture(sync_service, run, 2, "get_memo", as_structured(memo("b1")))
        expect(card).to_contain_text("2 captures", timeout=8000)
        assert page.url == f"{base}/sync"
        page.screenshot(path=str(shots / "sync-open-run.png"), full_page=True)
        closed = call_json("POST", f"/api/runs/{run}/close", {"captures": 2}, token=token, at=port)
        assert closed is not None and closed.status == 200, closed
        expect(page.locator(f"li.state-closed[id='{run}']")).to_be_visible(timeout=8000)
        expect(page.locator("#runs")).to_contain_text("No run is open now")
        # A second run whose count doesn't match is dropped: a failure, with what to do about it.
        again = call_json("POST", "/api/runs", {"source": "memo", "session": "s-browser"}, token=token, at=port)
        assert again is not None and again.status == 200, again
        _capture(sync_service, again.body["run"], 3, "list_memos", as_text([memo("b2")]))
        dropped = call_json("POST", f"/api/runs/{again.body['run']}/close", {"captures": 3}, token=token, at=port)
        assert dropped is not None and dropped.status == 400, dropped
        page.reload()
        expect(page.locator(".lead")).to_contain_text("nothing needs a session; 0 runs open; last whole run")
        expect(page.locator("li.failure")).to_contain_text("What to do: nothing; if it repeats")
        page.screenshot(path=str(shots / "sync.png"), full_page=True)

        # At phone width the runs and cursors stack, and nothing scrolls sideways.
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload()
        _fits(page, f"li.run[id='{run}']", "li.failure", ".machine")
        page.screenshot(path=str(shots / "sync-390.png"), full_page=True)
        assert not errors, errors
        browser.close()


def test_the_staging_page_in_a_browser(staging_service: tuple[int, str], tmp_path: Path) -> None:
    port, token = staging_service
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: errors.append(f"request failed: {r.method} {r.url} ({r.failure})"))
        page.on(
            "response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None
        )
        base = f"http://127.0.0.1:{port}"
        page.goto(f"{link.body['url']}&next=/staging")
        page.wait_for_url(f"{base}/staging")
        expect(page.locator(".lead")).to_have_text(
            "3 records wait at the gate: 2 for you to classify, 1 ready to promote"
        )

        # j moves to the first record and Enter opens it beside the list, without leaving the page.
        page.keyboard.press("j")
        page.keyboard.press("Enter")
        expect(page.locator("#panel-title")).to_have_text("Supplier call")
        assert page.url.startswith(f"{base}/staging?open=")

        # Two utterances checked and previewed: the overlapping third goes too. A real browser's post passes the
        # Guard: the page's Referrer-Policy lets it send its Origin, not null.
        page.locator("li.utterance input[value='2']").check()
        page.click("button[value='preview']")
        expect(page.locator(".preview")).to_contain_text("Removes 2 utterances: 00:12-00:31.")
        assert "&u=2" in page.url
        page.screenshot(path=str(shots / "staging.png"), full_page=True)

        # At phone width the open record covers the list and nothing scrolls sideways.
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload()
        _fits(page, "#panel .close", ".utterance", "button[value='work']")
        page.screenshot(path=str(shots / "staging-390.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 900})

        page.click("button[value='work']")
        expect(page.locator(".notice")).to_contain_text("classified work: it goes to raw at the next promotion")
        expect(page.locator("li.record.state-ready")).to_have_count(2)

        # The mail dropped as private, through its own question.
        page.click("text=The offer")
        page.click("text=Private: drop it")
        page.click("text=Drop it as private")
        expect(page.locator(".notice")).to_contain_text("dropped as private")
        expect(page.locator(".record-title")).to_have_count(2)

        page.click("text=Promote all")
        expect(page.locator(".notice")).to_contain_text("Promoted: 2 records written to raw and committed.")
        expect(page.locator(".lead")).to_have_text("Nothing waits at the gate")
        page.set_viewport_size({"width": 390, "height": 844})
        _fits(page, ".lead", '[data-action="keys"]')
        page.screenshot(path=str(shots / "staging-390-done.png"), full_page=True)
        assert not errors, errors
        browser.close()


def test_the_curate_page_in_a_browser(curate_service: tuple[int, str], tmp_path: Path) -> None:
    port, token = curate_service
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: errors.append(f"request failed: {r.method} {r.url} ({r.failure})"))
        page.on(
            "response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None
        )
        base = f"http://127.0.0.1:{port}"
        page.goto(f"{link.body['url']}&next=/curate")
        page.wait_for_url(f"{base}/curate")
        expect(page.locator(".lead")).to_have_text("No round on this machine yet.")

        # A round starts only on the click; a real browser's post passes the Guard.
        page.click("text=Start a round")
        expect(page.locator(".notice")).to_contain_text("A new round: 6 cards.")
        expect(page.locator("li.card")).to_have_count(6)
        # The keys are docket.js's, a deferred script: the new page's cards show before it has run, and a key pressed
        # then is lost. It has run once the page has finished parsing.
        page.wait_for_load_state("domcontentloaded")

        # j moves to the first card and Enter opens it beside the list, its snippets cut now.
        page.keyboard.press("j")
        expect(page.locator("li.card a.hit-link").first).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.locator("#panel-title")).to_have_text("1. New term: Northwind")
        expect(page.locator(".snippet mark").first).to_have_text("Nortwin")
        assert page.url.startswith(f"{base}/curate?open=")
        page.screenshot(path=str(shots / "curate.png"), full_page=True)

        # Edit opens the form in place; Cancel goes back to the card.
        page.click("#panel >> text=Edit")
        expect(page.locator(".edit-form")).to_be_visible()
        assert "edit=" in page.url
        page.screenshot(path=str(shots / "curate-edit.png"), full_page=True)
        page.click("#panel >> text=Cancel")
        expect(page.locator(".edit-form")).to_have_count(0)

        # Accept: the entity is written, the card shows it, the log lists it with its Undo.
        page.click("button[value='accept']")
        expect(page.locator(".notice")).to_contain_text("accepted and logged")
        expect(page.locator("li.card.state-accepted")).to_have_count(1)
        expect(page.locator(".log-line")).to_have_count(1)

        # At phone width the open card covers the list and nothing scrolls sideways.
        page.set_viewport_size({"width": 390, "height": 844})
        second = page.locator("li.card a.hit-link").nth(1).get_attribute("href")
        assert second is not None
        page.goto(f"{base}{second}")
        _fits(page, "#panel .close", "button[value='accept']", "button[value='reject']")
        page.screenshot(path=str(shots / "curate-390.png"), full_page=True)
        page.goto(f"{base}/curate")
        _fits(page, ".lead", ".log-line", '[data-action="keys"]')
        page.screenshot(path=str(shots / "curate-390-list.png"), full_page=True)
        assert not errors, errors
        browser.close()


def test_the_settings_page_in_a_browser(settings_service: tuple[int, str], tmp_path: Path) -> None:
    port, token = settings_service
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: errors.append(f"request failed: {r.method} {r.url} ({r.failure})"))
        page.on(
            "response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None
        )
        base = f"http://127.0.0.1:{port}"
        page.goto(f"{link.body['url']}&next=/settings")
        page.wait_for_url(f"{base}/settings")
        expect(page.locator(".lead")).to_contain_text("4 sources, 4 on; 4 never-store values")
        # No value of a list in the page until asked for. A form for each change a page makes, either way (DR-0601):
        # an add per list, phone on and the pattern off, never credentials off.
        expect(page.locator("main")).not_to_contain_text("Dora #2")
        expect(page.locator("form[action='/settings/never-store']")).to_have_count(3)
        expect(page.locator("form[action='/settings/redaction'] input[name='rule']")).to_have_count(2)
        expect(page.locator("form:has(input[name='rule'][value='credentials'])")).to_have_count(0)

        # A source's changes fold out without JavaScript's help, stricter first, each with its button.
        page.click("text=Change calls")
        changes = page.locator("#source-calls li.change")
        expect(changes.first).to_contain_text("Make it private")
        expect(changes.nth(2)).to_contain_text("lets more in")
        page.screenshot(path=str(shots / "settings.png"), full_page=True)

        page.click("a.show-values")
        page.wait_for_url(f"{base}/settings?show=lists")
        expect(page.locator("#list-people .value")).to_have_text(["Cleo Test", "Dora #2"])
        page.screenshot(path=str(shots / "settings-values.png"), full_page=True)

        # At phone width the cards stack, and nothing scrolls sideways.
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload()
        page.click("text=Change calls")
        _fits(
            page,
            "#source-calls",
            "#source-calls .act-form",
            "#list-people",
            "#list-people .button",
            ".setting-facts.models",
        )
        page.screenshot(path=str(shots / "settings-390.png"), full_page=True)

        # A value added: its own question, then the add; the address bar never holds it, at either width.
        page.set_viewport_size({"width": 1280, "height": 900})
        _add(page, base, "Ada Sample", shots / "settings-confirm.png")
        page.set_viewport_size({"width": 390, "height": 844})
        _fits(page, ".notice", "#list-people")
        assert not errors, errors
        # No entry of the tab's history holds the value: back lands on the question's answer, by its path alone.
        history = page.context.new_cdp_session(page).send("Page.getNavigationHistory")
        urls = [entry["url"] for entry in history["entries"]]
        assert f"{base}/settings/never-store" in urls
        assert not [u for u in urls if "Ada" in u or "Sample" in u], urls
        browser.close()

        # The same without JavaScript: plain forms, the same two posts.
        again = client.call("POST", "/api/session", token=token, at=port)
        assert again is not None
        scriptless = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        bare = scriptless.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
        plain = bare.new_page()
        plain.goto(f"{again.body['url']}&next=/settings")
        plain.wait_for_url(f"{base}/settings")
        _add(plain, base, "Ben Plain", shots / "settings-confirm-390.png")

        # A source made private, through its own question, and a rule turned on, each a plain form post.
        plain.click("#source-memo summary")
        plain.click("#source-memo button:has-text('Make it private')")
        expect(plain.locator("h1")).to_have_text("Make memo private?")
        _fits(plain, ".confirm-form .button.primary")
        plain.screenshot(path=str(shots / "settings-private-390.png"), full_page=True)
        plain.click(".confirm-form .button.primary")
        plain.wait_for_url(f"{base}/settings?done=memo-private")
        expect(plain.locator("main")).to_contain_text("memo is private: nothing from it is stored")
        plain.click("form:has(input[name='rule'][value='phone']) button")
        plain.wait_for_url(f"{base}/settings?done=phone-on")
        expect(plain.locator("main")).to_contain_text("Phone numbers are redacted from now on.")
        scriptless.close()
    found = VaultConfig.load(tmp_path / "project" / "vault" / CONFIG_FILE)
    assert found.never_store.people[-2:] == ["Ada Sample", "Ben Plain"]
    assert (found.sources["memo"].sensitivity, found.redaction.phone) == ("private", True)


def test_the_settings_page_says_when_an_edit_isn_t_in_force(settings_service: tuple[int, str], tmp_path: Path) -> None:
    """An edit of vault.yaml by hand that lets more in waits for its commit (DR-0521): the page says so at the top,
    naming no value, and says what the file now holds (DR-0601)."""
    port, token = settings_service
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "project" / "vault" / CONFIG_FILE
    edited = VaultConfig.load(path)
    edited.redaction.credentials = False
    path.write_text(edited.dump(), encoding="utf-8", newline="\n")
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        base = f"http://127.0.0.1:{port}"
        page.goto(f"{link.body['url']}&next=/settings")
        page.wait_for_url(f"{base}/settings")
        expect(page.locator(".notice").first).to_contain_text("vault.yaml has an edit that isn't in force")
        expect(page.locator("main")).to_contain_text("Credentials are not redacted")
        expect(page.locator("main")).not_to_contain_text("Dora #2")
        page.screenshot(path=str(shots / "settings-waiting.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload()
        _fits(page, ".notice")
        page.screenshot(path=str(shots / "settings-waiting-390.png"), full_page=True)
        browser.close()


# Windows paths as the page gets them: backslashes and no hyphen, so no place a line may break.
WINDOWS = r"C:\Users\AdaExampleSampleName"
CONFIG = WINDOWS + r"\AppData\Local\docket\config.yaml"
HERE = WINDOWS + r"\Documents\SharedProjectDocuments"


def test_windows_paths_wrap_on_the_settings_page_at_phone_width(
    settings_service: tuple[int, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each element that shows a path wraps it anywhere, so the page never scrolls sideways at 390 px (#193). CI runs
    on Linux, whose paths have slashes to break at, so the paths are made Windows-shaped here."""
    port, token = settings_service
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    real = settings_routes.gather

    def windows(service: Any) -> Facts:
        facts = real(service)
        sources = [replace(s, here=HERE) if s.entry.folder else s for s in facts.sources]
        root = Path(WINDOWS + r"\Projects\AdaExampleNotes\vault")
        return replace(facts, root=root, config_path=Path(CONFIG), sources=sources)

    monkeypatch.setattr(settings_routes, "gather", windows)
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        base = f"http://127.0.0.1:{port}"
        page.goto(f"{link.body['url']}&next=/settings")
        page.wait_for_url(f"{base}/settings")
        page.evaluate("document.querySelectorAll('details').forEach(d => { d.open = true; })")
        expect(page.locator("main")).to_contain_text(CONFIG)
        expect(page.locator("#source-documents")).to_contain_text(HERE)
        _fits(page, "#source-documents", "section[aria-labelledby='models-title']")
        page.screenshot(path=str(shots / "settings-windows-390.png"), full_page=True)
        browser.close()


def _seeking(request: Any) -> bool:
    return "/recording/audio" in request.url and request.failure == "net::ERR_ABORTED"


def test_the_recording_page_in_a_browser(recording_service: tuple[int, str], tmp_path: Path) -> None:
    port, token = recording_service
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    errors: list[str] = []
    base = f"http://127.0.0.1:{port}"
    note = f"{base}/recording?rid={CRANE}&doc={NOTE}"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        # A media element cancels its own requests as it seeks and reads ahead: that isn't a failure.
        page.on("requestfailed", lambda r: None if _seeking(r) else errors.append(f"request failed: {r.url}"))
        page.on(
            "response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None
        )
        page.goto(f"{link.body['url']}&next=/recording")
        page.wait_for_url(f"{base}/recording")

        # The start takes a locator; the page opens at its time.
        page.fill("#rid", f"r:{CRANE}@01:40")
        page.press("#rid", "Enter")
        expect(page.locator("h1")).to_have_text("Harbour crane check")
        expect(page.locator("li.segment.mark").first).to_contain_text("01:40")
        expect(page.locator(".audio")).to_contain_text("No audio stored yet")

        # A summary line's anchor marks the lines it points at, and the address holds the rid and the time.
        page.goto(note)
        page.click("a.anchor:has-text('@00:40-01:39')")
        page.wait_for_url(f"{note}&at=00:40-01:39#at")
        expect(page.locator("li.segment.mark")).to_have_count(3)
        expect(page.locator(".summary-column .line.cited")).to_contain_text("west crane needs a new brake pad")
        page.screenshot(path=str(shots / "recording.png"), full_page=True)

        # At phone width the columns stack, the link scrolls the marked line into view, and nothing scrolls sideways.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(note)
        page.click("a.anchor:has-text('@02:40')")
        page.wait_for_url(f"{note}&at=02:40#at")
        expect(page.locator("li#at")).to_be_in_viewport()
        _fits(page, ".recording-locator", ".summary-column", "li#at")
        page.screenshot(path=str(shots / "recording-390.png"))
        page.set_viewport_size({"width": 1440, "height": 900})
        page.click('[data-action="theme"]')
        page.click('[data-action="theme"]')
        page.reload()
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        page.screenshot(path=str(shots / "recording-dark.png"), full_page=True)
        page.click('[data-action="theme"]')

        # The tour's audio stored, and Whisper's transcript beside the recorder's: a line's time plays from there.
        with_audio(Vault.open(tmp_path / "vault"))
        tour = f"{base}/recording?rid={TOUR}"
        page.goto(tour)
        expect(page.locator("table.aligned tbody tr")).to_have_count(4)
        expect(page.locator("table.aligned tbody tr:nth-child(3) li.segment.cut")).to_have_count(2)
        page.click("table.aligned tbody tr:nth-child(2) td:first-child a.time")
        page.wait_for_function(
            "() => { const p = document.getElementById('player'); return p && !p.paused && p.currentTime >= 20; }"
        )
        assert page.url == f"{tour}&at=00:20#at"
        expect(page.locator("li.segment.playing")).to_contain_text("The new gate opens on Friday.")
        page.screenshot(path=str(shots / "recording-player.png"), full_page=True)
        assert not errors, errors
        browser.close()

        # Without JavaScript the page and its links work the same.
        again = client.call("POST", "/api/session", token=token, at=port)
        assert again is not None
        scriptless = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        bare = scriptless.new_context(java_script_enabled=False, viewport={"width": 1280, "height": 900})
        plain = bare.new_page()
        plain.goto(f"{again.body['url']}&next=/recording")
        plain.wait_for_url(f"{base}/recording")
        plain.goto(note)
        plain.click("li.segment:nth-child(5) a.time")
        plain.wait_for_url(f"{note}&at=01:20#at")
        expect(plain.locator("li.segment.mark")).to_have_text([re.compile("01:20")])
        expect(plain.locator(".summary-column .line.cited")).to_have_count(1)
        plain.screenshot(path=str(shots / "recording-no-script.png"), full_page=True)
        # The player starts at the line its link opens, by its own #t=.
        plain.goto(tour)
        plain.click("table.aligned tbody tr:nth-child(2) td:first-child a.time")
        plain.wait_for_url(f"{tour}&at=00:20#at")
        expect(plain.locator("#player")).to_have_attribute("src", re.compile(r"#t=20\.000$"))
        plain.screenshot(path=str(shots / "recording-player-no-script.png"), full_page=True)
        scriptless.close()


def test_the_briefs_page_in_a_browser(briefs_service: tuple[int, str], tmp_path: Path) -> None:
    port, token = briefs_service
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    errors: list[str] = []
    base = f"http://127.0.0.1:{port}"
    northwind = f"{base}/briefs?brief={NORTHWIND}"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: errors.append(f"request failed: {r.method} {r.url} ({r.failure})"))
        page.on(
            "response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None
        )
        page.goto(f"{link.body['url']}&next=/briefs")
        page.wait_for_url(f"{base}/briefs")

        # The briefs by entity type, then one opened, then one of its lines beside its raw.
        expect(page.locator(".brief-group h2")).to_have_text(["person", "customer", "site", "orphaned"])
        page.screenshot(path=str(shots / "briefs.png"), full_page=True)
        page.click("a.brief-name:has-text('Northwind')")
        page.wait_for_url(northwind)
        expect(page.locator("h1")).to_have_text("Northwind")
        page.click("a.line-link:has-text('supplier call')")
        page.wait_for_url(re.compile(r"&line=\d+#raw$"))
        expect(page.locator("#raw article.span")).to_contain_text("offer in writing")
        expect(page.locator(".line.cited")).to_contain_text("supplier call")
        # A flagged line says what changed under it, and the line it was built from as it reads now.
        page.click("a.line-link:has-text('above the budget')")
        expect(page.locator("#raw .cited-line")).to_contain_text("reads otherwise")
        page.screenshot(path=str(shots / "briefs-open.png"), full_page=True)

        # At phone width the columns stack, a line's link lands on its raw, and nothing scrolls sideways.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(northwind)
        page.click("a.line-link:has-text('supplier call')")
        page.wait_for_url(re.compile(r"&line=\d+#raw$"))
        expect(page.locator("#raw")).to_be_in_viewport()
        _fits(page, ".briefs-locator", ".brief-column", "#raw")
        page.screenshot(path=str(shots / "briefs-390.png"))
        page.set_viewport_size({"width": 1440, "height": 900})
        page.click('[data-action="theme"]')
        page.click('[data-action="theme"]')
        page.reload()
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        page.screenshot(path=str(shots / "briefs-dark.png"), full_page=True)
        assert not errors, errors
        browser.close()

        # Without JavaScript the page and its links work the same.
        again = client.call("POST", "/api/session", token=token, at=port)
        assert again is not None
        scriptless = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        bare = scriptless.new_context(java_script_enabled=False, viewport={"width": 1280, "height": 900})
        plain = bare.new_page()
        plain.goto(f"{again.body['url']}&next=/briefs")
        plain.wait_for_url(f"{base}/briefs")
        plain.click("a.type-chip:has-text('customer')")
        plain.wait_for_url(f"{base}/briefs?type=customer")
        plain.click("a.brief-name")
        plain.wait_for_url(f"{northwind}&type=customer")
        plain.click("a.line-link:has-text('by mail')")
        expect(plain.locator("#raw article.span")).to_contain_text("The consultant's offer")
        plain.screenshot(path=str(shots / "briefs-no-script.png"), full_page=True)
        scriptless.close()


def test_the_decisions_on_the_briefs_page_in_a_browser(decisions_service: tuple[int, str], tmp_path: Path) -> None:
    port, token = decisions_service
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    errors: list[str] = []
    base = f"http://127.0.0.1:{port}"
    fleet = f"{base}/briefs?brief={BRIEF}"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("requestfailed", lambda r: errors.append(f"request failed: {r.method} {r.url} ({r.failure})"))
        page.on(
            "response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None
        )
        page.goto(f"{link.body['url']}&next=/briefs")
        page.wait_for_url(f"{base}/briefs")

        # What waits shows on the list; its page has the candidate that contradicts beside what it contradicts.
        page.click(".waiting-list a")
        page.wait_for_url(fleet)
        expect(page.locator(".waiting").first.locator("h4").first).to_have_text("the pilot comes first after all")
        expect(page.locator(".waiting.paired .against h4")).to_have_text("the dashboard ships before the pilot")
        page.screenshot(path=str(shots / "decisions.png"), full_page=True)

        # A click confirms, and the page says so once and shows it decided.
        page.locator(".waiting", has_text="a review every week").get_by_role("button", name="Confirm").click()
        page.wait_for_url(f"{fleet}&done=confirm")
        expect(page.locator("main .notice").first).to_contain_text("Confirmed: it is a decision now.")
        expect(page.locator(".decisions > article h4").first).to_have_text("a review every week")
        page.screenshot(path=str(shots / "decisions-confirmed.png"), full_page=True)

        # At phone width the pair stacks and nothing scrolls sideways.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(fleet)
        _fits(page, ".decisions", ".waiting.paired")
        page.screenshot(path=str(shots / "decisions-390.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 900})
        page.click('[data-action="theme"]')
        page.click('[data-action="theme"]')
        page.reload()
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        page.screenshot(path=str(shots / "decisions-dark.png"), full_page=True)
        assert not errors, errors
        browser.close()

        # Without JavaScript the picks are plain form posts and work the same.
        again = client.call("POST", "/api/session", token=token, at=port)
        assert again is not None
        scriptless = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
        bare = scriptless.new_context(java_script_enabled=False, viewport={"width": 1280, "height": 900})
        plain = bare.new_page()
        plain.goto(f"{again.body['url']}&next=/briefs")
        plain.wait_for_url(f"{base}/briefs")
        plain.goto(fleet)
        plain.get_by_role("button", name="The old one stands").click()
        plain.wait_for_url(f"{fleet}&done=stand")
        expect(plain.locator(".decisions details.folded summary", has_text="it stands")).to_have_count(1)
        plain.screenshot(path=str(shots / "decisions-no-script.png"), full_page=True)
        scriptless.close()


@pytest.fixture
def day_root(tmp_path: Path) -> Path:
    """The invented day vault: seven items, a meeting with its points, two streams."""
    return day_vault(tmp_path).root


@pytest.fixture
def day_service(day_root: Path) -> Iterator[tuple[int, str]]:
    yield from _serve(day_root)


def _day_page(p: Any, service: tuple[int, str], errors: list[str], path: str = "/ledger") -> tuple[Any, Page, str]:
    """A browser signed in to the service, on a day page, collecting what goes wrong."""
    port, token = service
    link = client.call("POST", "/api/session", token=token, at=port)
    assert link is not None
    browser = p.chromium.launch(channel=None if BROWSER == "chromium" else BROWSER)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("requestfailed", lambda r: errors.append(f"request failed: {r.method} {r.url} ({r.failure})"))
    page.on("response", lambda r: errors.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None)
    base = f"http://127.0.0.1:{port}"
    page.goto(f"{link.body['url']}&next={quote(path)}")
    page.wait_for_url(f"{base}{path}")
    return browser, page, base


def test_the_ledger_s_keys_in_a_browser(day_service: tuple[int, str], tmp_path: Path) -> None:
    """The keys move the selection and the panel with it, a verdict's key writes and moves on, u undoes, and a verdict
    over ticked rows sets it on all of them, then clears it once all hold it (DR-0661, DR-0662)."""
    shots = Path(os.environ.get("DOCKET_TEST_SCREENSHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser, page, base = _day_page(p, day_service, errors)
        expect(page.locator("h1")).to_have_text("Ledger")
        expect(page.locator(".day-group-head").first).to_contain_text("Overdue")

        # j selects the first row and Enter opens it beside the list; j then carries the panel to the next row.
        page.keyboard.press("j")
        page.keyboard.press("Enter")
        expect(page.locator("#panel [data-item]")).to_have_attribute("data-item", "A-0001")
        page.keyboard.press("j")
        expect(page.locator("#panel [data-item]")).to_have_attribute("data-item", "A-0002")
        expect(page.locator("#row-A-0002")).to_have_class(re.compile(r"\bopen\b"))
        page.wait_for_url(f"{base}/ledger?open=A-0002")
        page.screenshot(path=str(shots / "ledger.png"), full_page=True)

        # 2 calls it claude, and the selection and the panel go on to the next row; u undoes it, and offers Redo.
        page.keyboard.press("2")
        page.wait_for_url(re.compile(r"/ledger\?open=A-0003&done="))
        expect(page.locator(".day-notice")).to_contain_text("A-0002: triage: claude")
        expect(page.locator("#row-A-0002 .tri")).to_have_class(re.compile(r"t-claude"))
        page.keyboard.press("u")
        expect(page.locator(".day-notice")).to_contain_text("Undone.")
        expect(page.locator(".day-notice button")).to_have_text("Redo")
        expect(page.locator("#row-A-0002 .tri")).not_to_have_class(re.compile(r"t-claude"))

        # Two rows ticked, one already chase: 4 sets chase on both; ticked again, 4 clears it on both.
        page.click("#panel .close")
        for ref in ("A-0001", "A-0003"):
            page.check(f"#row-{ref} .tick")
        expect(page.locator("#act")).to_be_visible()
        page.screenshot(path=str(shots / "ledger-ticked.png"), full_page=True)
        page.keyboard.press("4")
        expect(page.locator(".day-notice")).to_contain_text("A-0003: triage: chase")
        expect(page.locator("#row-A-0003")).to_have_attribute("data-triage", "chase")
        for ref in ("A-0001", "A-0003"):
            page.check(f"#row-{ref} .tick")
        expect(page.locator('#act .verdict[data-verdict="chase"]')).to_have_attribute("value", "triage:")
        page.keyboard.press("4")
        expect(page.locator(".day-notice")).to_contain_text("A-0001: triage cleared")
        expect(page.locator("#row-A-0001")).to_have_attribute("data-triage", "")

        # At phone width the open item covers the list and nothing scrolls sideways.
        page.goto(f"{base}/ledger?open=A-0003")
        page.set_viewport_size({"width": 390, "height": 844})
        _fits(page, "#panel .close", "#f-A-0003-title")
        page.screenshot(path=str(shots / "ledger-390.png"), full_page=True)
        assert not errors, errors
        browser.close()


def test_a_field_saves_on_change_in_a_browser(day_service: tuple[int, str]) -> None:
    """Notes typed and left by a click on another row are saved first, the panel waiting for the save; a select saves
    as it changes; a save the writer refuses keeps its text, marked unsaved, with the writer's words (DR-0661)."""
    errors: list[str] = []
    with sync_playwright() as p:
        browser, page, base = _day_page(p, day_service, errors, "/ledger?open=A-0003")
        page.fill("#f-A-0003-notes", "Call the venue on Monday")
        page.click("#row-A-0001 .row-link")
        expect(page.locator("#panel [data-item]")).to_have_attribute("data-item", "A-0001")
        page.click("#row-A-0003 .row-link")
        expect(page.locator("#f-A-0003-notes")).to_have_value("Call the venue on Monday")
        expect(page.locator("#history-A-0003")).to_contain_text("notes edited")

        page.select_option("#f-A-0003-kind", "task")
        expect(page.locator("#history-A-0003")).to_contain_text("kind: task")
        expect(page.locator("#row-A-0003 .kind")).to_have_text("task")

        page.fill("#f-A-0003-title", "")
        page.press("#f-A-0003-title", "Enter")
        expect(page.locator("#field-A-0003-title .field-problem")).to_have_text("title can't be cleared")
        expect(page.locator("#field-A-0003-title")).to_have_attribute("data-unsaved", "")
        expect(page.locator("#row-A-0003 .title")).to_have_text("Pick the venue")
        assert page.url == f"{base}/ledger?open=A-0003"
        # The refused save's 400, and the console's word on it, are the one failure the page expects.
        assert [e for e in errors if "400" not in e] == [], errors
        browser.close()


def test_a_tick_holds_the_poll_and_a_write_from_the_cli_reloads_once_it_is_cleared(
    day_service: tuple[int, str], day_root: Path
) -> None:
    """While a row is ticked the poll holds back, so a write from the CLI reloads nothing past the 5 seconds; once the
    tick is cleared, the next poll reloads the page with it (DR-0663)."""
    errors: list[str] = []
    with sync_playwright() as p:
        browser, page, _ = _day_page(p, day_service, errors)
        page.check("#row-A-0001 .tick")
        expect(page.locator("#act")).to_be_visible()
        page.evaluate("window.notReloaded = true")
        change = {"op": "set", "item": "A-0005", "fields": {"title": "Quote from the new caterer"}}
        store.write(Vault.open(day_root), [change], by="user", via="cli")
        page.wait_for_timeout(6_500)
        assert page.evaluate("window.notReloaded === true")
        assert page.is_checked("#row-A-0001 .tick")
        expect(page.locator("#row-A-0005 .title")).to_have_text("Quote from the caterer")

        page.keyboard.press("Escape")
        expect(page.locator("#row-A-0005 .title")).to_have_text("Quote from the new caterer", timeout=12_000)
        assert page.evaluate("window.notReloaded === undefined")
        assert not errors, errors
        browser.close()


def test_a_drop_s_why_and_a_snooze_s_day_take_the_focus_in_a_browser(
    day_service: tuple[int, str], day_root: Path
) -> None:
    """Delete or Backspace on a row, and a click on its drop, open the drop form with its why focused, so a why typed
    there, an e in it, never presses an item key; s on a row focuses the next working day, and Enter snoozes to it
    (DR-0662)."""
    errors: list[str] = []
    with sync_playwright() as p:
        browser, page, base = _day_page(p, day_service, errors)
        why = page.locator("#panel details.close-form input[name=note]")
        page.keyboard.press("j")
        page.keyboard.press("Backspace")
        expect(why).to_be_focused()
        assert page.url == f"{base}/ledger?open=A-0001&drop=1"

        page.goto(f"{base}/ledger")
        page.keyboard.press("j")
        page.keyboard.press("s")
        working = day_view.next_working_day(today()).isoformat()
        expect(page.locator(f'#panel [data-snooze] button[value="snooze:{working}"]')).to_be_focused()
        page.keyboard.press("Enter")
        page.wait_for_url(re.compile(r"/ledger\?open=A-0002&done="))
        expect(page.locator(".day-notice")).to_contain_text("A-0001: snooze")

        page.goto(f"{base}/ledger")
        before = day_routes.version(day_root)
        page.keyboard.press("j")
        page.keyboard.press("Delete")
        expect(why).to_be_focused()
        page.keyboard.type("replied, see the thread")
        expect(why).to_have_value("replied, see the thread")
        assert page.url == f"{base}/ledger?open=A-0002&drop=1"
        assert day_routes.version(day_root) == before
        page.keyboard.press("Enter")
        page.wait_for_url(re.compile(r"/ledger\?open=A-0003&done="))
        expect(page.locator(".day-notice")).to_contain_text("A-0002: dropped")

        # A click on a row's drop, which shows on its hover, focuses its why too.
        before = day_routes.version(day_root)
        page.hover("#row-A-0004")
        page.click("#row-A-0004 .quick [data-key=Delete]")
        expect(why).to_be_focused()
        page.keyboard.type("we met")
        expect(why).to_have_value("we met")
        assert page.url == f"{base}/ledger?open=A-0004&drop=1"
        assert day_routes.version(day_root) == before
        assert not errors, errors
        browser.close()


def test_space_ticks_and_ctrl_z_undoes_in_a_browser(day_service: tuple[int, str]) -> None:
    """The old tasks app's other keys: Space ticks the selected row, and Ctrl+Z undoes as u does (DR-0662)."""
    errors: list[str] = []
    with sync_playwright() as p:
        browser, page, _ = _day_page(p, day_service, errors)
        page.keyboard.press("j")
        page.keyboard.press(" ")
        expect(page.locator("#row-A-0001 .tick")).to_be_checked()
        expect(page.locator("#act")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#row-A-0001 .tick")).not_to_be_checked()

        page.keyboard.press("2")
        page.wait_for_url(re.compile(r"/ledger\?at=A-0002&done="))
        expect(page.locator(".day-notice")).to_contain_text("A-0001: triage: claude")
        page.keyboard.press("Control+z")
        expect(page.locator(".day-notice")).to_contain_text("Undone.")
        expect(page.locator("#row-A-0001")).to_have_attribute("data-triage", "chase")
        assert not errors, errors
        browser.close()


def test_staying_on_the_leave_guard_keeps_the_form_live_in_a_browser(
    day_service: tuple[int, str], day_root: Path
) -> None:
    """A post that would leave text typed elsewhere unsaved asks first; Stay writes nothing and leaves its buttons live,
    and Leave posts it with no second question (DR-0661)."""
    errors: list[str] = []
    with sync_playwright() as p:
        browser, page, base = _day_page(p, day_service, errors, "/ledger?open=A-0003")
        # Stay, then Leave; a question past those two is answered Leave and counted.
        answers, seen = [False, True], []

        def answer(dialog: Any) -> None:
            seen.append(dialog.type)
            leave = answers.pop(0) if answers else True
            if leave:
                dialog.accept()
            else:
                dialog.dismiss()

        page.on("dialog", answer)
        page.fill(".log-add input[name=note]", "a line not yet added")
        before = day_routes.version(day_root)
        claude = page.locator("#panel .call-form .verdict[data-verdict=claude]")
        claude.click()
        expect(claude).to_be_enabled()
        assert (seen, page.url) == (["confirm"], f"{base}/ledger?open=A-0003")
        assert day_routes.version(day_root) == before
        claude.click()
        page.wait_for_url(re.compile(r"done="))
        assert seen == ["confirm", "confirm"]
        expect(page.locator(".day-notice")).to_contain_text("A-0003: triage: claude")
        assert not errors, errors
        browser.close()


def test_open_and_show_in_folder_keep_the_panel_s_scroll_in_a_browser(
    day_service: tuple[int, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A material opened or shown in its folder comes back to its panel scrolled where it was (DR-0663); the launcher
    is patched, so nothing opens."""
    launched: list[tuple[str, bool]] = []
    monkeypatch.setattr(day_routes, "launch", lambda path, *, reveal: launched.append((Path(path).name, reveal)))
    errors: list[str] = []
    with sync_playwright() as p:
        browser, page, _ = _day_page(p, day_service, errors, "/ledger?open=A-0005")
        page.set_viewport_size({"width": 1440, "height": 520})
        scrolled = "document.getElementById('panel').scrollTop"
        for button, done in (("Open", "opened"), ("Show in folder", "shown")):
            page.locator("#panel .material-open").first.scroll_into_view_if_needed()
            kept = page.evaluate(scrolled)
            assert kept > 0
            page.locator("#panel .material-open").first.get_by_role("button", name=button).click()
            page.wait_for_url(re.compile(rf"done={done}"))
            expect(page.locator(".day-notice")).to_be_visible()
            assert abs(page.evaluate(scrolled) - kept) <= 2
            page.evaluate("document.getElementById('panel').scrollTop = 0")
        assert launched == [("plan.md", False), ("plan.md", True)]
        assert not errors, errors
        browser.close()


def _add(page: Page, base: str, value: str, shot: Path) -> None:
    """A never-store value added through the page: the field, the question, the add, the notice."""
    page.click("#list-people summary")
    page.fill("#add-people", value)
    page.click("#list-people .add-form button")
    expect(page.locator("h1")).to_have_text("Add to people?")
    expect(page.locator(".confirm-value")).to_have_text(value)
    assert value.split(maxsplit=1)[0] not in page.url
    page.screenshot(path=str(shot), full_page=True)
    page.click("text=Add it to people")
    page.wait_for_url(f"{base}/settings?done=listed-people-1")
    expect(page.locator("main")).to_contain_text("Added one value to people")
    expect(page.locator("main")).not_to_contain_text(value)


def _capture(service: tuple[int, str], run: str, n: int, tool: str, result: object) -> None:
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


def _fits(page: Page, *selectors: str) -> None:
    """Nothing scrolls sideways, and each of these sits inside the window."""
    wide = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert wide[0] == wide[1], wide
    for selector in selectors:
        box = page.locator(selector).first.bounding_box()
        assert box is not None, selector
        assert box["x"] >= 0 and box["x"] + box["width"] <= wide[1], (selector, box)
