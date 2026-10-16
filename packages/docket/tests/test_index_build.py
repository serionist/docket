from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import CALL, INDEX_FIXTURE, PARENT, copy_vault, index_template, make_record, plaud_records

from docket.errors import DocketError
from docket.index import build, locate, matching, rebuild, status, update
from docket.vault import rawlog
from docket.vault.records import RawRecord
from docket.vault.vault import Vault


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return copy_vault(template, tmp_path)


def rows(vault: Vault, sql: str, *args: object) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(build.db_path(vault))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def run_file(vault: Vault, name: str, lines: list[str]) -> Path:
    path = vault.root / "raw" / "chat" / "2026" / "10" / name
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8", newline="\n")
    return path


def test_a_rebuild_fills_every_table(vault: Vault) -> None:
    report = rebuild(vault)
    assert report.rebuilt and report.runs == 4 and report.docs == 7 and report.entities
    assert report.counts["records"] == 22 and report.counts["docs"] == 7 and report.counts["entities"] == 6
    assert report.counts["chunks"] > 30 and report.counts["mentions"] > 30
    assert report.problems == [
        "processed/digest/2026/10/2026-10-09-week.md: entities: no entity has the id 'no-such-entity'"
    ]
    assert rows(vault, "SELECT count(*) FROM segments WHERE rid = ?", CALL) == [(14,)]
    assert rows(vault, "SELECT person FROM segments WHERE rid = ? AND ordinal = 1", CALL) == [("dora-minta",)]
    assert rows(vault, "SELECT superseded_by FROM docs WHERE id = 'minutes:2026-10-13-fleet-review'") == [
        ("recording-note:2026-10-13-fleet-review",)
    ]
    assert rows(vault, "SELECT * FROM old_paths") == [
        (
            "legacy-note:old-northwind-notes",
            "notes/Northwind/Site Lead.md",
            "notes/northwind/site lead.md",
            "site lead.md",
        )
    ]
    assert not list((vault.root / "index").glob("*.tmp"))
    assert locate(vault, CALL) is not None and locate(vault, "calls:gone") is None


def test_two_rebuilds_in_two_processes_give_the_same_database(vault: Vault, tmp_path: Path) -> None:
    dumps: list[list[str]] = []
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONUTF8": "1"}
        cmd = [sys.executable, "-m", "docket", "index", "rebuild", "--vault", str(vault.root)]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, check=False)
        assert proc.returncode == 0, proc.stderr
        copy = tmp_path / f"seed-{seed}.sqlite"
        shutil.copy(build.db_path(vault), copy)
        conn = sqlite3.connect(copy)
        dumps.append(list(conn.iterdump()))
        conn.close()
    assert dumps[0] == dumps[1]


def test_only_the_latest_version_is_chunked_and_versions_are_counted(vault: Vault) -> None:
    edited = make_record(
        rid=f"{PARENT[:-17]}1791400300.000100", thread=PARENT, container="chat:C0OPS", author="U0DORA",
        text="A határidő csütörtök lett.", ts=datetime(2026, 10, 8, 7, 9, tzinfo=UTC),
    )  # fmt: skip
    vault.write_run("chat", [edited], datetime(2026, 10, 14, 9, 0, tzinfo=UTC))
    rebuild(vault)
    rid = edited.rid
    assert rows(vault, "SELECT versions, path FROM records WHERE rid = ?", rid) == [
        (2, "raw/chat/2026/10/2026-10-14T0900-test-box.jsonl")
    ]
    assert rows(vault, "SELECT text FROM chunks WHERE owner = ?", f"r {rid}") == [("A határidő csütörtök lett.",)]


@pytest.mark.parametrize("how", ["update", "rebuild"])
def test_a_promotion_split_over_run_files_is_indexed_in_its_order(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    rebuild(vault)
    monkeypatch.setattr(rawlog, "RUN_FILE_BYTES", 1500)
    # A Plaud recording and, at the end of the same promotion, its new title: two versions in two run files.
    first = plaud_records("first", "second", "third")
    renamed = plaud_records(title="Supplier sync, renamed")[0]
    vault.write_run("plaud", [*first, renamed], datetime(2026, 10, 16, 9, 0, tzinfo=UTC))
    files = [run.path for run in vault.rawlog.run_files() if run.source == "plaud"]
    assert len(files) > 2
    report = update(vault) if how == "update" else rebuild(vault)
    # Every split file is a new run to an index already built; a rebuild reads the template's four runs too.
    assert (report.rebuilt, report.runs) == ((False, len(files)) if how == "update" else (True, 4 + len(files)))
    last = files[-1].relative_to(vault.root).as_posix()
    assert rows(vault, "SELECT versions, title, path FROM records WHERE rid = ?", renamed.rid) == [
        (2, "Supplier sync, renamed", last)
    ]
    indexed = rows(vault, "SELECT path FROM run_files WHERE path LIKE 'raw/plaud/%' ORDER BY path")
    assert sorted(str(p) for (p,) in indexed) == sorted(f.relative_to(vault.root).as_posix() for f in files)
    assert rows(vault, "SELECT count(*) FROM records WHERE source = 'plaud'") == [(len(first),)]


def test_raw_lines_are_read_leniently(vault: Vault) -> None:
    good = make_record(90, text="still indexed")
    newer = json.loads(make_record(91, text="from a newer docket").to_json()) | {"mood": "new field"}
    run_file(
        vault,
        "2026-10-14T1200-test-box.jsonl",
        [good.to_json(), "{not json", "[1, 2]", json.dumps({"rid": "chat:x"}), json.dumps(newer), ""],
    )
    report = rebuild(vault)
    place = "raw/chat/2026/10/2026-10-14T1200-test-box.jsonl"
    assert f"{place}:2: not JSON: Expecting property name enclosed in double quotes" in report.problems
    assert f"{place}:3: not a raw record: a JSON object per line" in report.problems
    assert any(p.startswith(f"{place}:4: not a raw record: ") for p in report.problems)
    assert f"{place}:5: fields this Docket doesn't know, left out: mood" in report.problems
    texts = {r[0] for r in rows(vault, "SELECT text FROM chunks WHERE kind = 'message'")}
    assert {"still indexed", "from a newer docket"} <= texts


def test_each_version_s_offset_reads_back_its_line(vault: Vault) -> None:
    crlf = [make_record(94, text="a line with CRLF after it"), make_record(95, text="ő and ű, then CRLF")]
    path = vault.root / "raw" / "chat" / "2026" / "10" / "2026-10-14T1200-test-box.jsonl"
    path.write_bytes(b"".join(r.to_json().encode("utf-8") + b"\r\n" for r in crlf) + b"\r\n")
    rebuild(vault)
    found = rows(vault, "SELECT rid, v, path, line, offset FROM record_versions")
    assert len(found) == 24
    for rid, v, rel, line, offset in found:
        with (vault.root / str(rel)).open("rb") as f:
            f.seek(int(str(offset)))
            record = RawRecord.model_validate(json.loads(f.readline().decode("utf-8")))
        assert (record.rid, record.v) == (rid, v), (rel, line)
    assert rows(vault, "SELECT line, offset FROM record_versions WHERE path LIKE '%T1200%' ORDER BY line") == [
        (1, 0),
        (2, len(crlf[0].to_json().encode("utf-8")) + 2),
    ]


def test_a_line_that_isn_t_utf8_and_a_record_in_another_source_s_folder_are_indexed_and_reported(
    vault: Vault,
) -> None:
    menu = make_record(96, text="café menu").to_json().encode("utf-8").replace("é".encode(), b"\xe9")
    stray = make_record(rid="calls:stray", source="calls", kind="recording", container=None, text="A stray call.")
    path = vault.root / "raw" / "chat" / "2026" / "10" / "2026-10-14T1200-test-box.jsonl"
    path.write_bytes(menu + b"\n" + stray.to_json().encode("utf-8") + b"\n")
    report = rebuild(vault)
    place = "raw/chat/2026/10/2026-10-14T1200-test-box.jsonl"
    assert f"{place}:1: not UTF-8: indexed with its bad bytes replaced" in report.problems
    assert f"{place}:2: a record of calls in chat's folder" in report.problems
    texts = {r[0] for r in rows(vault, "SELECT text FROM chunks")}
    assert {"caf\N{REPLACEMENT CHARACTER} menu", "A stray call."} <= texts


def test_an_unknown_field_inside_an_attachment_is_left_out_and_the_record_indexed(vault: Vault) -> None:
    attached = json.loads(make_record(92, text="the winch manual", attachments=[{"name": "winch.pdf"}]).to_json())
    attached["attachments"][0] |= {"size": 1234, "pages": 9}
    odd = json.loads(make_record(93, text="an attachment that is only a name").to_json()) | {"attachments": ["x.pdf"]}
    run_file(vault, "2026-10-14T1200-test-box.jsonl", [json.dumps(attached), json.dumps(odd)])
    report = rebuild(vault)
    place = "raw/chat/2026/10/2026-10-14T1200-test-box.jsonl"
    assert (
        f"{place}:1: fields this Docket doesn't know, left out: attachments.pages, attachments.size" in report.problems
    )
    assert any(p.startswith(f"{place}:2: not a raw record: attachments.0") for p in report.problems)
    texts = {r[0] for r in rows(vault, "SELECT text FROM chunks WHERE kind = 'message'")}
    assert "the winch manual" in texts


def test_a_line_with_malformed_segments_is_a_problem_and_the_rest_is_indexed(vault: Vault) -> None:
    fine = make_record(rid="calls:fine", source="calls", kind="recording", container=None, text="Fine text.")
    broken = json.loads(fine.to_json()) | {"rid": "calls:broken", "segments": [{"start_ms": 5, "end_ms": 1}]}
    path = vault.root / "raw" / "calls" / "2026" / "10" / "2026-10-14T1200-test-box.jsonl"
    path.write_text(f"{json.dumps(broken)}\n{fine.to_json()}\n", encoding="utf-8", newline="\n")
    report = rebuild(vault)
    place = "raw/calls/2026/10/2026-10-14T1200-test-box.jsonl"
    assert any(p.startswith(f"{place}:1: not a raw record: segments.0") for p in report.problems), report.problems
    assert rows(vault, "SELECT count(*) FROM records WHERE rid = 'calls:broken'") == [(0,)]
    assert rows(vault, "SELECT text, kind FROM chunks WHERE rid = 'calls:fine'") == [("Fine text.", "record")]


def test_docs_that_can_t_be_read_are_problems(vault: Vault) -> None:
    folder = vault.root / "processed" / "digest"
    (folder / "broken.md").write_text("no frontmatter\n", encoding="utf-8")
    (folder / "latin1.md").write_bytes(b"---\nid: digest:x\n---\n\xe9\n")
    misplaced = (INDEX_FIXTURE / "docs" / "07-legacy.md").read_text(encoding="utf-8")
    (folder / "misplaced.md").write_text(misplaced, encoding="utf-8")
    problems = rebuild(vault).problems
    assert any(p.startswith("processed/digest/broken.md: the doc was refused") for p in problems)
    assert "processed/digest/latin1.md: isn't UTF-8" in problems
    assert any(p.startswith("processed/digest/misplaced.md: its id legacy-note:old-northwind-notes") for p in problems)


def test_staging_is_never_read(vault: Vault) -> None:
    staged = vault.root / "staging" / "chat" / "x.json"
    staged.parent.mkdir(parents=True)
    staged.write_text(json.dumps({"record": json.loads(make_record(77, text="staged secret").to_json())}), "utf-8")
    rebuild(vault)
    assert rows(vault, "SELECT count(*) FROM chunks WHERE text LIKE '%staged secret%'") == [(0,)]


def test_a_failure_halfway_leaves_the_old_index(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    rebuild(vault)
    before = build.db_path(vault).read_bytes()

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("halfway")

    monkeypatch.setattr(matching, "refresh", boom)
    with pytest.raises(RuntimeError, match="halfway"):
        rebuild(vault)
    assert build.db_path(vault).read_bytes() == before
    assert not list((vault.root / "index").glob("*.tmp"))


def interrupted_update(db: Path, tmp_path: Path) -> Path:
    """The index and the hot journal an update killed halfway leaves: its pages written, its journal still there."""
    crashed = tmp_path / "crashed"
    crashed.mkdir(exist_ok=True)
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute("PRAGMA cache_size = 1")
    conn.execute("BEGIN")
    conn.execute("UPDATE chunks SET text = text || ' and more words than fit in one page'")
    journal = db.with_name(db.name + "-journal")
    assert journal.exists()
    shutil.copy(db, crashed / db.name)
    shutil.copy(journal, crashed / journal.name)
    conn.execute("ROLLBACK")
    conn.close()
    shutil.copy(crashed / db.name, db)
    shutil.copy(crashed / journal.name, journal)
    return journal


def test_a_hot_journal_beside_the_old_index_is_rolled_back_first(vault: Vault, tmp_path: Path) -> None:
    rebuild(vault)
    db = build.db_path(vault)
    journal = interrupted_update(db, tmp_path)
    rebuild(vault)
    assert not journal.exists()
    check = sqlite3.connect(db)
    assert check.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert check.execute("SELECT count(*) FROM chunks WHERE text LIKE '%than fit in one page'").fetchone() == (0,)
    check.close()


def test_reading_after_an_interrupted_update_rolls_its_journal_back(vault: Vault, tmp_path: Path) -> None:
    rebuild(vault)
    db = build.db_path(vault)
    journal = interrupted_update(db, tmp_path)
    assert status(vault).stale is None
    assert not journal.exists()
    interrupted_update(db, tmp_path)
    build.open_index(vault).close()
    assert not journal.exists()
    check = sqlite3.connect(db)
    assert check.execute("SELECT count(*) FROM chunks WHERE text LIKE '%than fit in one page'").fetchone() == (0,)
    check.close()


def test_a_reader_never_writes(vault: Vault) -> None:
    rebuild(vault)
    conn = build.open_index(vault)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM chunks")
    conn.close()


@pytest.mark.skipif(sys.platform != "win32", reason="the \\\\?\\ prefix is Windows'")
def test_an_index_on_a_path_no_file_uri_can_name_opens(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    rebuild(vault)
    # A `\\?\` path, like a `\\server\share` one, has no file URI SQLite opens.
    long_form = Path("\\\\?\\" + str(build.db_path(vault)))
    monkeypatch.setattr(build, "db_path", lambda _vault: long_form)
    conn = build.open_index(vault)
    assert conn.execute("SELECT count(*) FROM records").fetchone() == (22,)
    conn.close()


def test_a_killed_rebuild_s_temp_file_goes_with_the_next_rebuild(vault: Vault) -> None:
    rebuild(vault)
    left = build.db_path(vault).with_name("docket.sqlite.99999.tmp")
    left.write_bytes(b"x" * 1000)
    rebuild(vault)
    assert not left.exists()


def test_an_old_index_that_won_t_open_goes_with_its_journals(vault: Vault) -> None:
    db = build.db_path(vault)
    db.parent.mkdir(exist_ok=True)
    db.write_bytes(b"not a database at all, just bytes" * 100)
    db.with_name(db.name + "-journal").write_bytes(b"junk")
    rebuild(vault)
    assert not db.with_name(db.name + "-journal").exists()
    assert status(vault).stale is None


def test_a_replace_refused_while_open_is_retried_then_reported(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    real = Path.replace
    refusals = {"left": 2}

    def flaky(self: Path, target: Path) -> Path:
        if refusals["left"]:
            refusals["left"] -= 1
            raise PermissionError("in use")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    monkeypatch.setattr(build, "REPLACE_WAIT", 0)
    rebuild(vault)
    assert refusals["left"] == 0

    def never(self: Path, target: Path) -> Path:
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "replace", never)
    monkeypatch.setattr(build, "REPLACE_SECONDS", 0)
    with pytest.raises(DocketError, match="open in another process"):
        rebuild(vault)
    assert not list((vault.root / "index").glob("*.tmp"))


def test_status_says_what_is_new_and_what_needs_a_rebuild(vault: Vault) -> None:
    assert status(vault).exists is False
    with pytest.raises(DocketError, match="no index yet"):
        locate(vault, CALL)
    rebuild(vault)
    st = status(vault)
    assert (st.stale, st.new_runs, st.changed_docs, st.entities_changed) == (None, 0, 0, False)
    assert st.columns == ["folded", "stem_en", "stem_hu"] and st.counts["docs"] == 7
    run_file(vault, "2026-10-14T1300-test-box.jsonl", [make_record(95).to_json()])
    note = next((vault.root / "processed" / "legacy-note").glob("*.md"))
    note.write_text(note.read_text(encoding="utf-8") + "More.\n", encoding="utf-8")
    (vault.root / "entities" / "person" / "cleo-test.yaml").unlink()
    st = status(vault)
    assert (st.new_runs, st.changed_docs, st.entities_changed) == (1, 1, True)
    first = sorted((vault.root / "raw" / "cal").rglob("*.jsonl"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    assert status(vault).changed_runs == 1
    vault.config.owner.languages = ["en", "de"]
    assert "languages" in str(status(vault).stale)
    with pytest.raises(DocketError, match="can't be used"):
        locate(vault, CALL)


def test_a_vault_without_processed_docs_or_entities_builds(tmp_path: Path) -> None:
    from docket_testkit import index_config

    vault = Vault.init(tmp_path / "empty", index_config(["en", "klingon"]), "test-box")
    shutil.rmtree(vault.root / "processed")
    shutil.rmtree(vault.root / "entities")
    report = rebuild(vault)
    assert report.counts["chunks"] == 0
    assert any(p.startswith("vault.yaml: vault.yaml's language 'klingon' has no stemmer") for p in report.problems)
    st = status(vault)
    assert (st.changed_docs, st.entities_changed) == (0, False)
