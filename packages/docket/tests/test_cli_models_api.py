"""The api provider's commands (#89): each of the three locks set only at the owner's console (DR-0537, DR-0539)."""

from __future__ import annotations

import getpass
from collections.abc import Callable
from pathlib import Path

import keyring
import pytest
from docket_testkit import AtConsole, empty_vault, make_config, sh
from models_kit import FAKE_KEY, MemoryKeyring

from docket import userconfig
from docket.cli import main
from docket.models import config, voyage
from docket.models.provider import Off
from docket.vault.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return empty_vault(tmp_path, make_config())


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_allowing_the_provider_asks_at_the_console_either_way(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, owner: Callable[..., AtConsole]
) -> None:
    root = str(vault.root)
    head = sh(vault.root, "git", "rev-parse", "HEAD")
    # In Claude Code's shell, and with no console (pytest's stdin is none): refused, nothing written.
    monkeypatch.setenv("CLAUDECODE", "1")
    code, _, err = run(capsys, "models", "allow", "voyage", "--vault", root)
    assert code != 0 and "CLAUDECODE is set" in err
    monkeypatch.delenv("CLAUDECODE")
    code, _, err = run(capsys, "models", "allow", "voyage", "--vault", root)
    assert code != 0 and "asks you at a console" in err
    assert sh(vault.root, "git", "rev-parse", "HEAD") == head
    assert Vault.open(vault.root).config.api_provider is None
    owner("code")
    code, out, _ = run(capsys, "models", "allow", "voyage", "--vault", root)
    assert code == 0, out
    assert "  api_provider: none -> voyage" in out and "go to Voyage AI over HTTPS" in out
    assert "api        this vault allows voyage, committed" in out
    assert sh(vault.root, "git", "log", "-1", "--format=%B").strip() == (
        "Config: api provider voyage\n\nconfirmed at the console"
    )
    assert Vault.open(vault.root).config.api_provider == "voyage"
    owner("code")
    code, out, _ = run(capsys, "models", "allow", "none", "--vault", root)
    assert code == 0 and "  api_provider: voyage -> none" in out
    assert Vault.open(vault.root).config.api_provider is None
    assert "api_provider" not in (vault.root / "vault.yaml").read_text(encoding="utf-8")
    code, _, err = run(capsys, "models", "allow", "cloud", "--vault", root)
    assert code != 0 and "voyage or none" in err


def test_choosing_the_api_and_keeping_its_key_ask_at_the_console(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, owner: Callable[..., AtConsole]
) -> None:
    chain = MemoryKeyring()
    keyring.set_keyring(chain)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": f" {FAKE_KEY} ")
    for claude in (True, False):
        if claude:
            monkeypatch.setenv("CLAUDECODE", "1")
        else:
            monkeypatch.delenv("CLAUDECODE")
        for args in (("models", "install", "--api"), ("models", "key", "voyage")):
            code, _, err = run(capsys, *args)
            assert code != 0 and ("CLAUDECODE is set" if claude else "asks you at a console") in err, args
    assert config.read() == Off() and chain.kept == {}
    owner()
    code, out, _ = run(capsys, "models", "install", "--api")
    assert code == 0 and "voyage-4 and rerank-2.5-lite through Voyage AI's API" in out
    chosen = config.read()
    assert isinstance(chosen, config.ApiChoice) and chosen.monthly_tokens == config.DEFAULT_MONTHLY_TOKENS
    assert "voyage-api-key" not in userconfig.config_path().read_text(encoding="utf-8")
    code, out, _ = run(capsys, "models", "key", "voyage")
    assert code == 0 and FAKE_KEY not in out
    assert chain.kept == {(voyage.KEY_SERVICE, voyage.KEY_USER): FAKE_KEY}
    assert FAKE_KEY not in userconfig.config_path().read_text(encoding="utf-8")
    code, out, _ = run(capsys, "models", "status")
    assert "key        in this machine's keychain" in out and FAKE_KEY not in out
    assert "sent       0 of 50,000,000 tokens this month" in out
    # Forgetting stops sending at once and asks nothing.
    monkeypatch.setenv("CLAUDECODE", "1")
    code, out, _ = run(capsys, "models", "key", "voyage", "--forget")
    assert code == 0 and "forgotten" in out and chain.kept == {}
