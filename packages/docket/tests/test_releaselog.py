"""The release log on temp vaults: reading it failing closed, folding several lines for one id, what a release
covers, and appending a line committed alone."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, make_record, sh

from docket.errors import DocketError
from docket.vault import releaselog
from docket.vault.config import VaultConfig
from docket.vault.cuts import Cut
from docket.vault.releaselog import RELEASES, Kept, LoggedCut, Release, Releases
from docket.vault.vault import Vault

PRIVATE_TITLE = "Dentist with Cleo"
AT = datetime(2026, 10, 15, 9, 12, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path, config: VaultConfig) -> Vault:
    return empty_vault(tmp_path, config)


def release(
    native: str, cuts: list[tuple[int, int]], keep: list[tuple[str, str]] | None = None, **fields: Any
) -> Release:
    return Release(
        id=native,
        cuts=[LoggedCut(start=s, end=e) for s, e in cuts],
        keep=[Kept(rid=r, v=v) for r, v in keep or []],
        at=fields.get("at", AT),
        machine=fields.get("machine", "test-box"),
    )


def write_log(vault: Vault, source: str, data: bytes) -> Path:
    path = vault.root / RELEASES / f"{source}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def link(target: Path, at: Path, *, folder: bool = False) -> None:
    try:
        at.symlink_to(target, target_is_directory=folder)
    except OSError:
        pytest.skip("this system can't make a symlink here")


def subjects(vault: Vault) -> list[str]:
    return sh(vault.root, "git", "log", "--format=%s").splitlines()


def test_a_line_is_ids_and_times_only() -> None:
    line = release("rec-cut-01", [(748_000, 846_000)], [("calls:rec-cut-01", "sha256:aa")]).line()
    assert json.loads(line) == {
        "schema_version": 1,
        "id": "rec-cut-01",
        "cuts": [{"start": 748_000, "end": 846_000}],
        "keep": [{"rid": "calls:rec-cut-01", "v": "sha256:aa"}],
        "at": "2026-10-15T09:12:00Z",
        "machine": "test-box",
    }


def test_several_lines_for_an_id_cut_their_union_and_keep_only_what_all_keep(vault: Vault) -> None:
    lines = [
        release("rec-1", [(10_000, 20_000)], [("calls:rec-1", "v1"), ("calls:rec-1/summary", "v2")]),
        release("rec-2", [(0, 5_000)]),
        release("rec-1", [(15_000, 30_000), (50_000, 60_000)], [("calls:rec-1", "v1")], machine="other-box"),
    ]
    write_log(vault, "calls", "".join(f"{r.line()}\n" for r in lines).encode())
    granted = releaselog.read(vault.root, "calls")
    assert set(granted) == {"rec-1", "rec-2"}
    one = granted["rec-1"]
    assert one.cuts == (Cut(start=10_000, end=30_000, unit="ms"), Cut(start=50_000, end=60_000, unit="ms"))
    assert one.keep == {("calls:rec-1", "v1")}
    assert one.first.machine == "test-box"
    assert releaselog.read_all(vault.root).of("calls") == granted
    assert releaselog.read(vault.root, "chat") == {}


def test_a_bad_line_is_named_by_its_number_never_its_text(vault: Vault) -> None:
    good = release("rec-1", [(0, 1000)]).line()
    write_log(vault, "calls", f'{good}\n\n{{"id": "{PRIVATE_TITLE}"}}\n'.encode())
    for attempt in (lambda: releaselog.read(vault.root, "calls"), lambda: releaselog.read_all(vault.root)):
        with pytest.raises(DocketError, match=r"state/releases/calls.jsonl: line 3 isn't a release") as e:
            attempt()
        assert "Dentist" not in str(e.value)


@pytest.mark.parametrize(
    "line",
    [
        '{"schema_version": 1, "id": "rec-1", "cuts": [{"start": 0, "end": 5}], "at": "2026-10-15T09:12:00Z", '
        '"machine": "m", "title": "x"}',
        '{"schema_version": 2, "id": "rec-1", "cuts": [{"start": 0, "end": 5}], "at": "2026-10-15T09:12:00Z", '
        '"machine": "m"}',
        '{"schema_version": 1, "id": "rec-1", "cuts": [], "at": "2026-10-15T09:12:00Z", "machine": "m"}',
        '{"schema_version": 1, "id": "rec-1", "cuts": [{"start": 5, "end": 5}], "at": "2026-10-15T09:12:00Z", '
        '"machine": "m"}',
        '{"schema_version": 1, "id": "rec 1", "cuts": [{"start": 0, "end": 5}], "at": "2026-10-15T09:12:00Z", '
        '"machine": "m"}',
        '{"schema_version": 1, "id": "rec-1", "cuts": [{"start": 0, "end": 5}], "at": "2026-10-15T09:12:00", '
        '"machine": "m"}',
        "not json",
    ],
)
def test_a_line_that_isn_t_a_release_fails_closed(vault: Vault, line: str) -> None:
    write_log(vault, "calls", f"{line}\n".encode())
    with pytest.raises(DocketError, match="line 1 isn't a release"):
        releaselog.read_all(vault.root)


def test_a_log_that_isn_t_utf8_or_whose_name_isn_t_a_source_s_fails_closed(vault: Vault) -> None:
    write_log(vault, "calls", b"\xff\xfe")
    with pytest.raises(DocketError, match=r"calls.jsonl isn't UTF-8"):
        releaselog.read(vault.root, "calls")
    (vault.root / RELEASES / "calls.jsonl").unlink()
    write_log(vault, "Calls", b"")
    with pytest.raises(DocketError, match=r"Calls.jsonl: .* isn't a source name.*fails closed"):
        releaselog.read_all(vault.root)


def test_no_folder_means_nothing_released_and_other_files_are_left_alone(vault: Vault) -> None:
    assert releaselog.read_all(vault.root) == Releases()
    write_log(vault, "calls", release("rec-1", [(0, 1000)]).line().encode())
    (vault.root / RELEASES / "README.txt").write_text("notes", encoding="utf-8")
    (vault.root / RELEASES / "sub.jsonl").mkdir()
    assert set(releaselog.read_all(vault.root).of("calls")) == {"rec-1"}


def test_unreadable_files_fail_closed(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_log(vault, "calls", b"")

    def refuse(self: Path) -> bytes:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    with pytest.raises(DocketError, match=r"calls.jsonl: can't read it: Permission denied"):
        releaselog.read(vault.root, "calls")
    assert path.exists()


def test_a_release_covers_the_rid_the_thread_the_container_and_what_is_under_the_id(vault: Vault) -> None:
    write_log(vault, "calls", release("rec-1", [(0, 1000)]).line().encode())
    releases = releaselog.read_all(vault.root)

    def covered(**fields: Any) -> bool:
        base: dict[str, Any] = {"rid": "calls:other", "source": "calls", "kind": "recording", "container": None}
        return releases.covering(make_record(**{**base, **fields})) is not None

    assert covered(rid="calls:rec-1")
    assert covered(rid="calls:rec-1/summary")
    assert covered(thread="calls:rec-1")
    assert covered(container="calls:rec-1/part")
    assert not covered(rid="calls:rec-1x")
    assert not covered()
    assert releases.covering(make_record(rid="chat:rec-1", source="chat")) is None


def test_adding_commits_the_log_alone_and_names_no_id(vault: Vault) -> None:
    releaselog.add(vault, "calls", release("rec-cut-01", [(0, 1000)]))
    releaselog.add(vault, "calls", release("rec-cut-02", [(5000, 9000)]))
    assert set(releaselog.read(vault.root, "calls")) == {"rec-cut-01", "rec-cut-02"}
    assert subjects(vault)[:2] == ["Releases: calls, 1 added on test-box", "Releases: calls, 1 added on test-box"]
    assert "rec-cut" not in "".join(subjects(vault))
    assert sh(vault.root, "git", "status", "--porcelain").strip() == ""
    assert releaselog.commit_pending(vault, "calls") is None
    assert releaselog.commit_pending(vault, "chat") is None


def test_a_log_without_its_last_newline_gets_one_before_the_next_line(vault: Vault) -> None:
    write_log(vault, "calls", release("rec-1", [(0, 1000)]).line().encode())
    releaselog.add(vault, "calls", release("rec-2", [(0, 1000)]))
    assert set(releaselog.read(vault.root, "calls")) == {"rec-1", "rec-2"}


def test_a_line_whose_commit_failed_is_committed_by_the_next_call(vault: Vault) -> None:
    write_log(vault, "calls", f"{release('rec-1', [(0, 1000)]).line()}\n".encode())
    assert releaselog.commit_pending(vault, "calls") is not None
    assert subjects(vault)[0] == "Releases: calls, the log committed on test-box"


def test_a_log_that_doesn_t_read_is_never_added_to(vault: Vault) -> None:
    path = write_log(vault, "calls", b"garbage\n")
    with pytest.raises(DocketError, match="line 1 isn't a release"):
        releaselog.add(vault, "calls", release("rec-1", [(0, 1000)]))
    assert path.read_bytes() == b"garbage\n"


def test_a_log_or_folder_that_is_a_link_is_refused_on_read_and_on_add(vault: Vault, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.jsonl"
    elsewhere.write_text(f"{release('rec-1', [(0, 1000)]).line()}\n", encoding="utf-8")
    (vault.root / RELEASES).mkdir(parents=True)
    link(elsewhere, vault.root / RELEASES / "calls.jsonl")
    with pytest.raises(DocketError, match=r"calls.jsonl is a link"):
        releaselog.read_all(vault.root)
    with pytest.raises(DocketError, match=r"calls.jsonl is a link"):
        releaselog.add(vault, "calls", release("rec-2", [(0, 1000)]))
    assert "rec-2" not in elsewhere.read_text(encoding="utf-8")
    (vault.root / RELEASES / "calls.jsonl").unlink()
    (vault.root / RELEASES).rmdir()
    target = tmp_path / "folder"
    target.mkdir()
    link(target, vault.root / RELEASES, folder=True)
    with pytest.raises(DocketError, match="state/releases is a link"):
        releaselog.read_all(vault.root)
    with pytest.raises(DocketError, match="isn't a source name"):
        releaselog.log_path(vault.root, "..")
