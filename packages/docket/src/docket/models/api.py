"""Turning the API provider on (#89): the three locks, each set only at the owner's console (DR-0537, DR-0539).

- the vault allows it: `api_provider` in vault.yaml, written through vault.yaml's one writer, which asks at the console
  for any change of it, since a key added later never only keeps more out (DR-0517, DR-0521);
- this machine chose it: `provider: api` in the user config's models block;
- the key: in the system keychain.

Removing the machine's choice or the key stops sending at once and asks nothing: neither touches the gate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from docket import console
from docket.errors import DocketError
from docket.models import config
from docket.models.catalogue import VOYAGE, VOYAGE_4, VOYAGE_RERANK
from docket.models.voyage import KeyStore
from docket.vault import settings
from docket.vault.config import VaultConfig
from docket.vault.vault import Vault

PROVIDERS = (VOYAGE,)
CHOOSING = "choosing the api provider for this machine, which sends a vault's text to Voyage AI"
KEEPING = "keeping the Voyage key in this machine's keychain"
SENDS = (
    "this vault's chunks, and every search's query and top 50 candidates, go to Voyage AI over HTTPS once this "
    "machine has chosen the api provider and keeps its key; the README's What leaves this machine says what and when"
)


def at_console(act: str) -> None:
    """Refused, before anything is written, without the owner at their own console."""
    problem = console.problem(act)
    if problem is not None:
        raise DocketError(problem)


def choose(*, reranker: bool = True, monthly: int | None = None) -> config.ApiChoice:
    """This machine's choice of the api provider, at the owner's console; the month's token cap kept when the config
    already gives one."""
    at_console(CHOOSING)
    now = config.read()
    tokens = monthly if monthly is not None else now.monthly_tokens if isinstance(now, config.ApiChoice) else None
    config.write_api(VOYAGE_4, VOYAGE_RERANK if reranker else None, tokens or config.DEFAULT_MONTHLY_TOKENS)
    chosen = config.read()
    if not isinstance(chosen, config.ApiChoice):
        raise DocketError("the models block didn't read back as the api provider; check your config")
    return chosen


def keep_key(read_key: Callable[[], str], keys: KeyStore | None = None) -> None:
    """Read the key at the owner's console, without echo, and keep it in the keychain only."""
    at_console(KEEPING)
    value = read_key().strip()
    if not value:
        raise DocketError("no key given, so the keychain is left as it was")
    (keys or KeyStore()).store(value)


def check_provider(name: str) -> str | None:
    """The provider a command names, or None for none."""
    if name == "none":
        return None
    if name not in PROVIDERS:
        raise DocketError(f"the api provider is {', '.join(PROVIDERS)} or none, not {name!r}")
    return name


@dataclass(frozen=True)
class Allowed:
    """What `docket models allow` did."""

    provider: str | None
    changed: bool = False
    commit: str | None = None
    declined: bool = False
    by_hand: str | None = None


def allow(vault: Vault, provider: str | None) -> Allowed:
    """Set the vault's `api_provider`, through vault.yaml's one writer: every change of it asks at the console."""

    def edit(current: VaultConfig) -> VaultConfig:
        return current.model_copy(update={"api_provider": provider})

    warnings = [SENDS] if provider is not None else []
    done = settings.rewrite(vault, edit, f"Config: api provider {provider or 'none'}", via="cli", warnings=warnings)
    line = settings.key_line("api_provider", provider) if provider is not None else "no api_provider line"
    return Allowed(provider, done.changed, done.commit, done.declined, line if done.by_hand else None)


def status(vault: Vault | None, keys: KeyStore | None = None) -> list[str]:
    """`docket models status`'s lines for the api provider: the key and whether this vault allows it."""
    out = ["key        in this machine's keychain" if (keys or KeyStore()).read() else "key        ! none: " + KEY_HINT]
    if vault is not None:
        allowed = vault.config.api_provider
        out.append(
            f"vault      allows {allowed}"
            if allowed is not None
            else "vault      ! doesn't allow sending its text: docket models allow voyage, at your console"
        )
    return out


KEY_HINT = "docket models key voyage, at your console"
