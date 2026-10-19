"""The day pages on #201's perf ledger (DR-0668): 5,000 items with 50,000 ops and 2,000 meetings with 20,000 point
ops, the index rebuilt. Each page is asked once before the clock, so SQLite's cache is warm as a running service's is,
then the Ledger, the Ledger with q and an item's panel are each held to a quarter second, the poll to a hundredth, a
button's write on one item to a tenth and a bulk of 50 to a quarter second, each on its own. A write is timed through
its route with the commit left out, as the ledger's own timed write is dry: git's commit isn't the page's to time.
Wall time is asserted only when no tracer runs."""

from __future__ import annotations

import gc
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from day_kit import SAME, signed_in
from docket_testkit import empty_vault, make_config, traced
from docket_webkit import parse
from ledger_kit import ITEMS, build
from starlette.testclient import TestClient

from docket.index import build as index_build
from docket.ledger import ops, store
from docket.vault.vault import Vault

PAGE_SECONDS = 0.25
POLL_SECONDS = 0.01
WRITE_SECONDS = 0.1
BULK_SECONDS = 0.25
BULK = 50


@pytest.fixture(scope="module")
def perf_vault(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    """The perf ledger in a vault, its files' times an hour back, as files a write didn't just write are, and the
    index rebuilt over it."""
    vault = empty_vault(tmp_path_factory.mktemp("day-perf"), make_config())
    build(vault.root)
    hour_ago = time.time_ns() - 3600 * 10**9
    for path in (vault.root / ops.OPS_DIR).iterdir():
        os.utime(path, ns=(hour_ago, hour_ago))
    index_build.rebuild(vault)
    return vault


@pytest.fixture
def client(perf_vault: Vault, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # The commit left out: the write is planned and checked as ever, and nothing lands.
    monkeypatch.setattr(store, "commit_ops", lambda vault, new, message: None)
    with signed_in(perf_vault) as found:
        yield found


def timed(ask: Callable[[], Any]) -> tuple[Any, float]:
    """The answer and its time, asked once before the clock."""
    ask()
    gc.collect()
    began = time.perf_counter()
    answer = ask()
    return answer, time.perf_counter() - began


@pytest.mark.perf
def test_the_ledger_its_panel_the_poll_and_a_write_answer_within_their_budgets(client: TestClient) -> None:
    page, page_s = timed(lambda: client.get("/ledger"))
    assert page.status_code == 200
    assert len(parse(page.text).find_all("li", class_="row")) == 100
    # Every word is a part of a title, a number or a log line: "4999" is in A-4999 and in round 4999's title.
    found, found_s = timed(lambda: client.get("/ledger?q=round+4999"))
    assert [r.attrs["data-ref"] for r in parse(found.text).find_all("li", class_="row")] == ["A-4999", f"A-{ITEMS:04d}"]
    panel, panel_s = timed(lambda: client.get("/ledger/panel?open=A-2500"))
    assert parse(panel.text).find("section", class_="item-panel").attrs["data-item"] == "A-2500"
    poll = parse(page.text).find("div", id="poll").attrs["hx-get"]
    quiet, poll_s = timed(lambda: client.get(poll))
    assert quiet.status_code == 204
    one = {"page": "ledger", "op": "triage:me", "item": "A-1200"}
    wrote, write_s = timed(lambda: client.post("/ledger/write", data=one, headers=SAME))
    assert wrote.status_code == 303
    bulk = {"page": "ledger", "op": "triage:claude", "item": [f"A-{n:04d}" for n in range(100, 100 + BULK)]}
    many, bulk_s = timed(lambda: client.post("/ledger/write", data=bulk, headers=SAME))
    assert many.status_code == 303
    print(
        f"day pages on {ITEMS:,} items: ledger {page_s * 1000:.0f} ms, with q {found_s * 1000:.0f} ms, panel "
        f"{panel_s * 1000:.0f} ms, poll {poll_s * 1000:.1f} ms, a write {write_s * 1000:.0f} ms, a bulk of {BULK} "
        f"{bulk_s * 1000:.0f} ms"
    )
    if not traced():
        assert page_s < PAGE_SECONDS, f"the Ledger took {page_s:.3f} s, over {PAGE_SECONDS} s"
        assert found_s < PAGE_SECONDS, f"the Ledger with q took {found_s:.3f} s, over {PAGE_SECONDS} s"
        assert panel_s < PAGE_SECONDS, f"an item's panel took {panel_s:.3f} s, over {PAGE_SECONDS} s"
        assert poll_s < POLL_SECONDS, f"the poll took {poll_s:.4f} s, over {POLL_SECONDS} s"
        assert write_s < WRITE_SECONDS, f"a write on one item took {write_s:.3f} s, over {WRITE_SECONDS} s"
        assert bulk_s < BULK_SECONDS, f"a bulk of {BULK} took {bulk_s:.3f} s, over {BULK_SECONDS} s"


def test_the_perf_ledger_s_builder_writes_its_files_under_the_root(tmp_path: Path) -> None:
    build(tmp_path, meetings=False)
    assert sorted(p.name for p in (tmp_path / ops.OPS_DIR).iterdir())
