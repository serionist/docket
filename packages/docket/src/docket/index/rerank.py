"""The rerank step's rules (#88): which chunks the reranker sees, and how its scores order them.

The top N starts with today's top k, the chunks behind the hits the search returns without a reranker, so none of them
is dropped unseen, then fills by walking the fusion at vector weight 1 from its top, skipping chunks already in
(DR-0453). The reranker orders the top N in two tiers, the chunks the all-words stage found first, and within each
tier the chunks an explicit people or entity boost lifted come first; each part goes by the reranker's score, ties in
the order the chunks came (DR-0452). Search, in `query`, fetches the texts, asks the models and says what happened.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from typing import Protocol, runtime_checkable

from docket.models.catalogue import Ranking


@runtime_checkable
class Reranks(Protocol):
    """What search needs of models that rerank (`provider.Models`): the reranker when it is on here, its pairs, why it
    isn't on, whether a search says so, and a rerank within a budget."""

    reranker: Ranking | None
    pairs: int
    rerank_why: str | None
    rerank_said: bool

    def rerank(self, query: str, texts: Sequence[str], budget: float | None) -> list[float] | str: ...


def candidates(today: Sequence[int], weight_one: Sequence[int], pairs: int) -> list[int]:
    """The reranker's top N: today's chunks first, then the weight-1 order from its top, skipping those already in."""
    top = list(today[:pairs])
    seen = set(top)
    for chunk in weight_one:
        if len(top) >= pairs:
            break
        if chunk not in seen:
            top.append(chunk)
            seen.add(chunk)
    return top


def order(top: Sequence[int], scores: Sequence[float], found_by: Mapping[int, str], boosted: Set[int]) -> list[int]:
    """The top N in two tiers, all-words hits first; boosted chunks first within each; then by the reranker's score."""
    place = {chunk: n for n, chunk in enumerate(top)}
    score = dict(zip(top, scores, strict=True))
    return sorted(top, key=lambda c: (found_by[c] != "all", c not in boosted, -score[c], place[c]))
