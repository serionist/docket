"""Media in staging, beside its record (DR-0547): the withheld marks, deleting media with a drop of its record, the
orphans the sweep clears, and promotion into `media/`.

A media file is deleted file first, sidecar next, key last: a crash leaves a sidecar without its file, which the next
sweep clears with its key, or a key for no file, which decrypts nothing. Never a file without its sidecar that the
promotion could take.

`staging/_withheld.jsonl` holds the hash of each rid a version dropped for a never-store value or as private sat on or
under, once each, so a later put for any of them fails its check (DR-0547). Hashes only, on this clone's disk, never in
git; nothing removes one.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from docket.errors import DocketError
from docket.media import crypto, layout
from docket.media.keystore import KeyStore
from docket.vault.home import Home, make_private
from docket.vault.staging import STAGING, retry, rid_hash

WITHHELD = "_withheld.jsonl"
TEMP = "media-"
# A temp file of a put untouched this long was left by a put that died: the download stops after 60 s without a byte.
STALE_SECONDS = 3600


@dataclass(frozen=True)
class Waiting:
    """A staged media file and its sidecar."""

    path: Path
    sidecar_path: Path
    sidecar: layout.Sidecar


@dataclass(frozen=True)
class Swept:
    """What a pass over `staging/_media/` found: the media that waits, how many orphans it deleted, and the sidecars
    whose file is in `media/` already, kept for the promotion that stores or cleans them."""

    waiting: list[Waiting]
    orphans: int
    leftover: list[tuple[Path, str]]


# -- the withheld marks ----------------------------------------------------------------------------------------------


def withheld_path(root: Path) -> Path:
    return root / STAGING / WITHHELD


def read_withheld(root: Path) -> frozenset[str]:
    """The withheld rid hashes; a torn last line (a crash mid-append) is skipped."""
    try:
        lines = withheld_path(root).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return frozenset()
    found: set[str] = set()
    for line in lines:
        try:
            value = json.loads(line)["rid"]
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(value, str):
            found.add(value)
    return frozenset(found)


def withhold(root: Path, rid: str) -> int:
    """Mark a dropped version's rid and every rid it nests under, each hash once; fsynced before the version goes.
    Returns how many lines were added."""
    have = read_withheld(root)
    new = [h for h in dict.fromkeys(rid_hash(r) for r in layout.owners(rid)) if h not in have]
    if not new:
        return 0
    path = withheld_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps({"rid": h}) + "\n" for h in new)
    with path.open("a+b") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                # A crash mid-append left a torn line; end it so the new lines stay whole.
                text = "\n" + text
        f.seek(0, os.SEEK_END)
        f.write(text.encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    return len(new)


# -- deleting --------------------------------------------------------------------------------------------------------


def read_sidecar(path: Path) -> layout.Sidecar | None:
    try:
        return layout.Sidecar.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError):
        return None


def rid_of(path: Path) -> str | None:
    """The rid a sidecar names, or None when there is none that reads."""
    sidecar = read_sidecar(path)
    return sidecar.rid if sidecar is not None else None


def key_of(path: Path) -> str | None:
    """A media file's key id, or None when its header doesn't read (an LFS pointer, a torn file)."""
    try:
        return crypto.key_id(path)
    except (OSError, DocketError):
        return None


def _stored_keys(root: Path, rid: str) -> frozenset[str] | None:
    """The keys of the rid's stored media, which no staging clean-up may delete; None when one of them doesn't read,
    so nothing that could be its key is deleted."""
    found: set[str] = set()
    for path in layout.stored_files(root, rid):
        key = key_of(path)
        if key is None:
            return None
        found.add(key)
    return frozenset(found)


def _forget_key(keys: KeyStore, key: str | None, keep: frozenset[str] | None) -> None:
    if key is not None and keep is not None and key not in keep:
        keys.delete(key)


def _forget_file(path: Path, keys: KeyStore, keep: frozenset[str] | None = frozenset()) -> None:
    """A media file, then its key read from its header."""
    key = key_of(path)
    retry(lambda: path.unlink(missing_ok=True))
    _forget_key(keys, key, keep)


def _forget_sidecar(path: Path, keys: KeyStore, keep: frozenset[str] | None) -> None:
    sidecar = read_sidecar(path)
    retry(lambda: path.unlink(missing_ok=True))
    _forget_key(keys, sidecar.key if sidecar is not None else None, keep)


def delete(root: Path, keys: KeyStore, rid: str) -> int:
    """Delete a rid's staged media: its files, then its sidecar, each with its key, never a key of its stored media.
    Returns how many files went."""
    keep = _stored_keys(root, rid)
    files = layout.staged_files(root, rid)
    for path in files:
        _forget_file(path, keys, keep)
    sidecar = layout.sidecar_path(root, rid)
    if sidecar.exists():
        _forget_sidecar(sidecar, keys, keep)
    return len(files)


def drop(root: Path, keys: KeyStore, rid: str) -> int:
    """Before a version of `rid` is dropped: delete the staged media on its rid and on each rid it nests under."""
    return sum(delete(root, keys, owner) for owner in layout.owners(rid))


# -- the pass over staging/_media/ -----------------------------------------------------------------------------------


def sweep(root: Path, home: Home, keys: KeyStore) -> Swept:
    """Every waiting media file with its sidecar. A file with no sidecar that matches it is deleted with its key; a
    sidecar with no file is deleted with its key, unless its rid has stored media, whose promotion it may still serve;
    one that doesn't read is deleted alone. Temp files a killed put left go too."""
    clear_temps(home)
    base = root / STAGING / layout.STAGED
    waiting: list[Waiting] = []
    leftover: list[tuple[Path, str]] = []
    orphans = 0
    if not base.is_dir():
        return Swept(waiting, orphans, leftover)
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        sidecars = {path.stem: read_sidecar(path) for path in sorted(folder.glob("*.json"))}
        matched: set[str] = set()
        for path in sorted(folder.glob(f"*{layout.SUFFIX}")):
            stem = path.name.split(".", 1)[0]
            sidecar = sidecars.get(stem)
            if (
                sidecar is None
                or stem != rid_hash(sidecar.rid)
                or layout.ext_of(path) != sidecar.ext
                or stem in matched
            ):
                _forget_file(path, keys)
                orphans += 1
                continue
            matched.add(stem)
            waiting.append(Waiting(path, folder / f"{stem}.json", sidecar))
        for stem in sorted(set(sidecars) - matched):
            sidecar = sidecars[stem]
            path = folder / f"{stem}.json"
            if sidecar is not None and layout.stored_files(root, sidecar.rid):
                leftover.append((path, sidecar.rid))
                continue
            _forget_sidecar(path, keys, frozenset() if sidecar is not None else None)
            orphans += 1
    return Swept(waiting, orphans, leftover)


def count_waiting(root: Path) -> int:
    """How many media files wait in staging."""
    base = root / STAGING / layout.STAGED
    return len(list(base.glob(f"*/*{layout.SUFFIX}"))) if base.is_dir() else 0


def clear_temps(home: Home, *, now: float | None = None) -> int:
    """Remove the encrypted temp files a killed put left: those untouched for an hour. A put's download runs outside
    the vault lock and writes its temp file as the bytes come, so one being written is never that old."""
    moment = time.time() if now is None else now
    stale: list[Path] = []
    found: list[Path] = list(home.tmp_dir.glob(f"{TEMP}*.tmp")) if home.tmp_dir.is_dir() else []
    for tmp in found:
        with contextlib.suppress(OSError):
            if moment - tmp.stat().st_mtime > STALE_SECONDS:
                tmp.unlink()
                stale.append(tmp)
    return len(stale)


def temp_file(home: Home) -> Path:
    return make_private(home.git_dir, home.tmp_dir) / f"{TEMP}{secrets.token_hex(6)}.{os.getpid()}.tmp"


def write_sidecar(home: Home, path: Path, sidecar: layout.Sidecar) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = temp_file(home)
    try:
        tmp.write_text(sidecar.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
        retry(lambda: tmp.replace(path))
    finally:
        tmp.unlink(missing_ok=True)


def move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    retry(lambda: source.replace(target))


def rel(root: Path, paths: Iterable[Path]) -> list[str]:
    return [p.relative_to(root).as_posix() for p in paths]
