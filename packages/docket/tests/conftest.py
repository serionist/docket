from __future__ import annotations

from collections.abc import Callable

import pytest
from docket_testkit import make_config, make_record

from docket.vault.config import VaultConfig
from docket.vault.records import RawRecord


@pytest.fixture(autouse=True)
def isolated(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs git with an empty config of its own, so the developer's identity, signing, hooks or line-ending
    settings can't change a result, and the user's Docket config is a temp folder."""
    home = tmp_path_factory.mktemp("home")
    empty = home / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("DOCKET_CONFIG_DIR", str(home / "docket"))
    monkeypatch.setenv("DOCKET_MACHINE", "test-box")
    monkeypatch.delenv("DOCKET_VAULT", raising=False)
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def config() -> VaultConfig:
    return make_config()


@pytest.fixture
def records() -> Callable[..., RawRecord]:
    return make_record
