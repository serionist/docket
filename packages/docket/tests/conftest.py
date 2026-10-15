from __future__ import annotations

from collections.abc import Callable

import keyring
import pytest
from docket_testkit import TEST_GITCONFIG, make_config, make_record, use_factory
from keyring.backends.fail import Keyring as FailKeyring

from docket.vault.config import VaultConfig
from docket.vault.records import RawRecord


@pytest.fixture(scope="session", autouse=True)
def kit_templates(tmp_path_factory: pytest.TempPathFactory) -> None:
    """The kit's builders find the session's shared templates through this factory (DR-0220)."""
    use_factory(tmp_path_factory)


@pytest.fixture(autouse=True)
def isolated(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs git with a config of its own, empty but for auto maintenance, so the developer's identity,
    signing, hooks or line-ending settings can't change a result, and the user's Docket config is a temp folder."""
    home = tmp_path_factory.mktemp("home")
    gitconfig = home / "gitconfig"
    gitconfig.write_text(TEST_GITCONFIG, encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("DOCKET_CONFIG_DIR", str(home / "docket"))
    monkeypatch.setenv("DOCKET_STATE_DIR", str(home / "docket-state"))
    # No test may reach the developer's real keychain: keyring's failing backend, here and in any subprocess.
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    keyring.set_keyring(FailKeyring())
    monkeypatch.setenv("DOCKET_MACHINE", "test-box")
    monkeypatch.delenv("DOCKET_VAULT", raising=False)
    # Claude Code sets it in the shells it runs, and a release refuses to run under it: tests set it where they mean to.
    monkeypatch.delenv("CLAUDECODE", raising=False)
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def config() -> VaultConfig:
    return make_config()


@pytest.fixture
def records() -> Callable[..., RawRecord]:
    return make_record
