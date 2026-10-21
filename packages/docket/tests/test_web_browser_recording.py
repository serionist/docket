"""The Recording page in a real browser (DR-0545, DR-0712, DR-0776, DR-0777): the start opens a locator at its time; a
summary line's anchor marks the lines it points at and scrolls them into view, the URL holding the rid, the doc and
the time; with the audio stored a line's time plays it from there, through the byte ranges the browser's session asks
for, the URL kept on the line; a held recording shows only that it is held; at phone width nothing scrolls sideways;
axe in both themes; and a long recording opened from Search's panel shows its lines within a quarter second.

It runs only with DOCKET_TEST_BROWSER set, as test_web_browser.py does, and reads axe-core from the web app's
node_modules: `npm ci` in packages/docket/web first. Invented people and a tone made here only.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from browser_kit import AXE, BROWSER, WATCH_CSP, axe, clean, dark, live, opened, sign_in
from docket_testkit import make_config, make_record, traced
from playwright.sync_api import Browser, Page, expect, sync_playwright
from recording_kit import CRANE, HELD, NOTE, TOUR, lines, recording_template, store_tone, with_audio

from docket import index, recall
from docket.vault.config import SourceConfig
from docket.vault.vault import Vault

pytestmark = pytest.mark.skipif(not BROWSER, reason="drives a real browser: DOCKET_TEST_BROWSER=chrome or msedge")

# DR-0668's budget for a page, held in Chrome from a link's click to the lines shown (DR-0712).
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
    return recording_template(tmp_path_factory)


@pytest.fixture
def root(template: Path, tmp_path: Path) -> Path:
    shutil.copytree(template, tmp_path / "vault")
    return tmp_path / "vault"


@pytest.fixture
def service(root: Path) -> Iterator[tuple[int, str]]:
    yield from live(root)


def seeking(request: Any) -> bool:
    """A media element cancels its own range requests as it seeks and reads ahead: that isn't a failure."""
    return "/recording/audio" in request.url and request.failure == "net::ERR_ABORTED"


def marked(page: Page) -> list[str]:
    return [str(t) for t in page.locator("li[data-mark] a").all_inner_texts()]


def test_the_start_opens_a_locator_and_an_anchor_marks_the_lines_it_points_at(
    browser: Browser, service: tuple[int, str]
) -> None:
    port, token = service
    page, errors = opened(browser, port, token, "/recording")
    page.get_by_label("Rid or locator").fill(f"r:{CRANE}@01:40")
    page.get_by_role("button", name="Open").click()
    expect(page.get_by_role("heading", level=1)).to_have_text("Harbour crane check")
    expect(page.locator("li[data-mark]").first).to_contain_text("01:40")
    expect(page.get_by_role("region", name="Audio")).to_contain_text("No audio stored yet")
    assert axe(page) == []

    note = f"/recording?rid={CRANE}&doc={NOTE}"
    page.goto(page.url.split("/recording")[0] + note)
    page.get_by_role("link", name="@00:40-01:39").click()
    expect(page).to_have_url(re.compile(re.escape(f"{note}&at=00:40-01:39#at") + "$"))
    expect(page.locator("li[data-mark]")).to_have_count(3)
    expect(page.locator("[data-cited]")).to_contain_text("west crane needs a new brake pad")
    expect(page.locator("li#at")).to_be_in_viewport()
    dark(page)
    assert axe(page) == []
    clean(page, errors)


def test_a_line_s_time_plays_the_stored_audio_from_there(
    browser: Browser, service: tuple[int, str], root: Path
) -> None:
    with_audio(Vault.open(root))
    port, token = service
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.add_init_script(WATCH_CSP)
    context.add_init_script(path=str(AXE))
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("requestfailed", lambda r: None if seeking(r) else errors.append(f"request failed: {r.url}"))
    page.goto(sign_in(port, token, f"/recording?rid={TOUR}"))
    expect(page.locator("table tbody tr")).to_have_count(4)
    # The cut stretch is one row, cut in both, and never plays.
    expect(page.locator("table tbody tr:nth-child(3) li[data-cut]")).to_have_count(2)
    tour = page.url
    page.locator("table tbody tr:nth-child(2) td:first-child a").click()
    page.wait_for_function(
        "() => { const p = document.querySelector('audio'); return p && !p.paused && p.currentTime >= 20; }"
    )
    expect(page).to_have_url(f"{tour}&at=00:20#at")
    expect(page.locator("li[data-playing]").first).to_contain_text("The new gate opens on Friday.")
    assert page.evaluate("document.querySelector('audio').getAttribute('src')").endswith(f"/recording/audio?rid={TOUR}")
    assert axe(page) == []
    assert errors == [] and page.evaluate("window.__csp") == []
    context.close()


def test_a_held_recording_shows_only_that_it_is_held(browser: Browser, service: tuple[int, str]) -> None:
    port, token = service
    page, errors = opened(browser, port, token, f"/recording?rid={HELD}")
    expect(page.get_by_text(f"{HELD} is held")).to_be_visible()
    expect(page.get_by_role("link", name="Held plaud recordings on the Staging page")).to_have_attribute(
        "href", "/staging?source=plaud"
    )
    expect(page.locator("main li")).to_have_count(0)
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    clean(page, errors)


def test_at_phone_width_the_columns_stack_and_the_marked_line_comes_into_view(
    browser: Browser, service: tuple[int, str]
) -> None:
    port, token = service
    narrow: dict[str, Any] = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True}
    page, errors = opened(browser, port, token, f"/recording?rid={CRANE}&doc={quote(NOTE)}", **narrow)
    page.get_by_role("link", name="@02:40").click()
    expect(page).to_have_url(re.compile(r"&at=02:40#at$"))
    expect(page.locator("li#at")).to_be_in_viewport()
    assert axe(page) == []
    dark(page)
    assert axe(page) == []
    clean(page, errors)


# -- a long recording (DR-0668, DR-0712) -----------------------------------------------------------------------------

LONG = "plaud:rec-long"
SAID = [("Ada Example", "The crane schedule for the week, line by line."), ("Ben Sample", "Noted, and the next.")]


@pytest.fixture(scope="module")
def long_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A two-hour call of 360 turns, one every 20 seconds, its transcript and a note on it, indexed."""
    config = make_config()
    config.sources = {"plaud": SourceConfig(sensitivity="mixed")}
    vault = Vault.init(tmp_path_factory.mktemp("recording-perf") / "vault", config, "test-box")
    under = {"source": "plaud", "container": LONG, "thread": LONG, "author": None}
    said = lines(SAID * 180)
    vault.write_run(
        "plaud",
        [
            make_record(rid=LONG, kind="recording", title="Crane schedule", text="", **under),
            make_record(
                rid=f"{LONG}/t", kind="transcript", segments=said, text="\n".join(s["text"] for s in said), **under
            ),
        ],
    )
    body = "\n".join(f"- The schedule, part {n}. @{n * 2:02d}:00" for n in range(1, 30))
    head = f"---\nid: recording-note:2026-10-14-crane-schedule\ntype: recording-note\ncovers:\n  - {LONG}/t\n"
    recall.doc_put(vault, f"{head}by: claude-test\n---\n\n# Crane schedule\n\n{body}\n")
    index.update(vault)
    return vault.root


@pytest.fixture
def long_service(long_root: Path) -> Iterator[tuple[int, str]]:
    yield from live(long_root)


@pytest.fixture
def played_service(long_root: Path, tmp_path: Path) -> Iterator[tuple[int, str]]:
    """The long call in a vault of this test's own, with a tone stored as its audio."""
    shutil.copytree(long_root, tmp_path / "vault")
    store_tone(Vault.open(tmp_path / "vault"), LONG, 30.0)
    yield from live(tmp_path / "vault")


def test_playing_a_far_line_keeps_it_in_view(browser: Browser, played_service: tuple[int, str]) -> None:
    """A page opened at a line, as a link from Search or Claude opens it: playing a line far below keeps that line in
    view, and the page never jumps back to the line it opened at."""
    port, token = played_service
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    context.add_init_script(WATCH_CSP)
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("requestfailed", lambda r: None if seeking(r) else errors.append(f"request failed: {r.url}"))
    page.goto(sign_in(port, token, f"/recording?rid={LONG}&at=02:20#at"))
    expect(page.locator("li#at")).to_contain_text("02:20")
    expect(page.locator("li#at")).to_be_in_viewport()
    far = page.get_by_role("link", name="1:06:40", exact=True)
    far.scroll_into_view_if_needed()
    far.click()
    expect(page).to_have_url(re.compile(r"&at=1:06:40#at$"))
    expect(page.locator("li#at")).to_contain_text("1:06:40")
    expect(page.locator("li[data-playing]")).to_contain_text("1:06:40")
    page.wait_for_timeout(300)
    expect(page.locator("li#at")).to_be_in_viewport()
    expect(page.locator("li", has_text="02:20").first).not_to_be_in_viewport()
    assert errors == [] and page.evaluate("window.__csp") == []
    context.close()


# From a click on the panel's link to the Recording page to the recording's last line in the page, timed in the page.
OPEN_FROM_A_LINK = """async () => {
  const link = document.querySelector('[data-panel-body] a[href^="/recording"]');
  const started = performance.now();
  link.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
  await new Promise((done) => {
    const look = () => (document.querySelectorAll('main ol li').length >= 360 ? done() : requestAnimationFrame(look));
    look();
  });
  return (performance.now() - started) / 1000;
}"""


# On a long recording: out of gate os's selection, while the browser jobs run it by file, as the others' (DR-0757).
@pytest.mark.nightly
@pytest.mark.perf
def test_a_long_recording_opens_within_its_budget(browser: Browser, long_service: tuple[int, str]) -> None:
    """A two-hour call of 360 turns, opened from the Recording page's own link on Search's panel, shows every line
    within a quarter second: once to warm the service's caches, then the best of three, each from a page loaded
    afresh."""
    port, token = long_service
    hit = f"r:{LONG}/t@00:20"
    page, errors = opened(browser, port, token, f"/search?hit={quote(hit)}")
    base = page.url.split("/search")[0]
    took: list[float] = []
    for _ in range(4):
        page.goto(f"{base}/search?hit={quote(hit)}")
        link = page.get_by_role("link", name="On the Recording page").first
        expect(link).to_be_visible()
        page.wait_for_load_state("networkidle")
        took.append(float(page.evaluate(OPEN_FROM_A_LINK)))
        expect(page).to_have_url(re.compile(r"/recording\?"))
    best = min(took[1:])
    runs = ", ".join(f"{t * 1000:.0f}" for t in took[1:])
    print(f"a recording of 360 lines from a link: {best * 1000:.0f} ms ({runs} ms)")
    if not traced():
        assert best < PAGE_SECONDS, f"the recording took {best:.3f} s from its link, over {PAGE_SECONDS} s"
    clean(page, errors)
