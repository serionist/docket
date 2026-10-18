"""Calendar's tests: they run where this folder lives, with or without Docket importable."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

HERE = Path(__file__).resolve().parent
# The tests' own helpers (calendar_payloads) import by name, wherever the folder lives.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
# The tests that run the mapper through Docket's own checks need docket importable; where it isn't, they are left out
# and the mapper's own tests still run.
collect_ignore = [] if importlib.util.find_spec("docket") else ["test_with_docket.py"]


@pytest.fixture(autouse=True)
def isolated(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Git with an empty config of its own, so no developer's identity, signing or hooks change a result; Docket's
    config and state in temp folders, the failing keyring and no DOCKET_VAULT, so no test reaches the user's own
    Docket, its keychain entry or their vault."""
    home = tmp_path_factory.mktemp("home")
    (home / "gitconfig").write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("DOCKET_CONFIG_DIR", str(home / "docket"))
    monkeypatch.setenv("DOCKET_STATE_DIR", str(home / "docket-state"))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    monkeypatch.setenv("DOCKET_MACHINE", "test-box")
    monkeypatch.delenv("DOCKET_VAULT", raising=False)
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def mapper() -> ModuleType:
    """This folder's mapper.py, loaded by path: it is a file in a skill folder, not a package."""
    spec = importlib.util.spec_from_file_location("calendar_mapper", HERE.parent / "mapper.py")
    if spec is None or spec.loader is None:  # pragma: no cover - the file is right there
        raise RuntimeError("can't load mapper.py")
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses look their module up by name, as they do when Docket runs it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
