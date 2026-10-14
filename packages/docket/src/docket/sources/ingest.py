"""One way in for a source's records: `ingest_run`, which `docket ingest` and the service's ingest tool both call.

In order: an import's hold files are added to the hold list; the hold list is read, and a held record waiting in
staging stops the run; the captures are counted against the manifest; an import folder's files join them; the mapper
runs; its output is checked, and anything under a held id is dropped and only counted; the rest goes through the gate.
What it reports is counts, ids and short reasons, never a title.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from docket.errors import DocketError
from docket.sources import runner
from docket.sources.contract import Capture, Manifest, SetAside, Waiting, check_output, mapper_input
from docket.vault import holds
from docket.vault.gate import Gate, StageResult
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

HOLD_FILE = "hold.txt"
CAPTURE_KEYS = frozenset({"tool", "arguments", "result", "captured", "run"})
_NOT_JSON = object()


class HeldInStagingError(DocketError):
    """Records on hold wait in staging: the run stops before anything is staged, until those files are deleted."""


@dataclass
class IngestReport:
    source: str
    holds_added: int = 0
    # Ids this import's hold files named that no capture had: a typo in hold.txt shows here.
    holds_unseen: list[str] = field(default_factory=list[str])
    on_hold: int = 0
    staged: StageResult = field(default_factory=StageResult)
    set_aside: list[SetAside] = field(default_factory=list[SetAside])
    waiting: list[Waiting] = field(default_factory=list[Waiting])
    held_in_raw: list[str] = field(default_factory=list[str])
    # Files in the import folder that aren't JSON, by their place in name order: a file's name can be a title.
    unreadable: list[int] = field(default_factory=list[int])
    files: int = 0


def ingest_run(
    vault: Vault,
    source: str,
    manifest: Manifest,
    captures: Sequence[Capture],
    *,
    folder: Path | None = None,
    hold_files: Sequence[Path] = (),
    time_limit: float = runner.TIME_LIMIT,
) -> IngestReport:
    """Map a run's captures, and an import folder's files, with the source's mapper, and stage what comes back."""
    if manifest.source != source:
        raise DocketError(f"the manifest is for {manifest.source}, not {source}")
    mapper = runner.find_mapper(vault, source)
    report = IngestReport(source)
    if folder is not None and not folder.is_dir():
        raise DocketError(f"{folder} isn't a folder")

    # Every hold file is read before any is added: a bad line stops the run with nothing written.
    named = [folder / HOLD_FILE] if folder is not None and (folder / HOLD_FILE).is_file() else []
    to_hold = [i for path in [*named, *hold_files] for i in holds.read_file(path, path.name)]
    if to_hold:
        report.holds_added = holds.add(vault, source, to_hold)

    held = holds.read(vault.root, source)
    lists = holds.Holds({source: held})
    gate = Gate(vault)
    waiting_held = [e.path for e in gate.list(source=source).entries if lists.covers(e.staged.record)]
    if waiting_held:
        files = ", ".join(p.relative_to(vault.root).as_posix() for p in waiting_held)
        raise HeldInStagingError(
            f"{len(waiting_held)} record(s) on hold wait in staging; delete these files, then run the ingest again: "
            f"{files}"
        )

    if len(captures) != manifest.captures or any(c.run != manifest.run for c in captures):
        raise DocketError(
            f"the manifest counts {manifest.captures} capture(s) of run {manifest.run} and {len(captures)} came, or "
            "one came from another run; the run is dropped and nothing is staged"
        )
    everything = list(captures)
    if folder is not None:
        more, report.unreadable, report.files = read_folder(folder, manifest.run, len(everything))
        everything += more

    answer = runner.run(mapper, mapper_input(source, manifest, everything, held), time_limit=time_limit)
    mapped = check_output(source, answer)
    seen = {i for i in mapped.held if i in held}
    kept: list[RawRecord] = []
    for record in mapped.records:
        ids = {holds.holder(ref, source, held) for ref in (record.rid, record.thread, record.container)} - {None}
        if ids:
            seen |= cast(set[str], ids)
        else:
            kept.append(record)
    report.set_aside = [e for e in mapped.set_aside if not _held(e.id, source, held, seen)]
    report.waiting = [w for w in mapped.waiting if not _held(w.id, source, held, seen)]
    report.on_hold = len(seen)
    report.holds_unseen = sorted(set(to_hold) - seen)
    report.staged = gate.stage(kept)
    report.held_in_raw = sorted(rid for rid in vault.rawlog.rids() if holds.covered(rid, source, held))
    return report


def _held(native: str | None, source: str, held: frozenset[str], seen: set[str]) -> bool:
    found = holds.holder(f"{source}:{native}", source, held) if native is not None else None
    if found is not None:
        seen.add(found)
    return found is not None


def read_folder(folder: Path, run: str, first_seq: int) -> tuple[list[Capture], list[int], int]:
    """An import folder's files in name order: a JSON file shaped like a capture as that capture, any other JSON file
    as a file for the mapper. Files that aren't JSON are only counted, by their place in that order."""
    listing = sorted((p for p in folder.iterdir() if p.is_file() and p.name != HOLD_FILE), key=lambda p: p.name)
    captures: list[Capture] = []
    unreadable: list[int] = []
    for place, path in enumerate(listing, start=1):
        data = _json(path)
        if data is _NOT_JSON:
            unreadable.append(place)
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        captures.append(_capture(data, run, first_seq + len(captures), path.name, modified))
    return captures, unreadable, len(listing)


def _json(path: Path) -> Any:
    if path.suffix.lower() != ".json":
        return _NOT_JSON
    try:
        return json.loads(path.read_bytes().decode("utf-8-sig"))
    except ValueError:
        return _NOT_JSON


def _capture(data: Any, run: str, seq: int, name: str, modified: datetime) -> Capture:
    """A saved capture reads as the capture it was, so a mapper sees it as it would live; anything else is a file."""
    if isinstance(data, dict):
        shaped = cast(dict[str, Any], data)
        if {"tool", "result", "captured"} <= set(shaped) <= CAPTURE_KEYS and isinstance(shaped["tool"], str):
            try:
                return Capture(
                    seq=seq,
                    run=run,
                    captured=shaped["captured"],
                    tool=shaped["tool"],
                    arguments=shaped.get("arguments") or {},
                    result=shaped["result"],
                )
            except ValidationError:
                pass
    return Capture(seq=seq, run=run, captured=modified, result=data, file=name)
