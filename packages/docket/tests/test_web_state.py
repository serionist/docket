from __future__ import annotations

import pytest
from starlette.datastructures import QueryParams

from docket.web.state import SearchState, StateError, is_locator


def read(query: str) -> SearchState:
    return SearchState.read(QueryParams(query))


def test_a_url_read_and_written_back_is_canonical() -> None:
    given = "until=2026-10-31&people=cleo-test&query=day++rate&sources=mail&people=ben-sample&since=2026-10-01&x=1"
    state = read(given)
    assert state.query == "day rate"
    assert state.people == ("ben-sample", "cleo-test")
    assert state.url() == (
        "/search?query=day+rate&sources=mail&people=ben-sample&people=cleo-test&since=2026-10-01&until=2026-10-31"
    )
    assert read(state.url().split("?", 1)[1]) == state


def test_defaults_are_left_out_and_others_kept() -> None:
    assert SearchState().url() == "/search"
    assert read("k=20&around=1&verify=0&query=").url() == "/search"
    state = read("query=fleet&k=40&hit=r:calls:rec-0101@00:00-01:30&around=2&verify=1&entities=northwind")
    assert state.url() == (
        "/search?query=fleet&entities=northwind&k=40&hit=r:calls:rec-0101@00:00-01:30&around=2&verify=1"
    )
    assert state.url("/search/panel").startswith("/search/panel?query=fleet")


def test_a_locator_keeps_its_colons_and_at_and_encodes_the_rest() -> None:
    state = SearchState(hit="r:mail:<offer-1@northwind.example>#1-4")
    assert state.url() == "/search?hit=r:mail:%3Coffer-1@northwind.example%3E%231-4"
    assert read(state.url().split("?", 1)[1]).hit == state.hit


def test_lines_are_a_list_of_lines_and_ranges_sorted_by_where_each_starts() -> None:
    state = read("lines=9&lines=4-9&lines=4&lines=2-3&lines=4-4&lines=")
    assert state.lines == ("2-3", "4", "4-9", "9")
    assert state.url() == "/search?lines=2-3&lines=4&lines=4-9&lines=9"
    assert state.line_numbers() == [2, 3, 4, 5, 6, 7, 8, 9]
    assert read("lines=4&lines=9").line_numbers() == [4, 9]


@pytest.mark.parametrize(
    ("query", "named"),
    [
        ("k=0", "k"),
        ("k=101", "k"),
        ("k=ten", "k"),
        ("around=31", "around"),
        ("around=-1", "around"),
        ("since=yesterday-ish", "since"),
        ("until=2026-13-01", "until"),
        ("lines=9-4", "lines"),
        ("lines=x", "lines"),
        ("lines=0", "lines"),
        ("verify=yes", "verify"),
    ],
)
def test_a_value_that_cant_be_read_is_an_error_naming_it(query: str, named: str) -> None:
    with pytest.raises(StateError, match=f"^{named} "):
        read(query)


def test_a_time_is_kept_as_written() -> None:
    state = read("since=2026-10-07T09:30%2B02:00")
    assert state.since == "2026-10-07T09:30+02:00"


@pytest.mark.parametrize(
    "text",
    [
        "r:calls:rec-0100@02:30",
        "[r:calls:rec-0100@02:30]",
        "r:mail:<offer-1@northwind.example>#1-4",
        "recording-note:2026-10-07-supplier-call",
        "r:chat:C0OPS/1791400800.000100",
    ],
)
def test_a_locator_query_is_recognized(text: str) -> None:
    assert is_locator(text)
    state = SearchState(query=text)
    assert state.locator_query
    assert state.open_hit == text
    assert not state.asked


@pytest.mark.parametrize(
    "text", ["", "day rate", "r:", "minutes:", "notes/calls/a.md@12:08", "r:calls:rec 0100", "r:nocolon@xx:yy", "x:y"]
)
def test_words_are_not_a_locator(text: str) -> None:
    assert not is_locator(text)


def test_what_counts_as_asked() -> None:
    assert not SearchState().asked
    assert SearchState(query="fleet").asked
    assert SearchState(sources=("chat",)).asked
    assert SearchState(since="2026-10-01").asked
    assert SearchState(query="r:calls:rec-0100", sources=("calls",)).asked
    assert SearchState(hit="r:calls:rec-0100").open_hit == "r:calls:rec-0100"
    assert SearchState(query="fleet").open_hit is None


def test_search_only_closes_the_panel_and_keeps_the_search() -> None:
    state = SearchState(query="fleet", k=40, hit="x:y", lines=("4",), around=3, verify=True)
    assert state.search_only() == SearchState(query="fleet", k=40)


def test_from_url_reads_the_search_page_and_nothing_else() -> None:
    assert SearchState.from_url("http://127.0.0.1:8741/search?query=fleet") == SearchState(query="fleet")
    assert SearchState.from_url("http://127.0.0.1:8741/search") == SearchState()
    assert SearchState.from_url("http://127.0.0.1:8741/today?query=fleet") is None
    assert SearchState.from_url("http://127.0.0.1:8741/search?k=0") is None
    assert SearchState.from_url("") is None
