from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from devkit import shard
from devkit.repo import KitError

INI = "[pytest]\nmarkers =\n    perf: timed\n    smoke: smoke\n    nightly: nightly\n"


def test_a_share_is_k_of_n_and_nothing_else() -> None:
    assert shard.parse("1/2") == (1, 2)
    assert shard.parse("3/3") == (3, 3)
    for bad in ("0/2", "3/2", "1", "a/b", "1/0", " 1/2"):
        with pytest.raises(ValueError, match="K/N"):
            shard.parse(bad)


def test_the_shares_split_the_files_whole_and_alternate_in_path_order() -> None:
    files = {"b/test_x.py", "a/test_y.py", "a/test_z.py", "c/test_w.py", "a/test_a.py"}
    first, second = shard.share(files, 1, 2), shard.share(files, 2, 2)
    assert first == {"a/test_a.py", "a/test_z.py", "c/test_w.py"}
    assert second == {"a/test_y.py", "b/test_x.py"}
    assert shard.share(files, 1, 1) == files


def test_weighed_files_go_heaviest_first_to_the_lightest_share() -> None:
    weights = {"a.py": 10.0, "b.py": 6.0, "c.py": 5.0, "d.py": 4.0, "e.py": 1.0}
    shares = [shard.share(set(weights), k, 2, weights) for k in (1, 2)]
    # a to the first, b and c to the second (11), d to the first (14), e to the second: 14 against 12.
    assert shares == [{"a.py", "d.py"}, {"b.py", "c.py", "e.py"}]
    # Alternating in path order would have put a, c and e together: 16 against 10.
    assert shard.share(set(weights), 1, 2) == {"a.py", "c.py", "e.py"}


def test_a_file_the_weights_don_t_know_weighs_their_median() -> None:
    weights = {"a.py": 9.0, "b.py": 3.0, "c.py": 1.0, "gone.py": 100.0}
    files = {"a.py", "b.py", "c.py", "new.py"}
    # The median of the files that are here, 3: a to one share, then b and new to the other, c to the lighter.
    assert shard.share(files, 1, 2, weights) == {"a.py"}
    assert shard.share(files, 2, 2, weights) == {"b.py", "c.py", "new.py"}


def test_each_system_has_weights_of_its_own(tmp_path: Path) -> None:
    path = tmp_path / "weights.json"
    path.write_text(json.dumps({"$comment": "x", "Windows": {"a.py": 2}, "Linux": {"a.py": 1}}), encoding="utf-8")
    assert shard.load_weights(path, "Windows") == {"a.py": 2.0}
    assert shard.load_weights(path, "Darwin") == {}


def systems_file(root: Path, system: list[str], same: list[str]) -> None:
    """A test-systems.json under the root: these files can differ by system, those run the same everywhere."""
    path = root / "tools" / "devkit" / "test-systems.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    listed = {f: {"kind": "the system's own calls", "why": "an invented reason"} for f in system}
    path.write_text(json.dumps({"system": listed, "same": same}), encoding="utf-8")


def logged(name: str, body: str) -> str:
    """A test module that notes in the file SHARD_LOG names that it was imported."""
    note = f"with Path(os.environ['SHARD_LOG']).open('a', encoding='utf-8') as log:\n    log.write('{name}\\n')\n\n"
    return f"import os\nfrom pathlib import Path\n\nimport pytest\n\n{note}{body}"


def two_tests(name: str) -> str:
    return f"def test_{name}_one():\n    pass\n\n@pytest.mark.perf\ndef test_{name}_timed():\n    pass\n"


def pytest_in(folder: Path, *args: str, log: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SHARD_LOG": str(log or folder / "imports.log")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "devkit.shard", "-p", "no:cacheprovider", *args],
        cwd=folder, capture_output=True, text=True, encoding="utf-8", check=False, env=env,
    )  # fmt: skip


def collected(folder: Path, *args: str, log: Path | None = None) -> tuple[int, list[str]]:
    proc = pytest_in(folder, "--collect-only", "-q", *args, log=log)
    return proc.returncode, sorted(ln for ln in proc.stdout.splitlines() if "::" in ln)


def imports(log: Path) -> set[str]:
    return set(log.read_text(encoding="utf-8").split()) if log.is_file() else set()


def test_a_share_imports_only_its_own_files_and_each_file_is_in_one_share(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text(INI, encoding="utf-8")
    names = ("a", "b", "c", "d")
    for name in names:
        (tmp_path / f"test_{name}.py").write_text(logged(f"test_{name}.py", two_tests(name)), encoding="utf-8")
    systems_file(tmp_path, [], [f"test_{name}.py" for name in names])
    code, everything = collected(tmp_path, log=tmp_path / "all.log")
    assert code == 0 and len(everything) == 8
    _, first = collected(tmp_path, "--shard=1/2", log=tmp_path / "1.log")
    _, second = collected(tmp_path, "--shard=2/2", log=tmp_path / "2.log")
    assert first == ["test_a.py::test_a_one", "test_a.py::test_a_timed", "test_c.py::test_c_one",
                     "test_c.py::test_c_timed"]  # fmt: skip
    assert sorted(first + second) == everything and not set(first) & set(second)
    # Each share imported its own files and none of the other's.
    assert imports(tmp_path / "1.log") == {"test_a.py", "test_c.py"}
    assert imports(tmp_path / "2.log") == {"test_b.py", "test_d.py"}
    # The timed run of a share takes its timed tests from the same files, though the marker leaves fewer to split.
    _, timed = collected(tmp_path, "--shard=1/2", "-m", "perf")
    assert timed == ["test_a.py::test_a_timed", "test_c.py::test_c_timed"]
    code, _ = collected(tmp_path, "--shard=3/2")
    assert code != 0


def test_a_test_file_the_list_doesn_t_name_fails_every_share(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text(INI, encoding="utf-8")
    for name in ("a", "b", "new"):
        (tmp_path / f"test_{name}.py").write_text(f"def test_{name}():\n    pass\n", encoding="utf-8")
    systems_file(tmp_path, ["test_a.py"], ["test_b.py"])
    unnamed = "test_new.py: tools/devkit/test-systems.json doesn't name it, so no share holds it: name it there"
    # In whichever share, found in a folder or given by name: no share would run it, so the run fails.
    for args in (["--shard=1/2"], ["-n", "2", "--shard=2/2"], ["--select=system"], ["test_new.py", "--shard=1/1"]):
        proc = pytest_in(tmp_path, "-q", *args)
        assert proc.returncode != 0, args
        assert unnamed in proc.stdout + proc.stderr, args
    # Without the plugin's options the run is pytest's own.
    assert pytest_in(tmp_path, "-q").returncode == 0
    # A file outside the run's arguments isn't the run's.
    assert pytest_in(tmp_path, "-q", "test_a.py", "--shard=1/1").returncode == 0


def test_a_file_whose_every_test_is_nightly_leaves_the_split_and_isn_t_imported(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text(INI, encoding="utf-8")
    big = "pytestmark = pytest.mark.nightly\n\ndef test_big():\n    pass\n"
    mixed = "@pytest.mark.nightly\ndef test_mixed_big():\n    pass\n\ndef test_mixed_small():\n    pass\n"
    for name, body in (("big", big), ("mixed", mixed), ("a", "def test_a():\n    pass\n"),
                       ("b", "def test_b():\n    pass\n")):  # fmt: skip
        (tmp_path / f"test_{name}.py").write_text(logged(f"test_{name}.py", body), encoding="utf-8")
    systems_file(tmp_path, [], ["test_a.py", "test_b.py", "test_big.py", "test_mixed.py"])
    weights = tmp_path / "weights.json"
    weighed = {"test_big.py": 100, "test_a.py": 3, "test_mixed.py": 2, "test_b.py": 1}
    weights.write_text(json.dumps({platform.system(): weighed}), encoding="utf-8")
    split = f"--shard-weights={weights}"
    # With every test, as the nightly runs them, the big file takes a share alone.
    _, first = collected(tmp_path, "--shard=1/2", split)
    assert first == ["test_big.py::test_big"]
    # A PR's run leaves it out before the split, so the other files balance: a against mixed and b, 3 and 3.
    _, first = collected(tmp_path, "--shard=1/2", split, "--leave-out=nightly", log=tmp_path / "1.log")
    _, second = collected(tmp_path, "--shard=2/2", split, "--leave-out=nightly", log=tmp_path / "2.log")
    assert first == ["test_a.py::test_a"]
    assert second == ["test_b.py::test_b", "test_mixed.py::test_mixed_small"]
    assert "test_big.py" not in imports(tmp_path / "1.log") | imports(tmp_path / "2.log")


@pytest.mark.parametrize(
    ("text", "every"),
    [
        ("import pytest\n\npytestmark = pytest.mark.nightly\n", True),
        ("import pytest\n\npytestmark = [pytest.mark.nightly, pytest.mark.skipif(True, reason='x')]\n", True),
        ('"""pytestmark = pytest.mark.nightly, said in a docstring."""\nimport pytest\n', False),
        ("import pytest\n\npytestmark = pytest.mark.skipif(True, reason='not pytest.mark.nightly')\n", False),
        ("import pytest\n\npytestmark = pytest.mark.nightly\npytestmark = pytest.mark.perf\n", False),
        ("import pytest\n\npytestmark = [pytest.mark.nightly]\ndel pytestmark\n", False),
        ("import pytest\n\nif True:\n    pytestmark = pytest.mark.nightly\n", False),
        ("import pytest\n\n@pytest.mark.nightly\ndef test_one():\n    pass\n", False),
        ("pytestmark = pytest.mark.nightly(\n", False),
    ],
)
def test_a_module_marks_every_test_only_by_its_one_top_level_pytestmark(text: str, every: bool) -> None:
    assert shard.marks_every_test(text, "nightly") is every


def test_select_system_keeps_the_listed_files_every_smoke_and_timed_test_and_the_shares_split_those(
    tmp_path: Path,
) -> None:
    (tmp_path / "pytest.ini").write_text(INI, encoding="utf-8")
    for name in ("a", "b", "c", "d"):
        (tmp_path / f"test_{name}.py").write_text(logged(f"test_{name}.py", two_tests(name)), encoding="utf-8")
    smoke = "@pytest.mark.smoke\ndef test_e_smoke():\n    pass\n\ndef test_e_other():\n    pass\n"
    (tmp_path / "test_e.py").write_text(logged("test_e.py", smoke), encoding="utf-8")
    (tmp_path / "test_f.py").write_text(logged("test_f.py", "def test_f():\n    pass\n"), encoding="utf-8")
    systems_file(tmp_path, ["test_a.py", "test_b.py", "test_c.py"], ["test_d.py", "test_e.py", "test_f.py"])
    code, picked = collected(tmp_path, "--select=system", log=tmp_path / "picked.log")
    assert code == 0
    assert picked == [
        "test_a.py::test_a_one",
        "test_a.py::test_a_timed",
        "test_b.py::test_b_one",
        "test_b.py::test_b_timed",
        "test_c.py::test_c_one",
        "test_c.py::test_c_timed",
        # A timed test runs on every system, whatever file holds it (DR-0757).
        "test_d.py::test_d_timed",
        "test_e.py::test_e_smoke",
    ]
    # A file under same that holds neither isn't imported.
    assert "test_f.py" not in imports(tmp_path / "picked.log")
    # The shares split the selected files, whole, and the timed run of a share keeps its files.
    _, first = collected(tmp_path, "--select=system", "--shard=1/2")
    _, second = collected(tmp_path, "--select=system", "--shard=2/2")
    assert sorted(first + second) == picked
    assert first == ["test_a.py::test_a_one", "test_a.py::test_a_timed", "test_c.py::test_c_one",
                     "test_c.py::test_c_timed", "test_e.py::test_e_smoke"]  # fmt: skip
    _, timed = collected(tmp_path, "--select=system", "--shard=1/2", "-m", "perf")
    assert timed == ["test_a.py::test_a_timed", "test_c.py::test_c_timed"]
    (tmp_path / "tools" / "devkit" / "test-systems.json").unlink()
    code, _ = collected(tmp_path, "--select=system")
    assert code != 0


def test_a_run_fails_on_a_timed_test_its_same_file_doesn_t_show(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text(INI, encoding="utf-8")
    # The mark comes from another module, so the file's text never shows it, and --select would leave the file out.
    (tmp_path / "marks.py").write_text("import pytest\n\ntimed = pytest.mark.perf\n", encoding="utf-8")
    hidden = "from marks import timed\n\n@timed\ndef test_hidden():\n    pass\n"
    (tmp_path / "test_g.py").write_text(hidden, encoding="utf-8")
    systems_file(tmp_path, [], ["test_g.py"])
    # Linux's shares run every file, so a PR's run there fails, though Windows' and macOS's never see the file: as
    # the file's collection error, which a run on two workers, as a CI share's, shows.
    proc = pytest_in(tmp_path, "-q", "-n", "2", "--shard=1/1")
    assert proc.returncode != 0
    assert "test_g.py::test_hidden is marked perf, but its file's text shows no pytest.mark.perf" in proc.stdout
    systems_file(tmp_path, ["test_g.py"], [])
    assert pytest_in(tmp_path, "-q", "--shard=1/1").returncode == 0


def test_a_share_lists_the_timed_tests_its_timed_run_takes(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text(INI, encoding="utf-8")
    for name in ("a", "b", "c"):
        (tmp_path / f"test_{name}.py").write_text(f"import pytest\n\n{two_tests(name)}", encoding="utf-8")
    (tmp_path / "test_d.py").write_text("def test_d():\n    pass\n", encoding="utf-8")
    systems_file(tmp_path, [], ["test_a.py", "test_b.py", "test_c.py", "test_d.py"])
    weights = tmp_path / "weights.json"
    weighed = {"test_d.py": 9, "test_a.py": 3, "test_b.py": 3, "test_c.py": 3}
    weights.write_text(json.dumps({platform.system(): weighed}), encoding="utf-8")
    out = tmp_path / "out" / "timed.json"
    split = [f"--shard-weights={weights}"]
    # d, the heaviest, holds the first share alone, and no timed test; the second, run on two workers as a CI share
    # is, holds the rest. Each list is what the share's timed run takes.
    for spec, workers, listed in (
        ("1/2", "0", []),
        ("2/2", "2", ["test_a.py::test_a_timed", "test_b.py::test_b_timed", "test_c.py::test_c_timed"]),
    ):
        proc = pytest_in(tmp_path, "-q", "-n", workers, "-m", "not perf", f"--shard={spec}", *split,
                         f"--shard-timed={out}")  # fmt: skip
        assert proc.returncode == 0, proc.stdout
        _, timed = collected(tmp_path, f"--shard={spec}", *split, "-m", "perf")
        assert shard.timed_tests(out) == timed == listed
    out.write_text("{}", encoding="utf-8")
    assert shard.timed_tests(out) is None
    assert shard.timed_tests(tmp_path / "none.json") is None


def test_a_share_splits_by_its_system_s_weights_and_writes_what_its_files_cost(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text(INI, encoding="utf-8")
    for name in ("a", "b", "c"):
        (tmp_path / f"test_{name}.py").write_text(f"def test_{name}():\n    pass\n", encoding="utf-8")
    (tmp_path / "test_d.py").write_text(
        "import pytest\n\n@pytest.mark.perf\ndef test_d():\n    pass\n", encoding="utf-8"
    )
    systems_file(tmp_path, [], ["test_a.py", "test_b.py", "test_c.py", "test_d.py"])
    weights = tmp_path / "weights.json"
    weighed = {"test_c.py": 9, "test_d.py": 4, "test_a.py": 1}
    weights.write_text(json.dumps({platform.system(): weighed}), encoding="utf-8")
    times = tmp_path / "out" / "times.json"
    run = ["-q", "-m", "not perf", f"--shard-weights={weights}", f"--shard-times={times}"]
    assert pytest_in(tmp_path, *run, "--shard=1/2").returncode == 0
    record = json.loads(times.read_text(encoding="utf-8"))
    # c, the heaviest, alone in the first share; b, which the median puts at 4, d and a in the second.
    assert list(record["files"]) == ["test_c.py"]
    assert {k: record[k] for k in ("system", "share", "marks", "workers")} == {
        "system": platform.system(),
        "share": "1/2",
        "marks": "not perf",
        "workers": 1,
    }
    assert pytest_in(tmp_path, *run, "--shard=2/2").returncode == 0
    files = json.loads(times.read_text(encoding="utf-8"))["files"]
    # d ran nothing in this run: 0, so its weight comes from what its share ran.
    assert list(files) == ["test_a.py", "test_b.py", "test_d.py"] and files["test_d.py"] == 0


def test_a_share_s_times_name_its_failed_tests_and_a_file_that_doesn_t_import(tmp_path: Path) -> None:
    body = (
        "import pytest\n\n"
        "@pytest.fixture\ndef broken():\n    raise RuntimeError('setup')\n\n"
        "def test_passes():\n    pass\n\n"
        "def test_fails():\n    assert 1 == 2\n\n"
        "def test_errors_in_setup(broken):\n    pass\n\n"
        "@pytest.mark.parametrize('n', [1, 2])\ndef test_one_case_fails(n):\n    assert n == 1\n"
    )
    (tmp_path / "test_a.py").write_text(body, encoding="utf-8")
    systems_file(tmp_path, [], ["test_a.py", "test_b.py"])
    times = tmp_path / "out" / "times.json"
    # Two workers, as a CI share runs: the controller gets each worker's reports.
    run = ["-q", "-n", "2", "--shard=1/1", f"--shard-times={times}"]
    proc = pytest_in(tmp_path, *run)
    assert proc.returncode == 1, proc.stdout
    failed = ["test_a.py::test_errors_in_setup", "test_a.py::test_fails", "test_a.py::test_one_case_fails[2]"]
    assert json.loads(times.read_text(encoding="utf-8"))["failed"] == failed
    # A file that doesn't import has no test to fail: its path names it, once though both workers collected it.
    (tmp_path / "test_b.py").write_text("import not_a_module_anywhere\n", encoding="utf-8")
    proc = pytest_in(tmp_path, *run)
    assert proc.returncode != 0, proc.stdout
    assert json.loads(times.read_text(encoding="utf-8"))["failed"] == [*failed, "test_b.py"]


def fake_config(root: Path, options: dict[str, str], args: list[str] | None = None) -> Any:
    """What the plugin reads of a pytest config, for a run started at the root with these options."""
    left: list[Any] = []

    def deselected(*, items: list[Any]) -> None:
        left.extend(items)

    return SimpleNamespace(
        rootpath=root,
        args=args or [str(root)],
        invocation_params=SimpleNamespace(dir=root),
        stash=pytest.Stash(),
        getoption=lambda name, default=None: options.get(name, default),
        getini=lambda name: ["test_*.py"],
        hook=SimpleNamespace(pytest_deselected=deselected),
        option=SimpleNamespace(markexpr=options.get("-m", "")),
        left=left,
    )


def test_the_times_name_a_failed_test_once_whichever_phase_failed(tmp_path: Path) -> None:
    systems_file(tmp_path, [], ["a.py", "b.py", "c.py"])
    options = {"numprocesses": "4", "--shard": "2/3", "--shard-key": "Windows", "-m": "not perf"}
    times = shard.Times(cast(pytest.Config, fake_config(tmp_path, options)), tmp_path / "times.json")

    def ran(nodeid: str, outcome: Any, when: Any = "call") -> None:
        where = (nodeid.split("::", maxsplit=1)[0], 0, nodeid)
        times.pytest_runtest_logreport(pytest.TestReport(nodeid, where, {}, outcome, None, when, duration=2.0))

    ran("a.py::passes", "passed")
    ran("a.py::fails_twice", "failed")
    ran("a.py::fails_twice", "failed", "teardown")
    ran("b.py::skipped", "skipped", "setup")
    times.pytest_collectreport(pytest.CollectReport("c.py", "failed", None, []))
    times.pytest_collectreport(pytest.CollectReport("d.py", "passed", None, []))
    times.pytest_sessionfinish()
    record = json.loads((tmp_path / "times.json").read_text(encoding="utf-8"))
    assert record["failed"] == ["a.py::fails_twice", "c.py"]
    assert record["files"] == {"a.py": 1.5, "b.py": 0.5}
    assert {k: record[k] for k in ("system", "share", "marks", "workers")} == {
        "system": "Windows",
        "share": "2/3",
        "marks": "not perf",
        "workers": 4,
    }


def fake_item(root: Path, rel: str, *marks: str) -> Any:
    return SimpleNamespace(
        path=root / rel,
        nodeid=f"{rel}::test_{'_'.join(marks) or 'plain'}",
        get_closest_marker=lambda name: object() if name in marks else None,
    )


def test_the_hooks_keep_a_share_s_files_and_refuse_a_file_no_share_holds(tmp_path: Path) -> None:
    for rel, text in (("a/test_x.py", ""), ("a/test_y.py", ""), ("b/test_z.py", "@pytest.mark.smoke\n"),
                      ("b/test_q.py", ""), ("b/test_n.py", "pytestmark = pytest.mark.nightly\n")):  # fmt: skip
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(f"import pytest\n\n{text}", encoding="utf-8")
    systems_file(tmp_path, ["a/test_x.py", "a/test_y.py"], ["b/test_z.py", "b/test_q.py", "b/test_n.py"])
    options = {"--select": "system", "--shard": "1/2", "--leave-out": "nightly", "--shard-timed": str(tmp_path / "t")}
    config = fake_config(tmp_path, options)
    found = shard.plan(cast(pytest.Config, config))
    # q holds neither mark and n is the nightly's whole: of x, y and z, x and z go to the first share in path order.
    assert found is not None and found.run == {"a/test_x.py", "a/test_y.py", "b/test_z.py"}
    assert found.mine == {"a/test_x.py", "b/test_z.py"}

    def ignored(rel: str, cfg: Any = config) -> bool | None:
        return shard.pytest_ignore_collect(tmp_path / rel, cast(pytest.Config, cfg))

    # Another share's file and the two left out whole are never collected; a kit isn't a test file, and a test file
    # the list doesn't name is collected, for its collection to fail.
    rels = ("a/test_x.py", "a/test_y.py", "b/test_q.py", "b/test_n.py", "a/kit.py", "a/test_new.py")
    assert [ignored(rel) for rel in rels] == [None, True, True, True, None, None]
    assert ignored("a/test_y.py", fake_config(tmp_path, {})) is None

    def problems(rel: str, *nodes: Any) -> list[str]:
        return shard.collect_problems(tmp_path / rel, cast(pytest.Config, config), list(nodes), found)

    unnamed = "a/test_new.py: tools/devkit/test-systems.json doesn't name it, so no share holds it: name it there"
    assert problems("a/test_new.py") == [unnamed]
    # Under same, a test marked smoke or perf that the file's text doesn't show fails it; one it shows doesn't.
    hidden = fake_item(tmp_path, "b/test_q.py", "perf")
    assert problems("b/test_q.py", hidden, fake_item(tmp_path, "b/test_q.py")) == [
        f"{hidden.nodeid} is marked perf, but its file's text shows no pytest.mark.perf, which is how Windows and "
        "macOS find the files under same that hold one: mark it in the file itself"
    ]
    assert problems("b/test_z.py", fake_item(tmp_path, "b/test_z.py", "smoke")) == []
    assert problems("a/test_x.py", fake_item(tmp_path, "a/test_x.py", "perf")) == []
    assert problems("a") == []

    x, x_timed = fake_item(tmp_path, "a/test_x.py"), fake_item(tmp_path, "a/test_x.py", "perf")
    x_nightly, y = fake_item(tmp_path, "a/test_x.py", "perf", "nightly"), fake_item(tmp_path, "a/test_y.py", "perf")
    z_smoke, z_other = fake_item(tmp_path, "b/test_z.py", "smoke"), fake_item(tmp_path, "b/test_z.py")
    items = [x, x_timed, x_nightly, y, z_smoke, z_other]
    kept = list(items)
    shard.pytest_collection_modifyitems(cast(pytest.Config, config), cast(list[pytest.Item], kept))
    assert kept == [x, x_timed, z_smoke]
    assert config.left == [x_nightly, y, z_other]
    # The share's timed tests, for the gate: the nightly's left out with the rest.
    assert shard.timed_tests(tmp_path / "t") == [x_timed.nodeid]
    # Without the plugin's options every test stays, and with the selection the list is needed.
    plain = list(items)
    shard.pytest_collection_modifyitems(cast(pytest.Config, fake_config(tmp_path, {})), cast(list[pytest.Item], plain))
    assert plain == items
    (tmp_path / "tools" / "devkit" / "test-systems.json").unlink()
    with pytest.raises(pytest.UsageError, match="is missing: it names each test file"):
        shard.plan(cast(pytest.Config, fake_config(tmp_path, {"--select": "system"})))


def times_file(path: Path, run: tuple[str, str, str], files: dict[str, float]) -> None:
    """A share's times file as the plugin writes it, for a run of (system, share, marks)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"system": run[0], "share": run[1], "marks": run[2], "workers": 4, "files": files}
    path.write_text(json.dumps(record), encoding="utf-8")


def test_a_run_s_times_update_each_system_s_weights_and_keep_the_others(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    # A file a share that failed ran: still in the repo, no time from this run.
    (root / "kept.py").write_text("", encoding="utf-8")
    weights = root / "test-weights.json"
    before = {"$comment": "kept", "Darwin": {"a.py": 7}, "Windows": {"gone.py": 1, "kept.py": 2, "a.py": 9}}
    weights.write_text(json.dumps(before), encoding="utf-8")
    run = tmp_path / "run"
    # As gh run download leaves them: an artifact a folder.
    times_file(run / "times-windows-1" / "w1.json", ("Windows", "1/2", "not perf"), {"a.py": 3.0, "b.py": 1.0})
    times_file(run / "times-windows-1" / "w1t.json", ("Windows", "1/2", "perf"), {"a.py": 2.5})
    times_file(run / "times-windows-2" / "w2.json", ("Windows", "2/2", "not perf"), {"c.py": 4.0})
    times_file(run / "times-linux-1" / "l1.json", ("Linux", "1/1", ""), {"a.py": 1.04})
    (run / "notes.json").write_text("[]", encoding="utf-8")
    lines = shard.merge_times(run, weights, root)
    data = json.loads(weights.read_text(encoding="utf-8"))
    assert list(data) == ["$comment", "Darwin", "Linux", "Windows"]
    assert data["$comment"] == "kept" and data["Darwin"] == {"a.py": 7}
    # The run's times win; kept.py keeps its weight; gone.py, no longer in the repo, goes.
    assert data["Windows"] == {"a.py": 5.5, "b.py": 1.0, "c.py": 4.0, "kept.py": 2.0}
    assert data["Linux"] == {"a.py": 1.0}
    assert lines == [
        "Linux: 1 files timed, 1 s; 0 kept as they were",
        "Windows: 3 files timed, 10 s; 1 kept as they were",
    ]


def test_the_times_of_two_runs_or_of_none_are_refused(tmp_path: Path) -> None:
    weights = tmp_path / "test-weights.json"
    with pytest.raises(KitError, match="no times files"):
        shard.merge_times(tmp_path, weights, tmp_path)
    times_file(tmp_path / "one" / "a.json", ("Windows", "1/2", "not perf"), {"a.py": 1.0})
    times_file(tmp_path / "two" / "a.json", ("Windows", "1/2", "not perf"), {"a.py": 1.2})
    with pytest.raises(KitError, match="twice"):
        shard.merge_times(tmp_path, weights, tmp_path)
    assert not weights.exists()
    (tmp_path / "two" / "a.json").unlink()
    shard.merge_times(tmp_path, weights, tmp_path)
    assert json.loads(weights.read_text(encoding="utf-8"))["$comment"] == shard.COMMENT


def test_a_key_of_its_own_reads_its_weights_and_writes_its_times_under_it(tmp_path: Path) -> None:
    for name in ("a", "b", "c"):
        (tmp_path / f"test_{name}.py").write_text(f"def test_{name}():\n    pass\n", encoding="utf-8")
    systems_file(tmp_path, [], ["test_a.py", "test_b.py", "test_c.py"])
    weights = tmp_path / "weights.json"
    # Under the system's own key c weighs least, so it shares the first share with a; under the browser's, most.
    data = {
        platform.system(): {"test_c.py": 1, "test_a.py": 5, "test_b.py": 5},
        "Linux browser": {"test_c.py": 9, "test_a.py": 1, "test_b.py": 1},
    }
    weights.write_text(json.dumps(data), encoding="utf-8")
    times = tmp_path / "out" / "times.json"
    proc = pytest_in(tmp_path, "-q", "--shard=1/2", f"--shard-weights={weights}", f"--shard-times={times}",
                     "--shard-key=Linux browser")  # fmt: skip
    assert proc.returncode == 0, proc.stdout
    record = json.loads(times.read_text(encoding="utf-8"))
    assert list(record["files"]) == ["test_c.py"]
    assert record["system"] == "Linux browser"
