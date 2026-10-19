"""Tests never touch the real Docket folders, not even to read them: a module's fixtures, which run before each test's
own folders are set, already see the session's."""

from __future__ import annotations

import os
from pathlib import Path

import keyring
import pytest
from docket_testkit import HOME_VAR
from keyring.backends.fail import Keyring as FailKeyring

from docket import userconfig
from docket.models import config


@pytest.fixture(scope="module")
def before_any_test() -> dict[str, Path]:
    return {
        "config": userconfig.config_path(),
        "state": userconfig.state_dir(),
        "log": userconfig.log_dir(),
        "cpu environment": config.env_dir("cpu"),
        "model files": config.files_dir(),
    }


def test_a_modules_fixtures_see_the_sessions_folders_and_never_the_users(before_any_test: dict[str, Path]) -> None:
    home = Path(os.environ[HOME_VAR])
    for name, path in before_any_test.items():
        assert path.is_relative_to(home), f"{name}: {path}"
    assert "DOCKET_VAULT" not in os.environ
    assert os.environ["PYTHON_KEYRING_BACKEND"] == "keyring.backends.fail.Keyring"
    assert isinstance(keyring.get_keyring(), FailKeyring)


def test_each_test_has_folders_of_its_own(before_any_test: dict[str, Path]) -> None:
    assert userconfig.config_path() != before_any_test["config"]
    assert config.env_dir("cpu") != before_any_test["cpu environment"]
