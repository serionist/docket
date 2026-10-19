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
