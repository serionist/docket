from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from docket_testkit import index_template
from docket_webkit import OURS, PORT, TOKEN, a11y_problems, html_problems, parse, signed_in
from starlette.testclient import TestClient

from docket.service import calls, logs
from docket.service.app import create_app
from docket.service.sessions import Sessions
from docket.service.state import Settings
from docket.web import pages, panel
from docket.web.templating import PAGE_CSP

# The vault's folder, named so a page that names it can't miss it; the template is built on the machine "test-box".
FOLDER = "ada-work-notes"
MACHINE = "test-box"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


@pytest.fixture
def stranger(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    """A browser without a session, on a vault whose folder has a name of its own."""
    shutil.copytree(template, tmp_path / FOLDER)
    app = create_app(Settings(port=PORT, vault=tmp_path / FOLDER, token=lambda: TOKEN), sessions=Sessions())
    with TestClient(app, base_url=OURS, follow_redirects=False) as found:
        yield found


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    root = logging.getLogger()
    levels = {name: logging.getLogger(name).level for name in ("", "docket", *logs.LIBRARIES)}
    path = tmp_path / "log" / "service.log"
    handler = logs.configure(path)
    yield path
    root.removeHandler(handler)
    handler.close()
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


def test_the_start_moves_on_to_search(client: TestClient) -> None:
    answer = client.get("/")
    assert answer.status_code == 303
    assert answer.headers["location"] == "/search"


def test_the_navigation_has_a_slot_for_every_page_and_links_the_built_ones(client: TestClient) -> None:
    page = parse(client.get("/search").text)
    nav = page.find("nav", class_="nav")
    links = nav.find_all("a")
    built = [p for p in pages.PAGES if p.built]
    assert [(a.attrs["href"], a.attrs.get("aria-current")) for a in links] == [
        (p.path, "page" if p.key == "search" else None) for p in built
    ]
    slots = nav.find_all("span", class_="nav-slot")
    assert len(slots) == len(pages.PAGES) - len(built)
    assert len(pages.PAGES) == 12
    assert [s.attrs["title"].split(":")[0] for s in slots[:5]] == ["Today", "Queue", "Ledger", "Meetings", "Outbox"]
    # No number beside a slot, which would read as a count: one muted "later" per group, the phase in the tooltip and
    # the screen-reader text.
    assert [li.text() for li in nav.find_all("li", class_="nav-later")] == ["later"] * 3
    assert all(li.attrs["aria-hidden"] == "true" for li in nav.find_all("li", class_="nav-later"))
    assert not any(ch.isdigit() for s in slots for ch in "".join(c for c in s.children if isinstance(c, str)))
    curate = next(s for s in slots if s.text().startswith("Curate"))
    assert "arrives in phase 1" in curate.text()
    assert curate.attrs["title"] == "Curate: the weekly batch of proposals; arrives in phase 1"
    finding = nav.find_all("ul", class_="nav-group")[1]
    assert [c.text() for c in finding.find_all("li")][:2] == ["Search", "later"]


@pytest.mark.parametrize(
    "path",
    [
        "/search",
        "/search?query=fleet",
        "/search/results?query=fleet",
        "/search/panel",
        "/sync",
        "/sync/runs",
        "/staging",
        "/staging/panel",
    ],
)
def test_every_page_and_fragment_carries_the_page_csp_and_no_store(client: TestClient, path: str) -> None:
    answer = client.get(path)
    assert answer.status_code == 200
    assert answer.headers["Content-Security-Policy"] == PAGE_CSP
    assert answer.headers["Cache-Control"] == "no-store"
    assert "unsafe" not in PAGE_CSP
    assert not html_problems(answer.text)


def test_a_page_that_isnt_there_is_html_and_the_api_keeps_json(client: TestClient) -> None:
    page = client.get("/curate")
    assert page.status_code == 404
    assert "There is no page here" in page.text
    assert page.headers["Content-Security-Policy"] == PAGE_CSP
    api = client.get("/api/nope")
    assert api.status_code == 404
    assert api.json() == {"detail": "Not Found"}
    assert client.post("/search", headers={"Origin": OURS}).status_code == 405


def test_a_failure_answers_the_error_page_and_the_log_keeps_no_words(
    client: TestClient, log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> object:
        try:
            raise KeyError("Kovács Ödön said the price")
        except KeyError as e:
            raise RuntimeError("Kovács Ödön") from e

    monkeypatch.setattr(calls, "search", broken)
    page = client.get("/search?query=fleet")
    assert page.status_code == 500
    assert page.text.startswith("<!doctype html>")
    assert "Something failed in Docket" in page.text
    # A fragment answers with a fragment: its own notice, never a whole page swapped into the results.
    fragment = client.get("/search/results?query=fleet&sources=chat&since=2026-10-01")
    assert fragment.status_code == 500
    assert "<html" not in fragment.text
    assert "Something failed in Docket" in parse(fragment.text).find("div", class_="error").text()
    carried = {(i.attrs["name"], i.attrs["value"]) for i in parse(fragment.text).find_all("input", form="search-form")}
    assert {("sources", "chat"), ("since", "2026-10-01")} <= carried
    monkeypatch.setattr(panel, "build", broken)
    in_panel = client.get("/search/panel?hit=r:calls:rec-0100")
    assert in_panel.status_code == 500
    assert "<html" not in in_panel.text
    assert "Something failed in Docket" in parse(in_panel.text).find("section", class_="panel").text()
    for answer in (page, fragment, in_panel):
        assert "Ödön" not in answer.text
    logged = log_file.read_text(encoding="utf-8")
    assert "builtins.RuntimeError" in logged
    assert "builtins.KeyError" in logged
    assert "GET /search/results failed" in logged
    assert "Ödön" not in logged


def test_the_footer_names_the_vault_and_the_machine_and_the_index(client: TestClient) -> None:
    client.get("/search?query=fleet")
    footer = parse(client.get("/search").text).find("footer").text()
    assert f"vault on {MACHINE}" in footer
    assert "chunks from 22 records and 7 docs" in footer


def test_without_a_vault_the_page_says_so(tmp_path: Path) -> None:
    sessions = Sessions()
    app = create_app(Settings(port=PORT, vault=None, token=lambda: TOKEN), sessions=sessions)
    with TestClient(app, base_url=OURS) as client:
        started = sessions.redeem(sessions.new_code())
        assert started is not None
        client.cookies.set("docket_session", started)
        page = client.get("/search?query=fleet")
        assert page.status_code == 400
        assert "the service has no vault" in page.text
        assert "no vault" in parse(page.text).find("footer").text()
        assert "the index is being brought up to date" in page.text
        panel = client.get("/search/panel?hit=r:calls:rec-0100")
        assert panel.status_code == 400
        assert "the service has no vault" in panel.text


def test_the_pages_a_stranger_reaches_name_neither_the_vault_nor_the_machine(
    stranger: TestClient, template: Path, tmp_path: Path
) -> None:
    sign_in = stranger.get("/search?query=day+rate")
    used = stranger.get("/session?code=made-up&next=/search?query=x")
    assert sign_in.status_code == used.status_code == 401
    assert 'docket open "/search?query=day+rate"' in parse(sign_in.text).find("code").text()
    assert 'docket open "/search?query=x"' in parse(used.text).find("code").text()
    assert stranger.get("/static/nope.css").text == "not found"
    with signed_in(template, tmp_path / "other") as client:
        missing = client.get("/curate")
        shell = client.get("/search")
    for answer in (sign_in, used, missing):
        assert FOLDER not in answer.text
        assert "vault" not in answer.text.lower()
        assert MACHINE not in answer.text
        assert not html_problems(answer.text)
        assert not a11y_problems(answer.text)
        assert "/static/css/tokens.css" in answer.text
    assert MACHINE in shell.text
