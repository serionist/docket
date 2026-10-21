"""The Search page's read, signed in, on the index fixture (DR-0174, DR-0175, DR-0709, DR-0775): the search Claude runs,
the canonical URL for what it read, the facets and chips with their links, how the query was read, the groups with
their marked snippets, the transcript hits' Recording page links, the states without results, and the refusals. The
page itself is the app's document; its links are the URLs these answers carry."""

from __future__ import annotations

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
from docket_webkit import signed_in
from starlette.testclient import TestClient

from docket import index
from docket.index.query import Group, Hit, QueryReading
from docket.service.app import ServiceApp
from docket.service.writer import IndexUpdater
from docket.vault.vault import Vault
from docket.web import views

QUESTIONS: list[dict[str, Any]] = yaml.safe_load((INDEX_FIXTURE / "questions.yaml").read_text(encoding="utf-8"))
TIME_WORDS = ("múlt héten", "last week", "yesterday")
API = "/api/web/search"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


def read(client: TestClient, query: str, status: int = 200, path: str = API) -> dict[str, Any]:
    answer = client.get(f"{path}?{query}" if query else path)
    assert answer.status_code == status, answer.text
    return answer.json()


def hits(found: dict[str, Any]) -> list[dict[str, Any]]:
    return [hit for group in found["results"]["groups"] for hit in group["hits"]]


def listed(found: dict[str, Any]) -> list[str]:
    """The hits the page lists, with the ones collapsed under them, in the page's order."""
    return [loc for hit in hits(found) for loc in (hit["locator"], *hit["also"])]


def facet(found: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    """A facet's items by their label."""
    return {item["label"]: item for f in found["results"]["facets"] if f["key"] == key for item in f["items"]}


def text_of(pieces: list[dict[str, Any]]) -> str:
    return "".join(p["text"] for p in pieces)


def answers(loc: str, expect: str) -> bool:
    return loc == expect or loc.startswith((f"{expect}@", f"{expect}#"))


@pytest.mark.parametrize("question", QUESTIONS, ids=[q["q"] for q in QUESTIONS])
def test_the_page_runs_the_search_claude_runs(client: TestClient, tmp_path: Path, question: dict[str, Any]) -> None:
    shown = listed(read(client, f"query={quote(question['q'])}&k=5"))
    result = index.search(Vault.open(tmp_path / "vault"), question["q"], k=5)
    assert sorted(shown) == sorted(loc for g in result.groups for h in g.hits for loc in (h.locator, *h.also))
    if not any(word in question["q"] for word in TIME_WORDS):
        # A time word's range moves with today; the fixture's own date is what the index tests freeze.
        assert any(answers(loc, question["expect"]) for loc in shown), shown


def test_the_page_is_the_app_s_and_each_read_answers_its_canonical_url(client: TestClient) -> None:
    page = client.get("/search?people=ben-sample&query=Nortwin&sources=calls")
    assert page.status_code == 200
    assert '<div id="root">' in page.text
    found = read(client, "people=ben-sample&query=Nortwin&sources=calls&unknown=kept+out")
    assert found["canonical"] == "/search?query=Nortwin&sources=calls&people=ben-sample"
    hit = "r:calls:rec-0100@01:50-03:20"
    opened = read(client, f"hit={quote(hit)}&query=Nortwin", path=f"{API}/panel")
    assert opened["canonical"] == f"/search?query=Nortwin&hit={hit}"
    assert opened["panel"]["close"] == "/search?query=Nortwin"
    closed = read(client, "query=Nortwin", path=f"{API}/panel")
    assert closed == {"canonical": "/search?query=Nortwin", "panel": None, "waiting": ""}


def test_the_facets_narrow_by_source_and_time_and_favour_people_and_entities(client: TestClient) -> None:
    found = read(client, "query=Nortwin")
    assert [(f["key"], f["title"], f["favours"]) for f in found["results"]["facets"]] == [
        ("sources", "Sources", False),
        ("people", "People", True),
        ("entities", "Entities", True),
        ("time", "Time", False),
    ]
    assert 0 < found["results"]["candidates"] <= 200
    assert facet(found, "sources")["calls"]["url"] == "/search?query=Nortwin&sources=calls"
    ben = facet(found, "people")["Ben Sample"]
    assert (ben["url"], ben["on"]) == ("/search?query=Nortwin&people=ben-sample", False)
    chosen = read(client, "query=Nortwin&people=ben-sample")
    assert chosen["results"]["chips"] == [{"label": "favour Ben Sample", "url": "/search?query=Nortwin"}]
    assert facet(chosen, "people")["Ben Sample"]["on"] is True
    assert facet(found, "entities")["Northwind"]["url"] == "/search?query=Nortwin&entities=northwind"
    month = facet(found, "time")["Oct 2026"]
    assert month["url"] == "/search?query=Nortwin&since=2026-10-01&until=2026-10-31"
    october = read(client, "query=Nortwin&since=2026-10-01&until=2026-10-31")
    assert october["results"]["chips"] == [{"label": "Oct 2026", "url": "/search?query=Nortwin"}]
    chosen_month = facet(october, "time")["Oct 2026"]
    assert (chosen_month["on"], chosen_month["url"]) == (True, "/search?query=Nortwin")


def test_dates_that_arent_a_month_are_chips_of_their_own(client: TestClient) -> None:
    found = read(client, "query=fleet&since=2026-10-02&until=2026-10-20&entities=nobody-known")
    assert [(c["label"], c["url"]) for c in found["results"]["chips"]] == [
        ("favour nobody-known", "/search?query=fleet&since=2026-10-02&until=2026-10-20"),
        ("since 2026-10-02", "/search?query=fleet&entities=nobody-known&until=2026-10-20"),
        ("until 2026-10-20", "/search?query=fleet&entities=nobody-known&since=2026-10-02"),
    ]


def test_the_line_says_how_search_read_the_query(client: TestClient) -> None:
    reading = read(client, "query=Nortwin+the+2026-10-07&people=Zoltan+Nobody")["results"]["reading"]
    assert reading[0].startswith("from 7 Oct 2026 to 7 Oct 2026")
    assert '"nortwin" is Northwind' in reading
    assert "not found as a person or entity: Zoltan Nobody" in reading
    assert read(client, "since=2026-10-08")["results"]["reading"] == ["from 8 Oct 2026 to now"]
    assert read(client, "until=2026-10-07T12:30")["results"]["reading"] == ["from the start to 7 Oct 2026"]


def test_a_group_shows_its_summary_and_each_hit_why_it_was_found(client: TestClient) -> None:
    found = read(client, "query=deadline+with+Ben")
    groups = found["results"]["groups"]
    summary = groups[0]["summary"]
    assert summary["id"] == "minutes:2026-10-07-supplier-call"
    assert summary["url"] == "/search?query=deadline+with+Ben&hit=minutes:2026-10-07-supplier-call"
    assert (groups[0]["kind"], groups[0]["icon"]) == ("recording", "recording")
    every = hits(found)
    assert any(h["stretch"] == "lines 21-22" for h in every)
    marked = {p["text"] for h in every for p in h["snippet"] if p["mark"]}
    assert "deadline" in marked
    assert "Friday" not in marked
    assert any(h["found"] == "some words" for h in every)
    assert any(h["url"].endswith("&lines=21-22") for h in every)


def test_a_transcript_hit_opens_on_the_recording_page_at_its_stretch(client: TestClient) -> None:
    found = hits(read(client, "query=pallet+labels+printer"))
    transcripts = {h["locator"]: h["recording"] for h in found if h["locator"].startswith("r:calls:")}
    assert transcripts
    for loc, link in transcripts.items():
        rid, _, stretch = loc.removeprefix("r:").partition("@")
        assert link == f"/recording?rid={rid}&at={stretch}#at"
    assert all(h["recording"] is None for h in found if not h["locator"].startswith("r:calls:"))
    # The link opens the Recording page with the hit's lines marked, the first of them its target.
    query = next(iter(transcripts.values())).partition("?")[2].partition("#")[0]
    page = client.get(f"/api/web/recording?{query}").json()["view"]
    lines = page["transcripts"][0]["lines"]
    assert [line["target"] for line in lines].count(True) == 1
    assert any(line["mark"] for line in lines)


def test_show_more_asks_for_twenty_more(client: TestClient) -> None:
    found = read(client, "query=Nortwin&k=2")
    assert len(listed(found)) >= 2
    assert found["results"]["more"] == "/search?query=Nortwin&k=22"
    assert read(client, "query=Nortwin&k=22")["results"]["more"] is None


def test_nothing_found_offers_each_filter_to_drop(client: TestClient) -> None:
    found = read(client, "query=zeppelin&sources=chat&entities=nobody-known")["results"]
    assert found["groups"] == []
    assert [d["url"] for d in found["drops"]] == [
        "/search?query=zeppelin&entities=nobody-known",
        "/search?query=zeppelin&sources=chat",
    ]
    plain = read(client, "query=zeppelin")["results"]
    assert (plain["drops"], plain["announce"]) == ([], "nothing found")


def test_the_results_say_what_came_back_for_the_live_region(client: TestClient) -> None:
    found = read(client, "query=Nortwin")["results"]
    assert found["announce"] == f"{found['hits']} hits in {len(found['groups'])} groups"
    assert read(client, "query=cold-rated+model&k=1")["results"]["announce"] == "1 hit in 1 group"


def test_the_start_runs_no_search_and_names_the_index(client: TestClient) -> None:
    found = read(client, "")
    assert found["results"] is None and found["locator"] is None and found["canonical"] == "/search"
    assert found["index_line"]


def test_a_bad_value_is_refused_naming_every_one_that_didn_t_read(client: TestClient) -> None:
    one = read(client, "query=day+rate&sources=chat&since=soon&until=2026-10-31", status=400)
    assert "since is a date or a time" in one["error"]
    both = read(client, "query=x&since=soon&until=later", status=400)
    assert "since is a date or a time" in both["error"] and "until is a date or a time" in both["error"]
    panel = read(client, "hit=r:calls:rec-0100&around=99", status=400, path=f"{API}/panel")
    assert "around is a whole number from 0 to 30" in panel["error"]
    assert read(client, "people=ben-sample&query=")["results"] is not None


def test_a_person_given_by_name_is_one_entry_with_its_id(client: TestClient) -> None:
    def labels(found: dict[str, Any], key: str) -> list[str]:
        return [i["label"] for f in found["results"]["facets"] if f["key"] == key for i in f["items"]]

    found = read(client, "query=Nortwin&people=Ben+Sample")
    assert labels(found, "people").count("Ben Sample") == 1
    ben = facet(found, "people")["Ben Sample"]
    assert (ben["on"], ben["url"]) == (True, "/search?query=Nortwin")
    assert [c["label"] for c in found["results"]["chips"]] == ["favour Ben Sample"]
    assert labels(read(client, "query=Nortwin&people=Ben"), "people").count("Ben Sample") == 1
    assert labels(read(client, "query=fleet&entities=Northwind"), "entities").count("Northwind") == 1


def test_the_index_being_built_is_a_state_that_asks_again(client: TestClient) -> None:
    app = client.app
    assert isinstance(app, ServiceApp)
    app.service.updater = IndexUpdater(lambda: time.sleep(0.5), wait_seconds=0.01)
    found = read(client, "query=fleet")
    assert found["results"] is None
    assert "the index is being built" in found["waiting"]


def test_a_record_s_markup_stays_text(client: TestClient, tmp_path: Path) -> None:
    vault = Vault.open(tmp_path / "vault")
    text = 'pallet <script>alert(1)</script> <img src=x onerror="alert(2)"> javascript:alert(3) <b>bold</b>'
    vault.write_run("chat", [make_record(7, container="chat:C0OPS", text=text)])
    found = hits(read(client, "query=pallet+onerror&sources=chat"))
    hit = next(h for h in found if "1791000007" in h["locator"])
    # The pieces give the words back as written, markup and all: the app shows them as text.
    assert "<script>alert(1)</script>" in text_of(hit["snippet"])
    marked = [p["text"] for p in hit["snippet"] if p["mark"]]
    assert "pallet" in marked and "onerror" in marked
    panel = read(client, f"hit={quote(hit['locator'])}", path=f"{API}/panel")["panel"]
    said = [p["text"] for span in panel["spans"] for p in span["paragraphs"]]
    assert any('<img src=x onerror="alert(2)">' in t for t in said)


def test_a_locator_as_the_query_opens_it_instead_of_searching(client: TestClient) -> None:
    query = "query=r:chat:C0OPS/1791400800.000100"
    found = read(client, query)
    assert (found["results"], found["locator"]) == (None, "r:chat:C0OPS/1791400800.000100")
    panel = read(client, query, path=f"{API}/panel")["panel"]
    assert panel["title"] and panel["locator"] == "r:chat:C0OPS/1791400800.000100"
    assert any("cold-rated" in p["text"] for span in panel["spans"] for p in span["paragraphs"])
    assert read(client, f"{query}&sources=chat")["results"]["groups"]


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


@pytest.mark.parametrize(
    ("text", "marks"),
    [
        ("we kick off, the kick-off, a kickoff; we'll see", ["kick", "off", "kick", "off", "kickoff"]),
        ("", []),
        ("Kickoff", ["Kickoff"]),
        ("no match here", []),
    ],
)
def test_a_snippet_s_pieces_give_its_text_back_with_the_query_s_words_marked(text: str, marks: list[str]) -> None:
    found = views.pieces(text, ["kickoff", "well"])
    assert "".join(p.text for p in found) == text
    assert [p.text for p in found if p.mark] == marks


def test_a_quoted_phrase_marks_its_words(client: TestClient) -> None:
    found = hits(read(client, 'query="day+rate"'))
    assert {p["text"].lower() for h in found for p in h["snippet"] if p["mark"]} >= {"day", "rate"}
