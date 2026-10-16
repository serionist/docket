"""What the service's routes and tools share: its settings, the browser sessions, the vault, the writer and the
index updater."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from docket import __version__, index, userconfig
from docket.errors import DocketError
from docket.index import IndexStatus
from docket.service.history import RunHistory
from docket.service.models import ModelsKeeper, ModelsStatus
from docket.service.runs import Runs
from docket.service.sessions import Sessions
from docket.service.writer import IndexUpdater, Writer
from docket.vault.home import Home
from docket.vault.vault import Vault

# How long a write of the service waits for another Docket process to let go of the vault: less than a client's
# timeout, so the client gets Docket's message rather than its own.
LOCK_SECONDS = 15.0


@dataclass
class Settings:
    port: int
    vault: Path | None
    token: Callable[[], str]
    # Where the token lives, for status: the system keychain or the file's path.
    store: str = "nowhere yet"
    started: datetime = field(default_factory=lambda: datetime.now(UTC))


class Status(BaseModel):
    service: str = "docket"
    version: str
    pid: int
    port: int
    started: datetime
    vault: str | None
    machine: str | None
    vault_problem: str | None
    # Where the service token lives.
    store: str
    index: IndexStatus | None = None
    models: ModelsStatus = ModelsStatus()


@dataclass(frozen=True)
class LastUpdate:
    """The last index update the service ran: when it ended, and the rows it left."""

    at: datetime
    counts: dict[str, int]


class Service:
    def __init__(self, settings: Settings, sessions: Sessions | None = None) -> None:
        self.settings = settings
        self.sessions = sessions or Sessions()
        self.writer = Writer()
        # Each ended run's line, in the vault's private folder (DR-0301), read before Runs records what a crash left.
        self.history = RunHistory(self._home if settings.vault is not None else None)
        self.history.load()
        # Open sync runs, in memory only; the hook's marker goes in the state folder (DR-0240).
        self.runs = Runs(userconfig.state_dir(), ended=self.history.add)
        self.updater = IndexUpdater(self._update)
        # The embedder's worker and the embedding pass, started by the app's lifespan (DR-0330).
        self.models = ModelsKeeper(self.vault)
        # Kept for the web app's footer: index.status() hashes every doc and entity file, too much for each page.
        self.last_update: LastUpdate | None = None

    def _update(self) -> None:
        report = index.update(self.vault())
        self.last_update = LastUpdate(datetime.now(UTC), dict(report.counts))
        self.models.poke()

    def _home(self) -> Home:
        return self.vault().home

    def vault(self) -> Vault:
        """The vault, opened for this call, so an edit to vault.yaml applies at once."""
        if self.settings.vault is None:
            raise DocketError("the service has no vault; run `docket service install --vault <folder>`")
        return Vault.open(self.settings.vault, lock_timeout=LOCK_SECONDS)

    def status(self) -> Status:
        vault: Vault | None = None
        problem: str | None = None
        found: IndexStatus | None = None
        try:
            vault = self.vault()
            found = self._index(vault)
        except DocketError as e:
            problem = str(e)
        except OSError as e:
            # A vault the service may not read, such as one under a macOS folder its Python has no permission for.
            problem = f"can't read the vault: {e.strerror or type(e).__name__}"
        named = vault.root if vault is not None else self.settings.vault
        return Status(
            version=__version__,
            pid=os.getpid(),
            port=self.settings.port,
            started=self.settings.started,
            vault=str(named) if named else None,
            machine=vault.machine if vault else None,
            vault_problem=problem,
            store=self.settings.store,
            index=found,
            models=self.models.status(),
        )

    @staticmethod
    def _index(vault: Vault) -> IndexStatus | None:
        """The index's status; None when it can't be read (no FTS5, an old SQLite), which search then names."""
        try:
            return index.status(vault)
        except DocketError:
            return None
