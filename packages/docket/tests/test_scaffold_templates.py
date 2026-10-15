"""The source templates the wheel carries: the blank one, and the Plaud reference, kept byte for byte with the example
under examples/sources/plaud, which stays the reference."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from docket_testkit import sh

from docket.errors import DocketError
from docket.sources import scaffold

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "examples" / "sources" / "plaud"
TEMPLATES = "packages/docket/src/docket/sources/templates"
# What a run of the example's tests leaves beside it, never part of the reference.
LEFTOVERS = {"__pycache__", ".pytest_cache"}


def example_files() -> dict[str, bytes]:
    return {
        p.relative_to(EXAMPLE).as_posix(): p.read_bytes()
        for p in sorted(EXAMPLE.rglob("*"))
        if p.is_file() and not LEFTOVERS & set(p.relative_to(EXAMPLE).parts)
    }


def test_the_plaud_copy_is_the_example_byte_for_byte() -> None:
    expected = example_files()
    copied = scaffold.template_files("plaud")
    assert sorted(copied) == sorted(expected), f"the files under {TEMPLATES}/plaud differ from the example's"
    for rel, data in expected.items():
        assert copied[rel] == data, f"copy examples/sources/plaud/{rel} to {TEMPLATES}/plaud/{rel}.tmpl"


def test_the_blank_template_is_a_skill_a_mapper_and_tests_in_lf() -> None:
    files = scaffold.template_files("blank")
    assert sorted(files) == [
        "SKILL.md",
        "mapper.py",
        "tests/conftest.py",
        "tests/test_mapper.py",
        "tests/test_with_docket.py",
    ]
    for rel, data in files.items():
        assert b"\r\n" not in data, rel
        assert b"{{source}}" in data or rel == "tests/test_mapper.py", rel


def test_a_template_that_doesn_t_exist_is_refused() -> None:
    with pytest.raises(DocketError, match="no template 'nothing'"):
        scaffold.template_files("nothing")


def load(path: Path, name: str, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up by name, as they do when Docket runs a mapper.
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_the_blank_mapper_reads_results_as_plaud_s_does(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blank = tmp_path / "mapper.py"
    blank.write_bytes(scaffold.template_files("blank")["mapper.py"].replace(b"{{source}}", b"zebra-notes"))
    mine = load(blank, "blank_template_mapper", monkeypatch)
    plaud = load(EXAMPLE / "mapper.py", "plaud_reference_mapper", monkeypatch)
    texts = [{"type": "text", "text": 'Said: {"items": [1]} and more.'}]
    results: list[Any] = [
        {"structuredContent": {"a": 1}},
        {"content": texts},
        texts,
        "preamble [1, 2] trailing",
        "no json here { at all",
        {"a": 1},
        None,
        [],
        42,
    ]
    for result in results:
        assert mine.unwrap(result) == plaud.unwrap(result), result
    for text in ('x {"a": [1]} y', "[", "{ not json } [3]", ""):
        assert mine.first_json(text) == plaud.first_json(text), text


def test_the_wheel_carries_every_template(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - the gate runs under uv
        pytest.skip("uv isn't on PATH")
    sh(REPO, uv, "build", "--package", "docket", "--wheel", "--quiet", "-o", str(tmp_path))
    (wheel,) = tmp_path.glob("docket-*.whl")
    names = set(zipfile.ZipFile(wheel).namelist())
    for kind in ("blank", "plaud"):
        for rel in scaffold.template_files(kind):
            assert f"docket/sources/templates/{kind}/{rel}.tmpl" in names
