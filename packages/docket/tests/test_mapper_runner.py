"""Running a source's mapper: finding it, the path rules, its own process and its limits. The mappers are tiny ones
written per test into a temp consuming project; the core never runs the Plaud example here."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from docket_testkit import template_project, write_mapper

from docket.errors import DocketError
from docket.sources import child, runner
from docket.sources.runner import MapperFailedError, consuming_repo, find_mapper
from docket.vault.config import SourceConfig
from docket.vault.vault import Vault

ECHO = """\
def map_run(run):
    print("a debugging line")
    return {"source": run["source"], "n": run["n"]}
"""
PRIVATE_TITLE = "Dentist with Cleo"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    # Built once: a vault takes seconds to make on some systems, and these tests only read it.
    return Vault.open(template_project(tmp_path_factory, {"plaud": ECHO}) / "vault")


@pytest.fixture
def vault(project: Vault) -> Vault:
    # Each test gets its own copy of the config to change; the files stay shared.
    return Vault(project.root, project.config.model_copy(deep=True))


def repo_of(vault: Vault) -> Path:
    return vault.root.parent


def test_a_mapper_runs_in_its_own_process_and_its_prints_go_to_stderr(vault: Vault) -> None:
    path = find_mapper(vault, "plaud")
    assert path == (repo_of(vault) / ".claude" / "skills" / "sources" / "plaud" / "mapper.py").resolve()
    assert runner.run(path, {"source": "plaud", "n": 3}) == {"source": "plaud", "n": 3}


@pytest.mark.parametrize(
    "mapper",
    [
        "/elsewhere/mapper.py",
        ".claude/skills/sources/plaud/../../../../mapper.py",
        ".claude/skills/sources/other/mapper.py",
        "vault/mapper.py",
        ".claude/skills/sources/plaud",
    ],
)
def test_a_mapper_outside_its_source_s_folder_is_refused(vault: Vault, mapper: str) -> None:
    # Past vault.yaml's own checks, as a hand-edited config read by an older check would be.
    vault.config.sources["plaud"] = SourceConfig.model_construct(sensitivity="mixed", mapper=mapper)
    with pytest.raises(DocketError, match=r"must be a file under \.claude/skills/sources/plaud/"):
        find_mapper(vault, "plaud")


def test_a_traversing_source_name_is_refused_before_any_path_is_built(vault: Vault) -> None:
    vault.config.sources["../.."] = SourceConfig.model_construct(sensitivity="mixed", mapper="mapper.py")
    with pytest.raises(DocketError, match="isn't a source name"):
        find_mapper(vault, "../..")


def test_a_source_without_a_mapper_or_its_file_is_refused(vault: Vault) -> None:
    with pytest.raises(DocketError, match=r"isn't in vault\.yaml"):
        find_mapper(vault, "slack")
    with pytest.raises(DocketError, match=r"has no mapper in vault\.yaml"):
        find_mapper(vault, "calls")
    vault.config.sources["calls"] = SourceConfig(sensitivity="mixed", mapper=".claude/skills/sources/calls/mapper.py")
    with pytest.raises(DocketError, match=r"no mapper at \.claude/skills/sources/calls/mapper\.py"):
        find_mapper(vault, "calls")


def test_a_mapper_linked_from_outside_is_refused(vault: Vault, tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "mapper.py").write_text(ECHO, encoding="utf-8")
    skills = repo_of(vault) / ".claude" / "skills" / "sources"
    try:
        (skills / "linked").mkdir()
        (skills / "linked" / "mapper.py").symlink_to(downloads / "mapper.py")
        (skills / "folder").symlink_to(downloads, target_is_directory=True)
    except OSError:
        pytest.skip("this system can't make a symlink here")
    for source in ("linked", "folder"):
        mapper = f".claude/skills/sources/{source}/mapper.py"
        vault.config.sources[source] = SourceConfig(sensitivity="mixed", mapper=mapper)
        with pytest.raises(DocketError, match="after links are followed"):
            find_mapper(vault, source)


def test_a_vault_outside_any_git_repo_runs_no_mapper(tmp_path: Path) -> None:
    with pytest.raises(DocketError, match="isn't inside a git repo"):
        consuming_repo(tmp_path / "loose" / "vault")


def test_a_mapper_past_its_time_limit_is_stopped(tmp_path: Path) -> None:
    path = write_mapper(tmp_path, "slow", "import time\n\ndef map_run(run):\n    time.sleep(60)\n")
    with pytest.raises(MapperFailedError, match="ran past its time limit of 1 s"):
        runner.run(path, {}, time_limit=1)


def test_a_helper_left_holding_the_output_fails_the_run_at_its_limit(tmp_path: Path) -> None:
    # The mapper answers at once, but a helper it started keeps its stdout and stderr open for longer than the limit.
    code = (
        "import subprocess, sys\n\n"
        "def map_run(run):\n"
        "    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "    return {}\n"
    )
    started = time.monotonic()
    with pytest.raises(MapperFailedError, match="the mapper left a process holding its output"):
        runner.run(write_mapper(tmp_path, "helper", code), {}, time_limit=3)
    assert time.monotonic() - started < 15


def test_output_past_the_cap_stops_the_mapper(tmp_path: Path) -> None:
    path = write_mapper(tmp_path, "flood", "def map_run(run):\n    return 'x' * 100000\n")
    with pytest.raises(MapperFailedError, match="passed the cap of 1000 bytes"):
        runner.run(path, {}, output_cap=1000)


def test_output_that_isn_t_json_fails_the_run(tmp_path: Path) -> None:
    code = "import sys\n\ndef map_run(run):\n    sys.__stdout__.write('not json')\n    sys.__stdout__.flush()\n"
    with pytest.raises(MapperFailedError, match="isn't JSON"):
        runner.run(write_mapper(tmp_path, "noisy", code), {})


def test_a_crash_keeps_its_traceback_out_of_the_message(tmp_path: Path) -> None:
    code = f"def map_run(run):\n    raise ValueError({PRIVATE_TITLE!r})\n"
    with pytest.raises(MapperFailedError, match=r"the mapper failed \(exit 1\)") as e:
        runner.run(write_mapper(tmp_path, "broken", code), {})
    assert PRIVATE_TITLE not in str(e.value)
    assert PRIVATE_TITLE in e.value.stderr


def test_the_mapper_sees_none_of_the_service_s_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKET_TOKEN", "an-invented-token")
    code = "import os\n\ndef map_run(run):\n    return sorted(k for k in os.environ if k.startswith('DOCKET'))\n"
    assert runner.run(write_mapper(tmp_path, "env", code), {}) == []


def test_the_child_loads_the_mapper_and_its_neighbours(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = tmp_path / "with-helper"
    folder.mkdir()
    (folder / "helper.py").write_text("def double(n):\n    return n * 2\n", encoding="utf-8")
    mapper = folder / "mapper.py"
    mapper.write_text("from helper import double\n\ndef map_run(run):\n    print('hi')\n    return double(run)\n")
    out = io.BytesIO()
    assert child.main([str(mapper)], io.BytesIO(b"21"), out) == 0
    assert out.getvalue() == b"42"
    assert capsys.readouterr().err == "hi\n"


def test_the_child_refuses_what_it_can_t_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = io.BytesIO()
    assert child.main([], io.BytesIO(b"{}"), out) == 2
    assert child.main([str(tmp_path / "mapper.txt")], io.BytesIO(b"{}"), out) == 2
    empty = tmp_path / "empty.py"
    empty.write_text("NOTHING = 1\n", encoding="utf-8")
    assert child.main([str(empty)], io.BytesIO(b"{}"), out) == 3
    assert out.getvalue() == b""
    assert "has no map_run" in capsys.readouterr().err


def test_a_mapper_can_use_dataclasses(tmp_path: Path) -> None:
    code = (
        "from __future__ import annotations\n\nfrom dataclasses import dataclass\n\n\n@dataclass\nclass Seen:\n"
        "    n: int\n\n\ndef map_run(run):\n    return Seen(run['n']).n + 1\n"
    )
    assert runner.run(write_mapper(tmp_path, "typed", code), {"n": 41}) == 42
