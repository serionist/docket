"""The redaction settings in vault.yaml: credentials on and phone numbers off by default, each user pattern on or off,
and the one switch files written before held. Secret-shaped strings are assembled from parts, as in test_redact.py."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from docket_testkit import make_config, make_record

from docket.errors import DocketError
from docket.vault.config import Redaction, RedactionPattern, VaultConfig
from docket.vault.redact import Redactor

HIDDEN = "hunter2-Example"
PHONE = "+1 555 010 0199"
NEW_FORM = "  credentials: true\n  phone: false\n"


def legacy(text: str, builtin: str = "true") -> str:
    """A vault.yaml as Docket wrote it before the redaction switches were split."""
    assert NEW_FORM in text
    return text.replace(NEW_FORM, f"  builtin: {builtin}\n")


def load(tmp_path: Path, text: str) -> VaultConfig:
    path = tmp_path / "vault.yaml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return VaultConfig.load(path)


def redacted(config: Redaction, text: str) -> tuple[str, Counter[str]]:
    counts = Counter[str]()
    return Redactor.from_config(config).text(text, counts), counts


def test_a_file_without_the_keys_loads_with_credentials_on_and_phone_off(tmp_path: Path) -> None:
    text = make_config().dump().replace(NEW_FORM, "")
    assert "credentials" not in text
    loaded = load(tmp_path, text)
    assert (loaded.redaction.credentials, loaded.redaction.phone) == (True, False)


@pytest.mark.parametrize(("builtin", "credentials"), [("true", True), ("false", False)])
def test_the_old_builtin_switch_reads_as_credentials(tmp_path: Path, builtin: str, credentials: bool) -> None:
    loaded = load(tmp_path, legacy(make_config().dump(), builtin))
    assert (loaded.redaction.credentials, loaded.redaction.phone) == (credentials, False)


def test_builtin_beside_credentials_is_refused_naming_both(tmp_path: Path) -> None:
    text = make_config().dump().replace(NEW_FORM, f"  builtin: true\n{NEW_FORM}")
    with pytest.raises(DocketError, match="builtin is the old name of credentials"):
        load(tmp_path, text)


def test_a_redaction_block_that_isn_t_a_map_is_refused(tmp_path: Path) -> None:
    text = make_config().dump().replace(f"redaction:\n{NEW_FORM}  patterns: []\n", "redaction: []\n")
    with pytest.raises(DocketError, match="redaction"):
        load(tmp_path, text)


def test_the_dump_writes_the_new_keys_and_never_builtin(tmp_path: Path) -> None:
    config = make_config()
    config.redaction = Redaction(phone=True, patterns=[RedactionPattern(name="door", pattern="x", enabled=False)])
    text = config.dump()
    assert "builtin" not in text
    assert "  phone: true\n" in text
    assert "    enabled: false\n" in text
    assert load(tmp_path, text) == config


def test_a_file_in_either_form_counts_as_docket_s_own() -> None:
    config = make_config()
    assert config.written_by_docket(config.dump())
    assert config.written_by_docket(legacy(config.dump()))
    assert not config.written_by_docket(config.dump().replace("sources:\n", "sources:  # mine\n"))
    config.redaction.phone = True
    assert config.redaction.legacy() is None
    assert not config.written_by_docket(legacy(make_config().dump()))


def test_the_defaults_redact_credentials_and_keep_phone_numbers() -> None:
    out, counts = redacted(Redaction(), f"password: {HIDDEN}, call {PHONE}")
    assert out == f"password: [redacted:password], call {PHONE}"
    assert counts == Counter({"password": 1})
    record, _ = Redactor.from_config(Redaction()).record(make_record(native={"password": HIDDEN, "mobile": PHONE}))
    assert record.native == {"password": "[redacted:password]", "mobile": PHONE}


def test_phone_on_redacts_both_and_credentials_off_only_phone_numbers() -> None:
    both, _ = redacted(Redaction(phone=True), f"password: {HIDDEN}, call {PHONE}")
    assert both == "password: [redacted:password], call [redacted:phone]"
    phones, _ = redacted(Redaction(credentials=False, phone=True), f"password: {HIDDEN}, call {PHONE}")
    assert phones == f"password: {HIDDEN}, call [redacted:phone]"
    record, _ = Redactor.from_config(Redaction(credentials=False, phone=True)).record(
        make_record(native={"password": HIDDEN, "mobile": PHONE})
    )
    assert record.native == {"password": HIDDEN, "mobile": "[redacted:phone]"}


def test_a_disabled_pattern_never_runs() -> None:
    patterns = [
        RedactionPattern(name="door", pattern=r"door code (?P<secret>\d+)", enabled=False),
        RedactionPattern(name="project", pattern=r"PRJ-\d+"),
    ]
    out, counts = redacted(Redaction(patterns=patterns), "door code 4711 for PRJ-12")
    assert out == "door code 4711 for [redacted:project]"
    assert counts == Counter({"project": 1})


def test_a_name_is_never_redacted_whatever_is_on() -> None:
    every = Redaction(phone=True)
    text = "Ben Sample called Cleo Test about Northwind"
    assert redacted(every, text) == (text, Counter())
