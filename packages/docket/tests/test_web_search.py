from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
import yaml
from docket_testkit import INDEX_FIXTURE, index_template, make_record
from docket_webkit import OURS, a11y_problems, html_problems, parse, signed_in
from starlette.testclient import TestClient

from docket import index
from docket.index.query import Group, Hit, QueryReading
from docket.service.app import ServiceApp
from docket.service.writer import IndexUpdater
from docket.vault.vault import Vault
from docket.web import views

QUESTIONS: list[dict[str, Any]] = yaml.safe_load((INDEX_FIXTURE / "questions.yaml").read_text(encoding="utf-8"))
TIME_WORDS = ("múlt héten", "last week", "yesterday")


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


def page_hits(html: str) -> list[str]:
    """The hits a page lists, with the ones collapsed under them, in the page's order."""
    tree = parse(html)
    out: list[str] = []
    for item in tree.find_all("li", class_="hit"):
        out.append(item.find("a", class_="hit-link").attrs["data-locator"])
        out += [span.text() for span in item.find_all("span", class_="locator")[1:]]
    return out


def answers(loc: str, expect: str) -> bool:
    return loc == expect or loc.startswith((f"{expect}@", f"{expect}#"))


def link_to(html: str, text: str) -> tuple[str, str]:
    """A link's page URL and fragment URL, by the text it shows."""
    found = [a for a in parse(html).find_all("a") if text in a.text()]
    assert found, text
    return found[0].attrs["href"], found[0].attrs["hx-get"]


@pytest.mark.parametrize("question", QUESTIONS, ids=[q["q"] for q in QUESTIONS])
def test_the_page_runs_the_search_claude_runs(client: TestClient, tmp_path: Path, question: dict[str, Any]) -> None:
    html = client.get("/search", params={"query": question["q"], "k": 5}).text
    shown = page_hits(html)
    vault = Vault.open(tmp_path / "vault")
    result = index.search(vault, question["q"], k=5)
    assert sorted(shown) == sorted(loc for g in result.groups for h in g.hits for loc in (h.locator, *h.also))
    if not any(word in question["q"] for word in TIME_WORDS):
        # A time word's range moves with today; the fixture's own date is what the index tests freeze.
        assert any(answers(loc, question["expect"]) for loc in shown), shown


def test_the_page_and_its_fragment_and_the_canonical_url(client: TestClient) -> None:
    page = client.get("/search?people=ben-sample&query=Nortwin&sources=calls")
    assert page.text.startswith("<!doctype html>")
    assert "HX-Push-Url" not in page.headers
    fragment = client.get("/search/results?people=ben-sample&query=Nortwin&sources=calls")
    assert not fragment.text.lstrip().startswith("<!doctype")
    assert 'class="groups"' in fragment.text
    canonical = "/search?query=Nortwin&sources=calls&people=ben-sample"
    assert fragment.headers["HX-Push-Url"] == canonical
    # Asked again from the page it already is: a replace, so history gains no entry.
    again = client.get("/search/results?query=Nortwin&people=ben-sample&sources=calls", headers={
        "HX-Request": "true", "HX-Current-URL": f"{OURS}{canonical}"
    })  # fmt: skip
    assert again.headers["HX-Replace-Url"] == canonical
    assert "HX-Push-Url" not in again.headers
    moved = client.get("/search/results?query=fleet", headers={"HX-Current-URL": f"{OURS}{canonical}"})
    assert moved.headers["HX-Push-Url"] == "/search?query=fleet"
    retry = client.get(
        "/search/results?query=fleet", headers={"HX-Current-URL": f"{OURS}/today", "X-Docket-Retry": "1"}
    )
    assert retry.headers["HX-Replace-Url"] == "/search?query=fleet"


def test_the_panel_fragment_names_the_page_with_the_hit_open(client: TestClient) -> None:
    hit = "r:calls:rec-0100@01:50-03:20"
    answer = client.get(f"/search/panel?query=Nortwin&hit={quote(hit)}")
    assert answer.headers["HX-Push-Url"] == f"/search?query=Nortwin&hit={hit}"
    same = client.get(
        f"/search/panel?query=Nortwin&hit={quote(hit)}",
        headers={"HX-Current-URL": f"{OURS}/search?hit={hit}&query=Nortwin"},
    )
    assert same.headers["HX-Replace-Url"] == f"/search?query=Nortwin&hit={hit}"
    closed = client.get("/search/panel?query=Nortwin")
    assert closed.status_code == 200
    assert closed.text.strip() == ""
    assert closed.headers["HX-Push-Url"] == "/search?query=Nortwin"


def test_the_facets_narrow_by_source_and_time_and_favour_people_and_entities(client: TestClient) -> None:
    html = client.get("/search?query=Nortwin").text
    tree = parse(html)
    titles = [h.text() for h in tree.find_all("h2") if h.attrs.get("id", "").startswith("facet-")]
    assert titles == ["Sources", "People favour", "Entities favour", "Time", "Dates"]
    candidates = parse(html).find("details", class_="facets").find("p", class_="facet-note").text()
    assert re.fullmatch(r"Counts are over the \d+ best matches, not the whole vault\.", candidates)
    url, fragment = link_to(html, "calls")
    assert url == "/search?query=Nortwin&sources=calls"
    assert fragment == "/search/results?query=Nortwin&sources=calls"
    url, _ = link_to(html, "Ben Sample")
    assert url == "/search?query=Nortwin&people=ben-sample"
    chosen = client.get(url).text
    chip = parse(chosen).find("a", class_="chip")
    assert chip.text().startswith("favour Ben Sample")
    assert chip.attrs["href"] == "/search?query=Nortwin"
    assert parse(chosen).find("a", class_="facet-item", aria_current="true").text().startswith("Ben Sample")
    url, _ = link_to(html, "Northwind")
    assert url == "/search?query=Nortwin&entities=northwind"
    url, _ = link_to(html, "Oct 2026")
    assert url == "/search?query=Nortwin&since=2026-10-01&until=2026-10-31"
    month = client.get(url).text
    assert [c.text() for c in parse(month).find_all("a", class_="chip")] == ["Oct 2026 : remove"]
    assert link_to(month, "Oct 2026")[0] == "/search?query=Nortwin"
    carried = {(i.attrs["name"], i.attrs["value"]) for i in parse(chosen).find_all("input", form="search-form")}
    assert ("people", "ben-sample") in carried


def test_dates_that_arent_a_month_are_chips_of_their_own(client: TestClient) -> None:
    html = client.get("/search?query=fleet&since=2026-10-02&until=2026-10-20&entities=nobody-known").text
    chips = [c.text() for c in parse(html).find_all("a", class_="chip")]
    assert chips == ["favour nobody-known : remove", "since 2026-10-02 : remove", "until 2026-10-20 : remove"]
    assert link_to(html, "since 2026-10-02")[0] == "/search?query=fleet&entities=nobody-known&until=2026-10-20"


def test_the_line_says_how_search_read_the_query(client: TestClient) -> None:
    html = client.get("/search?query=Nortwin+the+2026-10-07&people=Zoltan+Nobody").text
    reading = [s.text() for s in parse(html).find("p", class_="reading").find_all("span")]
    assert reading[0].startswith("from 7 Oct 2026 to 7 Oct 2026")
    assert '"nortwin" is Northwind' in reading
    assert "not found as a person or entity: Zoltan Nobody" in reading
    since = parse(client.get("/search?since=2026-10-08").text).find("p", class_="reading").text()
    assert since == "from 8 Oct 2026 to now"
    until = parse(client.get("/search?until=2026-10-07T12:30").text).find("p", class_="reading").text()
    assert until == "from the start to 7 Oct 2026"


def test_a_group_shows_its_summary_and_each_hit_why_it_was_found(client: TestClient) -> None:
    html = client.get("/search?query=deadline+with+Ben").text
    tree = parse(html)
    summary = tree.find("p", class_="summary-line").find("a")
    assert summary.attrs["data-locator"] == "minutes:2026-10-07-supplier-call"
    assert summary.attrs["hx-get"] == "/search/panel?query=deadline+with+Ben&hit=minutes:2026-10-07-supplier-call"
    assert "lines 21-22" in html
    marks = [m.text() for m in tree.find_all("mark")]
    assert "deadline" in marks
    assert "Friday" not in marks
    groups = tree.find_all("li", class_="group")
    assert groups[0].find("span", class_="kind").text() == "recording"
    assert any(b.text() == "some words" for b in tree.find_all("span", class_="badge"))
    doc_hits = [a.attrs["href"] for a in tree.find_all("a", class_="hit-link") if "lines=" in a.attrs["href"]]
    assert any(href.endswith("&lines=21-22") for href in doc_hits), doc_hits


def test_show_more_asks_for_twenty_more(client: TestClient) -> None:
    html = client.get("/search?query=Nortwin&k=2").text
    assert len(page_hits(html)) >= 2
    url, fragment = link_to(html, "Show more")
    assert url.endswith("&k=22")
    assert fragment.startswith("/search/results?")
    assert "Show more" not in client.get(url).text


def test_nothing_found_offers_each_filter_to_drop(client: TestClient) -> None:
    html = client.get("/search?query=zeppelin&sources=chat&entities=nobody-known").text
    assert "Nothing found" in html
    drops = [a.attrs["href"] for a in parse(html).find("div", class_="empty").find_all("a")]
    assert drops == ["/search?query=zeppelin&entities=nobody-known", "/search?query=zeppelin&sources=chat"]
    plain = client.get("/search?query=zeppelin").text
    assert "Fewer words, another spelling" in plain
    assert parse(plain).find("section", class_="results").attrs["data-announce"] == "nothing found"


def test_the_start_runs_no_search_and_says_what_a_query_can_hold(client: TestClient) -> None:
    html = client.get("/search").text
    assert "Find anything you kept" in html
    assert 'class="facets"' not in html
    assert parse(html).find("input", id="query").attrs["value"] == ""


def test_a_bad_date_keeps_the_query_and_the_filters_that_read(client: TestClient) -> None:
    page = client.get("/search?query=day+rate&sources=chat&people=ben-sample&since=soon&until=2026-10-31")
    assert page.status_code == 400
    tree = parse(page.text)
    assert tree.find("input", id="query").attrs["value"] == "day rate"
    assert tree.find("input", name="since").attrs["value"] == "soon"
    assert tree.find("input", name="until").attrs["value"] == "2026-10-31"
    carried = {(i.attrs["name"], i.attrs["value"]) for i in tree.find_all("input", type="hidden")}
    assert {("sources", "chat"), ("people", "ben-sample")} <= carried
    assert "since is a date or a time" in tree.find("div", class_="error").text()
    assert tree.find("details", class_="facets")
    both = client.get("/search/results?query=x&since=soon&until=later").text
    assert "since is a date or a time" in both
    assert "until is a date or a time" in both
    assert parse(both).find("input", name="until").attrs["value"] == "later"


def test_a_person_given_by_name_is_one_entry_with_its_id(client: TestClient) -> None:
    html = client.get("/search?query=Nortwin&people=Ben+Sample").text
    people = parse(html).find("section", aria_labelledby="facet-people")
    labels = [a.find("span", class_="label").text() for a in people.find_all("a")]
    assert labels.count("Ben Sample") == 1
    ben = next(a for a in people.find_all("a") if a.find("span", class_="label").text() == "Ben Sample")
    assert "on" in ben.attrs["class"].split()
    assert ben.attrs["href"] == "/search?query=Nortwin"
    assert [c.text() for c in parse(html).find_all("a", class_="chip")] == ["favour Ben Sample : remove"]
    by_alias = parse(client.get("/search?query=Nortwin&people=Ben").text).find(
        "section", aria_labelledby="facet-people"
    )
    assert [a.find("span", class_="label").text() for a in by_alias.find_all("a")].count("Ben Sample") == 1
    entity = client.get("/search?query=fleet&entities=Northwind").text
    found = parse(entity).find("section", aria_labelledby="facet-entities")
    assert [a.find("span", class_="label").text() for a in found.find_all("a")].count("Northwind") == 1


def test_a_bad_value_is_the_page_s_own_error(client: TestClient) -> None:
    for path in ("/search?query=fleet&since=soon", "/search/results?query=fleet&since=soon"):
        answer = client.get(path)
        assert answer.status_code == 400
        assert "since is a date or a time" in answer.text
        assert answer.headers["Content-Type"].startswith("text/html")
    panel = client.get("/search/panel?hit=r:calls:rec-0100&around=99")
    assert panel.status_code == 400
    assert "around is a whole number from 0 to 30" in panel.text
    refused = client.get("/search?people=ben-sample&query=")
    assert refused.status_code == 200


def test_the_index_being_built_is_a_state_that_asks_again(client: TestClient) -> None:
    app = client.app
    assert isinstance(app, ServiceApp)
    app.service.updater = IndexUpdater(lambda: time.sleep(0.5), wait_seconds=0.01)
    answer = client.get("/search/results?query=fleet")
    assert answer.status_code == 200
    waiting = parse(answer.text).find("div", class_="wait")
    assert "the index is being built" in waiting.text()
    assert waiting.attrs["hx-get"] == "/search/results?query=fleet"
    assert waiting.attrs["hx-trigger"] == "load delay:5s"
    assert waiting.attrs["hx-headers"] == '{"X-Docket-Retry": "1"}'
    assert not html_problems(answer.text)


def test_a_record_s_markup_stays_text(client: TestClient, tmp_path: Path) -> None:
    vault = Vault.open(tmp_path / "vault")
    text = 'pallet <script>alert(1)</script> <img src=x onerror="alert(2)"> javascript:alert(3) <b>bold</b>'
    vault.write_run("chat", [make_record(7, container="chat:C0OPS", text=text)])
    html = client.get("/search?query=pallet+onerror&sources=chat").text
    assert "<script>alert" not in html
    assert "<img src=x" not in html
    assert "<b>bold" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert not html_problems(html)
    assert not a11y_problems(html)
    hit = next(loc for loc in page_hits(html) if "1791000007" in loc)
    panel = client.get("/search/panel", params={"hit": hit}).text
    assert "&lt;img src=x onerror=" in panel
    assert "<img" not in panel
    assert not html_problems(panel)


def test_a_locator_as_the_query_opens_it_instead_of_searching(client: TestClient) -> None:
    html = client.get("/search?query=r:chat:C0OPS/1791400800.000100").text
    assert "Opened" in html
    assert 'class="groups"' not in html
    panel = parse(html).find("section", class_="panel")
    assert panel.find("h2").text()
    assert "cold-rated" in panel.text()
    filtered = client.get("/search?query=r:chat:C0OPS/1791400800.000100&sources=chat").text
    assert 'class="groups"' in filtered


def test_the_reading_line_and_the_group_times_read_plainly() -> None:
    tz = ZoneInfo("Europe/Budapest")
    reading = QueryReading(text="the offer", words=["the", "offer"], absent=["the"], phrases=["day rate"])
    assert views.reading_line(reading, {}, tz) == ["in no chunk: the"]
    assert views.terms(reading) == ["the", "offer", "day", "rate"]
    first = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
    group = Group(key="chat:C0X", kind="thread", title="t", first=first, last=first + timedelta(days=2))
    assert views._when(group, tz) == "7 Oct 2026 10:00 to 9 Oct 2026 10:00"  # pyright: ignore[reportPrivateUsage]
    assert views._when(group.model_copy(update={"first": None}), tz) == ""  # pyright: ignore[reportPrivateUsage]
    hit = Hit(locator="r:chat:C0X/1.000100", kind="message")
    assert views._stretch(hit) == ""  # pyright: ignore[reportPrivateUsage]
    assert views._stretch(Hit(locator="r:mail:m#3", kind="record")) == "\N{PILCROW SIGN} 3"  # pyright: ignore[reportPrivateUsage]
    assert views._stretch(Hit(locator="d", kind="doc", lines=(4, 4))) == "line 4"  # pyright: ignore[reportPrivateUsage]


def test_a_quoted_phrase_marks_its_words(client: TestClient) -> None:
    html = client.get('/search?query="day+rate"').text
    assert {m.text().lower() for m in parse(html).find_all("mark")} >= {"day", "rate"}
