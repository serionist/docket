from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import make_config

from docket.errors import DocketError
from docket.vault import machine
from docket.vault.config import VaultConfig


def test_the_config_round_trips(tmp_path: Path) -> None:
    config = make_config()
    path = tmp_path / "vault.yaml"
    path.write_text(config.dump(), encoding="utf-8")
    assert VaultConfig.load(path) == config


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "vault.yaml"
    path.write_text(make_config().dump().replace("never_store:", "never_stor:"), encoding="utf-8")
    with pytest.raises(DocketError, match="never_stor"):
        VaultConfig.load(path)


@pytest.mark.parametrize("zone", ["Europe/Budapest", "America/New_York", "UTC", "Asia/Kolkata"])
def test_iana_zones_are_accepted_on_every_system(zone: str) -> None:
    assert make_config().model_copy(update={}).owner.model_validate({"name": "A", "time_zone": zone}).time_zone == zone


def test_a_windows_zone_name_is_refused() -> None:
    from docket.vault.config import Owner

    with pytest.raises(ValueError, match="IANA"):
        Owner(name="A", time_zone="Central Europe Daylight Time")


def test_bad_yaml_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vault.yaml"
    path.write_text("owner: [unclosed\n", encoding="utf-8")
    with pytest.raises(DocketError, match="not valid YAML"):
        VaultConfig.load(path)


@pytest.mark.parametrize("name", ["laptop", "desktop-4k2j9qx", "a", "work-laptop"])
def test_valid_machine_names(name: str) -> None:
    assert machine.validate(name) == name


@pytest.mark.parametrize("name", ["Laptop", "laptop-2", "-x", "x-", "has space", "a" * 41, ""])
def test_invalid_machine_names(name: str) -> None:
    with pytest.raises(DocketError):
        machine.validate(name)


@pytest.mark.parametrize(
    ("host", "name"),
    [("DESKTOP-4K2J9QX", "desktop-4k2j9qx"), ("LAPTOP-2", "laptop-2-m"), ("my_box.local", "my-box-local")],
)
def test_names_from_host_names_are_valid(host: str, name: str) -> None:
    assert machine.from_host(host) == name
    machine.validate(name)


def test_the_machine_comes_from_env_then_the_clone_then_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    assert machine.resolve(tmp_path) == "test-box"
    monkeypatch.delenv("DOCKET_MACHINE")
    machine.remember(tmp_path, "work-laptop")
    assert machine.resolve(tmp_path) == "work-laptop"
    monkeypatch.setattr("socket.gethostname", lambda: "HOST-A")
    assert machine.resolve(None) == "host-a"
