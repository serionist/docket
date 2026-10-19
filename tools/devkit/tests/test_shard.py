from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from devkit import shard
from devkit.repo import KitError


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


def collected(folder: Path, *args: str) -> tuple[int, list[str]]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "devkit.shard", "-p", "no:cacheprovider", "--collect-only", "-q", *args],
        cwd=folder, capture_output=True, text=True, encoding="utf-8", check=False,
    )  # fmt: skip
    return proc.returncode, sorted(ln for ln in proc.stdout.splitlines() if "::" in ln)


def test_each_run_of_a_share_keeps_the_same_files_whatever_else_filters(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    perf: timed\n", encoding="utf-8")
    for name in ("a", "b", "c"):
        body = f"import pytest\n\ndef test_{name}_one():\n    pass\n\n"
        body += f"@pytest.mark.perf\ndef test_{name}_timed():\n    pass\n"
        (tmp_path / f"test_{name}.py").write_text(body, encoding="utf-8")
    code, everything = collected(tmp_path)
    assert code == 0 and len(everything) == 6
    _, first = collected(tmp_path, "--shard=1/2")
    _, second = collected(tmp_path, "--shard=2/2")
    assert first == [
        "test_a.py::test_a_one",
        "test_a.py::test_a_timed",
        "test_c.py::test_c_one",
        "test_c.py::test_c_timed",
    ]
    assert sorted(first + second) == everything
    # The timed run of a share takes its timed tests from the same files, though the marker leaves fewer to split.
    _, timed = collected(tmp_path, "--shard=1/2", "-m", "perf")
    assert timed == ["test_a.py::test_a_timed", "test_c.py::test_c_timed"]
    code, _ = collected(tmp_path, "--shard=3/2")
    assert code != 0


def test_a_share_splits_by_its_system_s_weights_and_writes_what_its_files_cost(tmp_path: Path) -> None:
    for name in ("a", "b", "c"):
        (tmp_path / f"test_{name}.py").write_text(f"def test_{name}():\n    pass\n", encoding="utf-8")
    weights = tmp_path / "weights.json"
    weights.write_text(json.dumps({platform.system(): {"test_c.py": 5, "test_a.py": 1}}), encoding="utf-8")
    times = tmp_path / "out" / "times.json"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "devkit.shard", "-p", "no:cacheprovider", "-q", "--shard=1/2",
         f"--shard-weights={weights}", f"--shard-times={times}"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout
    record = json.loads(times.read_text(encoding="utf-8"))
    # c, the heaviest, alone in the first share; a and b, which the median puts at 3, in the second.
    assert list(record["files"]) == ["test_c.py"]
    assert {k: record[k] for k in ("system", "share", "marks", "workers")} == {
        "system": platform.system(),
        "share": "1/2",
        "marks": "",
        "workers": 1,
    }


def times_file(path: Path, run: tuple[str, str, str], files: dict[str, float]) -> None:
    """A share's times file as the plugin writes it, for a run of (system, share, marks)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"system": run[0], "share": run[1], "marks": run[2], "workers": 4, "files": files}
    path.write_text(json.dumps(record), encoding="utf-8")


def test_a_run_s_times_rewrite_each_system_s_weights_and_keep_the_others(tmp_path: Path) -> None:
    weights = tmp_path / "test-weights.json"
    before = {"$comment": "kept", "Darwin": {"a.py": 7}, "Windows": {"old.py": 1}}
    weights.write_text(json.dumps(before), encoding="utf-8")
    run = tmp_path / "run"
    # As gh run download leaves them: an artifact a folder.
    times_file(run / "times-windows-1" / "w1.json", ("Windows", "1/2", "not perf"), {"a.py": 3.0, "b.py": 1.0})
    times_file(run / "times-windows-1" / "w1t.json", ("Windows", "1/2", "perf"), {"a.py": 2.5})
    times_file(run / "times-windows-2" / "w2.json", ("Windows", "2/2", "not perf"), {"c.py": 4.0})
    times_file(run / "times-linux-1" / "l1.json", ("Linux", "1/1", ""), {"a.py": 1.04})
    (run / "notes.json").write_text("[]", encoding="utf-8")
    lines = shard.merge_times(run, weights)
    data = json.loads(weights.read_text(encoding="utf-8"))
    assert list(data) == ["$comment", "Darwin", "Linux", "Windows"]
    assert data["$comment"] == "kept" and data["Darwin"] == {"a.py": 7}
    assert data["Windows"] == {"a.py": 5.5, "b.py": 1.0, "c.py": 4.0}
    assert data["Linux"] == {"a.py": 1.0}
    assert lines == ["Linux: 1 files, 1 s", "Windows: 3 files, 10 s"]


def test_the_times_of_two_runs_or_of_none_are_refused(tmp_path: Path) -> None:
    weights = tmp_path / "test-weights.json"
    with pytest.raises(KitError, match="no times files"):
        shard.merge_times(tmp_path, weights)
    times_file(tmp_path / "one" / "a.json", ("Windows", "1/2", "not perf"), {"a.py": 1.0})
    times_file(tmp_path / "two" / "a.json", ("Windows", "1/2", "not perf"), {"a.py": 1.2})
    with pytest.raises(KitError, match="twice"):
        shard.merge_times(tmp_path, weights)
    assert not weights.exists()
    (tmp_path / "two" / "a.json").unlink()
    shard.merge_times(tmp_path, weights)
    assert json.loads(weights.read_text(encoding="utf-8"))["$comment"] == shard.COMMENT
