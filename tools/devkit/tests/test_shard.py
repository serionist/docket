from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from devkit import shard


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
