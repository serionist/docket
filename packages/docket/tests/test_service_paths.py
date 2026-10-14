from __future__ import annotations

from pathlib import Path

import pytest
from platformdirs import user_log_path, user_state_path

from docket import userconfig
from docket.errors import DocketError


def test_the_state_and_log_folders_follow_the_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKET_STATE_DIR", str(tmp_path / "state"))
    assert userconfig.state_dir() == tmp_path / "state"
    assert userconfig.log_dir() == tmp_path / "state" / "log"


def test_without_the_override_the_folders_are_the_systems_own(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCKET_STATE_DIR")
    assert userconfig.state_dir() == user_state_path("docket", appauthor=False)
    assert userconfig.log_dir() == user_log_path("docket", appauthor=False)


def test_the_service_keys_sit_beside_the_default_vault(tmp_path: Path) -> None:
    assert userconfig.service() == userconfig.ServiceSettings()
    userconfig.set_default_vault(tmp_path / "vault")
    userconfig.set_service(vault=tmp_path / "vault", port=8741)
    settings = userconfig.set_service(token_store="file")  # noqa: S106 - the store's kind, not a secret
    expected = userconfig.ServiceSettings(vault=tmp_path / "vault", port=8741, token_store="file")  # noqa: S106
    assert settings == expected
    assert userconfig.default_vault() == tmp_path / "vault"
    assert set(userconfig.load()) == {"default_vault", "service"}


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("service: [a]\n", "service must be a map"),
        ("service: {vault: 3}\n", "service.vault"),
        ("service: {port: 70000}\n", "service.port"),
        ("service: {port: true}\n", "service.port"),
        ("service: {port: '8741'}\n", "service.port"),
        ("service: {token_store: vault}\n", "service.token_store"),
    ],
)
def test_a_service_key_of_the_wrong_kind_is_refused(text: str, names: str) -> None:
    userconfig.config_path().parent.mkdir(parents=True, exist_ok=True)
    userconfig.config_path().write_text(text, encoding="utf-8")
    with pytest.raises(DocketError, match=names):
        userconfig.service()


def test_a_service_section_that_isnt_a_map_is_replaced_on_write() -> None:
    userconfig.config_path().parent.mkdir(parents=True, exist_ok=True)
    userconfig.config_path().write_text("service: null\n", encoding="utf-8")
    assert userconfig.service() == userconfig.ServiceSettings()
    assert userconfig.set_service(port=9001).port == 9001
