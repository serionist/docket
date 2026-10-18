"""`docket scan` and `docket source new --folder` at the command line: counts and rids, never the path of a file left
out (DR-0480, DR-0481)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import AtConsole, cant_place, git, host_repo, make_config
from documents_scan_kit import SOURCE, folder_vault, name, put, rid

from docket.cli import main, scan_lines
from docket.documents.scan import ScanReport
from docket.vault.config import NeverStore, SourceConfig, VaultConfig
from docket.vault.gate import StageResult
from docket.vault.vault import Vault


def test_a_scan_says_what_it_did_and_what_comes_next(
    tmp_path: Path, config: VaultConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    config.never_store = NeverStore(keywords=["zebrafinch"])
    vault = folder_vault(tmp_path, config, work)
    put(work, "plan.md", "# Plan\n")
    put(work, "zebrafinch diary.md", "# never shown\n")
    put(work, "photo.png", b"png")
    assert main(["scan", "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert f"scan     documents: 3 file(s) in {work.resolve()}" in out
    assert "files    new 1" in out
    assert "skipped  never-store 1, other .png 1" in out
    assert "staged   documents: 0 pending, 1 ready, 0 already staged or in raw" in out
    assert "next     docket staging promote" in out
    assert "zebrafinch" not in out
    assert main(["scan", SOURCE, "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "files    same 1" in out
    assert "next" not in out


def test_the_guard_stops_with_its_fix_and_a_missing_folder_says_so(
    tmp_path: Path, config: VaultConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    work = tmp_path / "work"
    vault = folder_vault(tmp_path, config, work)
    for n in range(20):
        put(work, f"n{n}.md", f"# {n}\n")
    assert main(["scan", "--vault", str(vault.root)]) == 0
    assert main(["staging", "promote", "--vault", str(vault.root)]) == 0
    for n in range(20):
        (work / f"n{n}.md").unlink()
    capsys.readouterr()
    assert main(["scan", "--vault", str(vault.root)]) == 1
    out = capsys.readouterr().out
    assert "stopped  20 of the 20 files the vault holds are missing from the folder, so nothing was staged" in out
    assert "docket scan documents --accept-gone" in out
    assert main(["scan", "--accept-gone", "--vault", str(vault.root)]) == 0
    assert "gone     20" in capsys.readouterr().out
    name(vault, tmp_path / "unplugged")
    assert main(["scan", "--vault", str(vault.root)]) == 0
    assert f"no folder at {(tmp_path / 'unplugged').resolve()} on this machine" in capsys.readouterr().out


def test_a_vault_without_folder_sources_says_how_to_add_one(
    tmp_path: Path, config: VaultConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = folder_vault(tmp_path, config, tmp_path / "work")
    (vault.root / "vault.yaml").write_text(make_config().dump(), encoding="utf-8")
    assert main(["scan", "--vault", str(vault.root)]) == 2
    assert "docket source new <name> --folder <folder>" in capsys.readouterr().err


def test_every_line_of_a_report() -> None:
    report = ScanReport(
        SOURCE,
        folder="W",
        files=9,
        outcomes=Counter(
            {
                "same": 2,
                "changed": 1,
                "kept out": 1,
                "retry": 1,
                "on hold": 1,
                "placeholders": 2,
                "folders not listed": 1,
            }
        ),
        skipped=Counter({"excluded": 1}),
        gone=2,
        not_read=[rid("a.pdf")],
        unlisted=["team/archive"],
        staged=StageResult(ready=3, dropped=Counter({"never-store": 1})),
    )
    assert scan_lines(report) == [
        "scan     documents: 9 file(s) in W",
        "files    same 2, changed 1",
        "gone     2",
        f"not read {rid('a.pdf')}",
        "kept out 1, by the never-store lists",
        "retry    1: the reader stopped or crashed; tried once more at the next scan",
        "later    folders not listed 1, on hold 1, placeholders 2: they keep their last version",
        "         not listed now: team/archive",
        "skipped  excluded 1",
        "staged   documents: 0 pending, 3 ready, 0 already staged or in raw",
        "dropped  never-store 1",
    ]
    assert scan_lines(ScanReport(SOURCE, folder="W"))[1] == "files    none to read"


# -- docket source new --folder -----------------------------------------------------------------------------------


def repo_vault(tmp_path: Path) -> tuple[Path, Vault]:
    top = host_repo(tmp_path)
    return top, Vault.init(top / "vault", make_config("folder"), "test-box")


def test_a_folder_source_is_added_as_every_machine_reads_it(
    owner: Callable[..., AtConsole], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    top, vault = repo_vault(tmp_path)
    (top / "docs").mkdir()
    assert main(["source", "new", SOURCE, "--folder", str(top / "docs"), "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "source   documents in vault.yaml: a folder source, folder docs, committed " in out
    assert "next     docket scan documents" in out
    assert Vault.open(vault.root).config.sources[SOURCE] == SourceConfig(folder="docs")
    assert git(top, "log", "-1", "--format=%s").strip() == "Config: source documents"
    home = tmp_path / "home"
    (home / "Work").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for given, written in ((home / "Work", "~/Work"), (home, "~"), (Path("~/Notes"), "~/Notes"), (top, ".")):
        assert main(["source", "new", "papers", "--folder", str(given), "--vault", str(vault.root)]) == 0
        assert Vault.open(vault.root).config.sources["papers"].folder == written
    capsys.readouterr()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert main(["source", "new", "papers", "--folder", str(elsewhere), "--vault", str(vault.root)]) == 2
    assert "under folders" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra", [["--example"], ["--tool", "list_files"], ["--sensitivity", "mixed"], ["--lookback-days", "1"]]
)
def test_folder_goes_alone(tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]) -> None:
    _, vault = repo_vault(tmp_path)
    assert main(["source", "new", SOURCE, "--folder", "docs", *extra, "--vault", str(vault.root)]) == 2
    assert "--folder goes alone" in capsys.readouterr().err


def test_a_mapper_source_isn_t_turned_into_a_folder_source(
    owner: Callable[..., AtConsole], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    top, vault = repo_vault(tmp_path)
    assert main(["source", "new", "memo", "--tool", "get_memo", "--vault", str(vault.root)]) == 0
    assert main(["source", "new", "memo", "--folder", str(top), "--vault", str(vault.root)]) == 2
    assert "has a mapper or tools" in capsys.readouterr().err
    assert main(["source", "new", "Bad", "--folder", str(top), "--vault", str(vault.root)]) == 2


def test_a_hand_written_vault_yaml_gets_the_entry_to_paste_where_docket_can_t_place_it(
    owner: Callable[..., AtConsole],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cant_place(monkeypatch)
    top, vault = repo_vault(tmp_path)
    path = vault.root / "vault.yaml"
    path.write_text("# mine\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["source", "new", SOURCE, "--folder", str(top), "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "add the source under sources:" in out
    assert "  documents:\n" in out
    assert "    folder: .\n" in out


@pytest.mark.parametrize(
    ("level", "fix"),
    [
        ("mixed", "mixed would hold every page"),
        ("private", "enabled: false"),
    ],
)
def test_a_mixed_or_private_source_isn_t_turned_into_a_work_folder_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], level: str, fix: str
) -> None:
    top = host_repo(tmp_path)
    config = make_config("folder")
    config.sources["journal"] = SourceConfig(sensitivity=level)  # type: ignore[arg-type]
    vault = Vault.init(top / "vault", config, "test-box")
    before = (vault.root / "vault.yaml").read_bytes()
    assert main(["source", "new", "journal", "--folder", str(top), "--vault", str(vault.root)]) == 2
    assert fix in capsys.readouterr().err
    assert (vault.root / "vault.yaml").read_bytes() == before
    assert Vault.open(vault.root).config.sources["journal"].sensitivity == level
