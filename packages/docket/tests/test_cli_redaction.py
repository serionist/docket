"""Choosing redaction: init's flags and summary, `docket redaction` and `docket redaction set`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import AtConsole, empty_vault, make_config, sh

from docket.cli import main
from docket.vault import redaction
from docket.vault.config import RedactionPattern, VaultConfig
from docket.vault.vault import Vault

FLAGS = [
    "--owner", "Ada Example", "--email", "ada@example.invalid", "--time-zone", "Europe/Budapest",
    "--machine", "test-box", "--no-sources", "--remote", "none",
]  # fmt: skip


def make_vault(tmp_path: Path, *patterns: RedactionPattern) -> Vault:
    config = make_config()
    config.redaction.patterns = list(patterns)
    return empty_vault(tmp_path, config)


def settings(vault: Vault) -> VaultConfig:
    return VaultConfig.load(vault.root / "vault.yaml")


def subjects(vault: Vault) -> list[str]:
    return sh(vault.root, "git", "log", "--format=%s").splitlines()


def test_init_writes_each_flag_and_prints_the_settings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init", "--yes", "--path", str(tmp_path / "a"), *FLAGS]) == 0
    out = capsys.readouterr().out
    assert "redact:   credentials on, phone numbers off; names never" in out
    assert redaction.CREDENTIALS_OFF not in out
    first = VaultConfig.load(tmp_path / "a" / "vault.yaml").redaction
    assert (first.credentials, first.phone) == (True, False)
    args = ["init", "--yes", "--path", str(tmp_path / "b"), *FLAGS, "--no-redact-credentials", "--redact-phone"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "redact:   credentials off, phone numbers on" in out
    assert f"warning:  {redaction.CREDENTIALS_OFF}" in out
    second = VaultConfig.load(tmp_path / "b" / "vault.yaml").redaction
    assert (second.credentials, second.phone) == (False, True)


def test_redaction_prints_the_settings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = make_vault(tmp_path, RedactionPattern(name="door code", pattern="x", enabled=False))
    assert main(["redaction", "--vault", str(vault.root)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:2] == ["credentials", "on"]
    assert lines[1].split()[:2] == ["phone", "off"]
    assert lines[2].split() == ["pattern", "off", "door", "code"]
    assert lines[-1] == redaction.NAMES


def test_set_phone_on_commits_once_and_says_what_stays(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = make_vault(tmp_path)
    assert main(["redaction", "set", "phone", "on", "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "redact   phone on, committed" in out
    assert f"note     {redaction.TURNED_ON}" in out
    assert settings(vault).redaction.phone
    assert subjects(vault)[0] == "Config: redaction phone on"
    assert main(["redaction", "set", "phone", "on", "--vault", str(vault.root)]) == 0
    assert "redact   phone is on already" in capsys.readouterr().out
    assert subjects(vault)[0] == "Config: redaction phone on"
    assert len(subjects(vault)) == 2


def test_set_credentials_off_warns_what_is_stored(
    owner: Callable[..., AtConsole], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    owner("code", "code", "code", "code")  # each loosening below is typed back at the console (DR-0517)
    vault = make_vault(tmp_path)
    assert main(["redaction", "set", "credentials", "off", "--vault", str(vault.root)]) == 0
    assert f"note     {redaction.CREDENTIALS_OFF}" in capsys.readouterr().out
    assert not settings(vault).redaction.credentials
    assert main(["redaction", "--vault", str(vault.root)]) == 0
    assert f"warning      {redaction.CREDENTIALS_OFF}" in capsys.readouterr().out


def test_set_turns_a_pattern_off_by_its_name(owner: Callable[..., AtConsole], tmp_path: Path) -> None:
    owner("code", "code", "code", "code")  # each loosening below is typed back at the console (DR-0517)
    vault = make_vault(
        tmp_path, RedactionPattern(name="door code", pattern="x"), RedactionPattern(name="y", pattern="y")
    )
    assert main(["redaction", "set", "door code", "off", "--vault", str(vault.root)]) == 0
    assert [p.enabled for p in settings(vault).redaction.patterns] == [False, True]
    assert subjects(vault)[0] == "Config: redaction door code off"


@pytest.mark.parametrize(
    ("patterns", "rule", "state", "message"),
    [
        ([], "names", "on", "no redaction rule names: credentials, phone"),
        ([RedactionPattern(name="door", pattern="x")], "doors", "on", "credentials, phone, or door"),
        ([RedactionPattern(name="phone", pattern="x")], "phone", "on", "phone: true, under redaction:"),
        (
            [RedactionPattern(name="phone", pattern="x")],
            "phone",
            "off",
            "phone: false, under redaction:, or enabled: false",
        ),
        ([RedactionPattern(name="credentials", pattern="x")], "credentials", "on", "in place of builtin:"),
        (
            [RedactionPattern(name="door", pattern="x"), RedactionPattern(name="door", pattern="y")],
            "door",
            "off",
            "2 patterns are named door; change the one you mean in vault.yaml (enabled: false)",
        ),
    ],
)
def test_a_rule_set_can_t_place_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    patterns: list[RedactionPattern],
    rule: str,
    state: str,
    message: str,
) -> None:
    vault = make_vault(tmp_path, *patterns)
    before = (vault.root / "vault.yaml").read_text(encoding="utf-8")
    assert main(["redaction", "set", rule, state, "--vault", str(vault.root)]) == 2
    assert message in capsys.readouterr().err
    assert (vault.root / "vault.yaml").read_text(encoding="utf-8") == before


def test_the_group_s_vault_reaches_set(
    owner: Callable[..., AtConsole], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner("code", "code", "code", "code")  # each loosening below is typed back at the console (DR-0517)
    here = make_vault(tmp_path / "a")
    there = make_vault(tmp_path / "b")
    monkeypatch.chdir(here.root)
    assert main(["redaction", "--vault", str(there.root), "set", "credentials", "off"]) == 0
    assert not settings(there).redaction.credentials
    assert settings(here).redaction.credentials
    assert subjects(here)[0] != "Config: redaction credentials off"


def test_set_takes_on_or_off_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = make_vault(tmp_path)
    assert main(["redaction", "set", "phone", "yes", "--vault", str(vault.root)]) == 2
    assert "on or off" in capsys.readouterr().err


def test_a_hand_edited_vault_yaml_gets_the_line_to_change(
    owner: Callable[..., AtConsole], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    owner("code", "code", "code", "code")  # each loosening below is typed back at the console (DR-0517)
    vault = make_vault(tmp_path, RedactionPattern(name="door", pattern="x"))
    path = vault.root / "vault.yaml"
    mine = path.read_text(encoding="utf-8").replace("redaction:\n", "redaction:   # mine\n", 1)
    path.write_text(mine, encoding="utf-8", newline="\n")
    assert main(["redaction", "set", "phone", "on", "--vault", str(vault.root)]) == 1
    out = capsys.readouterr().out
    assert "left as it is; set it there: phone: true, under redaction:" in out
    assert path.read_text(encoding="utf-8") == mine
    assert main(["redaction", "set", "door", "off", "--vault", str(vault.root)]) == 1
    assert "enabled: false, under the pattern named door in redaction.patterns" in capsys.readouterr().out


def test_an_old_form_vault_yaml_is_rewritten_in_the_new_form(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    path = vault.root / "vault.yaml"
    old = path.read_text(encoding="utf-8").replace("  credentials: true\n  phone: false\n", "  builtin: true\n")
    path.write_text(old, encoding="utf-8", newline="\n")
    assert main(["redaction", "set", "phone", "on", "--vault", str(vault.root)]) == 0
    text = path.read_text(encoding="utf-8")
    assert "builtin" not in text
    assert "  credentials: true\n  phone: true\n" in text


def test_a_commit_that_fails_puts_vault_yaml_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = make_vault(tmp_path)
    before = (vault.root / "vault.yaml").read_text(encoding="utf-8")

    def boom(*_args: object) -> None:
        raise OSError("the disk is full")

    monkeypatch.setattr(Vault, "commit", boom)
    with pytest.raises(OSError, match="the disk is full"):
        redaction.set_rule(Vault.open(vault.root), "phone", True)
    assert (vault.root / "vault.yaml").read_text(encoding="utf-8") == before


def test_an_edit_not_committed_yet_is_committed_on_its_own_before_the_set(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    lists = settings(vault).never_store.model_copy(update={"keywords": ["Project Phoenix"]})
    edited = settings(vault).model_copy(update={"never_store": lists})
    (vault.root / "vault.yaml").write_text(edited.dump(), encoding="utf-8", newline="\n")
    assert main(["redaction", "set", "phone", "on", "--vault", str(vault.root)]) == 0
    assert subjects(vault)[:2] == ["Config: redaction phone on", "Config: vault.yaml as edited"]
    assert settings(vault).never_store.keywords == ["Project Phoenix"]
    assert sh(vault.root, "git", "status", "--porcelain").strip() == ""
