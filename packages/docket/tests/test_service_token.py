from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from docket import userconfig
from docket.errors import DocketError
from docket.service import system
from docket.service.token import ROTATE, SERVICE, USER, Tokens

ON_WINDOWS = os.name == "nt"
# The tests that read a file's mode run as the system they run on; the rest pretend to be Windows or macOS.
HERE = "Windows" if ON_WINDOWS else "Darwin"


class Memory:
    """A keychain that keeps what it is given."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password


class Refusing(Memory):
    """A keychain that is there and refuses every write, as Windows does with CredWrite error 1312."""

    def set_password(self, service: str, username: str, password: str) -> None:
        raise OSError(1312, "A specified logon session does not exist")


class Forgetful(Memory):
    """A keychain that takes a write and gives back something else."""

    def get_password(self, service: str, username: str) -> str | None:
        return "something-else"


class Gone(Memory):
    """A keychain that worked once and now raises on every call."""

    def get_password(self, service: str, username: str) -> str | None:
        raise RuntimeError("the Secret Service went away")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError("the Secret Service went away")


class Commands:
    """A runner that answers from a table and records every command."""

    def __init__(self, answers: dict[str, tuple[int, str]] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        code, out = self.answers.get(args[0], (0, ""))
        return subprocess.CompletedProcess(list(args), code, out, "")


FAKE_WHOAMI = (0, '"EXAMPLE\\ada","S-1-5-21-1111111111-2222222222-3333333333-1001"\n')
# On Windows the token file's DACL is set for real, so it must name the real user, or the test could no longer read it.
WHOAMI = (0, system.run(["whoami", "/user", "/fo", "csv", "/nh"]).stdout) if ON_WINDOWS else FAKE_WHOAMI


def tokens(backend: Memory | None = None, system_name: str = HERE, runner: Commands | None = None) -> Tokens:
    return Tokens(backend or Memory(), system=system_name, runner=runner or Commands({"whoami": WHOAMI}))


# -- choosing the store once -----------------------------------------------------------------------------------------


def test_install_records_the_keychain_when_a_write_reads_back() -> None:
    keychain = Memory()
    token = tokens(keychain).ensure()
    assert (token.store, token.written) == ("keyring", True)
    assert keychain.items[(SERVICE, USER)] == token.value
    assert userconfig.service().store == "keyring"
    assert not Tokens.file().exists()


@pytest.mark.parametrize("keychain", [Refusing(), Forgetful(), None], ids=["refuses", "forgets", "none"])
def test_install_records_the_file_when_the_keychain_cant_be_used(keychain: Memory | None) -> None:
    # None: conftest's failing backend, what keyring gives when there is no keychain at all.
    found = Tokens(keychain, system=HERE, runner=Commands({"whoami": WHOAMI}))
    token = found.ensure()
    assert token.store == "file"
    assert userconfig.service().store == "file"
    assert Tokens.file().read_text(encoding="utf-8").strip() == token.value
    assert found.read() == token.value


def test_on_linux_the_keychain_counts_only_with_the_user_managers_session_bus() -> None:
    no_bus = Commands({"systemctl": (0, "HOME=/home/ada\nPATH=/usr/bin\n")})
    assert tokens(Memory(), "Linux", no_bus).ensure().store == "file"
    assert no_bus.calls == [["systemctl", "--user", "show-environment"]]
    userconfig.config_path().unlink()
    Tokens.file().unlink()
    bus = Commands({"systemctl": (0, "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus\n")})
    assert tokens(Memory(), "Linux", bus).ensure().store == "keyring"


def test_on_linux_without_systemd_the_keychain_doesnt_count() -> None:
    missing = Commands({"systemctl": (127, "")})
    assert tokens(Memory(), "Linux", missing).ensure().store == "file"


# -- reading: the recorded store, never a new token ------------------------------------------------------------------


def test_nothing_recorded_is_refused_and_nothing_is_made() -> None:
    with pytest.raises(DocketError, match="docket service install") as e:
        tokens().read()
    assert ROTATE in str(e.value)
    assert userconfig.service().store is None
    assert not Tokens.file().exists()


def test_read_uses_only_the_recorded_store() -> None:
    keychain = Memory()
    made = tokens(keychain).ensure()
    # A file another process left beside it is never read while the keychain is the recorded store.
    system.write_private(Tokens.file(), "a-stale-token\n", system=HERE, runner=Commands({"whoami": WHOAMI}))
    assert tokens(keychain).read() == made.value


def test_a_recorded_keychain_that_is_gone_names_the_way_out_and_makes_nothing() -> None:
    tokens(Memory()).ensure()
    with pytest.raises(DocketError, match="the system keychain") as e:
        tokens(Gone()).read()
    assert ROTATE in str(e.value)
    assert userconfig.service().store == "keyring"
    assert not Tokens.file().exists()


def test_a_recorded_file_that_is_gone_names_the_way_out() -> None:
    tokens(Refusing()).ensure()
    Tokens.file().unlink()
    with pytest.raises(DocketError, match=ROTATE):
        tokens(Refusing()).read()
    assert not Tokens.file().exists()


def test_an_empty_store_is_refused() -> None:
    tokens(Refusing()).ensure()
    Tokens.file().write_text("\n", encoding="utf-8")
    with pytest.raises(DocketError, match="empty"):
        tokens(Refusing()).read()
    userconfig.set_service(store="keyring")
    with pytest.raises(DocketError, match="holds no token"):
        tokens(Memory()).read()


# -- rotating, and choosing again --------------------------------------------------------------------------------------


def test_install_keeps_a_token_the_store_gives_back() -> None:
    keychain = Memory()
    first = tokens(keychain).ensure()
    again = tokens(keychain).ensure()
    assert (again.value, again.written) == (first.value, False)


def test_rotate_writes_a_new_token_to_the_recorded_store() -> None:
    keychain = Memory()
    first = tokens(keychain).ensure()
    rotated = tokens(keychain).rotate()
    assert rotated.store == "keyring"
    assert rotated.written
    assert rotated.value != first.value
    assert tokens(keychain).read() == rotated.value


def test_rotate_with_nothing_recorded_chooses_as_install_does() -> None:
    token = tokens(Refusing()).rotate()
    assert token.store == "file"
    assert userconfig.service().store == "file"


def test_a_keychain_gone_for_good_is_chosen_again_by_install_and_rotate() -> None:
    tokens(Memory()).ensure()
    moved = tokens(Gone()).ensure()
    assert (moved.store, moved.moved_from, moved.written) == ("file", "keyring", True)
    assert userconfig.service().store == "file"
    assert tokens(Gone()).read() == moved.value
    userconfig.set_service(store="keyring")
    rotated = tokens(Gone()).rotate()
    assert (rotated.store, rotated.moved_from) == ("file", "keyring")


def test_a_token_file_that_cant_be_written_is_chosen_again() -> None:
    tokens(Refusing()).ensure()
    Tokens.file().unlink()
    Tokens.file().mkdir()  # a folder where the file was: neither readable nor writable as one
    rotated = tokens(Memory()).rotate()
    assert (rotated.store, rotated.moved_from) == ("keyring", "file")
    assert userconfig.service().store == "keyring"


def test_choosing_the_same_store_again_isnt_a_move() -> None:
    tokens(Refusing()).ensure()
    Tokens.file().unlink()
    token = tokens(Refusing()).ensure()
    assert (token.store, token.written, token.moved_from) == ("file", True, None)


# -- the file ----------------------------------------------------------------------------------------------------------


@pytest.mark.skipif(ON_WINDOWS, reason="the mode means nothing on Windows; the DACL test covers it")
def test_the_token_file_is_made_0600() -> None:
    tokens(Refusing(), "Linux", Commands()).rotate()
    assert Tokens.file().stat().st_mode & 0o777 == 0o600


def test_a_token_file_others_can_read_is_refused_on_macos_and_linux() -> None:
    tokens(Refusing(), "Linux", Commands()).rotate()
    # On Windows every file reads as 0o666 or 0o444, so pretending to be Linux sees it as open to others.
    Tokens.file().chmod(0o644)
    with pytest.raises(DocketError, match="others can read"):
        tokens(Refusing(), "Linux", Commands()).read()


def test_on_windows_the_file_gets_a_dacl_naming_only_the_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_for: list[tuple[str, str]] = []
    monkeypatch.setattr(system, "set_owner_only_dacl", lambda path, sid: set_for.append((path.name, sid)))
    runner = Commands({"whoami": FAKE_WHOAMI})
    system.write_private(tmp_path / "secret", "x\n", system="Windows", runner=runner)
    assert runner.calls == [["whoami", "/user", "/fo", "csv", "/nh"]]
    # Made private while still empty, under its temp name, before the text goes in.
    assert set_for == [(f".secret.{os.getpid()}.tmp", "S-1-5-21-1111111111-2222222222-3333333333-1001")]
    assert (tmp_path / "secret").read_text(encoding="utf-8") == "x\n"
    assert not system.others_can_read(tmp_path / "secret", system="Windows")


@pytest.mark.skipif(not ON_WINDOWS, reason="reads a real DACL")
def test_the_real_dacl_lists_only_the_user(tmp_path: Path) -> None:
    target = tmp_path / "secret"
    system.write_private(target, "x\n", system="Windows")
    shown = system.run(["icacls", str(target)]).stdout
    entries = [line.replace(str(target), "").strip() for line in shown.splitlines() if ":(" in line]
    assert len(entries) == 1, shown
    assert "(I)" not in shown
    assert entries[0].endswith(":(F)")


def test_a_failing_dacl_or_whoami_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(path: Path, sid: str) -> None:
        raise DocketError(f"can't make {path} private to this user: Windows error 5")

    monkeypatch.setattr(system, "set_owner_only_dacl", refused)
    with pytest.raises(DocketError, match="Windows error 5"):
        system.write_private(tmp_path / "s", "x", system="Windows", runner=Commands({"whoami": FAKE_WHOAMI}))
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(DocketError, match="SID"):
        system.windows_sid(Commands({"whoami": (1, "")}))
    with pytest.raises(DocketError, match="SID"):
        system.windows_sid(Commands({"whoami": (0, '"EXAMPLE\\ada","not-a-sid"\n')}))


def test_a_missing_program_reads_as_exit_127() -> None:
    assert system.run(["docket-no-such-program-here"]).returncode == 127


def test_the_header_is_the_json_headers_helper_prints() -> None:
    keychain = Memory()
    token = tokens(keychain).ensure()
    assert json.loads(tokens(keychain).header()) == {"Authorization": f"Bearer {token.value}"}


def test_where_names_the_store() -> None:
    assert tokens().where() == "nowhere yet"
    assert tokens().where("keyring") == "the system keychain"
    assert tokens().where("file") == str(Tokens.file())


def test_the_sid_is_asked_once_a_process_and_only_through_the_default_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process's user never changes, so its SID is kept; a runner passed in is asked each time, and its answer is
    never handed to another runner (DR-0767)."""
    monkeypatch.setattr(system, "_SID", [])
    asked: list[str] = []

    def whoami(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        asked.append(args[0])
        return subprocess.CompletedProcess(args, 0, FAKE_WHOAMI[1], "")

    monkeypatch.setattr(subprocess, "run", whoami)
    ours = "S-1-5-21-1111111111-2222222222-3333333333-1001"
    assert system.windows_sid() == system.windows_sid() == ours
    assert asked == ["whoami"]
    other = Commands({"whoami": (0, '"EXAMPLE\\ben","S-1-5-21-4444444444-5555555555-6666666666-1002"\n')})
    assert system.windows_sid(other) == system.windows_sid(other) == "S-1-5-21-4444444444-5555555555-6666666666-1002"
    assert len(other.calls) == 2
    assert system.windows_sid() == ours
    assert asked == ["whoami"]
