"""Helpers for the folder source's tests: a vault with a folder source whose folder is a temp folder, named for this
machine in the user's config (DR-0490), and the records a scan leaves in staging or raw."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from docket_testkit import empty_vault

from docket import userconfig
from docket.documents import records
from docket.documents.scan import ScanReport, scan
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.rawlog import RawLog
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

SOURCE = "documents"
# A fixed modification time, so a test can restore a file exactly.
MTIME = 1_791_000_000


def folder_vault(tmp_path: Path, config: VaultConfig, work: Path, **source: Any) -> Vault:
    """A vault whose source `documents` reads `work`, named for this machine in the user's config."""
    config.sources[SOURCE] = SourceConfig(folder="~/Work", **source)
    vault = empty_vault(tmp_path, config)
    work.mkdir(parents=True, exist_ok=True)
    name(vault, work)
    return vault


def name(vault: Vault, work: Path) -> None:
    userconfig.set_section("folders", {str(vault.root): {SOURCE: str(work)}})


def put(work: Path, rel: str, data: bytes | str, mtime: int = MTIME) -> Path:
    path = work.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8", newline="\n")
    else:
        path.write_bytes(data)
    os.utime(path, ns=(mtime * 10**9, mtime * 10**9))
    return path


def run(vault: Vault, **kwargs: Any) -> ScanReport:
    return scan(vault, SOURCE, **kwargs)


def staged(vault: Vault) -> list[RawRecord]:
    """The source's records in staging, in the order they were staged."""
    entries = Gate(vault).list(source=SOURCE).entries
    return [e.staged.record for e in sorted(entries, key=lambda e: e.staged.staged_at)]


def promote(vault: Vault) -> None:
    Gate(vault).promote(SOURCE)


def raw(vault: Vault) -> list[RawRecord]:
    """Every version in raw, in run order."""
    log = RawLog(vault.root, vault.machine, vault.lock)
    return [r for _, _, r in log.iter_records() if r.source == SOURCE]


def versions(vault: Vault, rid: str) -> list[RawRecord]:
    return [r for r in raw(vault) if r.rid == rid]


def rid(rel: str) -> str:
    return records.file_rid(SOURCE, rel)


def cycle(vault: Vault, **kwargs: Any) -> ScanReport:
    """A scan, then a promotion of what it staged."""
    report = run(vault, **kwargs)
    promote(vault)
    return report


def junction(target: Path, link: Path) -> None:
    """A junction at `link` leading to `target`: Windows' own kind of link, which `is_symlink` doesn't see."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        raise OSError("junctions are Windows' own")
