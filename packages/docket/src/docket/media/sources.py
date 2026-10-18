"""A source's media hosts and folders in vault.yaml (DR-0548), changed through vault.yaml's one writer.

Each is made on the owner's yes, at their own terminal or in chat to Claude, and committed saying who (DR-0601).
"""

from __future__ import annotations

from typing import Literal

from docket.errors import DocketError
from docket.vault import settings
from docket.vault.config import HOST, VaultConfig, folder_problem
from docket.vault.settings import Rewritten
from docket.vault.vault import Vault

Field = Literal["media_hosts", "media_folders"]
WORDS: dict[Field, str] = {"media_hosts": "media host", "media_folders": "media folder"}


def _checked(field: Field, value: str) -> str:
    if field == "media_hosts":
        if not HOST.match(value):
            raise DocketError(f"{value!r} isn't a host name: lowercase, with no scheme, port or path")
        return value
    problem = folder_problem(value)
    if problem is not None:
        raise DocketError(problem)
    return value


def set_media_list(vault: Vault, source: str, field: Field, value: str, *, add: bool) -> Rewritten:
    """Add or remove one host or folder of a source, and commit vault.yaml."""
    value = _checked(field, value)

    def edit(config: VaultConfig) -> VaultConfig:
        entry = config.sources.get(source)
        if entry is None:
            raise DocketError(f"source {source!r} isn't in vault.yaml")
        now: list[str] = getattr(entry, field)
        if (value in now) == add:
            return config
        listed = [*now, value] if add else [v for v in now if v != value]
        sources = {**config.sources, source: entry.model_copy(update={field: listed})}
        return config.model_copy(update={"sources": sources})

    word = WORDS[field]
    message = f"Config: {source}, a {word} {'added' if add else 'removed'}"
    return settings.rewrite(vault, edit, message, via="cli")
