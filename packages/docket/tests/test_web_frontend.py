"""The React app's build as the service serves it (DR-0703 to DR-0706): its document on each of its routes, with a
nonce of its own each time and the CSP that names it, its files under /static/app/, and the build stamp the gate holds
to the sources."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path

import pytest
from docket_testkit import index_template
from docket_webkit import OURS, PORT, TOKEN, signed_in
from starlette.testclient import TestClient

from docket.service.app import create_app
from docket.service.sessions import Sessions
from docket.service.state import Settings
from docket.web import frontend

NONCE = re.compile(r'<meta name="csp-nonce" content="([A-Za-z0-9+/=]+)"')
APP = Path(str(files("docket.web").joinpath("app")))


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


@pytest.fixture
def stranger(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    shutil.copytree(template, tmp_path / "strangers")
    app = create_app(Settings(port=PORT, vault=tmp_path / "strangers", token=lambda: TOKEN), sessions=Sessions())
    with TestClient(app, base_url=OURS, follow_redirects=False) as found:
        yield found


def test_each_route_of_the_app_answers_its_document_with_a_nonce_and_the_csp_naming_it(client: TestClient) -> None:
    assert frontend.BUILD.routes == ("/design", "/today", "/queue", "/ledger", "/meetings", "/search", "/recording")
    first, second = client.get("/design?part=tokens"), client.get("/ledger?open=A-0012&q=Ada%27s+offer")
    nonces = []
    for answer in (first, second):
        assert answer.status_code == 200
        assert answer.headers["content-type"].startswith("text/html")
        assert answer.headers["cache-control"] == "no-store"
        found = NONCE.search(answer.text)
        assert found is not None
        nonce = found.group(1)
        assert len(nonce) == 24, "16 bytes, base64"
        assert answer.headers["content-security-policy"] == frontend.csp(nonce)
        assert "script-src 'self';" in answer.headers["content-security-policy"]
        assert f"style-src 'self' 'nonce-{nonce}';" in answer.headers["content-security-policy"]
        assert "unsafe" not in answer.headers["content-security-policy"]
        assert '<script type="module" crossorigin src="/static/app/app.js">' in answer.text
        assert "<script>" not in answer.text and "style=" not in answer.text
        nonces.append(nonce)
    assert nonces[0] != nonces[1], "a nonce of its own for each response"


@pytest.mark.parametrize(("cookie", "theme"), [(None, "system"), ("dark", "dark"), ("light", "light"), ("x", "system")])
def test_the_document_carries_the_theme_the_cookie_chose(client: TestClient, cookie: str | None, theme: str) -> None:
    if cookie is not None:
        client.cookies.set("docket_theme", cookie)
    page = client.get("/design").text
    assert f'<html lang="en" data-theme="{theme}">' in page


def test_a_browser_without_a_session_gets_the_sign_in_page_naming_the_route(stranger: TestClient) -> None:
    answer = stranger.get("/design?part=tokens")
    assert answer.status_code == 401
    assert "docket open" in answer.text and "csp-nonce" not in answer.text


def test_the_build_s_files_are_served_as_static_files_are_and_its_document_only_through_a_route(
    client: TestClient, stranger: TestClient
) -> None:
    script = stranger.get("/static/app/app.js")
    assert script.status_code == 200, "static files need no session"
    assert script.headers["content-type"] == "text/javascript; charset=utf-8"
    assert script.headers["cache-control"] == "no-cache"
    again = stranger.get("/static/app/app.js", headers={"If-None-Match": script.headers["etag"]})
    assert again.status_code == 304
    assert stranger.get("/static/app/app.css").headers["content-type"] == "text/css; charset=utf-8"
    font = stranger.get("/static/app/atkinson-hyperlegible-next-latin-wght-normal.woff2")
    assert font.status_code == 200 and font.headers["content-type"] == "font/woff2"
    assert stranger.get("/static/app/routes.json").headers["content-type"] == "application/json"
    assert stranger.get("/static/app/licences.txt").status_code == 200
    assert client.get("/static/app/index.html").status_code == 404
    assert client.get("/static/app/nothing.js").status_code == 404
    assert client.get("/static/icon.svg").status_code == 200, "the Jinja pages' files stay where they are"


def test_a_path_the_app_doesn_t_list_is_still_the_server_s_own(client: TestClient) -> None:
    assert client.get("/design/more").status_code == 404
    page = client.get("/sync")
    assert page.status_code == 200 and '<div id="root">' not in page.text


def test_the_build_lists_its_licences_routes_and_stamp(tmp_path: Path) -> None:
    licences = (APP / "licences.txt").read_text(encoding="utf-8")
    for name in ("react", "@tanstack/react-router", "shadcn/ui", "@fontsource-variable/atkinson-hyperlegible-next"):
        assert f"== {name} " in licences
    assert json.loads((APP / "routes.json").read_text(encoding="utf-8")) == list(frontend.BUILD.routes)
    assert re.fullmatch(r"[0-9a-f]{64}", frontend.BUILD.inputs)
    assert "index.html" not in frontend.BUILD.files and "app.js" in frontend.BUILD.files


def test_a_build_whose_document_lacks_a_mark_stops_the_service(tmp_path: Path) -> None:
    shutil.copytree(APP, tmp_path / "app")
    assert frontend.load(tmp_path / "app").routes == frontend.BUILD.routes
    document = tmp_path / "app" / "index.html"
    document.write_text(document.read_text(encoding="utf-8").replace(frontend.NONCE_MARK, ""), encoding="utf-8")
    with pytest.raises(ValueError, match="docket-nonce"):
        frontend.load(tmp_path / "app")
