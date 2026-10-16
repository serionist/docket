"""The rerank step (DR-0452, DR-0453, DR-0454, DR-0459): which chunks the reranker sees, the two tiers it orders them
in, boosts first within each, and every way it is skipped, each leaving today's order and the vectors as they were."""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import models_kit
import pytest
from docket_testkit import copy_vault, index_template, make_record
from models_kit import FAKE, FAKE_RERANKER, fake_command, fake_models, needs_vectors
from test_index_vectors import choose

from docket import render
from docket.index import build, query, rebuild, rerank, search, update, vectors
from docket.index.embedpass import fill
from docket.models.catalogue import rerank_passage
from docket.models.client import PAST_BUDGET, Skip, Worker
from docket.models.provider import Models, Off
from docket.vault.vault import Vault
from docket.web.views import rerank_line

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
# The real scan, kept before a test stands in for it, so a second stand-in wraps it and not the first.
SCAN = vectors.scan
AT = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
# Each one's planted score is what the fake reranker gives it (models_kit.PLANTED).
ALL_LOW = ("chat:C0RR/1791480001.000100", "Zebra budget review for the quarter. [score -1]")
ANY_HIGH = ("chat:C0RR/1791480002.000100", "A zebra was painted on the wall. [score 5]")
VECTOR_TOP = ("chat:C0RR/1791480003.000100", "Zebbra bugdet migrashun notes. [score 9]")
BOOSTED = ("chat:C0RR/1791480004.000100", "Cleo Test asked about the zebra fence. [score 1]")
PLANTED = [ALL_LOW, ANY_HIGH, VECTOR_TOP, BOOSTED]


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    choose(monkeypatch)
    v = copy_vault(template, tmp_path)
    records = [
        make_record(n, rid=rid, container="chat:C0RR", text=text, ts=AT.replace(minute=n))
        for n, (rid, text) in enumerate(PLANTED)
    ]
    v.write_run("chat", records, AT)
    rebuild(v)
    fill(v, fake_models()[0])
    return v


def hits(result: query.SearchResult) -> list[query.Hit]:
    return sorted((h for g in result.groups for h in g.hits), key=lambda h: h.rank)


def order(result: query.SearchResult) -> list[str]:
    return [h.locator for h in hits(result)]


# -- the rules, on their own ---------------------------------------------------------------------------------------


def test_the_top_n_is_todays_chunks_then_the_weight_one_order_from_its_top() -> None:
    assert rerank.candidates([5, 3], [9, 3, 7, 5, 8], 4) == [5, 3, 9, 7]
    assert rerank.candidates([5, 3, 1], [9], 2) == [5, 3], "today's chunks fill a small N alone"
    assert rerank.candidates([], [9, 9, 4], 5) == [9, 4]


def test_the_order_is_two_tiers_boosts_first_in_each_then_scores() -> None:
    found_by = {1: "all", 2: "all", 3: "any", 4: "vector", 5: "entity", 6: "vector"}
    scores = [0.1, 0.5, 0.9, 0.95, 0.2, 0.95]
    assert rerank.order([1, 2, 3, 4, 5, 6], scores, found_by, set()) == [2, 1, 4, 6, 3, 5]
    # Boosted chunks lead their tier, whatever their score; a tie keeps the order they came in.
    assert rerank.order([1, 2, 3, 4, 5, 6], scores, found_by, {1, 5}) == [1, 2, 5, 4, 6, 3]


# -- the step in search ----------------------------------------------------------------------------------------------


@needs_vectors
def test_the_reranker_orders_the_top_in_two_tiers_and_says_what_it_did(vault: Vault) -> None:
    plain = search(vault, "zebra budget", k=10, now=NOW, models=fake_models()[0])
    assert plain.rerank == query.RerankRun(), "models with no reranker leave it off and silent"
    models, worker = fake_models(pairs=30)
    result = search(vault, "zebra budget", k=10, now=NOW, models=models)
    found = hits(result)
    rids = [h.rid for h in found]
    # The all-words hit leads whatever its score; below it the reranker's order, a chunk found by meaning included.
    assert rids[0] == ALL_LOW[0] and found[0].found_by == "all"
    assert rids.index(VECTOR_TOP[0]) < rids.index(ANY_HIGH[0]) < rids.index(BOOSTED[0])
    assert {h.found_by for h in found if h.rid == VECTOR_TOP[0]} == {"vector"}
    assert result.rerank == query.RerankRun(state="on", model="fake-reranker", pairs=len(worker.reranks[0][1]))
    asked, texts, deadline = worker.reranks[0]
    assert asked == "zebra budget" and any("[score 9]" in t for t in texts)
    assert deadline is not None and abs(deadline - (time.time() + 0.75)) < 5
    # Facets and the candidates count a chunk only the vectors found when it makes the top k, as without a reranker.
    shown = {h.locator for h in found if h.found_by == "vector"} - {
        h.locator for h in hits(plain) if h.found_by == "vector"
    }
    assert result.candidates == plain.candidates + len(shown)
    assert sum(result.facets["months"].values()) == sum(plain.facets["months"].values()) + len(shown)
    assert not any(line.startswith("not reranked") for line in render.search(result, NOW.tzinfo))  # type: ignore[arg-type]


@needs_vectors
def test_a_boosted_chunk_leads_its_tier_whatever_its_score(vault: Vault) -> None:
    models, _ = fake_models(pairs=30)
    result = search(vault, "zebra budget", people=["Cleo Test"], k=10, now=NOW, models=models)
    rids = [h.rid for h in hits(result)]
    assert result.query.boosts == ["cleo-test"]
    assert rids[0] == ALL_LOW[0] and rids[1] == BOOSTED[0], rids


@needs_vectors
def test_the_reranker_sees_the_chunks_behind_todays_hits_first(vault: Vault) -> None:
    today = search(vault, "zebra budget", k=2, now=NOW, models=fake_models()[0])
    conn = sqlite3.connect(build.db_path(vault))
    passages = {
        locator: rerank_passage(title, text)
        for locator, title, text in conn.execute("SELECT locator, title, text FROM chunks")
    }
    conn.close()
    models, worker = fake_models(pairs=4)
    search(vault, "zebra budget", k=2, now=NOW, models=models)
    sent = worker.reranks[0][1]
    behind = [passages[loc] for h in hits(today) for loc in (h.locator, *h.also)]
    assert sent[: len(behind)] == behind and len(sent) == 4


@pytest.mark.parametrize(
    ("setup", "state", "why"),
    [
        ("past the budget", "skipped", "the reranker passed its 0.75 s budget"),
        ("refused", "skipped", "the reranker refused a request: ValueError"),
        ("elsewhere", "skipped", "the reranker runs on the GPU in the service; docket search goes without it"),
        ("off here", "off", "fake-reranker is off on this machine: 30 pairs took 0.90 s, past 0.6 s"),
    ],
)
@needs_vectors
def test_a_rerank_that_cant_run_leaves_todays_order_and_the_vectors_on(
    vault: Vault, setup: str, state: str, why: str
) -> None:
    today = search(vault, "zebra budget", k=10, now=NOW, models=fake_models()[0])
    if setup == "past the budget":
        models, worker = fake_models(pairs=30)
        worker.rerank_answer = Skip(PAST_BUDGET, budget=True)
    elif setup == "refused":
        models, worker = fake_models(pairs=30)
        worker.rerank_answer = Skip("the reranker refused a request: ValueError")
    else:
        models, _ = fake_models(rerank_why=why, rerank_said=setup == "elsewhere")
    result = search(vault, "zebra budget", k=10, now=NOW, models=models)
    assert order(result) == order(today) and result.models.state == "on"
    assert result.rerank.state == state and result.rerank.why == why and result.rerank.pairs == 0
    lines = render.search(result, NOW.tzinfo)  # type: ignore[arg-type]
    said = f"not reranked: {why}"
    assert (said in lines) is (state == "skipped") and (rerank_line(result.rerank) == [said]) is (state == "skipped")


@needs_vectors
def test_a_query_of_time_words_or_one_candidate_asks_the_reranker_nothing(vault: Vault) -> None:
    models, worker = fake_models(pairs=30)
    timed = search(vault, "last week", now=NOW, models=models)
    assert timed.rerank.state == "skipped" and timed.rerank.why == (
        "nothing to compare: the query is only time words or filters"
    )
    single = search(vault, "budget", k=1, now=NOW, models=models, rerank_budget=None)
    assert single.rerank.state == "on" and single.rerank.pairs <= 30
    one, _ = fake_models(pairs=1)
    alone = search(vault, "zebra", k=1, now=NOW, models=one)
    assert alone.rerank == query.RerankRun(state="on", model="fake-reranker", pairs=1)
    assert all(len(texts) > 1 for _, texts, _ in worker.reranks)


def test_keyword_search_alone_and_models_off_have_no_rerank(vault: Vault) -> None:
    assert search(vault, "zebra budget", now=NOW).rerank == query.RerankRun()
    assert search(vault, "zebra budget", now=NOW, models=Off("off")).rerank == query.RerankRun()


@needs_vectors
def test_eval_reranks_with_no_budget(vault: Vault) -> None:
    models, worker = fake_models(pairs=30)
    search(vault, "zebra budget", k=10, now=NOW, models=models, rerank_budget=None)
    assert worker.reranks[0][2] is None


# -- #100's case, with the reranker on (DR-0338) -------------------------------------------------------------------


@pytest.fixture
def herd(vault: Vault) -> Iterator[tuple[Vault, set[int]]]:
    records = [
        make_record(
            n,
            rid=f"chat:C0H{n:02d}/{1791490000 + n}.000100",
            container=f"chat:C0H{n:02d}",
            text=f"Herd count {n}: the zebra migration passed the river.",
            ts=datetime(2026, 10, 9, 9, n % 60, tzinfo=UTC),
        )
        for n in range(71)
    ]
    vault.write_run("chat", records, datetime(2026, 10, 9, 10, 0, tzinfo=UTC))
    update(vault)
    fill(vault, fake_models()[0])
    conn = sqlite3.connect(build.db_path(vault))
    words = {int(r[0]) for r in conn.execute("SELECT id FROM chunks WHERE text LIKE '%zebra migration%'")}
    conn.close()
    yield vault, words


def scan_with(monkeypatch: pytest.MonkeyPatch, words: set[int], placed: dict[int, int] | None = None) -> None:
    """The vector scan without the all-words hits, so it holds chunks only it finds; `placed` puts an all-words hit
    back at a vector rank."""
    real = SCAN

    def apart(*args: Any, **kwargs: Any) -> list[tuple[int, str]] | str:
        found = real(*args, **kwargs)
        assert not isinstance(found, str)
        out = [f for f in found if f[0] not in words]
        everything = {f[0]: f for f in found}
        for chunk, at in (placed or {}).items():
            out.insert(at, everything[chunk])
        return out

    monkeypatch.setattr(vectors, "scan", apart)


def favour(monkeypatch: pytest.MonkeyPatch, wanted: Any) -> None:
    monkeypatch.setattr(models_kit, "pair_score", lambda q, text: 10.0 if wanted(text) else 0.0)


@needs_vectors
def test_a_vector_only_chunk_the_reranker_scores_highest_stays_after_every_all_words_hit(
    herd: tuple[Vault, set[int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, words = herd
    scan_with(monkeypatch, words)
    favour(monkeypatch, lambda text: "zebra migration" not in text)
    models, _ = fake_models(pairs=100)
    found = hits(search(vault, "zebra migration", k=100, now=NOW, models=models))
    by_all = [h.rank for h in found if h.found_by == "all"]
    by_vector = [h.rank for h in found if h.found_by == "vector"]
    assert len(by_all) == 71 and by_vector and max(by_all) < min(by_vector)


@needs_vectors
def test_an_all_words_hit_only_weight_one_brings_in_joins_the_top_and_leads_the_vectors(
    herd: tuple[Vault, set[int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, words = herd
    scan_with(monkeypatch, words)
    today = hits(search(vault, "zebra migration", k=100, now=NOW, models=fake_models()[0]))
    late = next(h for h in today if h.found_by == "all" and h.rank > 60)
    conn = sqlite3.connect(build.db_path(vault))
    chunk = conn.execute("SELECT id FROM chunks WHERE locator = ?", (late.locator,)).fetchone()[0]
    conn.close()
    # Thirtieth in the vector list: below today's top 10 at half weight, at the top of the order at full weight.
    scan_with(monkeypatch, words, {chunk: 29})
    plain = search(vault, "zebra migration", k=10, now=NOW, models=fake_models()[0])
    assert plain.models.state == "on" and late.locator not in [h.locator for h in hits(plain)]
    number = re.search(r"Herd count (\d+):", late.snippet)
    assert number is not None
    favour(monkeypatch, lambda text: f"Herd count {number.group(1)}:" in text)
    models, worker = fake_models(pairs=30)
    found = hits(search(vault, "zebra migration", k=10, now=NOW, models=models))
    assert found[0].locator == late.locator and len(worker.reranks[0][1]) == 30
    by_all = [h.rank for h in found if h.found_by == "all"]
    by_vector = [h.rank for h in found if h.found_by == "vector"]
    assert not by_vector or max(by_all) < min(by_vector)


# -- a worker past its budget, timed (DR-0430) -----------------------------------------------------------------------


@pytest.mark.perf
@needs_vectors
def test_a_rerank_past_its_budget_costs_at_most_its_budget_and_the_worker_answers_next(vault: Vault) -> None:
    rerank_spec = {"name": FAKE_RERANKER.name, "batch_seconds": 0}
    worker = Worker(fake_command(reranker=rerank_spec, rerank_delay=1.0))
    try:
        worker.start()
        models = Models(FAKE, worker, 8192, reranker=FAKE_RERANKER, pairs=30)
        keyword = time.monotonic()
        search(vault, "zebra budget", k=10, now=NOW)
        keyword = time.monotonic() - keyword
        started = time.monotonic()
        result = search(vault, "zebra budget", k=10, now=NOW, models=models)
        took = time.monotonic() - started
        assert result.rerank.state == "skipped" and result.rerank.why == "the reranker passed its 0.75 s budget"
        assert result.models.state == "on"
        print(f"keyword {keyword:.2f} s, with a rerank past its budget {took:.2f} s")
        assert took < keyword + 0.95 + 0.5
        # The worker stops its rerank at the next batch, so the next query's vector comes in its time.
        assert isinstance(models.query_vector("zebra"), bytes)
    finally:
        worker.stop()


def test_a_search_with_fewer_hits_than_k_sends_every_chunk_it_has(vault: Vault) -> None:
    # The worker can't embed the query here, so the candidates are the keyword stages' few, under k.
    models, worker = fake_models(pairs=30)
    worker.up = False
    result = search(vault, "zebra budget", k=100, now=NOW, models=models)
    assert result.models.state == "skipped" and result.rerank.state == "on"
    assert (
        len(worker.reranks[0][1]) == result.rerank.pairs == len(hits(result)) + sum(len(h.also) for h in hits(result))
    )
