"""The service's bearer token: where it lives, chosen once by install and recorded, and the only place read after.

The store is the OS keychain through keyring when a write and a read-back work there, else a file only the user can
read. It is chosen by `docket service install` (or `docket service token --rotate` where install never ran) and
recorded as `service.store`, so the service, the CLI and Claude Code's header helper always read the same one: a
process choosing for itself could land on the other store and make a second token. Nothing but those two commands
makes a token. Either way, any process running as the user can get it.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import keyring

from docket import userconfig
from docket.errors import DocketError
from docket.service.system import SYSTEM, Runner, others_can_read, run, write_private
from docket.userconfig import TokenStore

SERVICE = "docket"
USER = "service-token"
FILE_NAME = "service-token"
ROTATE = "docket service token --rotate"


class Keychain(Protocol):
    """The two calls Docket makes of a keyring backend."""

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...


@dataclass(frozen=True)
class Token:
    value: str
    store: TokenStore
    # Set when a token was written now: made, rotated, or moved to a new store.
    written: bool = False
    # The store that was recorded before, when this call chose another.
    moved_from: TokenStore | None = None


class Tokens:
    def __init__(self, backend: Keychain | None = None, *, system: str = SYSTEM, runner: Runner = run) -> None:
        self._backend = backend
        self.system = system
        self.runner = runner

    @property
    def backend(self) -> Keychain:
        # keyring's own methods carry a decorator whose type pyright can't line up with the protocol.
        return self._backend if self._backend is not None else cast(Keychain, keyring.get_keyring())

    @staticmethod
    def file() -> Path:
        return userconfig.config_path().parent / FILE_NAME

    def where(self, store: TokenStore | None = None) -> str:
        store = store or userconfig.service().store
        if store is None:
            return "nowhere yet"
        return "the system keychain" if store == "keyring" else str(self.file())

    # -- reading: the recorded store only, never a new token ---------------------------------------------------------

    def read(self) -> str:
        store = userconfig.service().store
        if store is None:
            raise DocketError(
                f"there is no service token yet; run `docket service install`, or `{ROTATE}` where the service runs "
                "by hand"
            )
        try:
            return self._read(store)
        except DocketError as e:
            raise DocketError(f"can't read the service token from {self.where(store)}: {e}; run `{ROTATE}`") from None

    def header(self) -> str:
        """The JSON object Claude Code's headersHelper prints."""
        return json.dumps({"Authorization": f"Bearer {self.read()}"})

    def _read(self, store: TokenStore) -> str:
        if store == "keyring":
            try:
                value = self.backend.get_password(SERVICE, USER)
            except Exception as e:
                raise DocketError(type(e).__name__) from None
            if not value:
                raise DocketError("it holds no token")
            return value
        path = self.file()
        try:
            if others_can_read(path, system=self.system):
                raise DocketError("others can read the file")
            value = path.read_text(encoding="utf-8").strip()
        except OSError as e:
            raise DocketError(e.strerror or type(e).__name__) from None
        if not value:
            raise DocketError("the file is empty")
        return value

    # -- writing: install and --rotate -------------------------------------------------------------------------------

    def ensure(self) -> Token:
        """Install's step: keep a token the recorded store gives back; make one where none is recorded or the
        recorded store is gone."""
        store = userconfig.service().store
        if store is not None:
            try:
                return Token(self._read(store), store)
            except DocketError:
                return self._choose(moved_from=store)
        return self._choose()

    def rotate(self) -> Token:
        """A new token in the recorded store; chosen as install chooses when none is recorded or it is gone."""
        store = userconfig.service().store
        if store is None:
            return self._choose()
        value = secrets.token_urlsafe(32)
        try:
            self._write(store, value)
        except DocketError:
            return self._choose(moved_from=store)
        return Token(value, store, written=True)

    def _choose(self, moved_from: TokenStore | None = None) -> Token:
        value = secrets.token_urlsafe(32)
        store: TokenStore = "keyring" if self._keyring_works(value) else "file"
        if store == "file":
            self._write("file", value)
        userconfig.set_service(store=store)
        return Token(value, store, written=True, moved_from=moved_from if moved_from != store else None)

    def _keyring_works(self, value: str) -> bool:
        """A write and a read-back that matches. A backend can be there and still refuse (Windows' CredWrite 1312 in
        a session without credentials). On Linux the unit reaches the Secret Service only through the user manager's
        session bus, so the keychain counts only when that has one."""
        if self.system == "Linux" and not self._user_manager_has_bus():
            return False
        try:
            self.backend.set_password(SERVICE, USER, value)
            return self.backend.get_password(SERVICE, USER) == value
        except Exception:
            return False

    def _user_manager_has_bus(self) -> bool:
        proc = self.runner(["systemctl", "--user", "show-environment"])
        return proc.returncode == 0 and any(
            line.startswith("DBUS_SESSION_BUS_ADDRESS=") for line in proc.stdout.splitlines()
        )

    def _write(self, store: TokenStore, value: str) -> None:
        if store == "keyring":
            try:
                self.backend.set_password(SERVICE, USER, value)
            except Exception as e:
                raise DocketError(type(e).__name__) from None
            return
        try:
            write_private(self.file(), value + "\n", system=self.system, runner=self.runner)
        except OSError as e:
            raise DocketError(e.strerror or type(e).__name__) from None
