from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import make_config, sh
from pydantic import ValidationError

from docket.cli import main
from docket.errors import DocketError
from docket.vault import machine
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.home import Home


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


@pytest.mark.parametrize("name", ["..", "../..", "a/b", "Plaud", "plaud.", ".plaud", "pl aud", ""])
def test_a_source_name_that_could_build_a_path_elsewhere_is_refused(name: str) -> None:
    data = {**make_config().model_dump(mode="json"), "sources": {name: {"sensitivity": "mixed"}}}
    with pytest.raises(ValidationError, match="isn't a source name"):
        VaultConfig.model_validate(data)


def test_the_wizard_s_sources_are_checked_the_same_way(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    flags = ["--owner", "Ada Example", "--email", "ada@example.invalid", "--time-zone", "Europe/Budapest"]
    args = ["init", "--yes", "--path", str(tmp_path / "vault"), "--remote", "none", *flags, "--source", "Plaud"]
    assert main(args) == 2
    assert "isn't a source name" in capsys.readouterr().err
    assert not (tmp_path / "vault").exists()


def test_a_source_s_mapper_tools_and_media_hosts_round_trip(tmp_path: Path) -> None:
    config = make_config()
    config.sources["plaud"] = SourceConfig(
        sensitivity="mixed",
        mapper=".claude/skills/sources/plaud/mapper.py",
        tools=["list_files", "get_transcript"],
        media_hosts=["media.example.invalid"],
    )
    path = tmp_path / "vault.yaml"
    path.write_text(config.dump(), encoding="utf-8")
    assert VaultConfig.load(path) == config


def test_a_file_written_before_the_source_keys_reads_unchanged(tmp_path: Path) -> None:
    keys = ("mapper: null", "tools: []", "media_hosts: []")
    text = "".join(f"{line}\n" for line in make_config().dump().splitlines() if line.strip() not in keys)
    assert "mapper" not in text
    path = tmp_path / "vault.yaml"
    path.write_text(text, encoding="utf-8")
    loaded = VaultConfig.load(path)
    assert loaded == make_config()
    assert loaded.sources["calls"].mapper is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mapper", "/abs/mapper.py", "relative to the consuming repo"),
        ("mapper", "C:/repo/mapper.py", "relative to the consuming repo"),
        ("mapper", ".claude\\skills\\sources\\plaud\\mapper.py", "written with /"),
        ("mapper", ".claude/skills/sources/plaud/../../../x.py", "with no \\.\\."),
        ("mapper", ".claude/skills/sources/plaud/mapper.sh", "names a .py file"),
        ("tools", ["mcp__plaud__list_files"], "without the host's mcp__ prefix"),
        ("tools", ["list files"], "isn't a tool's own name"),
        ("media_hosts", ["https://media.example.invalid"], "no scheme, port or path"),
        ("media_hosts", ["media.example.invalid:443"], "no scheme, port or path"),
        ("media_hosts", ["Media.Example.invalid"], "lowercase"),
    ],
)
def test_bad_source_keys_are_refused(field: str, value: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        SourceConfig.model_validate({field: value})


def test_a_key_given_twice_is_refused_not_quietly_replaced(tmp_path: Path) -> None:
    path = tmp_path / "vault.yaml"
    text = make_config().dump().replace("  people: []\n", "  people:\n  - U0KID\n  people: []\n")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DocketError, match="'people' is given twice"):
        VaultConfig.load(path)


def test_the_loader_stays_safe_and_reports_an_unusable_key(tmp_path: Path) -> None:
    path = tmp_path / "vault.yaml"
    path.write_text("owner: !!python/object/apply:os.getcwd []\n", encoding="utf-8")
    with pytest.raises(DocketError, match="not valid YAML"):
        VaultConfig.load(path)
    path.write_text("? [a, b]\n: 1\n", encoding="utf-8")
    with pytest.raises(DocketError, match="unhashable"):
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
    sh(tmp_path, "git", "init", "-q")
    home = Home.resolve(tmp_path, "repo")
    assert machine.resolve(home.private) == "test-box"
    monkeypatch.delenv("DOCKET_MACHINE")
    machine.remember(home, "work-laptop")
    assert machine.resolve(home.private) == "work-laptop"
    monkeypatch.setattr("socket.gethostname", lambda: "HOST-A")
    assert machine.resolve(None) == "host-a"
