"""This machine's models as data (DR-0510): what `docket models status` prints and the Settings page shows, read from
the user's config and the models' folders only."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from models_kit import make_env

from docket import userconfig
from docket.models import install, report


def choose(block: dict[str, Any]) -> None:
    path = userconfig.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"models": block}), encoding="utf-8")


def test_no_block_is_off_with_how_to_turn_them_on() -> None:
    found = report.read()
    assert not found.on
    assert report.lines(found) == ["models     off (docket models install turns them on)"]


def test_a_block_that_doesnt_read_is_off_with_its_reason() -> None:
    choose({"provider": "api", "embedder": "large"})
    found = report.read()
    assert not found.on
    assert found.models.startswith("off: the models block in")
    assert "names an embedder the api provider doesn't have" in found.models


def test_a_choice_shows_its_environment_files_search_and_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    choose({"device": "cpu"})
    monkeypatch.setattr(install, "vectors_problem", lambda: None)
    found = report.read()
    assert found.on
    assert found.environments == []
    assert found.files == "missing: onnx/model.onnx, tokenizer.json"
    assert found.search == "uses the vectors"
    assert found.reranker == "no reranker is installed; docket models install adds it"
    make_env("cpu")
    lines = report.lines(report.read())
    assert lines[0] == "models     e5-small on cpu"
    assert lines[1].startswith("env cpu    ready, batches of 8,192 characters (")
    assert lines[2:] == [
        "files      missing: onnx/model.onnx, tokenizer.json",
        "search     uses the vectors",
        "reranker   no reranker is installed; docket models install adds it",
    ]


def test_the_service_lines_name_the_worker_the_reranker_and_its_last_words() -> None:
    said, errors = report.service_lines(
        {
            "model": "e5-small",
            "state": "down",
            "why": "the worker stopped",
            "reranker": "mmarco-mMiniLMv2-L12-H384-v1",
            "rerank_why": None,
            "reranked": 3,
            "past_budget": 1,
            "errors": ["Traceback (most recent call last):", "OSError: no such file"],
        }
    )
    assert said == ["e5-small, down: the worker stopped", "mmarco-mMiniLMv2-L12-H384-v1: 3 reranked, 1 past the budget"]
    assert errors == ["Traceback (most recent call last):", "OSError: no such file"]
    off, none = report.service_lines({"state": "off", "reranker": "bge", "rerank_why": "too slow here", "errors": None})
    assert off == ["off", "bge: off: too slow here"]
    assert none == []
