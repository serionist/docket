from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from devkit import docs
from devkit.regime import Regime
from devkit.repo import KitError
from devkit.store import EntryFile, Store
from devkit.yamlio import Comments, emit_file

# The ids the tests create in their own throwaway repo, built so this file cites no decision of the real one.
DR1, DR2, DR3, DR7 = "DR-" + "0001", "DR-" + "0002", "DR-" + "0003", "DR-" + "0007"


def call(*args: str) -> int:
    return docs.main(list(args))


def store(root: Path) -> Store:
    return Store.load(Regime.load(root))


def own(root: Path, key: str, folder: str = "") -> Path:
    """The file of its own that a decision or convention gets in its folder's index.d/."""
    return (root / folder if folder else root) / "index.d" / f"{key}.yaml"


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
    first = s.find(DR1)
    assert first.entry["status"] == "proposed"
    assert first.entry["by"] == "Ada Example"
    assert s.find(DR2).entry["title"] == "Second"
    assert s.check() == []


def test_written_files_are_canonical_and_stable(repo: Path) -> None:
    add_decision("Use: colons, and 2026-10-14 dates")
    before = own(repo, DR1).read_text(encoding="utf-8")
    assert call("format") == 0
    assert own(repo, DR1).read_text(encoding="utf-8") == before
    assert call("check") == 0


def test_an_edit_that_adds_a_problem_is_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    add_decision("First")
    assert call("set", DR1, "--set", "cost=expensive") == 2
    assert "cost must be one of" in capsys.readouterr().err
    assert store(repo).find(DR1).entry["cost"] == "cheap"


def test_the_key_cannot_change(repo: Path) -> None:
    add_decision("First")
    assert call("set", DR1, "--set", "id=" + "DR-" + "0009") == 2


def test_supersede_links_both_ways(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    add_decision("Old")
    add_decision("New")
    (repo / "notes.md").write_text(f"We follow {DR1}.\n", encoding="utf-8")
    assert call("supersede", DR1, "--by", DR2) == 0
    out = capsys.readouterr().out
    assert "notes.md:1" in out
    s = store(repo)
    assert s.find(DR1).entry["superseded_by"] == [DR2]
    assert s.find(DR1).entry["status"] == "superseded"
    assert s.find(DR2).entry["supersedes"] == [DR1]
    problems = [str(p) for p in s.check()]
    assert problems == [f"notes.md:1: cites {DR1}, which is superseded; cite what replaced it"]


def test_citing_an_old_decision_in_a_record_is_history(repo: Path) -> None:
    add_decision("Old")
    add_decision("New")
    (repo / "docs" / "plans").mkdir()
    (repo / "docs" / "plans" / "2026-01-01-1-x.md").write_text(f"Per {DR1}.\n", encoding="utf-8")
    assert call("supersede", DR1, "--by", DR2) == 0
    assert store(repo).check() == []


def test_an_unknown_citation_fails(repo: Path) -> None:
    missing = "CV-" + "0042"  # built, so this file doesn't cite it itself
    (repo / "notes.md").write_text(f"See {missing}.\n", encoding="utf-8")
    assert [str(p) for p in store(repo).check()] == [f"notes.md:1: cites {missing}, which doesn't exist"]


def test_remove_keeps_the_entry(repo: Path) -> None:
    add_decision("Gone")
    assert call("remove", DR1, "--why", "never happened") == 0
    loc = store(repo).find(DR1)
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
    own(repo, DR1).write_text(own(repo, DR1).read_text(encoding="utf-8") + "\n", encoding="utf-8")
    loc = s.find(DR1)
    loc.entry["title"] = "Changed"
    loc.file.dirty = True
    with pytest.raises(Exception, match="changed on disk"):
        s.save({loc.file.rel})


def test_a_hand_edit_out_of_canonical_form_is_reported(repo: Path) -> None:
    add_decision("First")
    path = own(repo, DR1)
    path.write_text(
        path.read_text(encoding="utf-8").replace('at: "', "at: '").replace('"\n    by', "'\n    by"), "utf-8"
    )
    problems = [str(p) for p in store(repo).check()]
    assert problems == [f"index.d/{DR1}.yaml: not in canonical form; run `uv run docs format`"]
    assert call("format") == 0
    assert store(repo).check() == []


def test_a_stray_comment_blocks_format(repo: Path) -> None:
    add_decision("First")
    path = own(repo, DR1)
    path.write_text(path.read_text(encoding="utf-8").replace("    title:", "    # note\n    title:"), "utf-8")
    assert any("comment here is lost" in str(p) for p in store(repo).check())
    assert call("format") == 1


def test_values_come_from_files_and_literal_at_signs(repo: Path, tmp_path: Path) -> None:
    body = tmp_path / "why.md"
    body.write_text("Line one.\n\nLine two.\n", encoding="utf-8")
    args = ["add", "decisions", "--in", ".", "--set", "title=@@handle", "--set", "cost=cheap"]
    assert call(*args, "--set", f"description=@{body}") == 0
    entry = store(repo).find(DR1).entry
    assert entry["title"] == "@handle"
    assert entry["description"] == "Line one.\n\nLine two."


def test_a_value_from_stdin_is_read_once_as_utf8(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from devkit import docs as docs_module

    monkeypatch.setattr(docs_module, "_stdin", [])
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO("Árvíztűrő ok.\r\n".encode())))
    args = ["add", "decisions", "--in", ".", "--set", "title=T", "--set", "cost=cheap", "--set", "description=-"]
    assert call(*args) == 0
    assert store(repo).find(DR1).entry["description"] == "Árvíztűrő ok."


def test_in_dot_names_the_root(repo: Path) -> None:
    (repo / "pkg").mkdir()
    assert call("add", "reading", "--in", ".", "--set", "path=README.md", "--set", "role=guide") == 0
    assert call("add", "reading", "--in", "pkg", "--set", "path=README.md", "--set", "role=guide") == 0
    assert call("set", "README.md", "--in", ".", "--set", "note=the root one") == 0
    assert store(repo).find("README.md", folder="").entry["note"] == "the root one"


def test_supersede_never_drops_a_field_outside_the_schema(repo: Path) -> None:
    add_decision("Old")
    add_decision("New")
    path = own(repo, DR1)
    path.write_text(
        path.read_text(encoding="utf-8").replace("    cost: cheap\n", "    cost: cheap\n    extra: keep me\n", 1),
        "utf-8",
    )
    assert call("supersede", DR1, "--by", DR2) == 2
    assert "keep me" in path.read_text(encoding="utf-8")


def test_a_change_across_two_files_is_written_whole_or_not_at_all(repo: Path) -> None:
    (repo / "pkg").mkdir()
    add_decision("Root one")
    args = [
        "add",
        "decisions",
        "--in",
        "pkg",
        "--set",
        "title=Pkg one",
        "--set",
        "cost=cheap",
        "--set",
        "description=x",
    ]
    assert call(*args) == 0
    s = store(repo)
    first, second = s.find(DR1), s.find(DR2)
    first.entry["title"] = "Changed"
    second.entry["title"] = "Changed too"
    first.file.dirty = second.file.dirty = True
    other = own(repo, DR2, "pkg")
    other.write_text(other.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(Exception, match="changed on disk"):
        s.save({first.file.rel, second.file.rel})
    assert "Changed" not in own(repo, DR1).read_text(encoding="utf-8")


# -- a file of its own per decision and convention ----------------------------------------------------------------


def write_index(root: Path, folder: str, *titles: tuple[str, str]) -> Path:
    """An index.yaml holding decisions the way the tool wrote them before they had files of their own."""
    kind = store(root).regime.kind("decisions")
    entries = [
        {"id": i, "title": t, "status": "proposed", "at": "2026-01-01", "by": "Ada Example", "cost": "cheap"}
        | {"description": f"Why {t}."}
        for i, t in titles
    ]
    path = (root / folder if folder else root) / "index.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(emit_file([("decisions", entries, kind.shape)], Comments(), where="test"), "utf-8", newline="\n")
    return path


def test_a_new_decision_or_convention_gets_a_file_of_its_own(repo: Path) -> None:
    (repo / "pkg").mkdir()
    add_decision("Root rule")
    args = ["add", "conventions", "--in", "pkg", "--set", "rule=Be kind", "--set", "area=all"]
    assert call(*args, "--set", "review_only=true") == 0
    assert own(repo, DR1).read_text(encoding="utf-8").startswith(f"decisions:\n  - id: {DR1}\n    title: Root rule\n")
    assert own(repo, "CV-0001", "pkg").read_text(encoding="utf-8").startswith("conventions:\n  - id: CV-0001\n")
    assert not (repo / "index.yaml").exists()
    assert not (repo / "pkg" / "index.yaml").exists()
    assert call("add", "reading", "--in", ".", "--set", "path=README.md", "--set", "role=guide") == 0
    assert "README.md" in (repo / "index.yaml").read_text(encoding="utf-8")
    assert store(repo).check() == []


def test_entries_in_an_index_yaml_still_change_where_they_are(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    index = write_index(repo, "", (DR1, "Old"))
    add_decision("New")
    assert call("set", DR1, "--set", "status=decided") == 0
    assert call("supersede", DR1, "--by", DR2) == 0
    s = store(repo)
    assert s.find(DR1).file.rel == "index.yaml"
    assert s.find(DR1).entry["superseded_by"] == [DR2]
    assert s.find(DR2).file.rel == f"index.d/{DR2}.yaml"
    assert s.find(DR2).entry["supersedes"] == [DR1]
    assert call("remove", DR2, "--why", "never happened") == 0
    assert "removed_why: never happened" in own(repo, DR2).read_text(encoding="utf-8")
    assert "status: superseded" in index.read_text(encoding="utf-8")
    capsys.readouterr()
    assert call("list", "decisions", "--all", "--path", "pkg/x.py") == 0
    assert len(capsys.readouterr().out.splitlines()) == 2
    assert store(repo).check() == []


def test_door_counts_both_places_and_names_a_folder_with_entry_files_only(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "pkg").mkdir()
    write_index(repo, "", (DR1, "Old"))
    assert (
        call("add", "decisions", "--in", ".", "--set", "title=New", "--set", "cost=cheap", "--set", "description=x")
        == 0
    )
    assert (
        call("add", "decisions", "--in", "pkg", "--set", "title=Pkg", "--set", "cost=cheap", "--set", "description=x")
        == 0
    )
    capsys.readouterr()
    assert call("door", "pkg/x.py") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[:2] == ["index.yaml", "pkg/index.d/"]
    assert "binding here: 3 decisions (3 proposed" in out


@pytest.mark.parametrize(
    ("rel", "text", "problem"),
    [
        (f"index.d/{DR1}.yaml", "two", "holds 2 entries; a file in index.d/ holds one"),
        (f"index.d/{DR7}.yaml", "one", f"holds {DR1}, so its name is {DR1}.yaml"),
        ("index.d/README.yaml", "reading", "top-level key 'reading' isn't one of decisions, conventions"),
        ("index.d/notes.md", "text", "only <id>.yaml files belong in index.d/, right inside it"),
        (f"index.d/old/{DR1}.yaml", "one", "only <id>.yaml files belong in index.d/, right inside it"),
        (f"pkg/index.d/{DR1}.yaml", "empty", "holds 0 entries; a file in index.d/ holds one"),
    ],
)
def test_a_file_in_index_d_holds_one_entry_named_by_its_key(repo: Path, rel: str, text: str, problem: str) -> None:
    add_decision("First")
    add_decision("Second")
    first, second = (own(repo, k).read_text(encoding="utf-8") for k in (DR1, DR2))
    own(repo, DR1).unlink()
    own(repo, DR2).unlink()
    body = {
        "one": first,
        "two": first + second.removeprefix("decisions:\n"),
        "reading": "reading:\n  - { path: README.md, role: guide }\n",
        "text": "notes\n",
        "empty": "decisions: []\n",
    }[text]
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    (repo / "index.d" / ".DS_Store").write_bytes(b"\0\1")
    assert [str(p) for p in store(repo).check(citations=False)] == [f"{rel}: {problem}"]


@pytest.mark.parametrize(
    ("kind", "folder", "fields"),
    [
        ("decisions", "index.d", ["title=T", "cost=cheap", "description=x"]),
        ("conventions", "pkg/index.d", ["rule=Be kind", "area=all", "review_only=true"]),
        ("reading", "index.d", ["path=README.md", "role=guide"]),
    ],
)
def test_add_refuses_a_folder_inside_index_d(
    repo: Path, capsys: pytest.CaptureFixture[str], kind: str, folder: str, fields: list[str]
) -> None:
    add_decision("First")
    (repo / "pkg" / "index.d").mkdir(parents=True)
    before = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if ".git" not in p.parts)
    args = ["add", kind, "--in", folder]
    for f in fields:
        args += ["--set", f]
    assert call(*args) == 2
    assert "is inside an index.d/ folder" in capsys.readouterr().err
    assert sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if ".git" not in p.parts) == before
    assert store(repo).check() == []


def test_a_file_made_in_memory_inside_index_d_fails_the_check(repo: Path) -> None:
    add_decision("First")
    s = store(repo)
    entry = dict(s.find(DR1).entry, id=DR2)
    for rel, single in ((f"index.d/index.d/{DR2}.yaml", True), ("index.d/index.yaml", False)):
        f = EntryFile(rel=rel, folder="", raw="", created=True, dirty=True, single=single)
        f.sections["decisions"] = [entry]
        s.files[rel] = f
        assert f"{rel}: only <id>.yaml files belong in index.d/, right inside it" in [str(p) for p in s.check()]
        del s.files[rel]


def test_move_from_refuses_an_entry_that_sat_inside_index_d(repo: Path, git: Callable[..., str]) -> None:
    branches(repo, git)
    add_decision("First")
    write_index(repo, "index.d", (DR7, "Misplaced"))
    commit(repo, git, "an index.yaml where it doesn't belong")
    git(repo, "rm", "-q", "index.d/index.yaml")
    commit(repo, git, "gone")
    assert call("move", DR7, "--from", "HEAD~1") == 2
    assert not (repo / "index.d" / "index.d").exists()
    assert store(repo).check() == []


def test_one_id_in_an_index_yaml_and_in_a_file_of_its_own_is_reported(repo: Path) -> None:
    write_index(repo, "", (DR1, "Old"))
    add_decision("Other")
    own(repo, DR2).rename(own(repo, DR1))
    own(repo, DR1).write_text(own(repo, DR1).read_text(encoding="utf-8").replace(DR2, DR1), "utf-8")
    assert any("a key is used once" in str(p) for p in store(repo).check())


def test_the_regime_names_entry_dir_and_entry_kinds_together(repo: Path) -> None:
    path = repo / "docs" / "docs-regime.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("  entry_dir: index.d\n", ""), encoding="utf-8")
    with pytest.raises(KitError, match="go together"):
        Regime.load(repo)
    path.write_text(text.replace("entry_kinds: [decisions, conventions]", "entry_kinds: [reading]"), encoding="utf-8")
    with pytest.raises(KitError, match="isn't a numbered folder kind"):
        Regime.load(repo)
    path.write_text(text.replace("entry_dir: index.d", "entry_dir: a/b"), encoding="utf-8")
    with pytest.raises(KitError, match="one folder name"):
        Regime.load(repo)


# -- merging: two branches, one base, real git ---------------------------------------------------------------------


def branches(repo: Path, git: Callable[..., str]) -> None:
    """Settings that make the tests' rebases the same on every machine."""
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "config", "core.editor", "true")
    (repo / "pkg").mkdir(exist_ok=True)
    (repo / "pkg" / "code.py").write_text("x = 1\n", encoding="utf-8")


def commit(repo: Path, git: Callable[..., str], message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def add_in(folder: str, ident: str, title: str) -> None:
    args = ["add", "decisions", "--in", folder, "--set", f"id={ident}", "--set", f"title={title}"]
    assert call(*args, "--set", "cost=cheap", "--set", f"description=Why {title}.") == 0


def rebase(repo: Path, onto: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "rebase", onto], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False
    )


def test_two_branches_that_add_decisions_rebase_without_a_conflict(repo: Path, git: Callable[..., str]) -> None:
    branches(repo, git)
    add_decision("Shared")
    commit(repo, git, "base")
    git(repo, "switch", "-q", "-c", "a")
    add_in("pkg", DR3, "Alpha")
    commit(repo, git, "a adds a decision")
    git(repo, "switch", "-q", "main")
    git(repo, "switch", "-q", "-c", "b")
    add_in("pkg", DR7, "Beta")
    commit(repo, git, "b adds a decision")
    done = rebase(repo, "a")
    assert done.returncode == 0, done.stdout + done.stderr
    s = store(repo)
    assert s.find(DR3).entry["title"] == "Alpha"
    assert s.find(DR7).entry["title"] == "Beta"
    assert call("check") == 0


def test_the_same_id_in_one_folder_stops_the_rebase(repo: Path, git: Callable[..., str]) -> None:
    branches(repo, git)
    commit(repo, git, "base")
    git(repo, "switch", "-q", "-c", "a")
    add_in("pkg", DR3, "Alpha")
    commit(repo, git, "a")
    git(repo, "switch", "-q", "main")
    git(repo, "switch", "-q", "-c", "b")
    add_in("pkg", DR3, "Beta")
    commit(repo, git, "b")
    assert rebase(repo, "a").returncode != 0
    git(repo, "rebase", "--abort")


def test_the_same_id_in_two_folders_rebases_and_fails_the_check(repo: Path, git: Callable[..., str]) -> None:
    branches(repo, git)
    commit(repo, git, "base")
    git(repo, "switch", "-q", "-c", "a")
    add_in(".", DR3, "Alpha")
    commit(repo, git, "a")
    git(repo, "switch", "-q", "main")
    git(repo, "switch", "-q", "-c", "b")
    add_in("pkg", DR3, "Beta")
    commit(repo, git, "b")
    assert rebase(repo, "a").returncode == 0
    assert any("a key is used once" in str(p) for p in store(repo).check())


def test_an_add_beside_a_change_to_an_old_entry_rebases(repo: Path, git: Callable[..., str]) -> None:
    branches(repo, git)
    write_index(repo, "pkg", (DR1, "Old"), (DR2, "Older"))
    commit(repo, git, "base")
    git(repo, "switch", "-q", "-c", "a")
    assert call("set", DR2, "--set", "status=decided") == 0
    commit(repo, git, "a confirms the last old entry")
    git(repo, "switch", "-q", "main")
    git(repo, "switch", "-q", "-c", "b")
    add_in("pkg", DR3, "New")
    commit(repo, git, "b adds a decision")
    assert rebase(repo, "a").returncode == 0
    s = store(repo)
    assert s.find(DR2).entry["status"] == "decided"
    assert s.find(DR3).file.rel == f"pkg/index.d/{DR3}.yaml"
    assert s.check() == []


# -- moving a branch's own entries ----------------------------------------------------------------------------------


def test_move_takes_an_entry_out_of_index_yaml_unchanged(repo: Path) -> None:
    index = write_index(repo, "", (DR1, "Kept"), (DR2, "Moved"))
    before = store(repo).find(DR2).entry
    assert call("add", "reading", "--in", ".", "--set", "path=README.md", "--set", "role=guide") == 0
    assert call("move", DR2) == 0
    s = store(repo)
    assert s.find(DR2).file.rel == f"index.d/{DR2}.yaml"
    assert s.find(DR2).entry == before
    assert DR2 not in index.read_text(encoding="utf-8")
    assert s.find(DR1).file.rel == "index.yaml"
    assert s.check() == []
    assert call("move", DR2) == 2  # has a file of its own already
    assert call("move", "README.md") == 2  # reading entries stay in index.yaml
    assert call("move", DR7) == 2  # no such entry
    assert call("move", DR2, "--from", "HEAD") == 2  # in the tree already


def test_move_from_a_revision_brings_back_what_a_rebase_dropped(repo: Path, git: Callable[..., str]) -> None:
    branches(repo, git)
    write_index(repo, "pkg", (DR1, "Old"))
    commit(repo, git, "base")
    git(repo, "switch", "-q", "-c", "b")
    # A branch made before entries had files of their own: it appended to the index.yaml.
    write_index(repo, "pkg", (DR1, "Old"), (DR7, "Mine"))
    (repo / "pkg" / "code.py").write_text("x = 2\n", encoding="utf-8")
    commit(repo, git, "b appends a decision")
    git(repo, "switch", "-q", "main")
    write_index(repo, "pkg", (DR1, "Old"), (DR3, "Theirs"))
    commit(repo, git, "main appends a decision")
    git(repo, "switch", "-q", "b")
    assert rebase(repo, "main").returncode != 0
    git(repo, "checkout", "--ours", "--", "pkg/index.yaml")
    git(repo, "add", "pkg/index.yaml")
    git(repo, "rebase", "--continue")
    assert DR7 not in (repo / "pkg" / "index.yaml").read_text(encoding="utf-8")
    assert call("move", DR7, "--from", "ORIG_HEAD") == 0
    s = store(repo)
    assert s.find(DR7).file.rel == f"pkg/index.d/{DR7}.yaml"
    assert s.find(DR7).entry["title"] == "Mine"
    assert s.find(DR3).file.rel == "pkg/index.yaml"
    assert s.check() == []
    assert call("move", DR7, "--from", "ORIG_HEAD") == 2  # in the tree already
    assert call("move", "DR-" + "0099", "--from", "ORIG_HEAD") == 2  # not there either


def superseding_branch_rebased(repo: Path, git: Callable[..., str]) -> Path:
    """Branch b appends DR7 superseding the older DR1; main appends DR3 and edits DR1; b's rebase keeps main's file."""
    branches(repo, git)
    index = write_index(repo, "pkg", (DR1, "Old"))
    commit(repo, git, "base")
    git(repo, "switch", "-q", "-c", "b")
    write_index(repo, "pkg", (DR1, "Old"), (DR7, "Mine"))
    assert call("supersede", DR1, "--by", DR7) == 0
    (repo / "pkg" / "code.py").write_text("x = 2\n", encoding="utf-8")
    commit(repo, git, "b supersedes an old decision")
    git(repo, "switch", "-q", "main")
    write_index(repo, "pkg", (DR1, "Old"), (DR3, "Theirs"))
    assert call("set", DR1, "--set", "description=Why Old, said again on main.") == 0
    commit(repo, git, "main appends a decision and edits the old one")
    git(repo, "switch", "-q", "b")
    assert rebase(repo, "main").returncode != 0
    git(repo, "checkout", "--ours", "--", "pkg/index.yaml")
    git(repo, "add", "pkg/index.yaml")
    git(repo, "rebase", "--continue")
    return index


def test_move_from_restores_the_other_side_of_a_supersede(repo: Path, git: Callable[..., str]) -> None:
    index = superseding_branch_rebased(repo, git)
    assert "superseded" not in index.read_text(encoding="utf-8")
    assert call("set", DR1, "--set", "status=superseded", "--add", f"superseded_by={DR7}") == 2  # DR7 is gone
    assert call("move", DR7, "--from", "ORIG_HEAD") == 0
    s = store(repo)
    assert s.find(DR7).file.rel == f"pkg/index.d/{DR7}.yaml"
    assert s.find(DR7).entry["supersedes"] == [DR1]
    old = s.find(DR1)
    assert old.file.rel == "pkg/index.yaml"
    assert old.entry["status"] == "superseded"
    assert old.entry["superseded_by"] == [DR7]
    assert old.entry["description"] == "Why Old, said again on main."  # only the link comes back, main's edit stays
    assert s.find(DR3).file.rel == "pkg/index.yaml"
    assert s.check() == []


def test_move_from_takes_both_sides_when_the_rebase_dropped_both(repo: Path, git: Callable[..., str]) -> None:
    branches(repo, git)
    write_index(repo, "pkg", (DR1, "Old"))
    commit(repo, git, "base")
    git(repo, "switch", "-q", "-c", "b")
    write_index(repo, "pkg", (DR1, "Old"), (DR2, "Mine"), (DR7, "Mine again"))
    assert call("supersede", DR2, "--by", DR7) == 0
    (repo / "pkg" / "code.py").write_text("x = 2\n", encoding="utf-8")
    commit(repo, git, "b adds a decision and supersedes it")
    git(repo, "switch", "-q", "main")
    write_index(repo, "pkg", (DR1, "Old"), (DR3, "Theirs"))
    commit(repo, git, "main appends a decision")
    git(repo, "switch", "-q", "b")
    assert rebase(repo, "main").returncode != 0
    git(repo, "checkout", "--ours", "--", "pkg/index.yaml")
    git(repo, "add", "pkg/index.yaml")
    git(repo, "rebase", "--continue")
    assert call("move", DR7, "--from", "ORIG_HEAD") == 2  # what it supersedes is gone too
    assert call("move", DR7, DR2, "--from", "ORIG_HEAD") == 0
    s = store(repo)
    assert s.find(DR2).entry["superseded_by"] == [DR7]
    assert s.find(DR7).entry["supersedes"] == [DR2]
    assert s.check() == []


def test_move_from_writes_nothing_when_any_part_refuses(repo: Path, git: Callable[..., str]) -> None:
    index = superseding_branch_rebased(repo, git)
    before = index.read_text(encoding="utf-8")
    assert call("move", DR7, "DR-" + "0099", "--from", "ORIG_HEAD") == 2  # the second isn't at the revision
    assert index.read_text(encoding="utf-8") == before
    assert not own(repo, DR7, "pkg").exists()
    assert store(repo).check() == []
