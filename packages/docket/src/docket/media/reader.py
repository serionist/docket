"""The one reader (DR-0551): a rid's media, decrypted in memory for the service and the models' worker, or why it
can't be.

| State | When |
|---|---|
| `ok` | the stored file and its key are here: `read` gives the bytes |
| `not-fetched` | no media for the rid, staged or stored; a cut recording's is put, cut, at the next run |
| `staged` | it waits at the gate with its record |
| `held` | a hold covers the rid, stored or not |
| `recut` | the stored file was cut by less than its family needs now: not played; the next run puts it again |
| `not-downloaded` | an LFS pointer is in place of the stored file |
| `no-key` | the stored file is here, its key isn't, or this machine can't open the key's seal |

`state_of` answers without decrypting. Nothing plain is written to disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from docket.errors import DocketError
from docket.media import crypto, cutlog, layout
from docket.media import ranges as cut_ranges
from docket.media.keystore import KeyStore
from docket.vault import holds, releaselog
from docket.vault.records import owning_id
from docket.vault.spans import RawLookup
from docket.vault.staging import rid_hash
from docket.vault.vault import Vault

State = Literal["ok", "not-fetched", "staged", "held", "recut", "not-downloaded", "no-key"]


@dataclass(frozen=True)
class Media:
    state: State
    data: bytes | None = None
    ext: str | None = None

    @property
    def type(self) -> str | None:
        return layout.TYPES.get(self.ext) if self.ext is not None else None


def _stored(vault: Vault, rid: str) -> Path | None:
    found = layout.stored_files(vault.root, rid)
    if len(found) > 1:
        raise DocketError(f"{rid} has {len(found)} stored media files; one of them is a leftover to remove by hand")
    return found[0] if found else None


def _held(vault: Vault, rid: str) -> bool:
    source = layout.source_of(rid)
    return owning_id(rid, source, holds.read(vault.root, source)) is not None


def recut(vault: Vault, rid: str, raw: RawLookup | None = None) -> bool:
    """Whether a rid's stored file was cut by less than its family needs now: the release's cuts and the `[cut]`
    segments of its transcripts in raw, against its line in the cut log (DR-0562). A record cut by characters has no
    time to cut at, so its stored file isn't played either."""
    lookup = raw or RawLookup(vault.root)
    records = [r for other in lookup.nested(rid) for r in lookup.versions(other)]
    if not records:
        return False
    if any(cut_ranges.by_characters(r) for r in records):
        return True
    latest = (lookup.versions(rid) or records)[-1]
    release = releaselog.read_all(vault.root).covering(latest)
    needed = cut_ranges.needed(records, [(c.start, c.end) for c in release.cuts] if release is not None else [])
    logged = cutlog.logged(vault.root, layout.source_of(rid)).get(rid_hash(rid), ())
    return not cut_ranges.covers(logged, needed.ranges)


def state_of(vault: Vault, rid: str, keys: KeyStore | None = None, raw: RawLookup | None = None) -> State:
    """Why a rid's media can or can't be played here, from the files, the logs and the key store, without
    decrypting. A stored file under a hold, or cut by less than its family needs now, isn't played."""
    path = _stored(vault, rid)
    if path is not None:
        if _held(vault, rid):
            return "held"
        if recut(vault, rid, raw):
            return "recut"
        if crypto.is_pointer(path):
            return "not-downloaded"
        store = keys or KeyStore(vault.home)
        return "ok" if store.has(crypto.key_id(path)) else "no-key"
    if layout.staged_files(vault.root, rid):
        return "staged"
    return "held" if _held(vault, rid) else "not-fetched"


def read(vault: Vault, rid: str, keys: KeyStore | None = None, raw: RawLookup | None = None) -> Media:
    """The rid's stored media in memory, or the state that says why not."""
    store = keys or KeyStore(vault.home)
    state = state_of(vault, rid, store, raw)
    path = _stored(vault, rid)
    if state != "ok" or path is None:
        return Media(state)
    key = store.get(crypto.key_id(path))
    if key is None:
        return Media("no-key")
    return Media("ok", crypto.decrypt(path.read_bytes(), key.identity), layout.ext_of(path))
