"""The invented question set over the eval vault: big enough, every answer reachable, and no kind below its floor.

The floors are in tools/devkit/eval-floors.json (DR-0295). In the gate's full lane DOCKET_EVAL_REPORT names a file this
test writes the numbers to, and the gate fails a number above its floor or a floor lowered without its note.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import copy_vault
from eval_vault import EVAL_QUESTIONS, eval_template

from docket.index import rebuild
from docket.index.build import db_path
from docket.index.embedpass import fill
from docket.models import config
from docket.models.catalogue import E5_SMALL
from docket.models.provider import Models
from docket.models.runtime import open_models
from docket.questions import KINDS, EvalReport, Found, QuestionSet, answers, load, run
from docket.vault.anchors import parse_anchor
from docket.vault.vault import Vault

FLOORS = Path(__file__).resolve().parents[3] / "tools" / "devkit" / "eval-floors.json"
REPORT = "DOCKET_EVAL_REPORT"
# The question set with the embedder (DR-0339): a folder `docket models install --device cpu` filled, as the models CI
# job's cache does; its numbers go where the gate's models lane asks, against floors of their own.
MODELS = "DOCKET_TEST_MODELS"
MODELS_FLOORS = FLOORS.with_name("eval-floors-models.json")
MODELS_REPORT = "DOCKET_EVAL_MODELS_REPORT"


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    v = copy_vault(eval_template(tmp_path_factory), tmp_path_factory.mktemp("eval-set"))
    rebuild(v)
    return v


@pytest.fixture(scope="module")
def questions(vault: Vault) -> QuestionSet:
    return load(EVAL_QUESTIONS, ZoneInfo(vault.config.owner.time_zone))


def test_the_set_is_big_enough_and_in_both_languages(questions: QuestionSet) -> None:
    qs = questions.questions
    kinds = Counter(q.kind for q in qs)
    langs = Counter(q.lang for q in qs)
    assert len(qs) >= 60 and set(kinds) == set(KINDS) and min(kinds.values()) >= 10, kinds
    assert set(langs) == {"en", "hu"} and min(langs.values()) >= 25, langs
    assert {q.lang for q in qs if q.kind == "inflection"} == {"hu"}


def every_chunk(vault: Vault) -> list[Found]:
    """Every chunk of the index as a hit could give it."""
    conn = sqlite3.connect(db_path(vault))
    try:
        rows = conn.execute("SELECT kind, grp, locator, line_from, line_to, rids, anchors FROM chunks").fetchall()
    finally:
        conn.close()
    return [
        Found(
            rank=1,
            locator=locator,
            group=grp,
            lines=None if first is None else (first, last),
            holds=frozenset(json.loads(rids)) if kind == "thread" else frozenset(),
            cites=tuple(parse_anchor(f"[{a}]") for a in json.loads(anchors)),
        )
        for kind, grp, locator, first, last, rids, anchors in rows
    ]


def test_some_chunk_answers_every_wanted_locator(vault: Vault, questions: QuestionSet) -> None:
    chunks = every_chunk(vault)
    dead = [(q.n, w.text) for q in questions.questions for w in q.want if not any(answers(w, c) for c in chunks)]
    assert dead == []


def floors(file: Path = FLOORS) -> dict[str, dict[str, float]]:
    data: dict[str, dict[str, float]] = json.loads(file.read_text(encoding="utf-8"))
    return {kind: floor for kind, floor in data.items() if not kind.startswith("$")}


def write_report(report: EvalReport, env: str = REPORT) -> None:
    """The numbers per kind, for the gate's full lane (or its models lane) to hold against the floors."""
    path = os.environ.get(env)
    if path:
        numbers = {kind: {"recall": s.recall, "mrr": s.mrr} for kind, s in report.kinds.items()}
        Path(path).write_text(json.dumps(numbers, indent=2) + "\n", encoding="utf-8")


def test_no_kind_falls_below_its_floor(vault: Vault, questions: QuestionSet) -> None:
    report = run(vault, questions, k=10)
    write_report(report)
    below_floors(report, floors())
    assert report.unknown == [] and not any(r.refused for r in report.questions)


def below_floors(report: EvalReport, wanted: dict[str, dict[str, float]]) -> None:
    assert set(report.kinds) == set(wanted)
    below = [
        f"{kind} {measure}: {getattr(score, measure):.4f} is below its floor {wanted[kind][measure]}"
        for kind, score in report.kinds.items()
        for measure in ("recall", "mrr")
        if getattr(score, measure) + 1e-9 < wanted[kind][measure]
    ]
    assert below == [], below


@pytest.mark.skipif(not os.environ.get(MODELS), reason="the models: docket models install, then DOCKET_TEST_MODELS")
def test_with_the_embedder_no_kind_falls_below_its_floor(
    tmp_path_factory: pytest.TempPathFactory, questions: QuestionSet, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKET_MODELS_DIR", os.environ[MODELS])
    config.write("cpu", E5_SMALL)
    models = open_models()
    assert isinstance(models, Models) and models.why is None, getattr(models, "why", None)
    try:
        v = copy_vault(eval_template(tmp_path_factory), tmp_path_factory.mktemp("eval-models"))
        rebuild(v)
        embedded = fill(v, models)
        assert embedded.why is None and embedded.have == embedded.total
        report = run(v, questions, k=10, models=models)
    finally:
        models.close()
    write_report(report, MODELS_REPORT)
    assert report.vectors is not None and report.vectors.skipped == 0, report.vectors
    below_floors(report, floors(MODELS_FLOORS))
