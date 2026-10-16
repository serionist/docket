from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from devkit import evalfloors

FLOORS = {"exact-name": {"recall": 0.92, "mrr": 0.78}, "paraphrase": {"recall": 0.11, "mrr": 0.06}}


def test_a_kind_at_its_floor_passes_and_its_numbers_are_printed() -> None:
    measured = {"exact-name": {"recall": 0.9286, "mrr": 0.7899}, "paraphrase": {"recall": 0.1111, "mrr": 0.0667}}
    failures, line = evalfloors.judge(measured, FLOORS, FLOORS)
    assert failures == []
    assert line == "eval recall@10/MRR: exact-name 0.92/0.78, paraphrase 0.11/0.06"


def test_below_and_above_a_floor_fail() -> None:
    measured = {"exact-name": {"recall": 0.85, "mrr": 0.78}, "paraphrase": {"recall": 0.2, "mrr": 0.06}}
    failures, _ = evalfloors.judge(measured, FLOORS, None)
    assert failures == [
        "eval exact-name recall: 0.85 is below its floor of 0.92",
        "eval paraphrase recall: 0.20, floor 0.11: raise it to 0.20",
    ]


def test_a_kind_without_a_floor_or_a_floor_without_a_kind_fails() -> None:
    measured = {"exact-name": {"recall": 0.92, "mrr": 0.78}, "typo": {"recall": 0.5, "mrr": 0.4}}
    failures, _ = evalfloors.judge(measured, FLOORS, None)
    assert failures == [
        f"eval typo: measured but no floor; add it to {evalfloors.FLOORS}",
        "eval paraphrase: has a floor but nothing was measured",
    ]


def test_a_floor_lowered_from_the_base_needs_a_new_note() -> None:
    measured = {"exact-name": {"recall": 0.85, "mrr": 0.70}, "paraphrase": {"recall": 0.11, "mrr": 0.06}}
    lowered = {**FLOORS, "exact-name": {"recall": 0.85, "mrr": 0.70}}
    failures, _ = evalfloors.judge(measured, lowered, FLOORS)
    assert failures == ["eval exact-name: recall and mrr lowered from the base; say why in a new lowered note on it"]
    said = {**FLOORS, "exact-name": {"recall": 0.85, "mrr": 0.70, "lowered": "#71: two names traded for paraphrases"}}
    assert evalfloors.judge(measured, said, FLOORS)[0] == []
    # The base's own note doesn't cover a second lowering.
    again = {**said, "exact-name": {**said["exact-name"], "mrr": 0.6}}
    measured["exact-name"]["mrr"] = 0.6
    assert evalfloors.judge(measured, again, said)[0] == [
        "eval exact-name: mrr lowered from the base; say why in a new lowered note on it"
    ]


def test_check_reads_the_test_s_numbers_and_the_base_s_floors(repo: Path, git: Callable[..., str]) -> None:
    floors = repo / evalfloors.FLOORS
    floors.parent.mkdir(parents=True)
    floors.write_text(json.dumps({"$comment": "x", **FLOORS}), encoding="utf-8")
    assert evalfloors.check(repo, None) == (
        [f"eval: the question set's test wrote no numbers to {evalfloors.REPORT}"],
        "eval: not measured",
    )
    numbers = repo / evalfloors.REPORT
    numbers.parent.mkdir()
    numbers.write_text(json.dumps({k: dict(v) for k, v in FLOORS.items()}), encoding="utf-8")
    base = git(repo, "rev-parse", "HEAD").strip()
    # Before the file existed the base has no floors.
    assert evalfloors.base_floors(repo, base) is None and evalfloors.check(repo, base)[0] == []
    git(repo, "add", evalfloors.FLOORS)
    git(repo, "commit", "-q", "-m", "Floors")
    base = git(repo, "rev-parse", "HEAD").strip()
    assert evalfloors.base_floors(repo, base) == FLOORS
    floors.write_text(json.dumps({**FLOORS, "paraphrase": {"recall": 0.1, "mrr": 0.06}}), encoding="utf-8")
    numbers.write_text(json.dumps({**FLOORS, "paraphrase": {"recall": 0.1, "mrr": 0.06}}), encoding="utf-8")
    assert evalfloors.check(repo, base)[0] == [
        "eval paraphrase: recall lowered from the base; say why in a new lowered note on it"
    ]


def test_the_models_floors_print_a_rise_until_three_runs_agree(repo: Path) -> None:
    floors = repo / evalfloors.FLOORS_MODELS
    floors.parent.mkdir(parents=True)
    floors.write_text(json.dumps({"$above": "print", **FLOORS}), encoding="utf-8")
    numbers = repo / evalfloors.REPORT_MODELS
    numbers.parent.mkdir()
    numbers.write_text(json.dumps({**FLOORS, "paraphrase": {"recall": 0.2, "mrr": 0.06}}), encoding="utf-8")
    failures, line = evalfloors.check(repo, None, models=True)
    assert failures == []
    assert line.endswith(
        "; above the floor, to raise once three runs agree: eval paraphrase recall: 0.20, floor 0.11: raise it to 0.20"
    )
    floors.write_text(json.dumps({"$above": "fail", **FLOORS}), encoding="utf-8")
    assert evalfloors.check(repo, None, models=True)[0] == [
        "eval paraphrase recall: 0.20, floor 0.11: raise it to 0.20"
    ]
    numbers.unlink()
    assert evalfloors.check(repo, None, models=True)[0] == [
        f"eval: the question set's test wrote no numbers to {evalfloors.REPORT_MODELS}"
    ]
    measured = {"typo": {"recall": 0.5, "mrr": 0.4}}
    assert (
        f"add it to {evalfloors.FLOORS_MODELS}"
        in evalfloors.judge(measured, {}, None, file=evalfloors.FLOORS_MODELS)[0][0]
    )


def test_the_reranked_floors_are_a_file_of_their_own_under_the_same_rules(repo: Path) -> None:
    floors = repo / evalfloors.FLOORS_RERANK
    floors.parent.mkdir(parents=True)
    floors.write_text(json.dumps({"$above": "print", **FLOORS}), encoding="utf-8")
    numbers = repo / evalfloors.REPORT_RERANK
    numbers.parent.mkdir()
    numbers.write_text(json.dumps({**FLOORS, "exact-name": {"recall": 0.9, "mrr": 0.78}}), encoding="utf-8")
    assert evalfloors.check(repo, None, rerank=True)[0] == ["eval exact-name recall: 0.90 is below its floor of 0.92"]
    numbers.write_text(json.dumps(FLOORS), encoding="utf-8")
    assert evalfloors.check(repo, None, rerank=True)[0] == []
    # The repo's own file fails a rise too, since three CI runs agreed (DR-0458).
    assert evalfloors.above_fails(Path(__file__).resolve().parents[3], evalfloors.FLOORS_RERANK)
