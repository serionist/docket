"""Builders the forget's tests share: lines of the forgotten list made from invented rids, hashed as the list holds
them, and written through the list's own append (DR-0579). Nothing here forgets through a command: that comes with
`docket forget`."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docket.vault import forgotten
from docket.vault.forgotten import Forget, Version
from docket.vault.records import RawRecord, version
from docket.vault.staging import key_of, rid_hash
from docket.vault.vault import Vault

AT = datetime(2026, 10, 18, 9, 14, 2, tzinfo=UTC)
KEY = "ab" * 32


def stored(record: RawRecord) -> RawRecord:
    """A record as raw holds it: with its version."""
    return record.model_copy(update={"v": record.v or version(record)})


def line(
    *,
    rids: Sequence[str] = (),
    versions: Sequence[RawRecord] = (),
    files: Sequence[str] = (),
    media: Sequence[str] = (),
    keys: Sequence[str] = (),
    old_paths: Sequence[str] = (),
    import_hashes: Sequence[str] = (),
    machine: str = "test-box",
) -> Forget:
    """One forget's line, from rids, records and paths as they are, hashed as the list holds them."""
    return Forget(
        rids=[rid_hash(rid) for rid in rids],
        versions=[Version(rid=rid_hash(r.rid), key=key_of(r.rid, stored(r).v)) for r in versions],
        files=list(files),
        media=[rid_hash(rid) for rid in media],
        keys=list(keys),
        old_paths=[forgotten.old_path_hash(path) for path in old_paths],
        import_hashes=list(import_hashes),
        at=AT,
        machine=machine,
    )


def forget(vault: Vault, **fields: Any) -> str | None:
    """A line appended and committed alone, as a forget's first two writes are."""
    return forgotten.append(vault, line(**fields))


def write_raw_file(root: Path, rel: str, records: Sequence[RawRecord]) -> Path:
    """A run file written as it is, each record with its version and its run, for a test to change in place."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    run = path.name.removesuffix(".jsonl")
    text = "".join(stored(r).model_copy(update={"run": run}).to_json() + "\n" for r in records)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
