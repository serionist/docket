"""The models as search and the pass see them: batches cut by the budget, queries with their prefix, and the catalogue's
inputs."""

from __future__ import annotations

from datetime import datetime

from models_kit import FAKE, fake_models, pair_score, trigram_vector

from docket.models.catalogue import E5_SMALL, EMBEDDERS, input_hash
from docket.models.client import PAST_BUDGET, Skip
from docket.models.provider import CHAR_CAP, PAUSE_AFTER, PAUSE_SECONDS, Off, RerankCounts, batches, cost


def test_a_text_costs_its_length_up_to_the_cap() -> None:
    assert cost("") == 1
    assert cost("abc") == 3
    assert cost("x" * (CHAR_CAP * 3)) == CHAR_CAP


def test_batches_go_shortest_first_and_hold_what_fits_at_their_longest() -> None:
    texts = ["x" * 50, "x" * 10, "x" * 30, "x" * 10, "x" * 3000]
    # 10 and 10 fit 60 together; 30 with them would count as 3 x 30.
    assert batches(texts, 60) == [[1, 3], [2], [0], [4]]
    # One text past the budget is still a batch of its own.
    assert batches(["x" * 500], 10) == [[0]]
    assert batches([], 100) == []


def test_models_embed_queries_with_the_prefix_and_passages_in_order() -> None:
    models, worker = fake_models(budget=30)
    assert models.query_vector("paper price") == trigram_vector("query: paper price")
    assert worker.queries == ["query: paper price"]
    inputs = ["passage: " + "x" * n for n in (20, 1, 5)]
    assert models.passage_vectors(inputs) == [trigram_vector(t) for t in inputs]
    assert worker.batches == [[inputs[1], inputs[2]], [inputs[0]]]
    assert models.why is None
    models.close()
    assert models.why == "the models' worker isn't running"
    assert models.query_vector("paper") == "the models' worker isn't running"


def test_off_says_why_only_when_there_is_something_to_say() -> None:
    assert Off().why is None
    assert Off("the models block doesn't read").why == "the models block doesn't read"


def test_the_catalogue_builds_inputs_and_names_tables() -> None:
    assert EMBEDDERS["e5-small"] is E5_SMALL
    assert E5_SMALL.key == "e5_small_614241f6" and E5_SMALL.table == "chunk_vec_e5_small_614241f6"
    assert E5_SMALL.download == 470_268_510 + 17_082_730
    assert [f.path for f in E5_SMALL.files] == ["onnx/model.onnx", "tokenizer.json"]
    # A transcript window carries its recording's name only in its title, so the title goes on top.
    assert E5_SMALL.passage("Print run call", "so the printer called back") == (
        "passage: Print run call\nso the printer called back"
    )
    # A title the text's opening already holds adds nothing, and no title adds nothing.
    assert E5_SMALL.passage("Re: paper", "Re: paper\nThe price went up") == "passage: Re: paper\nThe price went up"
    assert E5_SMALL.passage(None, "text") == "passage: text"
    assert E5_SMALL.query("paper price") == "query: paper price"
    assert input_hash("passage: text") != input_hash("passage: text ")
    assert FAKE.dims == 64


def test_a_rerank_counts_and_passes_its_deadline_from_its_budget() -> None:
    models, worker = fake_models(pairs=30, wall=lambda: 1000.0)
    assert models.rerank("paper", ["the paper", "a boat"], 0.75) == [
        pair_score("paper", "the paper"), pair_score("paper", "a boat"),
    ]  # fmt: skip
    assert worker.reranks[-1][2] == 1000.75
    assert models.rerank("paper", ["a"], None) == [pair_score("paper", "a")] and worker.reranks[-1][2] is None
    assert models.counts == RerankCounts(done=2)


def test_three_budget_skips_in_a_row_pause_the_reranker_for_five_minutes() -> None:
    now = [100.0]
    models, worker = fake_models(pairs=30, clock=lambda: now[0], wall=lambda: 1_800_000_000.0 + now[0])
    worker.rerank_answer = Skip(PAST_BUDGET, budget=True)
    for _ in range(PAUSE_AFTER):
        assert models.rerank("q", ["a"], 0.75) == "the reranker passed its 0.75 s budget"
    asked = len(worker.reranks)
    paused = models.rerank("q", ["a"], 0.75)
    back = datetime.fromtimestamp(1_800_000_000.0 + 100 + PAUSE_SECONDS).astimezone()
    assert paused == f"the reranker passed its budget 3 times in a row, so search goes without it until {back:%H:%M}"
    assert len(worker.reranks) == asked, "a paused reranker isn't asked"
    assert models.counts.past_budget == PAUSE_AFTER and models.counts.in_a_row == 0
    now[0] += PAUSE_SECONDS
    worker.rerank_answer = None
    assert models.rerank("q", ["a"], 0.75) == [pair_score("q", "a")]
    assert models.counts.done == 1


def test_a_skip_that_isnt_the_budgets_is_said_and_not_counted() -> None:
    models, worker = fake_models(pairs=30)
    worker.rerank_answer = Skip("the reranker refused a request: ValueError")
    assert models.rerank("q", ["a"], 0.75) == "the reranker refused a request: ValueError"
    busy = "the models' worker is busy: a query passed its time"
    worker.rerank_answer = Skip(busy, budget=True)
    assert models.rerank("q", ["a"], 0.75) == busy
    # With no budget, a wait that ran out says the worker's words.
    worker.rerank_answer = Skip(PAST_BUDGET, budget=True)
    assert models.rerank("q", ["a"], None) == PAST_BUDGET
    assert models.counts == RerankCounts(past_budget=2, in_a_row=2)


def test_models_without_a_reranker_have_no_pairs() -> None:
    models, _ = fake_models(pairs=0, rerank_why="none here")
    assert models.reranker is None and models.pairs == 0 and models.rerank_why == "none here"
