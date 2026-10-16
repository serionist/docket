"""Vectors in the index (DR-0336): kept in step with the chunks by every update and rebuild, with no worker, and
filled by the embedding pass, which never holds the index while the worker works."""

from __future__ import annotations

import dataclasses
import sqlite3
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from docket_testkit import copy_vault, index_template, logical_dump, make_record
from models_kit import FAKE, fake_models, trigram_vector

from docket import recall, userconfig
from docket.cli import main
from docket.index import build, embedpass, rebuild, status, update, vectors
from docket.index.embedpass import fill
from docket.index.vectors import VecTable, table_of
from docket.models import catalogue
from docket.models.catalogue import Embedder, input_hash
from docket.models.client import WorkerError
from docket.vault.vault import Vault

LATER = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
TABLE = table_of(FAKE)
OTHER = dataclasses.replace(FAKE, name="fake-two", revision="f" * 40)


def choose(monkeypatch: pytest.MonkeyPatch, embedder: Embedder | None = FAKE) -> None:
    path = userconfig.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if embedder is None:
        path.write_text("{}\n", encoding="utf-8")
        return
    monkeypatch.setitem(catalogue.EMBEDDERS, embedder.name, embedder)
    path.write_text(yaml.safe_dump({"models": {"device": "cpu", "embedder": embedder.name}}), encoding="utf-8")


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    choose(monkeypatch)
    v = copy_vault(template, tmp_path)
    rebuild(v)
    return v


def rows(vault: Vault, table: VecTable = TABLE) -> dict[tuple[str, int], tuple[str, bytes]]:
    """The table's vectors by the chunk they belong to, each checked against that chunk's input now."""
    conn = sqlite3.connect(build.db_path(vault))
    try:
        found = conn.execute(
            f"SELECT c.owner, c.ordinal, c.title, c.text, v.input_hash, v.vec, v.ts IS c.ts, v.source IS c.source "  # noqa: S608
            f"FROM {table.name} v LEFT JOIN chunks c ON c.id = v.chunk"
        ).fetchall()
    finally:
        conn.close()
    out: dict[tuple[str, int], tuple[str, bytes]] = {}
    for owner, ordinal, title, text, hashed, vec, same_ts, same_source in found:
        assert owner is not None, "a vector outlived its chunk"
        assert hashed == input_hash(table.input(title, text)), f"{owner} keeps another input's vector"
        assert same_ts and same_source
        out[(owner, ordinal)] = (hashed, vec)
    return out


def chunks(vault: Vault) -> int:
    conn = sqlite3.connect(build.db_path(vault))
    try:
        return int(conn.execute("SELECT count(*) FROM chunks").fetchone()[0])
    finally:
        conn.close()


def test_a_rebuild_makes_the_configured_table_and_the_pass_fills_it(vault: Vault) -> None:
    assert rows(vault) == {}
    models, worker = fake_models()
    seen: list[tuple[int, int]] = []
    report = fill(vault, models, progress=lambda have, total: seen.append((have, total)), rows=10)
    total = chunks(vault)
    assert (report.have, report.total, report.why, report.dropped) == (total, total, None, [])
    assert report.embedded + report.copied == total and seen[-1] == (total, total)
    found = rows(vault)
    assert len(found) == total and all(
        vec == trigram_vector(input_hash_input(vault, key)) for key, (_, vec) in found.items()
    )
    assert status(vault).vectors[0].model_dump() == {"model": "fake", "have": total, "total": total}
    assert sum(len(b) for b in worker.batches) == report.embedded


def input_hash_input(vault: Vault, key: tuple[str, int]) -> str:
    conn = sqlite3.connect(build.db_path(vault))
    try:
        title, text = conn.execute("SELECT title, text FROM chunks WHERE owner = ? AND ordinal = ?", key).fetchone()
    finally:
        conn.close()
    return TABLE.input(title, text)


def test_a_rebuild_keeps_every_vector_with_no_worker(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    models, _ = fake_models()
    fill(vault, models)
    before = rows(vault)
    rebuild(vault, "a schema bump")
    assert rows(vault) == before
    # docket doc redact's rebuild too.
    assert main(["doc", "redact", "--vault", str(vault.root)]) == 0
    capsys.readouterr()
    assert len(rows(vault)) == chunks(vault)
    assert main(["index", "status", "--vault", str(vault.root)]) == 0
    assert f"vectors    fake  {chunks(vault)} of {chunks(vault)} chunks" in capsys.readouterr().out


def test_an_update_keeps_vectors_for_unchanged_input_and_drops_the_rest(vault: Vault) -> None:
    models, worker = fake_models()
    fill(vault, models)
    doc = recall.read(vault, "recording-note:2026-10-07-supplier-call").doc
    assert doc is not None
    recall.doc_put(vault, doc.text.replace("above the budget", "well above the budget"))
    vault.write_run(
        "chat", [make_record(rid="chat:C0OPS/1791400300.000100", container="chat:C0OPS", text="An edit.")], LATER
    )
    update(vault)
    # Right after the update and before any pass: every vector is its own chunk's, and only the changed ones lack one.
    kept = rows(vault)
    missing = chunks(vault) - len(kept)
    assert 0 < missing < 6
    worker.batches.clear()
    report = fill(vault, models)
    assert report.embedded == missing and len(rows(vault)) == chunks(vault)


def test_a_reused_id_with_new_text_gets_a_new_vector(vault: Vault) -> None:
    models, _ = fake_models()
    rid = "chat:C0OPS/1791490000.000100"
    vault.write_run("chat", [make_record(rid=rid, container="chat:C0OPS", text="First words.")], LATER)
    update(vault)
    fill(vault, models)
    conn = sqlite3.connect(build.db_path(vault))
    before = conn.execute("SELECT id FROM chunks WHERE owner = ?", (f"r {rid}",)).fetchone()[0]
    conn.close()
    later = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    vault.write_run("chat", [make_record(rid=rid, container="chat:C0OPS", text="Other words entirely.")], later)
    update(vault)
    conn = sqlite3.connect(build.db_path(vault))
    after, title, text = conn.execute("SELECT id, title, text FROM chunks WHERE owner = ?", (f"r {rid}",)).fetchone()
    conn.close()
    assert after == before, "SQLite gave the edited message its old id, the case the update must handle"
    assert (f"r {rid}", 0) not in rows(vault)
    fill(vault, models)
    assert rows(vault)[(f"r {rid}", 0)][1] == trigram_vector(TABLE.input(title, text))


def test_update_and_rebuild_agree_on_the_vectors(vault: Vault) -> None:
    models, _ = fake_models()
    fill(vault, models)
    vault.write_run("chat", [make_record(rid="chat:C0OPS/1791400300.000100", container="chat:C0OPS", text="x")], LATER)
    assert not update(vault).rebuilt
    fill(vault, models)
    updated = logical_dump(build.db_path(vault))
    rebuild(vault)
    rebuilt = logical_dump(build.db_path(vault))
    assert TABLE.name in updated and updated[TABLE.name] == rebuilt[TABLE.name]
    assert updated == rebuilt


def test_a_rebuild_over_a_file_it_cant_read_starts_the_table_empty(vault: Vault) -> None:
    models, _ = fake_models()
    fill(vault, models)
    build.db_path(vault).write_bytes(b"not a database at all, just bytes " * 200)
    rebuild(vault)
    assert rows(vault) == {}


def test_without_a_configured_embedder_a_rebuild_makes_no_table(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    choose(monkeypatch, None)
    rebuild(vault)
    conn = sqlite3.connect(build.db_path(vault))
    try:
        assert vectors.tables(conn) == []
    finally:
        conn.close()


def test_a_model_change_keeps_the_old_table_until_the_new_one_is_whole(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    models, _ = fake_models()
    fill(vault, models)
    choose(monkeypatch, OTHER)
    other, _ = fake_models()
    other.embedder = OTHER
    calls = [0]

    def once() -> bool:
        calls[0] += 1
        return calls[0] > 1

    report = fill(vault, other, stop=once, rows=5)
    assert report.dropped == [] and 0 < report.have < report.total
    assert len(rows(vault)) == chunks(vault)
    report = fill(vault, other)
    assert report.dropped == ["fake"] and len(rows(vault, table_of(OTHER))) == chunks(vault)
    conn = sqlite3.connect(build.db_path(vault))
    try:
        assert [t.key for t in vectors.tables(conn)] == [OTHER.key]
    finally:
        conn.close()


class Blocking:
    """Models whose worker waits until a test lets it go."""

    def __init__(self) -> None:
        self.embedder = FAKE
        self.inside = threading.Event()
        self.go = threading.Event()

    def passage_vectors(self, inputs: Sequence[str]) -> list[bytes]:
        self.inside.set()
        assert self.go.wait(30)
        return [trigram_vector(t) for t in inputs]


def test_an_update_commits_while_the_worker_is_busy(vault: Vault) -> None:
    models = Blocking()
    reports: list[embedpass.PassReport] = []
    thread = threading.Thread(target=lambda: reports.append(fill(vault, models)))
    thread.start()
    assert models.inside.wait(30)
    vault.write_run("chat", [make_record(rid="chat:C0OPS/1791400300.000100", container="chat:C0OPS", text="y")], LATER)
    started = time.monotonic()
    assert not update(vault).rebuilt
    # The pass holds neither the index's lock nor a read while the worker works, so the update didn't wait for it.
    assert time.monotonic() - started < build.BUSY_SECONDS
    models.go.set()
    thread.join(30)
    assert reports[0].why is None and len(rows(vault)) == chunks(vault)


class Editing:
    """Models whose first call changes a chunk's text, as an update can while the worker works."""

    def __init__(self, vault: Vault, then: str) -> None:
        self.embedder = FAKE
        self.vault = vault
        self.then = then
        self.calls = 0

    def passage_vectors(self, inputs: Sequence[str]) -> list[bytes]:
        self.calls += 1
        if self.calls == 1:
            if self.then == "edit":
                for n in range(3):
                    self.vault.write_run(
                        "chat",
                        [make_record(rid="chat:C0OPS/1791400300.000100", container="chat:C0OPS", text=f"Edit {n}.")],
                        datetime(2026, 10, 14, 9, n, tzinfo=UTC),
                    )
                update(self.vault)
            elif self.then == "fail":
                raise WorkerError("the models' worker stopped (exit 5)")
        return [trigram_vector(t) for t in inputs]


def test_a_vector_for_input_that_changed_meanwhile_is_not_written(vault: Vault) -> None:
    vault.write_run("chat", [make_record(rid="chat:C0OPS/1791400300.000100", container="chat:C0OPS", text="z")], LATER)
    update(vault)
    report = fill(vault, Editing(vault, "edit"))
    assert report.why is None and len(rows(vault)) == chunks(vault)


def test_the_pass_says_why_it_stopped(vault: Vault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report = fill(vault, Editing(vault, "fail"))
    assert report.why == "the models' worker stopped (exit 5)" and report.dropped == []
    monkeypatch.setattr(embedpass, "_write", lambda *a: 0)
    assert fill(vault, fake_models()[0]).why == "the index kept changing under the pass; the next one goes on"
    empty = Vault.init(tmp_path / "empty", vault.config, "test-box")
    assert fill(empty, fake_models()[0]).why == "there is no index yet"


def test_an_index_busy_past_its_timeout_stops_the_pass_and_says_so(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked(*args: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    busy = "the index was busy past its timeout, so the pass stopped; the next one goes on from there"
    monkeypatch.setattr(embedpass, "_write", locked)
    assert fill(vault, fake_models()[0]).why == busy
    monkeypatch.setattr(embedpass, "_missing", locked)
    assert fill(vault, fake_models()[0]).why == busy
    # So busy that even the look for the table fails: busy, since a rebuild's table is there or gone at once.
    monkeypatch.setattr(vectors, "holds", locked)
    conn = sqlite3.connect(build.db_path(vault))
    try:
        assert embedpass._stopped(conn, table_of(FAKE)) == busy  # pyright: ignore[reportPrivateUsage]
    finally:
        conn.close()


def test_a_table_a_rebuild_took_away_stops_the_pass(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    class Rebuilding:
        embedder = FAKE

        def passage_vectors(self, inputs: Sequence[str]) -> list[bytes]:
            choose(monkeypatch, None)
            rebuild(vault)
            return [trigram_vector(t) for t in inputs]

    report = fill(vault, Rebuilding())
    assert report.why == "the vector table went with a rebuild" and (report.have, report.total) == (0, 0)
    choose(monkeypatch)
    rebuild(vault)
    models, _ = fake_models()
    gone = [False]

    def take_it_away(have: int, total: int) -> None:
        if not gone[0]:
            gone[0] = True
            choose(monkeypatch, None)
            rebuild(vault)

    report = fill(vault, models, progress=take_it_away, rows=5)
    assert report.why == "the vector table went with a rebuild"


def test_tables_pass_over_rows_they_cant_read_and_create_refuses_a_bad_key(vault: Vault) -> None:
    conn = sqlite3.connect(build.db_path(vault))
    try:
        conn.execute("INSERT INTO meta (key, value) VALUES ('vec.junk', 'not json')")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('vec.BAD-KEY', "
            '\'{"model": "x", "revision": "y", "dims": 4, "prefix": ""}\')'
        )
        assert [t.key for t in vectors.tables(conn)] == [TABLE.key]
        with pytest.raises(ValueError, match="lowercase letters, digits and _"):
            vectors.create(conn, dataclasses.replace(TABLE, key="Bad Key"))
    finally:
        conn.close()
