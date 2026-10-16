from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import CALL, PARENT, copy_vault, index_template, make_record

from docket.index import build, rebuild, update
from docket.index.lookup import ReadIndex, open_read
from docket.vault.legacy import OldPath
from docket.vault.vault import Vault

NORTHWIND = "legacy-note:old-northwind-notes"
LEGACY = "processed/legacy-note/old-northwind-notes.md"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    v = copy_vault(template, tmp_path)
    rebuild(v)
    return v


def open_current(vault: Vault) -> ReadIndex | None:
    """The index when it is current for processed/, as read asks it about docs."""
    index = open_read(vault)
    if index is not None and not index.docs_current():
        index.close()
        return None
    return index


def test_a_current_index_answers_what_read_asks(vault: Vault) -> None:
    index = open_current(vault)
    assert index is not None
    try:
        assert index.old_paths("D:\\work\\notes\\northwind\\site lead.md") == [
            OldPath("notes/Northwind/Site Lead.md", NORTHWIND)
        ]
        assert index.supersedes() == [("recording-note:2026-10-13-fleet-review", ["minutes:2026-10-13-fleet-review"])]
        assert [p.relative_to(vault.root).as_posix() for p in index.covering(CALL)] == [
            "processed/digest/2026/10/2026-10-09-week.md",
            "processed/minutes/2026/10/2026-10-07-supplier-call.md",
            "processed/recording-note/2026/10/2026-10-07-supplier-call.md",
        ]
        assert index.covering("calls:nobody") == []
    finally:
        index.close()


def no_index(vault: Vault) -> None:
    build.db_path(vault).unlink()


def older_schema(vault: Vault) -> None:
    conn = sqlite3.connect(build.db_path(vault))
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()


def not_an_index(vault: Vault) -> None:
    build.db_path(vault).write_bytes(b"not a database, just bytes " * 100)


def no_doc_files(vault: Vault) -> None:
    conn = sqlite3.connect(build.db_path(vault))
    conn.execute("DROP TABLE doc_files")
    conn.commit()
    conn.close()


def doc_changed(vault: Vault) -> None:
    path = vault.root / LEGACY
    path.write_text(path.read_text(encoding="utf-8") + "More.\n", encoding="utf-8")


def doc_added(vault: Vault) -> None:
    (vault.root / "processed" / "legacy-note" / "another.md").write_text("# Another\n", encoding="utf-8")


def doc_removed(vault: Vault) -> None:
    (vault.root / LEGACY).unlink()


@pytest.mark.parametrize(
    "change", [no_index, older_schema, not_an_index, no_doc_files, doc_changed, doc_added, doc_removed]
)
def test_an_index_that_isn_t_current_is_none(vault: Vault, change: Callable[[Vault], None]) -> None:
    change(vault)
    assert open_current(vault) is None


def test_a_new_run_file_leaves_it_current_for_docs(vault: Vault) -> None:
    vault.write_run("chat", [make_record(90)])
    index = open_current(vault)
    assert index is not None
    index.close()


def test_asking_never_writes_the_index(vault: Vault) -> None:
    db = build.db_path(vault)
    before = (db.read_bytes(), db.stat().st_mtime_ns)
    index = open_current(vault)
    assert index is not None
    index.old_paths("notes/northwind/site lead.md")
    index.supersedes()
    index.covering(CALL)
    assert index.raw_current("calls")
    assert [p.line for p in index.places(CALL, "calls")] == [1]
    assert index.has(CALL, "calls") and not index.has(CALL, "chat")
    index.under(CALL, "calls", "transcript")
    index.group("thread", PARENT, "chat")
    index.close()
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before
    assert not db.with_name(db.name + "-journal").exists()


def raw_current(vault: Vault, source: str) -> bool:
    index = open_read(vault)
    assert index is not None
    try:
        return index.raw_current(source)
    finally:
        index.close()


def chat_run(vault: Vault) -> Path:
    return next((vault.root / "raw" / "chat").rglob("*.jsonl"))


def a_new_run(vault: Vault) -> None:
    vault.write_run("chat", [make_record(90)])


def a_run_touched(vault: Vault) -> None:
    path = chat_run(vault)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


def a_run_grown(vault: Vault) -> None:
    path = chat_run(vault)
    stat = path.stat()
    path.write_bytes(path.read_bytes() + b"\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def a_run_removed(vault: Vault) -> None:
    chat_run(vault).unlink()


def a_problem_line(vault: Vault) -> None:
    path = vault.root / "raw" / "chat" / "2026" / "10" / "2026-10-14T1200-test-box.jsonl"
    path.write_text(f"{make_record(91).to_json()}\n{{not json\n", encoding="utf-8", newline="\n")
    update(vault)


@pytest.mark.parametrize("change", [a_new_run, a_run_touched, a_run_grown, a_run_removed, a_problem_line])
def test_a_source_whose_runs_changed_isn_t_current_and_the_others_stay(
    vault: Vault, change: Callable[[Vault], None]
) -> None:
    assert raw_current(vault, "chat")
    change(vault)
    assert not raw_current(vault, "chat")
    assert raw_current(vault, "calls") and raw_current(vault, "mail")


def test_files_that_aren_t_runs_leave_a_source_current(vault: Vault) -> None:
    (vault.root / "raw" / "chat" / "README.md").write_text("Not a run.\n", encoding="utf-8")
    (vault.root / "raw" / "chat" / "2026" / "notes.txt").write_text("Not a run.\n", encoding="utf-8")
    (vault.root / "raw" / "chat" / "2026" / "10" / "notes.jsonl").write_text("{}\n", encoding="utf-8")
    assert raw_current(vault, "chat")
    assert raw_current(vault, "nosuch")


def test_an_index_without_its_run_files_is_current_for_no_source(vault: Vault) -> None:
    conn = sqlite3.connect(build.db_path(vault))
    conn.execute("DROP TABLE run_files")
    conn.commit()
    conn.close()
    assert not raw_current(vault, "chat")


def test_a_record_in_another_source_s_folder_makes_neither_source_current(vault: Vault) -> None:
    stray = make_record(rid="calls:stray", source="calls", kind="recording", container=None, text="A stray call.")
    path = vault.root / "raw" / "chat" / "2026" / "10" / "2026-10-14T1200-test-box.jsonl"
    path.write_text(f"{stray.to_json()}\n", encoding="utf-8", newline="\n")
    update(vault)
    assert not raw_current(vault, "chat")
    assert not raw_current(vault, "calls")
    assert raw_current(vault, "mail")


def test_records_group_by_thread_or_container_only(vault: Vault) -> None:
    index = open_read(vault)
    assert index is not None
    try:
        with pytest.raises(ValueError, match="thread or container"):
            index.group("author", "U0ADA", "chat")
    finally:
        index.close()


def test_an_update_that_takes_the_index_as_it_opens_sends_the_read_elsewhere(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = sqlite3.connect(build.db_path(vault), isolation_level=None)
    real = build.open_index

    def then_an_update(v: Vault, timeout: float) -> sqlite3.Connection:
        conn = real(v, timeout=timeout)
        writer.execute("BEGIN EXCLUSIVE")
        return conn

    monkeypatch.setattr(build, "open_index", then_an_update)
    try:
        assert open_read(vault) is None
    finally:
        writer.execute("ROLLBACK")
        writer.close()


def test_an_update_can_t_commit_while_a_read_asks(vault: Vault) -> None:
    index = open_current(vault)
    assert index is not None
    writer = sqlite3.connect(build.db_path(vault), timeout=0, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("DELETE FROM old_paths")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            writer.execute("COMMIT")
        assert index.old_paths("notes/northwind/site lead.md") == [OldPath("notes/Northwind/Site Lead.md", NORTHWIND)]
        writer.execute("ROLLBACK")
    finally:
        writer.close()
        index.close()


@pytest.mark.perf
def test_an_update_holding_the_index_sends_a_read_to_the_parse_route_at_once(vault: Vault) -> None:
    from docket import recall

    writer = sqlite3.connect(build.db_path(vault), isolation_level=None)
    try:
        writer.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        assert open_current(vault) is None
        assert time.monotonic() - started < 0.5
        found = recall.read(vault, "notes/northwind/site lead.md")
        assert found.resolved == NORTHWIND
        assert time.monotonic() - started < 1.0
        writer.execute("ROLLBACK")
    finally:
        writer.close()
