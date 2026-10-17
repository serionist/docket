"""Media keys carried to another machine, and kept as a backup (DR-0550): `docket media keys export` and `import`.

Nothing about a key travels in git. An export writes every key of this clone into one file, sealed with age's
passphrase mode under a passphrase Docket draws and shows once; the owner moves the file where they like and types the
passphrase on the other machine. Both run only at the owner's own console: Claude Code's shell, a pipe or a redirect is
refused, since whoever has the file and the passphrase opens every media file the clone stored. The export asks the OS
prompt first, or the code where there is none, since every key leaves the clone; the import's passphrase is its factor
(DR-0575).

The file's format is a door, pinned by its `schema_version`: a later Docket reads every earlier version.

```
{"schema_version": 1, "keys": [{"id": "<64 hex>", "identity": "AGE-SECRET-KEY-1...", "path": "...", "at": "..."}]}
```
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from docket import console
from docket.errors import DocketError
from docket.media import crypto
from docket.media.keystore import KeyStore
from docket.vault.git import toplevel
from docket.vault.vault import Vault

BUNDLE_VERSION = 1
EXPORT = "an export of this clone's media keys"
IMPORT = "an import of media keys"
# Six groups of four letters with no look-alikes: about 110 bits, typed from a note without guessing at a letter.
GROUPS = 6
GROUP = 4


class BundleKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity: str = Field(pattern=r"^AGE-SECRET-KEY-1[0-9A-Z]+$")
    path: str
    at: datetime


class Bundle(BaseModel):
    """An export, version 1. A later version is a new model here, with this one still read."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = BUNDLE_VERSION
    keys: list[BundleKey]


@dataclass(frozen=True)
class Exported:
    keys: int
    kept: bool
    # The owner said no before anything was written (DR-0575).
    declined: bool = False


@dataclass(frozen=True)
class Imported:
    added: int
    had: int


def draw() -> str:
    """A passphrase: six groups of four letters, drawn fresh."""
    return "-".join("".join(secrets.choice(console.CODE_LETTERS) for _ in range(GROUP)) for _ in range(GROUPS))


def _at_console(act: str) -> None:
    problem = console.problem(act)
    if problem is not None:
        raise DocketError(problem)


def _taken(target: Path) -> DocketError:
    return DocketError(f"{target} is there already; name a new file, so no export is ever written over")


def _write_new(target: Path, data: bytes) -> None:
    """`data` at `target`, a name nothing holds: written to a temp file beside it and synced first, then put in place
    without overwriting, by a hard link, or on a drive with none (FAT on a USB stick) by taking the name first and
    replacing it. A failure anywhere leaves nothing under the target's name."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.{secrets.token_hex(4)}.tmp"
    held = False
    try:
        with tmp.open("xb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(tmp, target)
        except FileExistsError:
            raise _taken(target) from None
        except OSError:
            try:
                with target.open("xb"):
                    held = True
            except FileExistsError:
                raise _taken(target) from None
            tmp.replace(target)
            held = False
    except BaseException:
        if held:
            target.unlink(missing_ok=True)
        raise
    finally:
        tmp.unlink(missing_ok=True)


def passphrase_of(typed: str) -> str:
    """A passphrase as Docket draws it, from however it was typed: its letters only, in groups of four."""
    letters = "".join(c for c in typed.upper() if c.isascii() and c.isalpha())
    return "-".join(letters[i : i + GROUP] for i in range(0, len(letters), GROUP))


def export(vault: Vault, target: Path, keys: KeyStore | None = None) -> Exported:
    """Write every key of this clone to `target`, sealed with a passphrase shown once the file is synced. The owner
    types its last group back, so a passphrase nobody kept never leaves a file behind that nothing opens."""
    _at_console(EXPORT)
    if toplevel(target.resolve().parent) is not None:
        raise DocketError(
            f"{target} is inside a git work tree, where a commit could take it; export to a folder outside any repo"
        )
    store = keys or KeyStore(vault.home)
    if store.info() is None or not store.ids():
        raise DocketError("this clone holds no media keys yet, so there is nothing to export")
    if target.exists():
        raise _taken(target)
    found: list[BundleKey] = []
    for key_id in store.ids():
        key = store.get(key_id)
        if key is None:
            raise DocketError(
                "this machine can't open its media key store's seal (the keychain didn't answer, or the seal's file "
                "went), so nothing is exported"
            )
        found.append(BundleKey(id=key_id, identity=key.identity, path=key.path, at=key.at))
    screen = [f"{len(found)} media key(s) of this clone go into {target}, sealed with a passphrase shown once."]
    asked = console.confirm(
        screen, "export them", "export nothing", "nothing was exported", act="export this clone's media keys"
    )
    if asked != "yes":
        return Exported(len(found), kept=False, declined=True)
    passphrase = draw()
    sealed = crypto.seal_with_passphrase(Bundle(keys=found).model_dump_json(), passphrase)
    try:
        _write_new(target, sealed)
    except OSError as e:
        raise DocketError(f"can't write {target}: {e.strerror or type(e).__name__}; nothing was exported") from None
    console.say(f"Wrote {len(found)} media key(s) to {target}.")
    console.say(f"Its passphrase, shown this once: {passphrase}")
    console.say("Keep the two apart: the file in one place, the passphrase in another.")
    last = passphrase.rsplit("-", 1)[1]
    if console.ask("Type the passphrase's last four letters to confirm you kept it: ").strip().upper() != last:
        target.unlink(missing_ok=True)
        console.say("That isn't its last four letters; the file is deleted, since nothing could open it.")
        return Exported(len(found), kept=False)
    store.mark_exported([k.id for k in found])
    return Exported(len(found), kept=True)


def read_bundle(text: str) -> Bundle:
    """An export's keys, from any version this Docket knows; a later one is named, not guessed at."""
    try:
        return Bundle.model_validate_json(text)
    except ValidationError as e:
        if any(err["loc"][:1] == ("schema_version",) for err in e.errors()):
            raise DocketError("that export was made by a newer Docket; update this one, then import it") from None
        raise DocketError("that file isn't a media key export Docket can read") from None


def import_keys(vault: Vault, source: Path, keys: KeyStore | None = None) -> Imported:
    """Add the keys an export holds and this clone lacks, after the passphrase typed at the console."""
    _at_console(IMPORT)
    try:
        data = source.read_bytes()
    except OSError as e:
        raise DocketError(f"can't read {source}: {e.strerror or type(e).__name__}") from None
    passphrase = passphrase_of(console.ask_hidden("Its passphrase: "))
    try:
        bundle = read_bundle(crypto.open_with_passphrase(data, passphrase))
    except DocketError as e:
        raise DocketError(f"{source}: {e}") from None
    store = keys or KeyStore(vault.home)
    store.ensure()
    added = had = 0
    for key in bundle.keys:
        if store.has(key.id):
            had += 1
            continue
        store.add(key.id, key.identity, key.path)
        added += 1
    # An export holds them all: lose this clone, and the same file brings them back.
    store.mark_exported([k.id for k in bundle.keys])
    return Imported(added, had)
