from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import keyring
import pytest
from docket_testkit import TEST_GITCONFIG, AtConsole, FakePrompt, make_config, make_record, use_factory
from keyring.backends.fail import Keyring as FailKeyring

from docket import console, osprompt
from docket.models import voyage
from docket.vault.config import VaultConfig
from docket.vault.records import RawRecord

# The session's own folders, set before any fixture runs: tests never touch the real Docket folders, not even to read
# them, and a module's fixtures run before `isolated` gives each test folders of its own.
HOME_VAR = "DOCKET_TEST_HOME"


def pytest_configure(config: pytest.Config) -> None:
    home = Path(tempfile.mkdtemp(prefix="docket-tests-"))
    gitconfig = home / "gitconfig"
    gitconfig.write_text(TEST_GITCONFIG, encoding="utf-8")
    os.environ.update(
        {
            HOME_VAR: str(home),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "DOCKET_CONFIG_DIR": str(home / "docket"),
            "DOCKET_STATE_DIR": str(home / "docket-state"),
            "DOCKET_MODELS_DIR": str(home / "docket-models"),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
        }
    )
    os.environ.pop("DOCKET_VAULT", None)
    keyring.set_keyring(FailKeyring())


def pytest_unconfigure(config: pytest.Config) -> None:
    home = os.environ.pop(HOME_VAR, None)
    if home is not None:
        shutil.rmtree(home, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def kit_templates(tmp_path_factory: pytest.TempPathFactory) -> None:
    """The kit's builders find the session's shared templates through this factory (DR-0531)."""
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
    # The models' environments and files, never the developer's own.
    monkeypatch.setenv("DOCKET_MODELS_DIR", str(home / "docket-models"))
    # No test may reach the developer's real keychain: keyring's failing backend, here and in any subprocess.
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    keyring.set_keyring(FailKeyring())
    monkeypatch.setenv("DOCKET_MACHINE", "test-box")
    monkeypatch.delenv("DOCKET_VAULT", raising=False)
    # Claude Code sets it in the shells it runs, and a release refuses to run under it: tests set it where they mean to.
    monkeypatch.delenv("CLAUDECODE", raising=False)
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(name, raising=False)


# The tests that call the system's own prompt checks run only where this is set: CI, never a developer's machine.
OSPROMPT_VAR = "DOCKET_TEST_OSPROMPT"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(OSPROMPT_VAR):
        return
    skip = pytest.mark.skip(reason=f"calls the system's prompt checks: set {OSPROMPT_VAR}, as CI does")
    for item in items:
        if item.get_closest_marker("osprompt") is not None:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_os_prompt(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches the system's prompt or its checks (DR-0572): the console code everywhere, unless a test installs
    a fake prompt (`os_prompt`) or is marked `osprompt`, which only CI runs."""
    if request.node.get_closest_marker("osprompt") is not None:
        return
    monkeypatch.setattr(osprompt, "find", lambda: osprompt.NoPrompt("no OS prompt in tests"))
    monkeypatch.setattr(osprompt, "status", lambda: ["the console code: no OS prompt in tests"])


@pytest.fixture
def os_prompt(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakePrompt]:
    """A prompt set up on this machine, answering each question from the script given: Verified() or Refused(why)."""

    def install(*answers: osprompt.Answer) -> FakePrompt:
        fake = FakePrompt(*answers)
        monkeypatch.setattr(osprompt, "find", lambda: osprompt.Prompt("Fake Hello", fake))
        return fake

    return install


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API provider's one network path fails every test that reaches it: a test that means to send installs its
    own fake (`models_kit.FakeVoyage`). No test makes a real call or holds a real key."""

    def refused(*_: object, **__: object) -> dict[str, object]:
        raise AssertionError("a test reached Voyage's real network path; install models_kit.FakeVoyage")

    monkeypatch.setattr(voyage, "send", refused)


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., AtConsole]]:
    """The owner at their own console, for a release or a vault.yaml change that isn't only stricter: no refusal, and
    each question answered from the script given."""

    def at_console(*answers: str | Callable[[], str]) -> AtConsole:
        person = AtConsole(*answers)
        monkeypatch.setattr(console, "problem", lambda act: None)
        monkeypatch.setattr(console, "ask", person)
        return person

    yield at_console


@pytest.fixture
def config() -> VaultConfig:
    return make_config()


@pytest.fixture
def records() -> Callable[..., RawRecord]:
    return make_record
