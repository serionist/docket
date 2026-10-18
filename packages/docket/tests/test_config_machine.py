from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import empty_vault, git, make_config, sh
from pydantic import ValidationError

from docket import userconfig
from docket.cli import main
from docket.errors import DocketError
from docket.vault import machine, settings
from docket.vault.config import CONFIG_FILE, SourceConfig, VaultConfig
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


def test_a_source_s_mapper_tools_and_media_folders_round_trip(tmp_path: Path) -> None:
    config = make_config()
    config.sources["plaud"] = SourceConfig(
        sensitivity="mixed",
        mapper=".claude/skills/sources/plaud/mapper.py",
        tools=["list_files", "get_transcript"],
        media_folders=["~/Downloads/Calls"],
    )
    path = tmp_path / "vault.yaml"
    path.write_text(config.dump(), encoding="utf-8")
    assert VaultConfig.load(path) == config
    # Written only when set, so a vault.yaml without media folders reads as before on an older Docket.
    assert "media_folders" not in make_config().dump()


@pytest.mark.parametrize("days", [0, 1, 30])
def test_a_source_s_lookback_round_trips_and_is_written_only_when_set(tmp_path: Path, days: int) -> None:
    config = make_config()
    config.sources["chat"] = SourceConfig(tools=["list_chats"], lookback_days=days)
    path = tmp_path / "vault.yaml"
    path.write_text(config.dump(), encoding="utf-8")
    assert f"    lookback_days: {days}\n" in config.dump()
    assert VaultConfig.load(path) == config
    assert "lookback_days" not in make_config().dump()


@pytest.mark.parametrize("value", [-1, 31, "7", 1.5, True])
def test_a_lookback_that_isn_t_a_whole_number_of_days_from_0_to_30_is_refused(value: object) -> None:
    with pytest.raises(ValidationError, match="lookback_days"):
        SourceConfig.model_validate({"lookback_days": value})


def test_media_hosts_from_before_any_https_host_is_read_and_ignored(tmp_path: Path) -> None:
    """A source's old `media_hosts`, any list, reads as if it weren't there, and Docket never writes it; a file Docket
    wrote while it had one is still in Docket's form (DR-0604)."""
    config = make_config()
    old = config.dump().replace("  tools: []\n", "  tools: []\n    media_hosts: []\n")
    assert old.count("media_hosts: []") == len(config.sources)
    assert config.written_by_docket(old)
    path = tmp_path / "vault.yaml"
    path.write_text(old.replace("media_hosts: []", "media_hosts: [media.example, 42]", 1), encoding="utf-8")
    loaded = VaultConfig.load(path)
    assert loaded == config and "media_hosts" not in loaded.dump()
    assert not config.written_by_docket(path.read_text(encoding="utf-8"))


def test_an_old_docket_form_with_hosts_listed_is_still_docket_s_own(tmp_path: Path) -> None:
    """Docket wrote a source's hosts as a block list after its tools: such a file, its redaction block from before the
    switches were split too, is Docket's form whatever hosts it lists, so the next write is whole and drops the key."""
    config = make_config()
    old = config.dump().replace("  tools: []\n", "  tools: []\n    media_hosts: []\n")
    listed = old.replace("    media_hosts: []\n", "    media_hosts:\n    - media.example\n    - 42\n", 1)
    assert config.written_by_docket(listed)
    assert config.written_by_docket(listed.replace("  credentials: true\n  phone: false\n", "  builtin: true\n"))
    assert not config.written_by_docket(listed.replace("    - 42\n", "    - 42   # mine\n"))
    vault = empty_vault(tmp_path, config)
    path = vault.root / CONFIG_FILE
    path.write_text(listed, encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qam", "An old vault.yaml")
    phone = config.redaction.model_copy(update={"phone": True})
    assert settings.rewrite(vault, lambda c: c.model_copy(update={"redaction": phone}), "Config: a", via="cli").changed
    assert path.read_text(encoding="utf-8") == config.model_copy(update={"redaction": phone}).dump()


@pytest.mark.parametrize(
    "text", ["storage: [repo\n", "- storage\n", "storage: repo\n", "sources: 3\n", "sources:\n  chat: media_hosts\n"]
)
def test_a_text_that_doesn_t_read_as_settings_is_never_docket_s_form(text: str) -> None:
    assert not make_config().written_by_docket(text)


@pytest.mark.parametrize("written", ["work", "", "[get_memo]"])
def test_a_source_that_isn_t_a_mapping_is_refused_by_name(tmp_path: Path, written: str) -> None:
    """Old keys are taken out of a source's mapping only: anything else under its name is refused, as before."""
    text = make_config().dump()
    start, end = text.index("  chat:\n"), text.index("  calls:\n")
    path = tmp_path / "vault.yaml"
    path.write_text(f"{text[:start]}  chat: {written}\n{text[end:]}", encoding="utf-8")
    with pytest.raises(DocketError, match=r"sources.chat: Input should be a valid dictionary"):
        VaultConfig.load(path)


def test_a_file_written_before_the_source_keys_reads_unchanged(tmp_path: Path) -> None:
    keys = ("mapper: null", "tools: []")
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
        ("media_folders", ["C:/Users/x"], "an absolute path"),
        ("media_folders", ["~/a/../../b"], r"with no \.\."),
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


# -- a folder source's keys (DR-0481, DR-0490) ----------------------------------------------------------------------


def test_a_folder_source_round_trips_and_a_vault_without_one_writes_neither_key(tmp_path: Path) -> None:
    assert "folder" not in make_config().dump() and "exclude" not in make_config().dump()
    config = make_config()
    config.sources["documents"] = SourceConfig(folder="~/Work", exclude=["archive/*", "*.draft.md"])
    path = tmp_path / "vault.yaml"
    path.write_text(config.dump(), encoding="utf-8")
    assert VaultConfig.load(path) == config
    assert "folder: ~/Work" in config.dump()
    for value in ("docs", "docs/papers", ".", "~", "~/Work/2026"):
        assert SourceConfig(folder=value).folder == value
    # A hand-written `folder: null` is no folder.
    assert SourceConfig.model_validate({"folder": None}) == SourceConfig()


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"folder": "/home/ada/Work"}, "is an absolute path, which names one machine's drive"),
        ({"folder": "C:/Users/ada/Work"}, "give a machine whose folder is elsewhere its own path"),
        ({"folder": "D:Work"}, "is an absolute path"),
        ({"folder": "//server/share/Work"}, "is an absolute path"),
        ({"folder": r"docs\papers"}, "written with /"),
        ({"folder": "~ada/Work"}, "another user's isn't taken"),
        ({"folder": "~//Work"}, "is an absolute path"),
        ({"folder": "~/C:/Work"}, "is an absolute path"),
        ({"folder": "docs/../../elsewhere"}, r"with no \.\."),
        ({"folder": " "}, r"with no \.\."),
        ({"folder": "docs", "sensitivity": "mixed"}, "mixed would hold every page on the Staging page"),
        ({"folder": "docs", "sensitivity": "private"}, "set enabled: false instead"),
        ({"folder": "docs", "mapper": ".claude/skills/sources/x/mapper.py"}, "has no mapper or tools"),
        ({"folder": "docs", "tools": ["list_files"]}, "has no mapper or tools"),
        ({"exclude": ["archive/*"]}, "exclude goes with folder"),
        ({"folder": "docs", "exclude": [" "]}, "never empty"),
    ],
)
def test_a_folder_source_s_bad_keys_are_refused_with_their_fix(entry: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        SourceConfig.model_validate(entry)


def test_this_machine_s_folders_come_from_the_user_s_config(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    assert userconfig.folders() == {}
    userconfig.set_section("folders", {str(vault): {"documents": str(tmp_path / "Work")}})
    assert userconfig.folders() == {vault.resolve(): {"documents": tmp_path / "Work"}}
    assert userconfig.folder_for(vault, "documents") == tmp_path / "Work"
    assert userconfig.folder_for(vault, "other") is None
    assert userconfig.folder_for(tmp_path, "documents") is None


@pytest.mark.parametrize(
    ("block", "message"),
    [
        (["a"], "must be a map from a vault's folder to its sources' folders"),
        ({"vault": ["a"]}, "must be a vault's folder, with a map of its sources' folders"),
        ({"vault": {"documents": "Work"}}, "documents must be an absolute folder"),
        ({"vault": {"documents": 3}}, "documents must be an absolute folder"),
    ],
)
def test_a_folders_block_of_the_wrong_kind_is_refused(block: object, message: str) -> None:
    userconfig.set_section("folders", block)  # type: ignore[arg-type]
    with pytest.raises(DocketError, match=message):
        userconfig.folders()
