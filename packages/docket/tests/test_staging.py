from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import make_record
from pydantic import ValidationError

from docket.vault import staging as staging_module
from docket.vault.cuts import Cut
from docket.vault.records import version
from docket.vault.staging import Staged, Staging, key_of, retry, rid_hash

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def staged(n: int = 1, **fields: object) -> Staged:
    record = make_record(n)
    record = record.model_copy(update={"v": version(record)})
    return Staged(state="pending", staged_at=NOW, record=record, **fields)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> Staging:
    (tmp_path / ".git").mkdir()
    return Staging(tmp_path)


def test_a_staged_version_is_written_read_replaced_and_deleted(store: Staging) -> None:
    item = staged(cuts=[Cut(start=0, end=3)])
    path = store.write(item)
    assert path == store.dir / "chat" / f"{key_of(item.record.rid, item.record.v)}.json"
    assert store.read(path) == item
    store.write(item.model_copy(update={"state": "ready", "classified_by": "claude"}))
    assert store.read(path).state == "ready"
    assert [e.key for e in store.scan().entries] == [item.key]
    store.delete(path)
    assert store.scan().entries == []
    assert list(store.tmp_dir.iterdir()) == []


def test_an_empty_or_missing_staging_scans_as_nothing(store: Staging) -> None:
    assert store.scan().entries == []
    assert store.decided().keys == frozenset()


def test_a_failed_rename_leaves_the_old_file_whole_and_no_temp_file(
    store: Staging, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = staged()
    path = store.write(item)
    before = path.read_bytes()

    def broken(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", broken)
    with pytest.raises(OSError, match="disk full"):
        store.write(item.model_copy(update={"state": "ready"}))
    assert path.read_bytes() == before
    assert list(store.tmp_dir.iterdir()) == []


def test_a_file_another_process_holds_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def flaky() -> None:
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use")

    monkeypatch.setattr(staging_module, "_RETRY_PAUSE", 0.0)
    retry(flaky)
    assert len(calls) == 3

    def always() -> None:
        raise PermissionError("in use")

    monkeypatch.setattr(staging_module, "RETRY_SECONDS", 0.0)
    with pytest.raises(PermissionError):
        retry(always)


def test_an_unreadable_file_is_reported_not_returned(store: Staging) -> None:
    store.write(staged())
    bad = store.dir / "chat" / "0123456789abcdef.json"
    bad.write_text("{not json", encoding="utf-8")
    empty = store.dir / "chat" / "fedcba9876543210.json"
    empty.write_text("", encoding="utf-8")
    scan = store.scan()
    assert len(scan.entries) == 1
    assert sorted(p.name for p, _ in scan.unreadable) == [bad.name, empty.name]
    assert all(message for _, message in scan.unreadable)


def test_a_staged_time_needs_its_zone() -> None:
    with pytest.raises(ValidationError, match="time zone"):
        Staged(state="pending", staged_at=datetime(2026, 10, 14), record=make_record())


def test_temp_files_a_crash_left_are_cleared(store: Staging) -> None:
    assert store.clear_temp() == 0
    store.tmp_dir.mkdir(parents=True)
    (store.tmp_dir / "staging-abc.123.tmp").write_text("x", encoding="utf-8")
    (store.tmp_dir / "2026-10-14T1550-test-box.jsonl.9.tmp").write_text("x", encoding="utf-8")
    assert store.clear_temp() == 1
    assert [p.name for p in store.tmp_dir.iterdir()] == ["2026-10-14T1550-test-box.jsonl.9.tmp"]


def test_the_decided_memory_keeps_hashes_and_survives_a_torn_line(store: Staging) -> None:
    store.remember([])
    assert not store.decided_file.exists()
    store.remember([("chat:one", "k1")])
    with store.decided_file.open("ab") as f:
        f.write(b'{"rid": "torn')
    store.remember([("chat:two", "k2")])
    text = store.decided_file.read_text(encoding="utf-8")
    assert "chat:one" not in text
    assert text.endswith("\n")
    decided = store.decided()
    assert decided.keys == frozenset({"k1", "k2"})
    assert decided.rids == frozenset({rid_hash("chat:one"), rid_hash("chat:two")})
    store.decided_file.write_text('{"rid": "x"}\n[1]\n', encoding="utf-8")
    assert store.decided().keys == frozenset()
