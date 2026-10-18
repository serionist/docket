"""`docket source new`: a source's folder written where the runner looks, from the blank template or the Plaud
reference, never over a folder or through a link out of the repo, and the source registered in vault.yaml."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import AtConsole, cant_place, git, host_repo, make_config, sh

from docket.cli import main
from docket.errors import DocketError
from docket.sources import scaffold
from docket.sources.runner import find_mapper
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.vault import Vault

REPO = Path(__file__).resolve().parents[3]
BLANK = ["SKILL.md", "mapper.py", "tests/conftest.py", "tests/test_mapper.py", "tests/test_with_docket.py"]


def folder_vault_in(top: Path) -> Vault:
    return Vault.init(top / "vault", make_config("folder"), "test-box")


def skills(top: Path, name: str) -> Path:
    return top / ".claude" / "skills" / "sources" / name


def head(top: Path) -> tuple[str, list[str]]:
    """The last commit's subject and the files it touched."""
    subject = git(top, "log", "-1", "--format=%s").strip()
    return subject, git(top, "show", "--name-only", "--format=", "HEAD").split()


def run_tests(folder: Path) -> subprocess.CompletedProcess[str]:
    """The folder's own tests, in their own process, as a user runs them where the folder lives."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=folder,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_a_blank_source_is_written_where_the_runner_looks_and_registered(
    owner: Callable[..., AtConsole], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    where = tmp_path / "Árvíztűrő teszt"
    where.mkdir()
    top = host_repo(where)
    vault = folder_vault_in(top)
    args = ["source", "new", "zebra-notes", "--sensitivity", "work", "--tool", "list_notes", "--tool", "get_note"]
    assert main([*args, "--vault", str(vault.root)]) == 0
    folder = skills(top, "zebra-notes")
    files = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    assert files == BLANK
    for rel in files:
        data = (folder / rel).read_bytes()
        assert b"{{source}}" not in data
        assert b"\r\n" not in data
    assert (folder / "SKILL.md").read_text(encoding="utf-8").startswith("---\nname: zebra-notes\n")
    assert VaultConfig.load(vault.root / "vault.yaml").sources["zebra-notes"] == SourceConfig(
        sensitivity="work", mapper=".claude/skills/sources/zebra-notes/mapper.py", tools=["list_notes", "get_note"]
    )
    # Docket commits only the vault's file; the folder is the user's to commit.
    assert head(top) == ("Config: source zebra-notes", ["vault/vault.yaml"])
    assert "?? .claude/" in git(top, "status", "--porcelain", "--untracked-files=normal")
    assert find_mapper(Vault.open(vault.root), "zebra-notes") == (folder / "mapper.py").resolve()
    out = capsys.readouterr().out
    assert f"wrote    {folder.resolve()}, from the blank template" in out
    assert "source   zebra-notes in vault.yaml: work, mapper .claude/skills/sources/zebra-notes/mapper.py" in out
    assert "tools list_notes, get_note, committed " in out
    assert "next     the folder is yours to commit" in out
    # Not leftover: no temp folder beside it.
    assert [p.name for p in folder.parent.iterdir()] == ["zebra-notes"]


def test_the_blank_folder_passes_its_tests_and_ruff_and_a_run_stages_nothing(
    owner: Callable[..., AtConsole], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    top = host_repo(tmp_path)
    vault = folder_vault_in(top)
    assert main(["source", "new", "zebra-notes", "--vault", str(vault.root)]) == 0
    folder = skills(top, "zebra-notes")
    tested = run_tests(folder)
    assert tested.returncode == 0, tested.stdout + tested.stderr
    assert "4 passed" in tested.stdout
    linted = subprocess.run(
        [
            sys.executable, "-m", "ruff", "check", "--no-cache",
            "--config", str(REPO / "pyproject.toml"),
            # The repo's own ignores for tests, which its globs can't reach in a folder outside it.
            "--config", "lint.extend-per-file-ignores = {'tests/*' = ['S101', 'S603', 'S607', 'PLC0415']}",
            ".",
        ],
        cwd=folder, capture_output=True, text=True, encoding="utf-8", check=False,
    )  # fmt: skip
    assert linted.returncode == 0, linted.stdout + linted.stderr
    payloads = tmp_path / "payloads"
    payloads.mkdir()
    (payloads / "one.json").write_text(json.dumps({"id": "n1", "text": "Morning, Ben."}), encoding="utf-8")
    capsys.readouterr()
    assert main(["ingest", "zebra-notes", str(payloads), "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "staged     zebra-notes: 0 pending, 0 ready" in out
    assert "set aside  (no id)  not-mapped-yet" in out


def test_example_copies_the_plaud_reference_and_registers_it(
    owner: Callable[..., AtConsole], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    top = host_repo(tmp_path)
    vault = folder_vault_in(top)
    assert main(["source", "new", "plaud", "--example", "--vault", str(vault.root)]) == 0
    folder = skills(top, "plaud")
    copied = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    assert copied == scaffold.template_files("plaud")
    assert VaultConfig.load(vault.root / "vault.yaml").sources["plaud"] == SourceConfig(
        sensitivity="mixed",
        mapper=".claude/skills/sources/plaud/mapper.py",
        tools=["list_files", "get_file", "get_transcript", "get_note"],
    )
    assert ", Docket's reference" in capsys.readouterr().out
    # The copy's own tests, the end-to-end import of its invented backfill among them.
    tested = run_tests(folder)
    assert tested.returncode == 0, tested.stdout + tested.stderr


def test_a_nested_repo_of_its_own_commits_in_its_own_history(owner: Callable[..., AtConsole], tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sh(project, "git", "init", "-q", "-b", "main")
    vault = Vault.init(project / "vault", make_config(), "test-box")
    made = scaffold.new_source(vault, "zebra-notes")
    assert made.folder == skills(project, "zebra-notes").resolve()
    assert made.commit is not None
    assert head(vault.root) == ("Config: source zebra-notes", ["vault.yaml"])
    # Sensitivity: Docket's guess for a source it doesn't know, with no tools yet.
    assert (made.entry.sensitivity, made.entry.tools) == ("mixed", [])


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["..", ], "isn't a source name"),
        (["a/b"], "isn't a source name"),
        (["Plaud"], "isn't a source name"),
        (["notes."], "isn't a source name"),
        (["zebra-notes", "--example"], "no reference source named zebra-notes; --example copies one of: plaud"),
        (["zebra-notes", "--sensitivity", "secret"], "--sensitivity is work, mixed or private, not 'secret'"),
        (["zebra-notes", "--tool", "mcp__plaud__list_files"], "isn't a tool's own name"),
    ],
)  # fmt: skip
def test_a_bad_request_is_refused_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str], message: str
) -> None:
    top = host_repo(tmp_path)
    vault = folder_vault_in(top)
    before = (vault.root / "vault.yaml").read_bytes()
    assert main(["source", "new", *args, "--vault", str(vault.root)]) == 2
    assert message in capsys.readouterr().err
    assert not (top / ".claude").exists()
    assert (vault.root / "vault.yaml").read_bytes() == before


def test_a_folder_that_exists_is_never_written_over(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    top = host_repo(tmp_path)
    vault = folder_vault_in(top)
    mine = skills(top, "zebra-notes")
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("my own\n", encoding="utf-8")
    before = (vault.root / "vault.yaml").read_bytes()
    assert main(["source", "new", "zebra-notes", "--vault", str(vault.root)]) == 2
    assert "already exists; a source's folder is never written over" in capsys.readouterr().err
    assert [p.name for p in mine.iterdir()] == ["SKILL.md"]
    assert (vault.root / "vault.yaml").read_bytes() == before


def test_a_vault_outside_any_git_repo_has_no_project_to_write_into(tmp_path: Path) -> None:
    vault = Vault.init(tmp_path / "lone", make_config(), "test-box")
    with pytest.raises(DocketError, match="isn't inside a git repo"):
        scaffold.new_source(vault, "zebra-notes")


def link_folder(link: Path, target: Path) -> None:
    """A link to a folder: a symlink, else on Windows a junction, which needs no privilege."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if sys.platform != "win32":
            pytest.skip("this system can't make a link to a folder here")
    import _winapi  # pyright: ignore[reportMissingModuleSource]

    _winapi.CreateJunction(str(target), str(link))


@pytest.mark.parametrize("into", ["out of the repo", "into the vault"])
def test_a_linked_sources_folder_that_leads_elsewhere_is_refused(tmp_path: Path, into: str) -> None:
    top = host_repo(tmp_path)
    vault = folder_vault_in(top)
    elsewhere = tmp_path / "elsewhere" if into == "out of the repo" else vault.root / "processed" / "x"
    elsewhere.mkdir(parents=True)
    (top / ".claude" / "skills").mkdir(parents=True)
    link_folder(top / ".claude" / "skills" / "sources", elsewhere)
    before = (vault.root / "vault.yaml").read_bytes()
    with pytest.raises(DocketError, match=r"must stay in .* and outside the vault after links are followed"):
        scaffold.new_source(vault, "zebra-notes")
    assert list(elsewhere.iterdir()) == []
    assert (vault.root / "vault.yaml").read_bytes() == before


def test_a_vault_yaml_with_the_user_s_own_lines_gets_the_entry_where_docket_can_t_place_it(
    owner: Callable[..., AtConsole],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cant_place(monkeypatch)
    top = host_repo(tmp_path)
    vault = folder_vault_in(top)
    path = vault.root / "vault.yaml"
    mine = path.read_text(encoding="utf-8").replace("sources:\n", "sources:   # mine, keep it\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    assert main(["source", "new", "zebra-notes", "--tool", "list_notes", "--vault", str(vault.root)]) == 0
    assert path.read_text(encoding="utf-8") == mine
    assert skills(top, "zebra-notes").is_dir()
    out = capsys.readouterr().out
    assert "vault.yaml has a layout Docket can't edit for this; add the source under sources:" in out
    block = out.split("add the source under sources:\n", 1)[1].split("tests    ", 1)[0]
    pasted = mine.replace("sources:   # mine, keep it\n", "sources:   # mine, keep it\n" + block, 1)
    path.write_text(pasted, encoding="utf-8", newline="\n")
    assert VaultConfig.load(path).sources["zebra-notes"] == SourceConfig(
        sensitivity="mixed", mapper=".claude/skills/sources/zebra-notes/mapper.py", tools=["list_notes"]
    )


def test_an_entry_already_there_keeps_what_the_flags_don_t_set(owner: Callable[..., AtConsole], tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    config = make_config("folder")
    config.sources["zebra-notes"] = SourceConfig(sensitivity="private", tools=["get_note"], media_folders=["~/Notes"])
    vault = Vault.init(top / "vault", config, "test-box")
    kept = scaffold.new_source(vault, "zebra-notes").entry
    assert (kept.sensitivity, kept.tools, kept.media_folders) == ("private", ["get_note"], ["~/Notes"])
    assert kept.mapper == ".claude/skills/sources/zebra-notes/mapper.py"
    assert VaultConfig.load(vault.root / "vault.yaml").sources["zebra-notes"].media_folders == ["~/Notes"]
    calls = scaffold.new_source(Vault.open(vault.root), "calls", sensitivity="work", tools=["list_calls"]).entry
    assert (calls.sensitivity, calls.tools) == ("work", ["list_calls"])


def test_a_source_registered_as_it_stands_needs_no_commit(
    owner: Callable[..., AtConsole], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    top = host_repo(tmp_path)
    vault = folder_vault_in(top)
    assert main(["source", "new", "zebra-notes", "--vault", str(vault.root)]) == 0
    before = head(top)
    sh(top, "git", "clean", "-qfdx", ".claude")
    capsys.readouterr()
    assert main(["source", "new", "zebra-notes", "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "source   zebra-notes in vault.yaml: mixed" in out
    assert "committed" not in out
    assert head(top) == before


def test_a_source_already_as_it_stands_in_a_hand_edited_file_needs_no_paste(
    owner: Callable[..., AtConsole], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The writer looks for no change before Docket's form: an entry already as `docket source new` would write it, in
    a vault.yaml with the user's own lines, is registered as it stands, with no commit and no block to paste again."""
    top = host_repo(tmp_path)
    vault = folder_vault_in(top)
    assert main(["source", "new", "zebra-notes", "--vault", str(vault.root)]) == 0
    path = vault.root / "vault.yaml"
    mine = path.read_text(encoding="utf-8").replace("sources:\n", "sources:   # mine\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    before = head(top)
    sh(top, "git", "clean", "-qfdx", ".claude")
    capsys.readouterr()
    assert main(["source", "new", "zebra-notes", "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "source   zebra-notes in vault.yaml: mixed" in out
    assert "add under sources:" not in out
    assert "committed" not in out
    assert path.read_text(encoding="utf-8") == mine
    assert head(top) == before


def test_a_vault_yaml_written_before_the_redaction_switches_is_still_docket_s_own(
    owner: Callable[..., AtConsole], tmp_path: Path
) -> None:
    top = host_repo(tmp_path)
    vault = folder_vault_in(top)
    path = vault.root / "vault.yaml"
    old = path.read_text(encoding="utf-8").replace("  credentials: true\n  phone: false\n", "  builtin: true\n")
    path.write_text(old, encoding="utf-8", newline="\n")
    git(top, "commit", "-qam", "an old vault.yaml")
    assert main(["source", "new", "zebra-notes", "--vault", str(vault.root)]) == 0
    text = path.read_text(encoding="utf-8")
    assert "builtin" not in text
    assert "  credentials: true\n  phone: false\n" in text
    assert VaultConfig.load(path).sources["zebra-notes"].mapper == ".claude/skills/sources/zebra-notes/mapper.py"


def test_a_write_that_fails_half_way_leaves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = host_repo(tmp_path)
    vault = folder_vault_in(top)
    before = (vault.root / "vault.yaml").read_bytes()
    real = Path.write_bytes
    written: list[Path] = []

    def second_fails(self: Path, data: bytes) -> int:
        written.append(self)
        if len(written) == 2:
            raise OSError("disk full")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", second_fails)
    with pytest.raises(OSError, match="disk full"):
        scaffold.new_source(vault, "zebra-notes")
    monkeypatch.undo()
    assert list(skills(top, "zebra-notes").parent.iterdir()) == []
    assert (vault.root / "vault.yaml").read_bytes() == before


def test_a_commit_that_fails_takes_the_folder_and_the_entry_back(
    owner: Callable[..., AtConsole], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    top = host_repo(tmp_path)
    vault = folder_vault_in(top)
    before = (vault.root / "vault.yaml").read_bytes()

    def refuse(self: Vault, paths: list[str], message: str) -> str | None:
        raise DocketError("another git holds the index")

    monkeypatch.setattr(Vault, "commit", refuse)
    with pytest.raises(DocketError, match="another git holds the index"):
        scaffold.new_source(vault, "zebra-notes")
    assert not skills(top, "zebra-notes").exists()
    assert (vault.root / "vault.yaml").read_bytes() == before
