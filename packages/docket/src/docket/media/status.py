"""`docket media status`: this clone's key store, its keys, and the media waiting and stored, by counts only."""

from __future__ import annotations

from docket.media import crypto, layout
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
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
    out.append(f"waiting  {media_staged.count_waiting(vault.root)} file(s) in staging")
    folder = vault.root / layout.STORED
    stored = sorted(folder.glob(f"*/*/*/*{layout.SUFFIX}")) if folder.is_dir() else []
    pointers = [p for p in stored if crypto.is_pointer(p)]
    keyless = [p for p in stored if p not in pointers and not store.has(crypto.key_id(p))]
    out.append(f"stored   {len(stored)} file(s); {len(keyless)} without a key here, {len(pointers)} not downloaded")
    return out
