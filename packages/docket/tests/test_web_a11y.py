from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from docket_testkit import index_template
from docket_webkit import a11y_problems, html_problems, parse, signed_in
from starlette.testclient import TestClient

from docket.web import assets, pages

PAGES = [
    "/search",
    "/search?query=Nortwin",
    "/search?query=deadline+with+Ben&people=ben-sample&since=2026-10-01&until=2026-10-31",
    "/search?query=zeppelin&sources=chat",
    "/search?query=fleet&since=soon",
    f"/search?query=Nortwin&hit={quote('r:calls:rec-0100@01:50-03:20')}",
    f"/search?query=Nortwin&hit={quote('r:calls:rec-0100@01:50-03:20')}&verify=1",
    "/search?query=fleet&hit=recording-note:2026-10-13-fleet-review&lines=12",
    "/search?hit=r:calls:rec-9999",
    "/sync",
    "/sync?source=calls&k=40",
    "/sync?run=run-20261015T000000-ffffffffffffffff",
    "/sync?k=0",
    "/staging",
    "/staging?source=calls&state=pending&k=100",
    "/staging?open=" + "0" * 32,
    "/staging?k=0",
    "/curate",
    "/curate?open=abcdef",
    "/curate?open=nope",
    "/settings",
    "/settings?show=lists",
    "/settings?show=nope",
]
FRAGMENTS = [
    "/search/results?query=pallet+labels",
    f"/search/panel?hit={quote('r:mail:<offer-1@northwind.example>#1-4')}",
    "/search/panel?hit=legacy-note:old-northwind-notes",
    "/sync/runs",
    "/sync/runs?k=0",
    "/staging/panel",
    "/staging/panel?open=" + "0" * 32,
    "/staging/panel?open=nope",
    "/curate/panel",
    "/curate/panel?open=abcdef",
    "/curate/panel?open=nope",
]


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


@pytest.mark.parametrize("path", PAGES)
def test_every_page_state_passes_the_checks(client: TestClient, path: str) -> None:
    html = client.get(path).text
    assert not a11y_problems(html), path
    assert not html_problems(html), path
    tree = parse(html)
    assert tree.find("p", id="announce").attrs["aria-live"] == "polite"
    assert tree.find("main").attrs["id"] == "main"
    assert tree.find("nav").attrs["aria-label"] == "Pages"


@pytest.mark.parametrize("path", FRAGMENTS)
def test_every_fragment_passes_the_checks(client: TestClient, path: str) -> None:
    html = client.get(path).text
    assert not a11y_problems(html, whole=False), path
    assert not html_problems(html), path


def test_the_results_say_what_came_back_for_the_live_region(client: TestClient) -> None:
    results = parse(client.get("/search?query=Nortwin").text).find("section", class_="results")
    assert re.fullmatch(r"\d+ hits in \d+ groups", results.attrs["data-announce"])
    single = parse(client.get("/search?query=cold-rated+model&k=1").text).find("section", class_="results")
    assert single.attrs["data-announce"] == "1 hit in 1 group"


@pytest.mark.parametrize("page", sorted(pages.PAGE_KEYS))
def test_the_keys_dialog_lists_every_key_and_the_script_handles_each(client: TestClient, page: str) -> None:
    path = next(p.path for p in pages.PAGES if p.key == page)
    dialog = parse(client.get(path).text).find("dialog", id="keys-dialog")
    listed = [kbd.attrs["data-key"] for kbd in dialog.find_all("kbd") if "data-key" in kbd.attrs]
    assert listed == [key for names, _ in pages.PAGE_KEYS[page] for key in names]
    script = assets.ASSETS["js/docket.js"].data.decode("utf-8")
    for key in listed:
        if key != "Enter":  # a focused link opens on Enter by itself
            assert f'"{key}"' in script, key
