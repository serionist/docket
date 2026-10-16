"""A second scan of a big unchanged folder: a stat each, nothing read (DR-0486).

The folder holds 5,000 notes, 500 under coverage, where nothing is timed. It is a shared template the scan only reads,
so it is read where it was built; the vault scanned once against it is a template too, copied for each test, since a
scan takes its lock in the vault. Both are templates the untimed test asks for as well, so in a lane without coverage
the parallel run builds them and the timed run, alone, only times (DR-0492).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from docket_testkit import copied, make_config, shared, traced
from documents_scan_kit import SOURCE, cycle, name, put, raw, run

from docket.documents import records
from docket.vault.config import SourceConfig
from docket.vault.vault import Vault

FILES = 5000
TRACED_FILES = 500
# A second scan of 5,000 unchanged files takes about 0.4 s on a desktop.
SECONDS = 3.0


def files() -> int:
    return TRACED_FILES if traced() else FILES


def notes(folder: Path, count: int) -> Path:
    work = folder / "work"
    for n in range(count):
        put(work, f"folder-{n % 50:02}/note-{n:04}.md", f"# Note {n}\n\nWhat was said in meeting {n}.\n")
    return work


def scanned(folder: Path, work: Path) -> Path:
    config = make_config()
    config.sources[SOURCE] = SourceConfig(folder="~/Work")
    vault = Vault.init(folder / "vault", config, "test-box")
    name(vault, work)
    cycle(vault)
    return vault.root


@pytest.fixture(scope="module")
def folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The notes, read where they were built: a scan writes nothing in its folder, and the template check says if one
    did."""
    count = files()
    return shared(lambda f: notes(f, count), f"documents-notes-{count}", tmp_path_factory)


@pytest.fixture
def vault(folder: Path, tmp_path: Path) -> Vault:
    """A copy of the vault the folder was scanned into once, its folder named for this machine."""
    root = copied(tmp_path, lambda f: scanned(f, folder), f"documents-scanned-{files()}")
    found = Vault.open(root)
    name(found, folder)
    return found


def test_the_big_folder_s_vault_holds_every_file_and_its_cache_knows_each(vault: Vault) -> None:
    count = files()
    assert sum(1 for r in raw(vault) if r.kind == records.FILE_KIND) == count
    cache = json.loads((vault.home.private / "documents" / f"{SOURCE}.json").read_text(encoding="utf-8"))
    assert len(cache["files"]) == count


@pytest.mark.perf
def test_a_second_scan_of_5000_unchanged_files_takes_under_3_seconds(vault: Vault) -> None:
    count = files()
    started = time.perf_counter()
    report = run(vault)
    took = time.perf_counter() - started
    print(f"\na second scan of {count} unchanged files took {took:.2f} s")
    assert report.outcomes == {"same": count}
    if not traced():
        assert took < SECONDS, f"{took:.2f} s"
