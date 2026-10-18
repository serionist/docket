"""This clone's media keys (DR-0549): one key file per media file, sealed in the clone's private folder, never in the
tree or in git.

```
<private>/media/store.json          {"schema_version": 1, "id", "store": "keyring" | "file", "recipient"}
<private>/media/keys/<key id>.age   {"identity", "path", "at"}, sealed with age to the store's recipient
```

The seal is a store identity: in the OS keychain as `docket` / `media-store:<id>`, one entry per clone, or, on a
machine whose owner opted in with `docket media keys init --store file`, in a file only the user can read in Docket's
per-user state folder, outside any repo. The keychain is chosen at the first put when a write and a read-back work
there; when they don't, the put is refused naming that command. A key is added with the store's public recipient, so
storing media never reads the keychain; reading media does.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import keyring
from pydantic import BaseModel, ConfigDict, ValidationError

from docket import paths
from docket.errors import DocketError
from docket.media import crypto
from docket.service.system import SYSTEM, Runner, others_can_read, run, write_private
from docket.vault.home import Home, make_private
from docket.vault.staging import retry

Store = Literal["keyring", "file"]
STORES: tuple[Store, ...] = ("keyring", "file")
SERVICE = "docket"
FOLDER = "media"
STORE_FILE = "store.json"
KEYS = "keys"
# Key ids only, never a key: which keys an export holds.
EXPORTED = "exported.json"
INIT_FILE = "docket media keys init --store file"
NO_KEYCHAIN = (
    "no keychain answers on this machine, so its media keys have nowhere to go; `docket media keys init --store "
    "file` keeps their seal in a file only you can read, outside any repo"
)


class Keychain(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...


class StoreInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str
    store: Store
    recipient: str


@dataclass(frozen=True)
class Key:
    identity: str
    path: str
    at: datetime


class KeyStore:
    def __init__(self, home: Home, backend: Keychain | None = None, *, system: str = SYSTEM, runner: Runner = run):
        self.home = home
        self._backend = backend
        self.system = system
        self.runner = runner
        self._seal: str | None = None

    @property
    def folder(self) -> Path:
        return self.home.private / FOLDER

    @property
    def backend(self) -> Keychain:
        return self._backend if self._backend is not None else cast(Keychain, keyring.get_keyring())

    def _key_file(self, key_id: str) -> Path:
        if len(key_id) != 64 or any(c not in "0123456789abcdef" for c in key_id):
            raise DocketError("a media key's id is 64 hex digits")
        return self.folder / KEYS / f"{key_id}.age"

    # -- the store --------------------------------------------------------------------------------------------------

    def info(self) -> StoreInfo | None:
        path = self.folder / STORE_FILE
        try:
            return StoreInfo.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, ValidationError):
            raise DocketError(f"{path} isn't a media key store Docket can read; move it aside to start again") from None

    def init(self, store: Store) -> StoreInfo:
        """Make this clone's store, in the keychain or, by the owner's choice, a file. A clone that has one keeps it:
        its keys are sealed to it."""
        found = self.info()
        if found is not None:
            if found.store == store:
                return found
            raise DocketError(
                f"this clone's media keys are already sealed with a {found.store} store; they stay where they are"
            )
        secret, recipient = crypto.new_identity()
        info = StoreInfo(id=secrets.token_hex(8), store=store, recipient=recipient)
        if store == "keyring":
            if not self._keychain_works(info.id, secret):
                raise DocketError(NO_KEYCHAIN)
        else:
            write_private(self._seal_file(info.id), secret + "\n", system=self.system, runner=self.runner)
        self._write(self.folder / STORE_FILE, info.model_dump_json().encode("utf-8"))
        self._seal = secret
        return info

    def ensure(self) -> StoreInfo:
        """The store, made in the keychain at the first put; refused, naming the opt-in, where no keychain answers."""
        return self.info() or self.init("keyring")

    def _seal_file(self, store_id: str) -> Path:
        return paths.state_dir() / "media-stores" / store_id

    def _keychain_works(self, store_id: str, secret: str) -> bool:
        """A write and a read-back that match. On Linux the service reaches the Secret Service only through the user
        manager's session bus, so the keychain counts only when that has one, as for the service's token."""
        if self.system == "Linux" and not self._user_manager_has_bus():
            return False
        try:
            self.backend.set_password(SERVICE, f"media-store:{store_id}", secret)
            return self.backend.get_password(SERVICE, f"media-store:{store_id}") == secret
        except Exception:
            return False

    def _user_manager_has_bus(self) -> bool:
        proc = self.runner(["systemctl", "--user", "show-environment"])
        return proc.returncode == 0 and any(
            line.startswith("DBUS_SESSION_BUS_ADDRESS=") for line in proc.stdout.splitlines()
        )

    def _store_secret(self, info: StoreInfo) -> str | None:
        """The store identity, or None when this machine can't give it: no answer from the keychain, or no file."""
        if self._seal is not None:
            return self._seal
        if info.store == "keyring":
            try:
                secret = self.backend.get_password(SERVICE, f"media-store:{info.id}")
            except Exception:
                return None
        else:
            path = self._seal_file(info.id)
            try:
                if others_can_read(path, system=self.system):
                    raise DocketError(f"others can read {path}; make it yours alone, then try again")
                secret = path.read_text(encoding="utf-8").strip()
            except OSError:
                return None
        self._seal = secret or None
        return self._seal

    # -- keys -------------------------------------------------------------------------------------------------------

    def add(self, key_id: str, identity: str, path: str) -> None:
        info = self.ensure()
        body = json.dumps({"identity": identity, "path": path, "at": datetime.now(UTC).isoformat()})
        self._write(self._key_file(key_id), crypto.seal(body, info.recipient))

    def has(self, key_id: str) -> bool:
        """Whether this clone holds a key, without opening it."""
        return self._key_file(key_id).is_file()

    def get(self, key_id: str) -> Key | None:
        """A key, or None when this clone doesn't hold it or can't open its seal."""
        path = self._key_file(key_id)
        info = self.info()
        if info is None or not path.is_file():
            return None
        secret = self._store_secret(info)
        if secret is None:
            return None
        data = json.loads(crypto.unseal(path.read_bytes(), secret))
        return Key(str(data["identity"]), str(data["path"]), datetime.fromisoformat(str(data["at"])))

    def delete(self, key_id: str) -> None:
        """Forget a key: what it opened can't be opened again from this clone."""
        path = self._key_file(key_id)
        retry(lambda: path.unlink(missing_ok=True))

    def count(self) -> int:
        return len(self.ids())

    def ids(self) -> list[str]:
        """Every key this clone holds, by id, without opening one."""
        folder = self.folder / KEYS
        return sorted(p.stem for p in folder.glob("*.age")) if folder.is_dir() else []

    # -- which keys an export holds (DR-0550) ------------------------------------------------------------------------

    def exported(self) -> frozenset[str]:
        """The ids of the keys an export made or read on this clone holds; a file that doesn't read holds none."""
        try:
            data: Any = json.loads((self.folder / EXPORTED).read_text(encoding="utf-8"))
            ids: Any = data["ids"]
        except (OSError, ValueError, KeyError, TypeError):
            return frozenset()
        if not isinstance(ids, list):
            return frozenset()
        return frozenset(i for i in cast(list[Any], ids) if isinstance(i, str))

    def mark_exported(self, ids: list[str], *, without: Collection[str] = ()) -> None:
        """Record the ids an export holds, and leave out `without`, the keys a forget listed, which no export may say
        it holds (DR-0595)."""
        held = sorted((self.exported() | set(ids)) - set(without))
        self._write(self.folder / EXPORTED, json.dumps({"schema_version": 1, "ids": held}).encode("utf-8"))

    def unbacked(self) -> int:
        """How many of this clone's keys no export holds yet: lose the clone, and their media is gone."""
        held = self.exported()
        return sum(1 for key in self.ids() if key not in held)

    def _write(self, path: Path, data: bytes) -> None:
        """Through a temp file in Docket's private folder and a rename, so a crash leaves the old file or the new."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = make_private(self.home.git_dir, self.home.tmp_dir) / f"media-key.{secrets.token_hex(4)}.{os.getpid()}.tmp"
        try:
            with tmp.open("wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            retry(lambda: tmp.replace(path))
        finally:
            tmp.unlink(missing_ok=True)
