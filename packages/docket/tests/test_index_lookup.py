from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import CALL, copy_vault, index_template

from docket.index import build, rebuild
from docket.index.lookup import open_current
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


def test_a_new_run_file_leaves_it_current(vault: Vault) -> None:
    from docket_testkit import make_record

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
    index.close()
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before
    assert not db.with_name(db.name + "-journal").exists()


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
