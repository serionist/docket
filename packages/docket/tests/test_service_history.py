"""The run history (DR-0301): one line per ended run in the vault's private folder, read back after a restart, lines
that don't read skipped, trimmed, and a write that fails kept in memory and named."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docket_testkit import sh

from docket.errors import DocketError
from docket.service import history
from docket.service.history import FILE, RunHistory, RunLine
from docket.vault.home import Home

NOW = datetime(2026, 10, 15, 9, 12, tzinfo=UTC)


@pytest.fixture
def home(tmp_path: Path) -> Home:
    root = tmp_path / "vault"
    root.mkdir()
    sh(root, "git", "init", "-q")
    git_dir = root / ".git"
    return Home(root, "repo", root, "", git_dir, git_dir / "docket")


def a_line(n: int, source: str = "memo", **fields: object) -> RunLine:
    base: dict[str, object] = {
        "run": f"run-{n}",
        "source": source,
        "opened": NOW + timedelta(minutes=n),
        "ended": NOW + timedelta(minutes=n, seconds=30),
        "outcome": "closed",
        "kind": history.CLOSED,
    }
    return RunLine.model_validate(base | fields)


def file_of(home: Home) -> Path:
    return home.private / FILE


def test_lines_survive_a_restart_newest_first(home: Home) -> None:
    first = RunHistory(lambda: home)
    first.load()
    for n, source in enumerate(["memo", "chat", "memo"]):
        first.add(a_line(n, source))
    assert len(file_of(home).read_text(encoding="utf-8").splitlines()) == 3
    again = RunHistory(lambda: home)
    again.load()
    assert [line.run for line in again.recent()] == ["run-2", "run-1", "run-0"]
    assert [line.run for line in again.recent("memo")] == ["run-2", "run-0"]
    assert [line.run for line in again.recent(k=1)] == ["run-2"]
    found = again.find("run-1")
    assert found is not None
    assert (found.source, found.whole) == ("chat", True)
    assert again.find("run-9") is None
    assert again.problem is None


def test_a_line_that_doesnt_read_is_skipped(home: Home) -> None:
    good = a_line(1).model_dump_json()
    newer = good.replace('"v":1', '"v":2')
    file_of(home).parent.mkdir(parents=True)
    file_of(home).write_text(f"{good}\n{newer}\nnot json\n{good[:40]}", encoding="utf-8")
    found = RunHistory(lambda: home)
    found.load()
    assert [line.run for line in found.recent()] == ["run-1"]


def test_past_twice_the_kept_lines_the_file_keeps_the_newest(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(history, "KEEP", 3)
    monkeypatch.setattr(history, "TRIM_AT", 6)
    kept = RunHistory(lambda: home)
    for n in range(7):
        kept.add(a_line(n))
    runs = [RunLine.model_validate_json(t).run for t in file_of(home).read_text(encoding="utf-8").splitlines()]
    assert runs == ["run-4", "run-5", "run-6"]
    kept.add(a_line(7))
    assert len(file_of(home).read_text(encoding="utf-8").splitlines()) == 4
    assert [line.run for line in kept.recent()] == ["run-7", "run-6", "run-5"]


def test_without_a_vault_the_lines_stay_in_memory(home: Home) -> None:
    nowhere = RunHistory(None)
    nowhere.load()
    nowhere.add(a_line(1))
    assert [line.run for line in nowhere.recent()] == ["run-1"]
    assert nowhere.problem is None

    attempts: list[int] = []

    def opens_on_the_second_try() -> Home:
        attempts.append(1)
        if len(attempts) == 1:
            raise DocketError("the vault isn't there yet")
        return home

    file_of(home).parent.mkdir(parents=True)
    file_of(home).write_text(a_line(0).model_dump_json() + "\n", encoding="utf-8")
    late = RunHistory(opens_on_the_second_try)
    late.load()
    assert late.problem == "the vault isn't there yet"
    late.add(a_line(1))
    # The file's older lines come before the one that ended while the vault was away.
    assert [line.run for line in late.recent()] == ["run-1", "run-0"]
    assert late.problem is None


def test_a_history_that_couldnt_load_at_start_loads_when_the_page_asks(home: Home) -> None:
    file_of(home).parent.mkdir(parents=True)
    file_of(home).write_text(a_line(0).model_dump_json() + "\n", encoding="utf-8")
    broken = [True]

    def opens_once_fixed() -> Home:
        if broken[0]:
            raise UnicodeDecodeError("utf-8", b"\xf5", 0, 1, "invalid start byte")
        return home

    found = RunHistory(opens_once_fixed)
    found.load()
    assert found.problem == "UnicodeDecodeError"
    assert found.recent() == []
    assert found.find("run-0") is None
    broken[0] = False
    assert [line.run for line in found.recent()] == ["run-0"]
    assert found.problem is None
    found_again = RunHistory(opens_once_fixed)
    assert found_again.find("run-0") is not None


def test_a_missing_git_dir_is_refused_and_never_made(home: Home, tmp_path: Path) -> None:
    gone = Home(home.root, "repo", home.root, "", tmp_path / "nowhere" / ".git", tmp_path / "nowhere" / ".git" / "d")
    kept = RunHistory(lambda: gone)
    kept.add(a_line(1))
    assert kept.problem is not None
    assert "isn't a git repository" in kept.problem
    assert not (tmp_path / "nowhere").exists()
    assert [line.run for line in kept.recent()] == ["run-1"]


def test_a_file_that_cant_be_read_or_written_is_named_by_its_type(home: Home) -> None:
    file_of(home).parent.mkdir(parents=True)
    file_of(home).write_bytes(b"\xff\xfe not utf-8\n")
    unreadable = RunHistory(lambda: home)
    unreadable.load()
    assert unreadable.problem == "UnicodeDecodeError"
    file_of(home).unlink()
    file_of(home).mkdir()
    blocked = RunHistory(lambda: home)
    blocked.add(a_line(1))
    assert blocked.problem in {"IsADirectoryError", "PermissionError"}
    assert [line.run for line in blocked.recent()] == ["run-1"]


def test_a_link_at_the_file_is_refused(home: Home, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("", encoding="utf-8")
    file_of(home).parent.mkdir(parents=True)
    try:
        file_of(home).symlink_to(target)
    except OSError:
        pytest.skip("this system makes no symbolic links for this user")
    linked = RunHistory(lambda: home)
    linked.add(a_line(1))
    assert linked.problem is not None
    assert "is a link" in linked.problem
    assert target.read_text(encoding="utf-8") == ""


def test_a_long_reason_is_cut_and_the_id_lists_are_capped() -> None:
    assert history.capped_why(None) is None
    assert history.capped_why("short") == "short"
    long = history.capped_why("x" * 600)
    assert long is not None
    assert len(long) == history.WHY_CHARS
    assert history.first_ids(f"id-{n:02}" for n in range(30)) == [f"id-{n:02}" for n in range(20)]
