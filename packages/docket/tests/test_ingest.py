"""`ingest_run` and `docket ingest` on temp vaults, with an invented mapper for an invented `memo` source: the import
order, holds on every route, the run checks, the folder, and output that never names a title."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import copy_project, sh, template_project

from docket.cli import main
from docket.errors import DocketError
from docket.sources.contract import Capture, Manifest, SetAside, Waiting
from docket.sources.ingest import ingest_run
from docket.sources.runner import MapperFailedError
from docket.vault import holds
from docket.vault.gate import Gate
from docket.vault.holds import HOLDS
from docket.vault.vault import Vault

# Reads each capture's result, or a file's content, as one memo or a list of them. `leak` returns a held memo anyway,
# to test the core's own hold check; `sleep` and `crash` misbehave on purpose.
MAPPER = """\
import time


def map_run(run):
    held = set(run["holds"])
    out = {"contract": 1, "records": [], "set_aside": [], "waiting": [], "held": []}
    for capture in run["captures"]:
        items = capture["result"]
        for item in items if isinstance(items, list) else [items]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                out["set_aside"].append({"id": None, "reason": "bad-item"})
                continue
            memo = item["id"]
            if memo in held and not item.get("leak"):
                out["held"].append(memo)
                continue
            if item.get("sleep"):
                time.sleep(item["sleep"])
            if item.get("crash"):
                raise ValueError(item["crash"])
            if item.get("wait"):
                out["waiting"].append({"id": memo, "for": "summary"})
                continue
            if "ts" not in item:
                out["set_aside"].append({"id": memo, "reason": "no-ts", "key": "ts"})
                continue
            out["records"].append(
                {
                    "rid": "memo:" + memo,
                    "source": "memo",
                    "kind": "memo",
                    "container": "memo:" + memo,
                    "ts": item["ts"],
                    "title": item.get("title"),
                    "text": item.get("text", ""),
                    "fetched": capture["captured"],
                    "via": "memo-file" if capture["file"] else "memo-tool",
                }
            )
    return out
"""
TS = "2026-10-14T09:30:00Z"
PRIVATE_TITLE = "Dentist with Cleo"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_project(tmp_path_factory, {"memo": MAPPER})


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return copy_project(template, tmp_path)


def memo(memo_id: str, **fields: Any) -> dict[str, Any]:
    return {"id": memo_id, "ts": TS, "title": PRIVATE_TITLE, "text": f"memo {memo_id}", **fields}


def folder_with(tmp_path: Path, files: dict[str, Any]) -> Path:
    folder = tmp_path / "import"
    folder.mkdir(exist_ok=True)
    for name, content in files.items():
        data = content if isinstance(content, bytes) else json.dumps(content).encode("utf-8")
        (folder / name).write_bytes(data)
    return folder


def ingest(vault: Vault, folder: Path, *extra: str) -> int:
    return main(["ingest", "memo", str(folder), "--vault", str(vault.root), *extra])


def staged(vault: Vault) -> dict[str, str]:
    return {e.staged.record.rid: e.staged.record.via for e in Gate(vault).list(source="memo").entries}


def commits(vault: Vault) -> list[str]:
    return sh(vault.root, "git", "log", "--format=%s").splitlines()


def manifest(captures: int, **fields: Any) -> Manifest:
    return Manifest(run="run-1", source="memo", captures=captures, **fields)


def capture(seq: int, result: Any, run: str = "run-1") -> Capture:
    captured = datetime(2026, 10, 14, 10, seq, tzinfo=UTC)
    return Capture(seq=seq, run=run, captured=captured, tool="list_memos", result=result)


def test_an_import_reads_files_and_saved_captures_and_names_no_title(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = {"tool": "list_memos", "arguments": {"page": 1}, "result": [memo("m2")], "captured": "2026-10-14T10:00:00Z"}
    folder = folder_with(
        tmp_path,
        {
            "a.json": memo("m1"),
            "b.json": saved,
            "c.json": {"id": "m3"},
            "d.json": memo("m4", wait=True),
            "e.json": b"{not json",
            "f.json": {"tool": "list_memos", "result": [], "captured": "not a time"},
            "notes.txt": b"a note the owner left here",
        },
    )
    assert ingest(vault, folder) == 0
    out = capsys.readouterr().out
    assert "staged     memo: 2 pending, 0 ready, 0 already staged or in raw" in out
    assert "set aside  m3  no-ts  ts" in out
    assert "set aside  (no id)  bad-item" in out
    assert "waiting    m4  for summary" in out
    assert "skipped    file(s) 5, 7 of 7 in name order: not JSON" in out
    assert PRIVATE_TITLE not in out
    assert "notes.txt" not in out
    assert staged(vault) == {"memo:m1": "memo-file", "memo:m2": "memo-tool"}
    fetched = {e.staged.record.rid: e.staged.record.fetched for e in Gate(vault).list().entries}
    assert fetched["memo:m2"] == datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    assert ingest(vault, folder) == 0
    assert "0 pending, 0 ready, 2 already staged or in raw" in capsys.readouterr().out


def test_an_import_adds_its_holds_first_and_names_a_hold_no_file_had(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = folder_with(
        tmp_path,
        {
            "a.json": memo("m1"),
            "b.json": memo("m2"),
            "c.json": memo("m5"),
            "hold.txt": "﻿m1 \r\n\r\nm9\r\n".encode(),
        },
    )
    other = tmp_path / "more-holds.txt"
    other.write_text("m2\n", encoding="utf-8")
    assert ingest(vault, folder, "--hold", str(other)) == 0
    out = capsys.readouterr().out
    assert "holds      memo: 3 added" in out
    assert "holds      m9 isn't in this import; check hold.txt for a typo" in out
    assert "on hold    2" in out
    assert "m1" not in out
    assert "m2" not in out
    assert staged(vault) == {"memo:m5": "memo-file"}
    assert holds.read(vault.root, "memo") == {"m1", "m2", "m9"}
    assert commits(vault)[0] == "Holds: memo, 3 added on test-box"


def test_a_bad_hold_line_stops_the_import_before_anything_is_written(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = folder_with(tmp_path, {"a.json": memo("m1"), "hold.txt": f"m1\n{PRIVATE_TITLE}\n".encode()})
    before = commits(vault)
    assert ingest(vault, folder) == 2
    err = capsys.readouterr().err
    assert "hold.txt: line 2 isn't an id" in err
    assert PRIVATE_TITLE not in err
    assert staged(vault) == {}
    assert not (vault.root / HOLDS).exists()
    assert commits(vault) == before


def test_a_record_on_hold_in_staging_stops_every_ingest_until_its_file_is_deleted(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = folder_with(tmp_path, {"a.json": memo("m1"), "b.json": memo("m2")})
    assert ingest(vault, folder) == 0
    # The hold arrives after the record was staged, as on a clone that staged it before a sync brought the hold.
    holds.add(vault, "memo", ["m1"])
    [path] = [e.path for e in Gate(vault).list().entries if e.staged.record.rid == "memo:m1"]
    rel = path.relative_to(vault.root).as_posix()
    for _ in range(2):
        capsys.readouterr()
        assert ingest(vault, folder) == 2
        assert f"1 record(s) on hold wait in staging; delete these files, then run the ingest again: {rel}" in (
            capsys.readouterr().err
        )
    path.unlink()
    assert ingest(vault, folder) == 0
    out = capsys.readouterr().out
    assert "on hold    1" in out
    assert staged(vault) == {"memo:m2": "memo-file"}


def test_the_core_drops_what_a_mapper_returns_on_hold_and_names_what_raw_already_has(
    vault: Vault, tmp_path: Path
) -> None:
    first = ingest_run(vault, "memo", manifest(1), [capture(0, [memo("m6")])])
    assert first.staged.pending == 1
    gate = Gate(vault)
    gate.classify("memo:m6", "work")
    gate.promote()
    holds.add(vault, "memo", ["m1", "m2", "m3", "m6"])
    leaked = [memo("m1", leak=True), {"id": "m2", "leak": True, "wait": True}, {"id": "m3", "leak": True}, memo("m7")]
    report = ingest_run(vault, "memo", manifest(1), [capture(0, leaked)])
    assert report.on_hold == 3
    assert report.waiting == []
    assert report.set_aside == []
    assert report.staged.pending == 1
    assert staged(vault) == {"memo:m7": "memo-tool"}
    assert report.held_in_raw == ["memo:m6"]
    assert ingest_run(vault, "memo", manifest(0), []).held_in_raw == ["memo:m6"]


def test_the_import_says_what_the_gate_dropped_and_what_raw_holds_on_hold(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = folder_with(tmp_path, {"a.json": memo("m6"), "b.json": memo("m7", text="about the zebrafinch")})
    config = vault.root / "vault.yaml"
    text = config.read_text(encoding="utf-8").replace("  keywords: []", "  keywords:\n  - zebrafinch")
    config.write_text(text, encoding="utf-8")
    assert ingest(vault, folder) == 0
    out = capsys.readouterr().out
    assert "dropped    never-store 1" in out
    assert "zebrafinch" not in out
    gate = Gate(vault)
    gate.classify("memo:m6", "work")
    gate.promote()
    holds.add(vault, "memo", ["m6"])
    assert ingest(vault, folder) == 0
    assert "in raw     memo:m6 is on hold but already in raw; it waits for docket forget" in capsys.readouterr().out


def test_the_service_s_route_maps_a_run_s_captures_and_checks_their_count(vault: Vault) -> None:
    report = ingest_run(vault, "memo", manifest(2), [capture(0, memo("m1")), capture(1, [memo("m2"), {"id": "m3"}])])
    assert report.staged.pending == 2
    assert report.set_aside == [SetAside("m3", "no-ts", None, "ts")]
    assert report.waiting == []
    with pytest.raises(DocketError, match="the run is dropped and nothing is staged"):
        ingest_run(vault, "memo", manifest(3), [capture(0, memo("m4"))])
    with pytest.raises(DocketError, match="the run is dropped and nothing is staged"):
        ingest_run(vault, "memo", manifest(1), [capture(0, memo("m4"), run="run-2")])
    with pytest.raises(DocketError, match="the manifest is for memo, not chat"):
        ingest_run(vault, "chat", manifest(0), [])
    assert set(staged(vault)) == {"memo:m1", "memo:m2"}
    waiting = ingest_run(vault, "memo", manifest(1), [capture(0, {"id": "m8", "wait": True})]).waiting
    assert waiting == [Waiting("m8", "summary")]


def test_a_mapper_that_times_out_stages_nothing_and_keeps_the_holds_it_added(vault: Vault, tmp_path: Path) -> None:
    folder = folder_with(tmp_path, {"a.json": memo("m1"), "b.json": memo("m2", sleep=30), "hold.txt": b"m9\n"})
    with pytest.raises(MapperFailedError, match="ran past its time limit"):
        ingest_run(vault, "memo", manifest(0), [], folder=folder, time_limit=1)
    assert staged(vault) == {}
    assert holds.read(vault.root, "memo") == {"m9"}


def test_a_crash_s_own_words_are_printed_only_on_request(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = folder_with(tmp_path, {"a.json": memo("m1", crash=PRIVATE_TITLE)})
    assert ingest(vault, folder) == 2
    err = capsys.readouterr().err
    assert "docket: the mapper failed (exit 1); run it again with --mapper-errors to see the mapper's own errors" in err
    assert PRIVATE_TITLE not in err
    assert ingest(vault, folder, "--mapper-errors") == 2
    err = capsys.readouterr().err
    assert PRIVATE_TITLE in err
    assert "run it again" not in err


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("slack", "source 'slack' isn't in vault.yaml"),
        ("calls", "source 'calls' has no mapper in vault.yaml"),
        ("Memo", "isn't a source name"),
    ],
)
def test_a_source_without_a_mapper_is_refused_before_anything_is_read(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str, message: str
) -> None:
    folder = folder_with(tmp_path, {"hold.txt": b"m1\n"})
    assert main(["ingest", source, str(folder), "--vault", str(vault.root)]) == 2
    assert message in capsys.readouterr().err
    assert not (vault.root / HOLDS).exists()


def test_a_folder_that_isn_t_one_is_refused(vault: Vault, tmp_path: Path) -> None:
    with pytest.raises(DocketError, match="isn't a folder"):
        ingest_run(vault, "memo", manifest(0), [], folder=tmp_path / "missing")
