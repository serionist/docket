from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docket_testkit import PARENT, copy_vault, index_template, logical_dump, make_record, notes_template

from docket import recall
from docket.index import build, matching, rebuild, status, update
from docket.index.schema import SCHEMA_VERSION
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

LATER = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    v = copy_vault(template, tmp_path)
    rebuild(v)
    return v


def reply(n: int, text: str, author: str = "U0CLEO") -> RawRecord:
    return make_record(
        rid=f"chat:C0OPS/{1791400000 + n * 100}.000100", container="chat:C0OPS", thread=PARENT, author=author,
        text=text, ts=datetime(2026, 10, 8, 7, n * 3 % 60, tzinfo=UTC),
    )  # fmt: skip


def same_as_rebuild(vault: Vault, change: Callable[[], object]) -> build.BuildReport:
    change()
    report = update(vault)
    assert not report.rebuilt, report.reason
    updated = logical_dump(build.db_path(vault))
    rebuild(vault)
    rebuilt = logical_dump(build.db_path(vault))
    for table in rebuilt:
        assert updated[table] == rebuilt[table], table
    return report


def test_nothing_changed_is_a_no_op(vault: Vault) -> None:
    report = update(vault)
    assert (report.rebuilt, report.runs, report.docs, report.entities) == (False, 0, 0, False)


def test_new_runs_new_versions_and_a_moved_thread_window(vault: Vault) -> None:
    def change() -> None:
        vault.write_run(
            "chat", [reply(14, "One more reply about the labels."), reply(3, "A határidő csütörtök.")], LATER
        )
        vault.write_run("mail", [make_record(rid="mail:<m2@x.example>", source="mail", kind="mail", text="Hi.")], LATER)

    report = same_as_rebuild(vault, change)
    assert report.runs == 2


def test_an_edited_parent_without_a_thread_field_is_cut_again_in_its_windows(vault: Vault) -> None:
    parent = "chat:C0OPS/1791500000.000100"

    def message(n: int, text: str, thread: str | None) -> RawRecord:
        return make_record(
            rid=f"chat:C0OPS/{1791500000 + n}.000100", container="chat:C0OPS", thread=thread, text=text,
            ts=datetime(2026, 10, 9, 7, n, tzinfo=UTC),
        )  # fmt: skip

    first = [message(0, "Kickoff plan alpha for the crane", None)]
    vault.write_run("chat", [*first, message(1, "Reply one", parent), message(2, "Reply two", parent)], LATER)
    update(vault)
    edited = message(0, "Kickoff plan omega for the crane", None)
    same_as_rebuild(vault, lambda: vault.write_run("chat", [edited], datetime(2026, 10, 14, 10, 0, tzinfo=UTC)))
    conn = sqlite3.connect(build.db_path(vault))
    texts = [r[0] for r in conn.execute("SELECT text FROM chunks WHERE owner = ?", (f"t {parent}",))]
    conn.close()
    assert texts and all("omega" in t and "alpha" not in t for t in texts)


def test_a_run_from_another_machine_that_is_older_keeps_the_newer_version(vault: Vault) -> None:
    def change() -> None:
        older = reply(3, "An older edit that arrived late.")
        vault.rawlog.machine = "other-box"
        vault.write_run("chat", [older], datetime(2026, 10, 14, 6, 0, tzinfo=UTC))

    same_as_rebuild(vault, change)
    conn = sqlite3.connect(build.db_path(vault))
    text = conn.execute("SELECT text FROM chunks WHERE owner = ?", (f"r {reply(3, '').rid}",)).fetchone()
    conn.close()
    assert text == ("A határidő péntek, addig kell az új címkenyomtató.",)


def test_a_doc_changed_and_a_doc_removed(vault: Vault) -> None:
    def change() -> None:
        doc = recall.read(vault, "recording-note:2026-10-07-supplier-call").doc
        assert doc is not None
        recall.doc_put(vault, doc.text.replace("above the budget", "well above the budget"))
        next((vault.root / "processed" / "legacy-note").glob("*.md")).unlink()

    report = same_as_rebuild(vault, change)
    assert report.docs == 2


def legacy_note(slug: str, *old_paths: str) -> str:
    paths = "".join(f"  - {p}\n" for p in old_paths)
    return f"---\nid: legacy-note:{slug}\ntype: legacy-note\nby: import\nold_paths:\n{paths}---\n\n# {slug}\n"


def test_old_paths_follow_their_notes_as_a_rebuild_would(vault: Vault) -> None:
    def put() -> None:
        recall.doc_put(vault, legacy_note("supplier", "notes/Calls/Supplier.md", "old/notes/calls/supplier.md"))
        recall.doc_put(vault, legacy_note("kickoff", "notes/calls/kickoff.md"))

    assert same_as_rebuild(vault, put).docs == 2
    # The fixture's own legacy note keeps an old path too.
    assert dump(vault, "SELECT doc, path, folded, name FROM old_paths ORDER BY doc, path") == [
        ("legacy-note:kickoff", "notes/calls/kickoff.md", "notes/calls/kickoff.md", "kickoff.md"),
        (
            "legacy-note:old-northwind-notes",
            "notes/Northwind/Site Lead.md",
            "notes/northwind/site lead.md",
            "site lead.md",
        ),
        ("legacy-note:supplier", "notes/Calls/Supplier.md", "notes/calls/supplier.md", "supplier.md"),
        ("legacy-note:supplier", "old/notes/calls/supplier.md", "old/notes/calls/supplier.md", "supplier.md"),
    ]

    def edit() -> None:
        path = vault.root / "processed" / "legacy-note" / "supplier.md"
        path.write_text(legacy_note("supplier", "notes/calls/supplier-call.md"), encoding="utf-8", newline="\n")
        (vault.root / "processed" / "legacy-note" / "kickoff.md").unlink()

    assert same_as_rebuild(vault, edit).docs == 2
    assert dump(vault, "SELECT doc, path FROM old_paths ORDER BY doc") == [
        ("legacy-note:old-northwind-notes", "notes/Northwind/Site Lead.md"),
        ("legacy-note:supplier", "notes/calls/supplier-call.md"),
    ]


def test_an_import_then_an_update_equals_a_rebuild(
    vault: Vault, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    from docket.imports.notes import import_notes

    shutil.copytree(notes_template(tmp_path_factory), tmp_path / "work")
    report = same_as_rebuild(vault, lambda: import_notes(vault, tmp_path / "work" / "notes"))
    assert report.docs > 5
    paths = [row[0] for row in dump(vault, "SELECT path FROM old_paths")]
    assert "notes/calls/2026-09-10-supplier-call.md" in paths


def dump(vault: Vault, sql: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(build.db_path(vault))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_an_entity_changed_recomputes_its_mentions(vault: Vault) -> None:
    def change() -> None:
        path = vault.root / "entities" / "person" / "cleo-test.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "aliases:\n  - {text: Cleó}\n", encoding="utf-8")

    assert same_as_rebuild(vault, change).entities


def test_a_new_misspelling_in_the_vocabulary_is_found(vault: Vault) -> None:
    same_as_rebuild(vault, lambda: vault.write_run("chat", [reply(15, "Nortvin called about the labels.")], LATER))
    conn = sqlite3.connect(build.db_path(vault))
    terms = conn.execute("SELECT term FROM alias_terms WHERE entity = 'northwind' AND how = 'asr'").fetchall()
    conn.close()
    assert ("nortvin",) in terms


def test_a_run_that_floods_a_stem_form_drops_it_as_a_rebuild_would(vault: Vault) -> None:
    (vault.root / "entities" / "person" / "mark.yaml").write_text(
        "id: mark\ntype: person\nname: {en: Mark}\n", encoding="utf-8"
    )
    vault.write_run("chat", [make_record(200 + n, text=f"Már megvan {n}.") for n in range(3)], LATER)
    update(vault)

    def how() -> str:
        conn = sqlite3.connect(build.db_path(vault))
        row = conn.execute("SELECT how FROM alias_terms WHERE entity = 'mark' AND term = 'mar'").fetchone()
        conn.close()
        return row[0]

    assert how() == "stem"
    flood = [make_record(300 + n, text=f"Már kész {n}.") for n in range(60)]
    same_as_rebuild(vault, lambda: vault.write_run("chat", flood, datetime(2026, 10, 14, 10, 0, tzinfo=UTC)))
    assert how() == "common"


def test_a_shortened_run_file_or_new_languages_or_another_version_rebuild(vault: Vault) -> None:
    first = sorted((vault.root / "raw" / "chat").rglob("*.jsonl"))[0]
    first.write_text(first.read_text(encoding="utf-8").splitlines(keepends=True)[0], encoding="utf-8")
    report = update(vault)
    assert report.rebuilt and "changed or went" in str(report.reason)
    vault.config.owner.languages = ["en", "hu", "de"]
    report = update(vault)
    assert report.rebuilt and "languages" in str(report.reason)
    assert status(vault).columns == ["folded", "stem_en", "stem_de", "stem_hu"]
    conn = sqlite3.connect(build.db_path(vault))
    conn.execute("UPDATE meta SET value = '0.0.0' WHERE key = 'snowballstemmer'")
    conn.commit()
    conn.close()
    assert "snowballstemmer" in str(update(vault).reason)


def test_hyphenated_compounds_come_and_go_as_in_a_rebuild(vault: Vault) -> None:
    # A reply says kick-off and e-mail-cím, then a new version of it drops them: its chunk and its thread's windows are
    # indexed with the joined words and deleted with them again, so the FTS5 rows end as a rebuild's (DR-0280).
    same_as_rebuild(
        vault, lambda: vault.write_run("chat", [reply(15, "The kick-off moved; the e-mail-cím list is out.")], LATER)
    )
    terms = folded_terms(vault)
    assert {"kickoff", "emailcim", "email"} <= terms
    same_as_rebuild(
        vault, lambda: vault.write_run("chat", [reply(15, "The kickoff moved.")], LATER + timedelta(hours=1))
    )
    terms = folded_terms(vault)
    assert "kickoff" in terms and "emailcim" not in terms


def folded_terms(vault: Vault) -> set[str]:
    conn = sqlite3.connect(build.db_path(vault))
    try:
        return {r[0] for r in conn.execute("SELECT term FROM vocab WHERE col = 'folded'")}
    finally:
        conn.close()


def test_an_index_of_the_schema_before_rebuilds(vault: Vault) -> None:
    conn = sqlite3.connect(build.db_path(vault))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
    conn.commit()
    conn.close()
    report = update(vault)
    assert (
        report.rebuilt
        and report.reason == f"it has schema {SCHEMA_VERSION - 1}, and this Docket writes {SCHEMA_VERSION}"
    )
    assert status(vault).stale is None


@pytest.mark.parametrize("old", [1, 2, 3])
def test_an_index_of_an_older_schema_rebuilds(vault: Vault, old: int) -> None:
    conn = sqlite3.connect(build.db_path(vault))
    conn.execute(f"PRAGMA user_version = {old}")
    conn.commit()
    conn.close()
    report = update(vault)
    assert report.rebuilt and report.reason == f"it has schema {old}, and this Docket writes {SCHEMA_VERSION}"
    assert status(vault).stale is None


def test_a_run_file_written_again_at_the_same_length_rebuilds(vault: Vault) -> None:
    first = sorted((vault.root / "raw" / "chat").rglob("*.jsonl"))[0]
    data = first.read_bytes()
    redacted = data.replace(b"Pallet labels", b"XXXXXX XXXXXX")
    assert len(redacted) == len(data) and redacted != data
    before = first.stat().st_mtime_ns
    first.write_bytes(redacted)
    # What a pull of a rewritten history leaves: the file written later. Set apart by a second for filesystems that
    # keep coarse times.
    os.utime(first, ns=(before + 1_000_000_000, before + 1_000_000_000))
    assert status(vault).changed_runs == 1
    report = update(vault)
    assert report.rebuilt and "changed or went" in str(report.reason)
    conn = sqlite3.connect(build.db_path(vault))
    old = conn.execute("SELECT count(*) FROM chunks WHERE text LIKE 'Pallet labels at the harbour keep%'").fetchone()
    conn.close()
    assert old == (0,)


def test_no_index_yet_builds_one(template: Path, tmp_path: Path) -> None:
    v = copy_vault(template, tmp_path)
    report = update(v)
    assert report.rebuilt and report.reason == "there was no index"


def test_a_failed_update_leaves_the_index_as_it_was(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    before = logical_dump(build.db_path(vault))
    vault.write_run("chat", [reply(16, "This one fails halfway.")], LATER)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("halfway")

    monkeypatch.setattr(matching, "refresh", boom)
    with pytest.raises(RuntimeError, match="halfway"):
        update(vault)
    assert logical_dump(build.db_path(vault)) == before
