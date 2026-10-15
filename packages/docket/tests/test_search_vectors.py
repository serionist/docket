"""The vector stage (DR-0337, DR-0338): a chunk only the vectors find, the search's filters on the vector list, its ids
checked against the chunks, the facets, and every way it is skipped, each leaving keyword search as it was."""

from __future__ import annotations

import importlib
import sqlite3
import time
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import copy_vault, index_template, make_record
from models_kit import FAKE, fake_models, trigram_vector
from test_index_vectors import choose

from docket import questions, recall, render
from docket.index import build, query, rebuild, search, update, vectors
from docket.index.embedpass import fill
from docket.models.provider import Off
from docket.vault.vault import Vault
from docket.web.views import vectors_line

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
ZEBRA = "chat:C0OPS/1791480000.000100"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    choose(monkeypatch)
    v = copy_vault(template, tmp_path)
    # A message keyword search can't reach from the query below, and whose letters the query shares.
    v.write_run(
        "chat",
        [
            make_record(
                rid=ZEBRA,
                container="chat:C0OPS",
                text="Quarterly zebra migration budget approved.",
                ts=datetime(2026, 10, 9, 8, 0, tzinfo=UTC),
            )
        ],
        datetime(2026, 10, 9, 8, 0, tzinfo=UTC),
    )
    rebuild(v)
    fill(v, fake_models()[0])
    return v


def hits(result: query.SearchResult) -> list[query.Hit]:
    return sorted((h for g in result.groups for h in g.hits), key=lambda h: h.rank)


def test_a_chunk_only_the_vectors_find_comes_back(vault: Vault) -> None:
    models, worker = fake_models()
    keyword = search(vault, "zebbra migrashun", now=NOW)
    assert not hits(keyword) or all(h.rid != ZEBRA for h in hits(keyword))
    result = search(vault, "zebbra migrashun", now=NOW, models=models)
    found = hits(result)
    assert found[0].rid == ZEBRA and found[0].found_by == "vector"
    assert result.models.model_dump() == {"state": "on", "model": "fake", "why": None}
    assert worker.queries == ["query: zebbra migrashun"]
    assert not any("no vectors" in line for line in render.search(result, NOW.tzinfo))  # type: ignore[arg-type]


def test_the_searchs_filters_apply_to_the_vector_list(vault: Vault) -> None:
    models, _ = fake_models()
    later = search(vault, "zebbra migrashun", since=date(2026, 10, 10), now=NOW, models=models)
    assert all(h.rid != ZEBRA for h in hits(later))
    other = search(vault, "zebbra migrashun", sources=["mail"], now=NOW, models=models)
    assert hits(other), "the vector list still finds something in the mail"
    assert all(h.rid != ZEBRA for h in hits(other)), [(h.locator, h.found_by) for h in hits(other)]
    conn = sqlite3.connect(build.db_path(vault))
    sources = {r[0]: r[1] for r in conn.execute("SELECT locator, source FROM chunks")}
    conn.close()
    assert {sources[h.locator] for h in hits(other)} == {"mail"}


def test_ids_the_search_cant_hold_are_dropped(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = sqlite3.connect(build.db_path(vault))
    zebra = conn.execute("SELECT id FROM chunks WHERE rid = ?", (ZEBRA,)).fetchone()[0]
    conn.close()
    real = vectors.scan

    def stale(*args: object, **kwargs: object) -> list[tuple[int, str]] | str:
        found = real(*args, **kwargs)  # type: ignore[arg-type]
        assert not isinstance(found, str)
        # An id gone since, and one whose text changed since its vector was made.
        return [(999_999, "gone"), (zebra, "0" * 64), *(f for f in found if f[0] != zebra)]

    monkeypatch.setattr(vectors, "scan", stale)
    result = search(vault, "zebbra migrashun", now=NOW, models=fake_models()[0])
    assert all(h.rid != ZEBRA for h in hits(result))


def test_facets_count_a_vector_only_chunk_only_when_it_made_the_top(vault: Vault) -> None:
    models, _ = fake_models()
    narrow = search(vault, "zebbra migrashun", k=1, now=NOW, models=models)
    wide = search(vault, "zebbra migrashun", k=50, now=NOW, models=models)
    assert sum(narrow.facets["sources"].values()) == 1
    assert sum(wide.facets["sources"].values()) > 1


def test_a_search_right_after_a_re_put_finds_only_vectors_of_chunks_it_holds(vault: Vault) -> None:
    doc = recall.read(vault, "recording-note:2026-10-07-supplier-call").doc
    assert doc is not None
    recall.doc_put(vault, doc.text.replace("above the budget", "well above the budget"))
    update(vault)
    result = search(vault, "supplier budget", now=NOW, models=fake_models()[0])
    assert hits(result) and result.models.state == "on"


@pytest.mark.parametrize(
    ("models", "state", "why"),
    [
        (None, "off", None),
        (Off(), "off", None),
        (
            Off("the models block in config.yaml doesn't read"),
            "skipped",
            "the models block in config.yaml doesn't read",
        ),
    ],
)
def test_without_models_keyword_search_answers_as_it_did(
    vault: Vault, models: Off | None, state: str, why: str | None
) -> None:
    plain = search(vault, "supplier budget", now=NOW)
    result = search(vault, "supplier budget", now=NOW, models=models)
    assert result.groups == plain.groups and result.facets == plain.facets
    assert (result.models.state, result.models.why) == (state, why)
    lines = render.search(result, NOW.tzinfo)  # type: ignore[arg-type]
    assert (f"no vectors in this search: {why}" in lines) == (why is not None)
    assert vectors_line(result.models) == ([f"no vectors in this search: {why}"] if why else [])


def test_a_worker_that_is_down_or_slow_skips_the_stage(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    plain = search(vault, "supplier budget", now=NOW)
    models, worker = fake_models()
    worker.up, worker.why = False, "the models' worker stopped (exit 5)"
    result = search(vault, "supplier budget", now=NOW, models=models)
    assert result.groups == plain.groups
    assert (result.models.state, result.models.why) == ("skipped", "the models' worker stopped (exit 5)")
    worker.up = True
    real = worker.query

    def slow(text: str) -> bytes | str:
        time.sleep(0.5)
        return real(text)

    monkeypatch.setattr(worker, "query", slow)
    monkeypatch.setattr(query, "VECTOR_SECONDS", 0.05)
    result = search(vault, "supplier budget", now=NOW, models=models)
    assert result.groups == plain.groups and result.models.why == "the vector stage took more than 0.05 s"


def test_a_query_of_time_words_only_has_nothing_to_embed(vault: Vault) -> None:
    result = search(vault, "last week", now=NOW, models=fake_models()[0])
    assert result.models.why == "nothing to embed: the query is only time words or filters"


def test_no_table_no_sqlite_vec_or_a_scan_that_fails_skip_the_stage(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    models, _ = fake_models()
    choose(monkeypatch, None)
    rebuild(vault)
    result = search(vault, "supplier budget", now=NOW, models=models)
    assert result.models.why == "the index has no vectors for fake yet"
    choose(monkeypatch)
    rebuild(vault)
    fill(vault, models)

    def missing(name: str) -> object:
        raise ImportError(name)

    with monkeypatch.context() as m:
        m.setattr(importlib, "import_module", missing)
        result = search(vault, "supplier budget", now=NOW, models=models)
    assert result.models.why == "sqlite-vec has no build for this system, so search goes without vectors"
    monkeypatch.setattr(models, "query_vector", lambda words: trigram_vector(words, 8))
    result = search(vault, "supplier budget", now=NOW, models=models)
    assert result.models.why == "the vector scan failed: OperationalError"
    assert vectors.load_extension(object()) == (  # type: ignore[arg-type]
        "this Python's SQLite can't load extensions, so search goes without vectors"
    )
    assert FAKE.dims == 64


def test_eval_says_what_the_vectors_covered_and_how_many_questions_went_without(vault: Vault, tmp_path: Path) -> None:
    file = tmp_path / "questions.yaml"
    file.write_text(
        'schema_version: 1\nasked: "2026-10-14T12:00:00+02:00"\nquestions:\n'
        f'  - {{q: zebbra migrashun, want: ["r:{ZEBRA}"], kind: paraphrase, lang: en}}\n'
        '  - {q: supplier budget, want: ["r:calls:rec-0101"], kind: exact-name, lang: en}\n',
        encoding="utf-8",
    )
    asked = questions.load(file, ZoneInfo(vault.config.owner.time_zone))
    models, worker = fake_models()
    report = questions.run(vault, asked, models=models)
    seen = report.vectors
    assert seen is not None and (seen.model, seen.skipped) == ("fake", 0) and seen.have == seen.total > 0
    assert report.kinds["paraphrase"].recall == 1.0
    lines = render.eval_report(report)
    assert lines[1] == f"vectors   fake: {seen.have} of {seen.total} chunks"
    worker.up, worker.why = False, "the models' worker stopped (exit 5)"
    report = questions.run(vault, asked, models=models)
    assert report.vectors is not None and report.vectors.skipped == 2
    assert "          2 question(s) ran without them: the models' worker stopped (exit 5)" in render.eval_report(report)
    assert questions.run(vault, asked).vectors is None
    assert questions.run(vault, asked, models=Off("off")).vectors is None
