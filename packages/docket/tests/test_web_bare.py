"""The pages the service writes itself (DR-0793), and what a miss or a failure answers (DR-0792): the sign-in page a
browser without a session gets and the used-link page a used or stale link gets, each plain HTML with no script and a
policy that allows none, naming neither the vault nor the machine; a page path the app has no route for, the app's
own document with 404; a failure on a page path, the plain page that says so with 500, and on the API its JSON; none
of them names a path, an error or a trace."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from docket_testkit import index_template
from docket_webkit import OURS, PORT, TOKEN, a11y_problems, html_problems, parse, signed_in
from httpx2 import Response
from starlette.testclient import TestClient

from docket.service import logs
from docket.service.app import create_app
from docket.service.sessions import Sessions
from docket.service.state import Settings
from docket.web import bare, frontend
from docket.web import sync as sync_routes
from docket.web.display import FAILED

# The vault's folder, named so a page that names it can't miss it; the template is built on the machine "test-box".
FOLDER = "ada-work-notes"
MACHINE = "test-box"
# A path and a query that would show if a page echoed what it was asked.
ASKED = "/nowhere-for-Kovacs?query=Ödön+said"


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


def plain(answer: Response, status: int) -> str:
    """A plain page, checked: its status, its policy and no-store, no script, and the structure's rules."""
    assert answer.status_code == status
    assert answer.headers["Content-Security-Policy"] == bare.CSP
    assert "script-src" not in bare.CSP and "unsafe" not in bare.CSP
    assert answer.headers["Cache-Control"] == "no-store"
    assert not html_problems(answer.text)
    assert not a11y_problems(answer.text)
    return answer.text


def test_the_pages_a_stranger_reaches_name_neither_the_vault_nor_the_machine(stranger: TestClient) -> None:
    sign_in = plain(stranger.get("/search?query=day+rate"), 401)
    used = plain(stranger.get("/session?code=made-up&next=/search?query=x"), 401)
    assert parse(sign_in).find("h1").text() == "Open Docket from a terminal on this machine"
    assert parse(sign_in).find("code").text() == 'docket open "/search?query=day+rate"'
    assert parse(used).find("h1").text() == "This link was used, or is over a minute old"
    assert parse(used).find("code").text() == 'docket open "/search?query=x"'
    for page in (sign_in, used):
        assert FOLDER not in page and MACHINE not in page
        assert "vault" not in page.lower()
        # The app's stylesheet and the icon, both served to a stranger, and nothing else.
        links = [el.attrs["href"] for el in parse(page).find_all("link")]
        assert links == [bare.ICON, bare.STYLESHEET]
        assert all(stranger.get(href).status_code == 200 for href in links)
    assert stranger.get("/static/nope.css").text == "not found"


def test_every_page_path_a_stranger_asks_for_is_the_sign_in_page(stranger: TestClient) -> None:
    for path in ("/", "/today", "/sync?source=memo", "/settings?show=lists", ASKED, "/static", "/staticky"):
        answer = stranger.get(path)
        assert answer.status_code == 401, path
        assert "Open Docket from a terminal on this machine" in answer.text, path
    assert stranger.get("/api/web/settings").status_code == 401
    assert "docket open" not in stranger.get("/api/web/settings").text


def test_a_page_path_the_app_has_no_route_for_is_its_document_with_404(client: TestClient) -> None:
    for path in (ASKED, "/outbox", "/design/more", "/sync/runs", "/settings/never-store"):
        answer = client.get(path)
        assert answer.status_code == 404, path
        assert '<div id="root"></div>' in answer.text
        assert answer.headers["Content-Security-Policy"].startswith("default-src 'none'; script-src 'self';")
        assert answer.headers["Cache-Control"] == "no-store"
        assert "Kovacs" not in answer.text and "Ödön" not in answer.text
    assert client.head("/outbox").status_code == 404
    # The API keeps its JSON, a write to a page path its own, and neither names the path.
    for path in ("/api/nope", "/api/web/nope"):
        missing = client.get(path)
        assert (missing.status_code, missing.json()) == (404, {"detail": "Not Found"}), path
    assert client.post("/outbox", json={}, headers={"Origin": OURS}).json() == {"detail": "Not Found"}
    assert client.post("/sync", json={}, headers={"Origin": OURS}).status_code == 405


def test_a_failure_on_a_page_path_is_the_plain_page_with_no_detail(
    client: TestClient, log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("Kovács Ödön said the price")

    monkeypatch.setattr(frontend, "document", broken)
    for path in ("/today", "/settings?show=lists", ASKED):
        page = plain(client.get(path), 500)
        assert parse(page).find("h1").text() == "Something failed in Docket"
        assert "Kovács" not in page and "Ödön" not in page and "Traceback" not in page and "nowhere" not in page
        assert parse(page).find("a", href="/").text() == "Back to Today"
    api = client.get("/api/web/sync")
    assert api.status_code == 200
    logged = log_file.read_text(encoding="utf-8")
    assert "builtins.RuntimeError" in logged and "Ödön" not in logged


def test_a_failure_on_the_api_is_its_json_with_no_detail(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object) -> object:
        raise RuntimeError("Kovács Ödön")

    monkeypatch.setattr(sync_routes, "gather", broken)
    answer = client.get("/api/web/sync")
    assert (answer.status_code, answer.json()) == (500, {"error": FAILED})


def test_the_failure_page_says_the_words_the_app_says(client: TestClient) -> None:
    page = bare.failed_page()
    assert FAILED.split(". ", 1)[0] in page and FAILED.split(". ", 1)[1] in page
    assert not html_problems(page) and not a11y_problems(page)


def test_the_checks_catch_what_they_look_for() -> None:
    page = bare.sign_in_page("/today")
    assert not html_problems(page)
    assert not a11y_problems(page)
    planted = page.replace('<main id="main"', '<main id="main" style="color: red" onclick="go()"')
    assert {p.split(":")[0] for p in html_problems(planted)} == {"a style attribute", "a handler attribute onclick"}
    planted = page.replace("</body>", '<script src="/static/app/app.js"></script><form></form></body>')
    assert {p.split(":")[0] for p in html_problems(planted)} == {"a script", "a form"}
    planted = page.replace(
        '<link rel="icon"', '<link rel="stylesheet" href="https://cdn.example/x.css"><link rel="icon"'
    )
    assert html_problems(planted) == ["an asset from elsewhere: https://cdn.example/x.css"]
    planted = page.replace('<link rel="icon"', '<link rel="stylesheet" href="/static/gone.css"><link rel="icon"')
    assert html_problems(planted) == ["an asset that isn't served: /static/gone.css"]
    planted = page.replace("<h1>Open Docket from a terminal on this machine</h1>", "").replace('lang="en"', "")
    assert {"the page has no lang", "the page has 0 h1 elements"} <= set(a11y_problems(planted))
    planted = page.replace('href="#main"', 'href="#nowhere"')
    assert a11y_problems(planted) == ["no skip link, or it lands nowhere"]
    assert a11y_problems(page + '<p id="main"></p>') == ["the id 'main' appears twice"]
