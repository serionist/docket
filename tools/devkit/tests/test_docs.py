from __future__ import annotations

from pathlib import Path

import pytest

from devkit import docs
from devkit.regime import Regime
from devkit.store import Store


def call(*args: str) -> int:
    return docs.main(list(args))


def store(root: Path) -> Store:
    return Store.load(Regime.load(root))


def add_decision(title: str, **fields: str) -> None:
    args = ["add", "decisions", "--in", ".", "--set", f"title={title}", "--set", "cost=cheap"]
    args += ["--set", f"description=Why {title}."]
    for k, v in fields.items():
        args += ["--set", f"{k}={v}"]
    assert call(*args) == 0


def test_add_numbers_decisions_and_fills_defaults(repo: Path) -> None:
    add_decision("First")
    add_decision("Second")
    s = store(repo)
    first = s.find("DR-0001")
    assert first.entry["status"] == "proposed"
    assert first.entry["by"] == "Ada Example"
    assert s.find("DR-0002").entry["title"] == "Second"
    assert s.check() == []


def test_written_files_are_canonical_and_stable(repo: Path) -> None:
    add_decision("Use: colons, and 2026-10-14 dates")
    before = (repo / "index.yaml").read_text(encoding="utf-8")
    assert call("format") == 0
    assert (repo / "index.yaml").read_text(encoding="utf-8") == before
    assert call("check") == 0


def test_an_edit_that_adds_a_problem_is_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    add_decision("First")
    assert call("set", "DR-0001", "--set", "cost=expensive") == 2
    assert "cost must be one of" in capsys.readouterr().err
    assert store(repo).find("DR-0001").entry["cost"] == "cheap"


def test_the_key_cannot_change(repo: Path) -> None:
    add_decision("First")
    assert call("set", "DR-0001", "--set", "id=DR-0009") == 2


def test_supersede_links_both_ways(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    add_decision("Old")
    add_decision("New")
    (repo / "notes.md").write_text("We follow DR-0001.\n", encoding="utf-8")
    assert call("supersede", "DR-0001", "--by", "DR-0002") == 0
    out = capsys.readouterr().out
    assert "notes.md:1" in out
    s = store(repo)
    assert s.find("DR-0001").entry["superseded_by"] == ["DR-0002"]
    assert s.find("DR-0001").entry["status"] == "superseded"
    assert s.find("DR-0002").entry["supersedes"] == ["DR-0001"]
    problems = [str(p) for p in s.check()]
    assert problems == ["notes.md:1: cites DR-0001, which is superseded; cite what replaced it"]


def test_citing_an_old_decision_in_a_record_is_history(repo: Path) -> None:
    add_decision("Old")
    add_decision("New")
    (repo / "docs" / "plans").mkdir()
    (repo / "docs" / "plans" / "2026-01-01-1-x.md").write_text("Per DR-0001.\n", encoding="utf-8")
    assert call("supersede", "DR-0001", "--by", "DR-0002") == 0
    assert store(repo).check() == []


def test_an_unknown_citation_fails(repo: Path) -> None:
    missing = "CV-" + "0042"  # built, so this file doesn't cite it itself
    (repo / "notes.md").write_text(f"See {missing}.\n", encoding="utf-8")
    assert [str(p) for p in store(repo).check()] == [f"notes.md:1: cites {missing}, which doesn't exist"]


def test_remove_keeps_the_entry(repo: Path) -> None:
    add_decision("Gone")
    assert call("remove", "DR-0001", "--why", "never happened") == 0
    loc = store(repo).find("DR-0001")
    assert loc.entry["removed_why"] == "never happened"
    assert not loc.living


def test_conventions_need_a_check_or_review_only(repo: Path) -> None:
    assert call("add", "conventions", "--in", ".", "--set", "rule=Be kind", "--set", "area=all") == 2
    args = ["add", "conventions", "--in", ".", "--set", "rule=Be kind", "--set", "area=all"]
    assert call(*args, "--set", "review_only=true") == 0
    assert store(repo).find("CV-0001").entry["review_only"] is True


def test_terms_are_sorted_and_found_ignoring_case(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert call("add", "terms", "--set", "term=zebra", "--set", "definition=A striped animal.") == 0
    assert call("add", "terms", "--set", "term=Apple", "--set", "definition=A fruit.") == 0
    text = (repo / "docs" / "glossary.yaml").read_text(encoding="utf-8")
    assert text.index("Apple") < text.index("zebra")
    assert call("show", "apple") == 0
    assert "A fruit." in capsys.readouterr().out
    assert call("add", "terms", "--set", "term=APPLE", "--set", "definition=Again.") == 2


def test_door_lists_reading_from_the_root_down(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "pkg" / "sub").mkdir(parents=True)
    (repo / "pkg" / "guide.md").write_text("x\n", encoding="utf-8")
    assert call("add", "reading", "--in", ".", "--set", "path=README.md", "--set", "role=guide") == 0
    assert call("add", "reading", "--in", "pkg", "--set", "path=pkg/guide.md", "--set", "role=guide") == 0
    add_decision("Root rule")
    capsys.readouterr()
    assert call("door", "pkg/sub/file.py") == 0
    out = capsys.readouterr().out
    assert out.index("README.md") < out.index("pkg/guide.md")
    assert "1 decisions (1 proposed" in out


def test_reading_paths_must_exist(repo: Path) -> None:
    assert call("add", "reading", "--in", ".", "--set", "path=missing.md", "--set", "role=guide") == 2


def test_a_file_changed_on_disk_is_not_overwritten(repo: Path) -> None:
    add_decision("First")
    s = store(repo)
    (repo / "index.yaml").write_text((repo / "index.yaml").read_text(encoding="utf-8") + "\n", encoding="utf-8")
    loc = s.find("DR-0001")
    loc.entry["title"] = "Changed"
    loc.file.dirty = True
    with pytest.raises(Exception, match="changed on disk"):
        s.save({loc.file.rel})


def test_a_hand_edit_out_of_canonical_form_is_reported(repo: Path) -> None:
    add_decision("First")
    path = repo / "index.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace('at: "', "at: '").replace('"\n    by', "'\n    by"), "utf-8"
    )
    problems = [str(p) for p in store(repo).check()]
    assert problems == ["index.yaml: not in canonical form; run `uv run docs format`"]
    assert call("format") == 0
    assert store(repo).check() == []


def test_a_stray_comment_blocks_format(repo: Path) -> None:
    add_decision("First")
    path = repo / "index.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("    title:", "    # note\n    title:"), "utf-8")
    assert any("comment here is lost" in str(p) for p in store(repo).check())
    assert call("format") == 1


def test_values_come_from_files_and_literal_at_signs(repo: Path, tmp_path: Path) -> None:
    body = tmp_path / "why.md"
    body.write_text("Line one.\n\nLine two.\n", encoding="utf-8")
    args = ["add", "decisions", "--in", ".", "--set", "title=@@handle", "--set", "cost=cheap"]
    assert call(*args, "--set", f"description=@{body}") == 0
    entry = store(repo).find("DR-0001").entry
    assert entry["title"] == "@handle"
    assert entry["description"] == "Line one.\n\nLine two."
