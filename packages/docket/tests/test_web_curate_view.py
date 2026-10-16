"""The Curate page's view without a service: its URL in one canonical form, what each change's line says, and the
measures; a never-store value hidden wherever a key or a line would show it."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from starlette.datastructures import QueryParams

from docket.curate.batch import Measure
from docket.vault.config import NeverStore
from docket.web import curate_view
from docket.web.curate_view import CurateState, CurateStateError, canonical, done_line, measures, stop_key

AT = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)


def test_the_url_reads_into_one_canonical_form() -> None:
    assert CurateState.read(QueryParams("open=1a2b3c")) == CurateState(open="1a2b3c")
    assert CurateState.read(QueryParams("edit=1a2b3c&open=1a2b3c")) == CurateState(open="1a2b3c", edit=True)
    state = CurateState.read(QueryParams("edit=1a2b3c&done=accepted-1a2b3c"))
    assert state.url() == "/curate?edit=1a2b3c"
    assert state.url(done=True) == "/curate?edit=1a2b3c&done=accepted-1a2b3c"
    assert CurateState().url(curate_view.PANEL) == "/curate/panel"
    assert canonical("http://127.0.0.1:8741/curate/panel?open=1a2b3c") == "/curate?open=1a2b3c"
    assert canonical("http://127.0.0.1:8741/curate?open=nope") is None
    assert canonical("http://127.0.0.1:8741/staging?open=1a2b3c") is None


@pytest.mark.parametrize("query", ["open=1A2B3C", "edit=12345", "open=1a2b3c&edit=4d5e6f", "done=accepted"])
def test_a_url_part_that_doesn_t_read_is_refused(query: str) -> None:
    with pytest.raises(CurateStateError):
        CurateState.read(QueryParams(query))


@pytest.mark.parametrize(
    ("done", "says"),
    [
        ("accepted-1a2b3c", "Card 1a2b3c accepted and logged"),
        ("rejected-1a2b3c", "Card 1a2b3c rejected: its name"),
        ("edited-1a2b3c", "Card 1a2b3c accepted as you edited it"),
        ("undone-2", "Undone: 2 changes taken back"),
        ("undone-1", "Undone: 1 change taken back"),
        ("lifted-1", "Lifted: curation may offer"),
        ("round-15", "A new round: 15 cards."),
        ("round-0", "A new round, with nothing to propose"),
    ],
)
def test_each_change_says_what_it_did(done: str, says: str) -> None:
    line = done_line(done)
    assert line is not None and line.startswith(says)


def test_a_change_the_page_doesn_t_know_says_nothing() -> None:
    assert done_line("promoted-1-0") is None and done_line(None) is None


def test_the_measures_before_and_after() -> None:
    before = Measure(at=AT, all=(0.8, 0.5), kinds={"typo": (0.75, 0.46)})
    assert measures(None, None) is None
    only = measures(before, None)
    assert only is not None and only.rows == (("all", "0.80 / 0.50", ""), ("typo", "0.75 / 0.46", ""))
    after = Measure(at=AT, all=(0.9, 0.6), kinds={"exact-name": (1.0, 0.9)})
    both = measures(before, after)
    assert both is not None
    assert both.rows == (
        ("all", "0.80 / 0.50", "0.90 / 0.60"),
        ("exact-name", "", "1.00 / 0.90"),
        ("typo", "0.75 / 0.46", ""),
    )


def test_a_never_store_value_is_hidden_in_a_key() -> None:
    lists = NeverStore(keywords=["Nortwin"])
    assert stop_key("new:-:nortwin", lists) == "new:-:(on a never-store list)"
    assert stop_key("new:-:northwind", lists) == "new:-:northwind"
