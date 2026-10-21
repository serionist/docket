"""One encoding for every page link the service builds (DR-0704): urlencode with one URL_SAFE, which the React app's
router writes by too. The canonical strings here are the ones its Vitest round-trip holds byte for byte."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from starlette.datastructures import QueryParams

from docket.web import urls
from docket.web.briefs_state import BriefsState
from docket.web.curate_view import CurateState
from docket.web.day_state import DayState
from docket.web.recording_state import RecordingState
from docket.web.staging_view import StagingState
from docket.web.state import SearchState
from docket.web.sync_view import SyncState

CANONICAL = Path(__file__).parents[1] / "web" / "src" / "test" / "canonical.json"
WEB = Path(__file__).parents[1] / "src" / "docket" / "web"

READERS: dict[str, Callable[[QueryParams], Any]] = {
    "/search": SearchState.read,
    "/ledger": DayState.read,
    "/queue": lambda params: DayState.read(params, "queue"),
    "/meetings": lambda params: DayState.read(params, "meetings"),
    "/today": lambda params: DayState.read(params, "today"),
    "/staging": StagingState.read,
    "/curate": CurateState.read,
    "/sync": SyncState.read,
    "/briefs": BriefsState.read,
    "/recording": RecordingState.read,
}


def canonical_urls() -> list[str]:
    return json.loads(CANONICAL.read_text(encoding="utf-8"))["urls"]


@pytest.mark.parametrize("url", canonical_urls())
def test_each_shared_url_is_its_page_s_own_canonical_form(url: str) -> None:
    path, _, query = url.partition("?")
    state = READERS[path](QueryParams(query))
    assert state.url() == url


def test_the_shared_urls_hold_one_from_every_page() -> None:
    assert {url.partition("?")[0] for url in canonical_urls()} == set(READERS)


def test_the_ledger_s_parameters_go_in_the_order_the_app_writes_them_in() -> None:
    order = json.loads(CANONICAL.read_text(encoding="utf-8"))["order"]["/ledger"]
    every = DayState(open="A-0002", at="A-0001", q="x", stream="ops", kind="task", who="Ada", k=200, state="all",
                     group="who", archived=True, drop=True, text="a.md")  # fmt: skip
    assert [name for name, _ in every.pairs()] == order


def test_the_queue_s_parameters_are_the_ledger_s_without_its_own() -> None:
    order = json.loads(CANONICAL.read_text(encoding="utf-8"))["order"]["/queue"]
    every = DayState(page="queue", open="A-0002", at="A-0001", q="x", stream="ops", kind="task", who="Ada", k=200,
                     drop=True, text="a.md")  # fmt: skip
    assert [name for name, _ in every.pairs()] == order
    assert DayState(page="queue", state="all", group="who", archived=True).canonical().pairs() == []


def test_meetings_takes_the_shared_q_and_stream_and_its_own_suggest_and_leaves_the_items_filters_out() -> None:
    order = json.loads(CANONICAL.read_text(encoding="utf-8"))["order"]["/meetings"]
    every = DayState(page="meetings", open="M-0002", at="M-0001", q="x", stream="ops", text="a.md", suggest=True)
    assert [name for name, _ in every.pairs()] == order
    # The items' filters are never refused there, and suggest goes only with an open meeting and only on Meetings.
    asked = QueryParams("kind=bogus&who=Ada&k=0&suggest=1&state=nope")
    assert DayState.read(asked, "meetings").url() == "/meetings"
    assert DayState.read(QueryParams("open=M-0002&suggest=1"), "ledger").url() == "/ledger?open=M-0002"


def test_today_takes_no_filter_drop_with_an_item_and_suggest_with_a_meeting() -> None:
    order = json.loads(CANONICAL.read_text(encoding="utf-8"))["order"]["/today"]
    item = DayState(page="today", open="A-0002", at="A-0001", drop=True, text="a.md")
    assert [name for name, _ in item.pairs()] == [k for k in order if k != "suggest"]
    meeting = DayState(page="today", open="M-0002", text="a.md", suggest=True)
    assert [name for name, _ in meeting.pairs()] == ["open", "text", "suggest"]
    # Every filter is left out, never refused, and drop and suggest go only with the panel that takes them.
    asked = QueryParams("q=x&stream=ops&kind=bogus&who=Ada&k=0&state=nope&group=x&archived=2")
    assert DayState.read(asked, "today").url() == "/today"
    assert DayState.read(QueryParams("open=M-0002&drop=1"), "today").url() == "/today?open=M-0002"
    assert DayState.read(QueryParams("open=A-0002&suggest=1"), "today").url() == "/today?open=A-0002"
    assert DayState.read(QueryParams("at=A-0001&suggest=1"), "today").url() == "/today?at=A-0001"


def test_the_rule_keeps_ascii_letters_digits_and_url_safe_and_encodes_the_rest() -> None:
    assert urls.URL_SAFE == ":@/,"
    assert urls.query([("q", "AZaz09_.-~:@/,")]) == "q=AZaz09_.-~:@/,"
    assert urls.query([("q", "a b*!'()?&=#%+")]) == "q=a+b%2A%21%27%28%29%3F%26%3D%23%25%2B"
    assert urls.query([("q", "Őr ü")]) == "q=%C5%90r+%C3%BC"
    assert urls.link("/sync", []) == "/sync"
    assert urls.link("/sync", [("source", "calls")]) == "/sync?source=calls"


def test_no_page_keeps_a_rule_of_its_own() -> None:
    for module in sorted(WEB.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        if module.name == "urls.py":
            continue
        assert "URL_SAFE =" not in text, module.name
        assert "urlencode(" not in text, f"{module.name} builds a link without docket.web.urls"
