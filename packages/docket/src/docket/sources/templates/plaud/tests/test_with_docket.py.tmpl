"""The Plaud mapper through Docket: the core's checks, the versions it computes, and `docket ingest` end to end, with
this folder copied into a temp project the way a user copies it."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from plaud_payloads import (
    BACKFILL_SUMMARY,
    CALL,
    CALL_PAGE_1,
    CALL_PAGE_2,
    HELD,
    PRIVATE_TITLE,
    SHORT,
    UNREADY,
    Run,
    live_call,
    meta,
    structured,
)

from docket.cli import main
from docket.sources.contract import check_output
from docket.vault.config import Owner, SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.records import RawRecord, version
from docket.vault.vault import Vault

EXAMPLE = Path(__file__).resolve().parents[1]

# One version per kind, pinned: a change to the mapper's output or the core's projection shows up here first. Change
# them only with a migration in mind, since every record the lookback reads again would get a new version.
GOLDEN = {
    f"plaud:{CALL}": "sha256:8ba206bf9dddaf0d361aba1fbd69d1511a1dc74bcb6563ff3284636db5f41c14",
    f"plaud:{CALL}/transaction_polish": "sha256:91f5005d45ac5fac0f0ebcddb4ca977e15fd6f33082f5be41768e3e2f9f76183",
    f"plaud:{CALL}/summary": "sha256:96d23dccf11179e623ad46d6acfdbe370238db1366abf88e1634da414cc16901",
}


def checked(mapper: ModuleType, run: Run, holds: list[str] | None = None) -> dict[str, RawRecord]:
    return {r.rid: r for r in check_output("plaud", mapper.map_run(run.payload(holds))).records}


def test_the_core_accepts_every_record_and_pins_one_version_per_kind(mapper: ModuleType) -> None:
    records = checked(mapper, live_call(Run()))
    assert {rid: version(r) for rid, r in records.items()} == GOLDEN
    transcript = records[f"plaud:{CALL}/transaction_polish"]
    assert transcript.text.splitlines()[0] == "Ben Sample: Morning, Cleo."


def test_page_order_a_serial_number_or_a_link_never_versions_a_recording(mapper: ModuleType) -> None:
    first = checked(mapper, live_call(Run()))
    detail = meta(CALL, "Supplier call", serial_number="SN-0001", presigned_url="https://example.invalid/a?t=1")
    again = (
        Run()
        .transcript(CALL, CALL_PAGE_2, cursor="c2")
        .note(CALL)
        .transcript(CALL, [*reversed(CALL_PAGE_1), CALL_PAGE_1[0]], next_cursor="c2")
        .add("get_file", structured(detail), file_id=CALL)
    )
    second = checked(mapper, again)
    assert {rid: version(r) for rid, r in second.items()} == {rid: version(r) for rid, r in first.items()}


def test_a_backfill_file_and_a_capture_of_one_recording_version_alike(mapper: ModuleType) -> None:
    captured = checked(mapper, live_call(Run()))
    backfilled = checked(mapper, Run().listing(meta(CALL, "Supplier call")).backfill(CALL))
    assert {rid: version(r) for rid, r in backfilled.items()} == {rid: version(r) for rid, r in captured.items()}


def test_a_new_name_a_renamed_speaker_or_an_edited_tab_is_a_new_version(mapper: ModuleType) -> None:
    base = checked(mapper, live_call(Run()))
    renamed = [{**s, "speaker": "Ben S."} if s["speaker"] == "Ben Sample" else s for s in CALL_PAGE_1]
    changed = checked(
        mapper,
        Run()
        .transcript(CALL, renamed, next_cursor="c2")
        .transcript(CALL, CALL_PAGE_2, cursor="c2")
        .note(CALL, [{"data_type": "auto_sum_note", "data_title": "Summary", "data_content": "Price NOT agreed."}])
        .listing(meta(CALL, "Supplier call, take two")),
    )
    for rid in base:
        assert version(changed[rid]) != version(base[rid])


# -- docket ingest, end to end ---------------------------------------------------------------------------------------


def project_with_the_example(tmp_path: Path) -> Vault:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
    shutil.copytree(EXAMPLE, project / ".claude" / "skills" / "sources" / "plaud")
    config = VaultConfig(
        owner=Owner(name="Ada Example", emails=["ada@example.invalid"], time_zone="Europe/Budapest"),
        primary_machine="test-box",
        sources={
            "plaud": SourceConfig(
                sensitivity="mixed",
                mapper=".claude/skills/sources/plaud/mapper.py",
                tools=["list_files", "get_file", "get_transcript", "get_note"],
            )
        },
    )
    return Vault.init(project / "vault", config, "test-box")


def write_folder(folder: Path, files: dict[str, Any]) -> None:
    folder.mkdir()
    for name, content in files.items():
        data = content.encode("utf-8") if isinstance(content, str) else json.dumps(content).encode("utf-8")
        (folder / name).write_bytes(data)


def test_docket_ingest_imports_a_backfill_through_the_example(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = project_with_the_example(tmp_path)
    listing = {
        "tool": "list_files",
        "arguments": {"page": 1},
        "result": structured(
            {
                "files": [
                    meta(CALL, "Supplier call"),
                    meta(SHORT, "Quick sync"),
                    meta(HELD, PRIVATE_TITLE),
                ]
            }
        ),
        "captured": "2026-10-14T10:00:00Z",
    }

    def backfill(file_id: str, block: str | None = "transaction_polish") -> dict[str, Any]:
        segments = [*CALL_PAGE_1, *CALL_PAGE_2] if block else []
        return {"file_id": file_id, "block": block, "segments": segments, "summary": BACKFILL_SUMMARY}

    folder = tmp_path / "backfill"
    write_folder(
        folder,
        {
            "0-listing.json": listing,
            f"{CALL}.json": backfill(CALL),
            f"{SHORT}.json": backfill(SHORT, block=None),
            f"{UNREADY}.json": backfill(UNREADY),
            f"{HELD}.json": backfill(HELD),
            "hold.txt": f"{HELD}\n",
        },
    )
    args = ["ingest", "plaud", str(folder), "--vault", str(vault.root)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "holds      plaud: 1 added" in out
    assert "staged     plaud: 5 pending, 0 ready, 0 already staged or in raw" in out
    assert "on hold    1" in out
    assert f"set aside  {UNREADY}  no-meta  start_at" in out
    assert PRIVATE_TITLE not in out
    assert HELD not in out
    entries = Gate(vault).list(source="plaud").entries
    assert sorted(e.staged.record.rid for e in entries) == sorted(
        [
            f"plaud:{CALL}",
            f"plaud:{CALL}/transaction_polish",
            f"plaud:{CALL}/summary",
            f"plaud:{SHORT}",
            f"plaud:{SHORT}/summary",
        ]
    )
    assert {e.staged.state for e in entries} == {"pending"}
    assert main(args) == 0
    assert "staged     plaud: 0 pending, 0 ready, 5 already staged or in raw" in capsys.readouterr().out
