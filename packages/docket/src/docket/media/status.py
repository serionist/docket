"""`docket media status`: this clone's key store, its keys, and the media waiting and stored, by counts only: stored
files without a key, not downloaded, waiting for a new cut, and those a cut by characters keeps unplayed."""

from __future__ import annotations

from pathlib import Path

from docket.media import crypto, layout, reader
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
from docket.vault.spans import RawLookup
from docket.vault.staging import rid_hash
from docket.vault.vault import Vault


def lines(vault: Vault, keys: KeyStore | None = None) -> list[str]:
    store = keys or KeyStore(vault.home)
    info = store.info()
    if info is None:
        out = ["store    none yet: the first put makes one in the system keychain"]
    else:
        where = "the system keychain" if info.store == "keyring" else "a file only you can read"
        out = [f"store    {where}, store {info.id}"]
    out.append(f"keys     {store.count()}")
    unbacked = store.unbacked()
    if unbacked:
        out.append(
            f"backup   {unbacked} key(s) no export holds; `docket media keys export <file>` in your own terminal"
        )
    elif store.count():
        out.append("backup   every key is in an export")
    out.append(f"waiting  {media_staged.count_waiting(vault.root)} file(s) in staging")
    folder = vault.root / layout.STORED
    stored = sorted(folder.glob(f"*/*/*/*{layout.SUFFIX}")) if folder.is_dir() else []
    pointers = [p for p in stored if crypto.is_pointer(p)]
    keyless = [p for p in stored if p not in pointers and not store.has(crypto.key_id(p))]
    line = f"stored   {len(stored)} file(s); {len(keyless)} without a key here, {len(pointers)} not downloaded"
    recut, characters = _unplayed(vault, stored)
    if recut:
        line += f", {recut} waiting for a new cut"
    if characters:
        line += f", {characters} unplayed: a record of each was cut by characters, so its audio can't be cut to match"
    out.append(line)
    return out


def _unplayed(vault: Vault, stored: list[Path]) -> tuple[int, int]:
    """How many stored files wait for a new cut, and how many a cut by characters keeps unplayed (DR-0562)."""
    raw = RawLookup(vault.root)
    names: dict[str, dict[str, str]] = {}
    recut = characters = 0
    for path in stored:
        source = path.parent.parent.parent.name
        if source not in names:
            names[source] = {rid_hash(rid): rid for rid in raw.rids(source)}
        rid = names[source].get(path.name.split(".", 1)[0])
        if rid is None:
            continue
        cut = reader.cuts_of(vault, rid, path, raw)
        recut += cut == "recut"
        characters += cut == "cut-by-characters"
    return recut, characters
