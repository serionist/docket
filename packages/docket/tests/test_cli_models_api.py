"""The api provider's commands (#89): the three locks (DR-0537, DR-0539), the vault's allow and the machine's choice
made on the owner's yes and the key typed only at the owner's own console (DR-0599)."""

from __future__ import annotations

import getpass
from collections.abc import Callable
from pathlib import Path

import keyring
import pytest
import typer
from docket_testkit import AtConsole, cant_place, empty_vault, git, make_config, sh
from models_kit import FAKE_KEY, MemoryKeyring

from docket import cli, console, userconfig
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


def test_allowing_the_provider_asks_nothing_either_way_and_says_who(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = str(vault.root)
    # In Claude Code's shell, on the owner's yes in chat (DR-0599): made, the commit saying so.
    monkeypatch.setenv("CLAUDECODE", "1")
    code, out, _ = run(capsys, "models", "allow", "voyage", "--vault", root)
    assert code == 0, out
    assert "api        this vault allows voyage, committed" in out
    assert sh(vault.root, "git", "log", "-1", "--format=%B").strip() == (
        f"Config: api provider voyage\n\n{console.BY_CLAUDE}"
    )
    assert Vault.open(vault.root).config.api_provider == "voyage"
    monkeypatch.delenv("CLAUDECODE")
    code, out, _ = run(capsys, "models", "allow", "none", "--vault", root)
    assert code == 0 and "this vault allows no api provider, committed" in out
    assert Vault.open(vault.root).config.api_provider is None
    assert "api_provider" not in (vault.root / "vault.yaml").read_text(encoding="utf-8")
    code, _, err = run(capsys, "models", "allow", "cloud", "--vault", root)
    assert code != 0 and "voyage or none" in err


def test_the_key_is_typed_only_at_the_console_and_the_choice_asks_nothing(
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
        code, _, err = run(capsys, "models", "key", "voyage")
        assert code != 0 and ("CLAUDECODE is set" if claude else "asks you at a console") in err
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


def test_the_commands_refuse_what_they_can_t_do(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, owner: Callable[..., AtConsole]
) -> None:
    keyring.set_keyring(MemoryKeyring())
    code, _, err = run(capsys, "models", "key", "none")
    assert code != 0 and "give the provider whose key this is" in err
    owner()
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "   ")
    code, _, err = run(capsys, "models", "key", "voyage")
    assert code != 0 and "no key given" in err
    root = str(vault.root)
    assert run(capsys, "models", "allow", "voyage", "--vault", root)[0] == 0
    code, out, _ = run(capsys, "models", "allow", "voyage", "--vault", root)
    assert code == 0 and "this vault allows voyage already" in out
    path = vault.root / "vault.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "# mine\n", encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qm", "My comment", "--", "vault.yaml")
    cant_place(monkeypatch)
    code, out, _ = run(capsys, "models", "allow", "none", "--vault", root)
    assert code == 1 and "set it there: no api_provider line" in out


def test_status_and_check_for_the_api(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, owner: Callable[..., AtConsole]
) -> None:
    from models_kit import with_voyage

    fake = with_voyage(monkeypatch, key=False)
    root = str(vault.root)
    code, out, _ = run(capsys, "models", "status", "--vault", root)
    assert "key        ! none: docket models key voyage" in out
    assert "vault      ! doesn't allow sending its text" in out
    code, out, _ = run(capsys, "models", "status", "--check", "--vault", root)
    assert code == 1 and "doesn't allow sending its text to voyage" in out
    run(capsys, "models", "allow", "voyage", "--vault", root)
    keyring.get_keyring().set_password(voyage.KEY_SERVICE, voyage.KEY_USER, FAKE_KEY)
    code, out, _ = run(capsys, "models", "status", "--check", "--vault", root)
    assert code == 0 and "vault      allows voyage" in out and "api        up: voyage-4, 512 dims" in out
    assert fake.paths() == ["/embeddings"]
    elsewhere = vault.root.parent / "not-a-vault"
    elsewhere.mkdir()
    code, out, err = run(capsys, "models", "status", "--check", "--vault", str(elsewhere))
    assert code != 0 and "vault      " not in out and "no vault at or above" in err
    with pytest.raises(typer.Exit):
        cli._check_api(None)  # pyright: ignore[reportPrivateUsage]
    assert "the api provider opens for a vault" in capsys.readouterr().out
