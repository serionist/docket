from __future__ import annotations

import json
import os
import plistlib
import subprocess
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from docket import userconfig
from docket.errors import DocketError
from docket.service import client, install
from docket.service.install import ServiceStatus
from docket.service.platforms import (
    Launch,
    Linux,
    Mac,
    RunKey,
    Windows,
    agent_plist,
    task_xml,
    unit_quote,
    user_unit,
)
from docket.service.token import Tokens
from docket.vault.config import VaultConfig
from docket.vault.vault import Vault

GOLDEN = Path(__file__).parent / "fixtures" / "service"
SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
WHOAMI = f'"EXAMPLE\\ada","{SID}"\n'
TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
# A home folder with a space, a percent sign and accented letters: every quoting rule meets it.
HOME = "Árvíztűrő Teszt %USERNAME% $HOME"


def launch(base: str = f"/home/{HOME}", python: str = "venv/bin/python") -> Launch:
    sep = "\\" if "\\" in base else "/"
    return Launch(
        python=f"{base}{sep}{python}",
        args=("-X", "utf8", "-m", "docket", "serve", "--env-file", sep.join([base, "docket", "service-env.json"])),
        log_dir=sep.join([base, "docket", "log"]),
    )


Answer = tuple[int, str] | Callable[[list[str]], tuple[int, str]]


class Commands:
    """A runner that answers from a table keyed by the command's first words, and records every command."""

    def __init__(self, answers: dict[str, Answer] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(args)
        self.calls.append(argv)
        answer: Answer = (0, "")
        for key, found in self.answers.items():
            if " ".join(argv).startswith(key):
                answer = found
                break
        code, out = answer(argv) if callable(answer) else answer
        return subprocess.CompletedProcess(argv, code, out, "an error" if code else "")


class Memory:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


# -- Windows -----------------------------------------------------------------------------------------------------------


def test_the_task_xml_matches_its_golden_file_and_runs_on_battery() -> None:
    windows = launch(f"C:\\Users\\{HOME}", "venv\\Scripts\\pythonw.exe")
    xml = task_xml(windows, SID, datetime(2026, 10, 15, 9, 30, 12, 345))
    assert xml == (GOLDEN / "task.xml").read_text(encoding="utf-8")
    root = ET.fromstring(xml.split("\n", 1)[1])  # noqa: S314 - the task Docket itself wrote
    settings = root.find("t:Settings", TASK_NS)
    assert settings is not None
    assert settings.findtext("t:DisallowStartIfOnBatteries", namespaces=TASK_NS) == "false"
    assert settings.findtext("t:StopIfGoingOnBatteries", namespaces=TASK_NS) == "false"
    assert settings.findtext("t:MultipleInstancesPolicy", namespaces=TASK_NS) == "IgnoreNew"
    trigger = root.find("t:Triggers/t:LogonTrigger", TASK_NS)
    assert trigger is not None
    assert trigger.findtext("t:UserId", namespaces=TASK_NS) == SID
    every = root.find("t:Triggers/t:TimeTrigger", TASK_NS)
    assert every is not None
    assert every.findtext("t:Repetition/t:Interval", namespaces=TASK_NS) == "PT5M"
    assert every.findtext("t:StartBoundary", namespaces=TASK_NS) == "2026-10-15T09:30:12"
    assert root.findtext("t:Principals/t:Principal/t:UserId", namespaces=TASK_NS) == SID
    command = root.findtext("t:Actions/t:Exec/t:Command", namespaces=TASK_NS)
    assert command == f"C:\\Users\\{HOME}\\venv\\Scripts\\pythonw.exe"
    arguments = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=TASK_NS)
    assert arguments is not None
    assert arguments.startswith("-X utf8 -m docket serve --env-file ")


def windows_installer(
    runner: Commands, tmp_path: Path, registry: Memory, stopped: list[str], started: list[list[str]] | None = None
) -> Windows:
    return Windows(
        runner=runner,
        registry=lambda: registry,
        start=(started if started is not None else []).append,
        stop_pid=lambda: stopped.append("pid"),
        work=tmp_path,
        name="DocketTest",
    )


def test_windows_install_writes_utf16_xml_and_starts_the_task(tmp_path: Path) -> None:
    seen: list[bytes] = []

    def create(argv: list[str]) -> tuple[int, str]:
        seen.append(Path(argv[argv.index("/XML") + 1]).read_bytes())
        return (0, "")

    runner = Commands({"whoami": (0, WHOAMI), "schtasks /Create": create})
    registry = Memory()
    registry.set("DocketTest", "an old run key")
    how = windows_installer(runner, tmp_path / "work", registry, []).install(launch(str(tmp_path / "home")))
    assert how == "the Task Scheduler task DocketTest, at your logon"
    assert seen[0].startswith(b"\xff\xfe")
    assert "<UserId>S-1-5-21" in seen[0].decode("utf-16")
    assert [c[:2] for c in runner.calls[1:]] == [["schtasks", "/Create"], ["schtasks", "/End"], ["schtasks", "/Run"]]
    assert registry.values == {}
    assert list((tmp_path / "work").iterdir()) == []
    assert (tmp_path / "home" / "docket" / "log").is_dir()


def test_windows_falls_back_to_the_run_key_when_task_scheduler_refuses(tmp_path: Path) -> None:
    runner = Commands({"whoami": (0, WHOAMI), "schtasks /Create": (1, ""), "schtasks /Query": (1, "")})
    registry = Memory()
    stopped: list[str] = []
    started: list[list[str]] = []
    installer = windows_installer(runner, tmp_path, registry, stopped, started)
    chosen = launch(str(tmp_path / HOME), "venv/Scripts/pythonw.exe")
    how = installer.install(chosen)
    assert how.startswith("the Run key value DocketTest")
    assert "isn't restarted after a crash" in how
    assert registry.values["DocketTest"] == subprocess.list2cmdline(chosen.argv())
    assert registry.values["DocketTest"].startswith('"')
    assert stopped == ["pid"]
    assert started[0][1:3] == ["-X", "utf8"]
    assert installer.installed() == "the Run key value DocketTest"


def test_a_task_that_doesnt_start_is_refused(tmp_path: Path) -> None:
    runner = Commands({"whoami": (0, WHOAMI), "schtasks /Run": (1, "")})
    with pytest.raises(DocketError, match="didn't start it"):
        windows_installer(runner, tmp_path, Memory(), []).install(launch(str(tmp_path)))


def test_windows_stop_disables_the_task_and_ends_a_run_key_service_by_its_pid(tmp_path: Path) -> None:
    runner = Commands({"schtasks /Query": (0, "")})
    registry = Memory()
    registry.set("DocketTest", "x")
    stopped: list[str] = []
    installer = windows_installer(runner, tmp_path, registry, stopped)
    installer.stop()
    assert ["schtasks", "/End", "/TN", "DocketTest"] in runner.calls
    assert ["schtasks", "/Change", "/TN", "DocketTest", "/DISABLE"] in runner.calls
    assert stopped == ["pid"]
    assert registry.values == {}
    assert installer.installed() == "the Task Scheduler task DocketTest"


def test_windows_uninstall_deletes_the_task(tmp_path: Path) -> None:
    runner = Commands({"schtasks /Query": (0, "")})
    windows_installer(runner, tmp_path, Memory(), []).uninstall()
    assert runner.calls[-1] == ["schtasks", "/Delete", "/TN", "DocketTest", "/F"]
    nothing = Commands({"schtasks /Query": (1, "")})
    installer = windows_installer(nothing, tmp_path, Memory(), [])
    installer.uninstall()
    assert installer.installed() is None
    assert not [c for c in nothing.calls if "/Delete" in c]


@pytest.mark.skipif(os.name != "nt", reason="the Run key is Windows' own")
def test_the_real_run_key_takes_and_gives_back_a_value() -> None:
    name = f"DocketTest-{uuid.uuid4().hex[:8]}"
    key = RunKey()
    try:
        assert key.get(name) is None
        key.set(name, '"C:\\x\\pythonw.exe" -m docket serve')
        assert key.get(name) == '"C:\\x\\pythonw.exe" -m docket serve'
    finally:
        key.delete(name)
    assert key.get(name) is None
    key.delete(name)


# -- macOS -------------------------------------------------------------------------------------------------------------


def test_the_plist_matches_its_golden_file_and_restarts_on_any_unclean_exit() -> None:
    data = agent_plist(launch(f"/Users/{HOME}"), "io.github.serionist.docket-test")
    assert data == (GOLDEN / "agent.plist").read_bytes()
    parsed = plistlib.loads(data)
    assert parsed["KeepAlive"] == {"SuccessfulExit": False}
    assert parsed["RunAtLoad"] is True
    assert parsed["ProgramArguments"][1:3] == ["-X", "utf8"]


def mac(runner: Commands, tmp_path: Path) -> Mac:
    return Mac(runner=runner, home=tmp_path, uid=501, label="docket-test", sleep=lambda _s: None)


def test_mac_install_waits_for_bootout_to_finish_before_bootstrap(tmp_path: Path) -> None:
    prints = iter([0, 0, 113])
    runner = Commands({"launchctl print": lambda _a: (next(prints), "")})
    installer = mac(runner, tmp_path)
    assert installer.install(launch(str(tmp_path))) == "the LaunchAgent docket-test, at your logon"
    assert [c[1] for c in runner.calls] == ["bootout", "print", "print", "print", "bootstrap"]
    assert runner.calls[-1] == ["launchctl", "bootstrap", "gui/501", str(installer.plist)]
    assert installer.installed() == f"the LaunchAgent {installer.plist}"


def test_mac_gives_up_on_an_agent_that_never_goes(tmp_path: Path) -> None:
    with pytest.raises(DocketError, match="didn't let go of docket-test"):
        mac(Commands({"launchctl print": (0, "")}), tmp_path).install(launch(str(tmp_path)))


def test_mac_bootstrap_failing_is_refused(tmp_path: Path) -> None:
    runner = Commands({"launchctl print": (113, ""), "launchctl bootstrap": (5, "")})
    with pytest.raises(DocketError, match="couldn't load"):
        mac(runner, tmp_path).install(launch(str(tmp_path)))


def test_mac_stop_and_uninstall(tmp_path: Path) -> None:
    runner = Commands({"launchctl print": (113, "")})
    installer = mac(runner, tmp_path)
    installer.install(launch(str(tmp_path)))
    installer.stop()
    assert runner.calls[-2][1] == "bootout"
    installer.uninstall()
    assert not installer.plist.exists()
    assert installer.installed() is None


# -- Linux -------------------------------------------------------------------------------------------------------------


def test_the_unit_matches_its_golden_file_and_retries_every_five_seconds() -> None:
    unit = user_unit(launch(f"/home/{HOME}"))
    assert unit == (GOLDEN / "docket.service").read_text(encoding="utf-8")
    assert "Restart=on-failure\nRestartSec=5\n" in unit


@pytest.mark.parametrize(
    ("arg", "quoted"),
    [
        ("plain", '"plain"'),
        ("a b", '"a b"'),
        ("50%", '"50%%"'),
        ("$HOME", '"$$HOME"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("C:\\x", '"C:\\\\x"'),
    ],
)
def test_a_unit_argument_is_quoted_with_specifiers_and_variables_escaped(arg: str, quoted: str) -> None:
    assert unit_quote(arg) == quoted


def linux(runner: Commands, tmp_path: Path) -> Linux:
    return Linux(runner=runner, config_home=tmp_path, unit="docket-test.service")


def test_linux_install_reloads_enables_and_restarts(tmp_path: Path) -> None:
    runner = Commands()
    installer = linux(runner, tmp_path)
    assert installer.install(launch(str(tmp_path))) == "the systemd user unit docket-test.service, at your logon"
    assert [c[2:] for c in runner.calls] == [
        ["show-environment"],
        ["daemon-reload"],
        ["enable", "docket-test.service"],
        ["restart", "docket-test.service"],
    ]
    assert installer.path.read_text(encoding="utf-8").startswith("[Unit]\n")
    installer.stop()
    assert runner.calls[-1][2:] == ["stop", "docket-test.service"]
    installer.uninstall()
    assert not installer.path.exists()
    assert installer.installed() is None


def test_linux_without_a_user_instance_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DocketError, match="no systemd user instance"):
        linux(Commands({"systemctl --user show-environment": (1, "")}), tmp_path).install(launch(str(tmp_path)))


def test_a_failing_systemctl_step_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DocketError, match=r"systemctl --user enable docket-test\.service failed"):
        linux(Commands({"systemctl --user enable": (1, "")}), tmp_path).install(launch(str(tmp_path)))


# -- what install captures and writes ----------------------------------------------------------------------------------


def test_the_environment_captured_from_the_installing_shell() -> None:
    shell = {
        "PATH": "/opt/homebrew/bin:/usr/bin",
        "SSH_AUTH_SOCK": "/run/user/1000/agent.sock",
        "DOCKET_CONFIG_DIR": "/x/config",
        "DOCKET_STATE_DIR": "/x/state",
        "HOME": "/home/ada",
    }
    assert install.captured_environment(shell, "Linux") == {
        "PATH": "/opt/homebrew/bin:/usr/bin",
        "SSH_AUTH_SOCK": "/run/user/1000/agent.sock",
        "DOCKET_CONFIG_DIR": "/x/config",
        "DOCKET_STATE_DIR": "/x/state",
    }
    assert "SSH_AUTH_SOCK" not in install.captured_environment(shell, "Darwin")
    assert install.captured_environment({"PATH": "/usr/bin"}, "Windows") == {"PATH": "/usr/bin"}


def test_serve_applies_the_captured_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "env.json"
    path.write_text(json.dumps({"DOCKET_TEST_ONLY": "yes", "NOT_TEXT": 3}), encoding="utf-8")
    monkeypatch.delenv("DOCKET_TEST_ONLY", raising=False)
    install.apply_environment(path)
    assert os.environ.pop("DOCKET_TEST_ONLY") == "yes"
    assert "NOT_TEXT" not in os.environ
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(DocketError, match="isn't a map"):
        install.apply_environment(path)
    with pytest.raises(DocketError, match="can't read the service's environment"):
        install.apply_environment(tmp_path / "missing.json")


def test_the_launch_uses_pythonw_on_windows_and_utf8_mode_everywhere(tmp_path: Path) -> None:
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    assert install.launch_for("Windows", str(python)).python == str(python)
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    found = install.launch_for("Windows", str(python))
    assert found.python == str(tmp_path / "pythonw.exe")
    assert found.args[:5] == ("-X", "utf8", "-m", "docket", "serve")
    assert found.args[-1] == str(install.env_file())
    assert install.launch_for("Linux", str(python)).python == str(python)


def test_an_installer_per_system() -> None:
    assert isinstance(install.installer_for("Windows"), Windows)
    assert isinstance(install.installer_for("Darwin"), Mac)
    assert isinstance(install.installer_for("Linux"), Linux)
    with pytest.raises(DocketError, match="can't register a service on Plan9"):
        install.installer_for("Plan9")


# -- the install flow --------------------------------------------------------------------------------------------------


class Recorder:
    """An installer that records what it was asked to do."""

    def __init__(self) -> None:
        self.launches: list[Launch] = []

    def install(self, launch: Launch) -> str:
        self.launches.append(launch)
        return "a test registration"

    def stop(self) -> None: ...

    def uninstall(self) -> None: ...

    def installed(self) -> str | None:
        return "a test registration" if self.launches else None


def answering(monkeypatch: pytest.MonkeyPatch, body: Callable[[], dict[str, Any]], status: int = 200) -> None:
    def call(method: str, path: str, *, token: str | None = None, at: int | None = None) -> client.Answer:
        return client.Answer(status, body())

    monkeypatch.setattr(client, "call", call)


def a_vault(tmp_path: Path, config: VaultConfig) -> Path:
    return Vault.init(tmp_path / "vault", config, "test-box").root


def test_install_records_the_settings_writes_the_environment_and_waits(
    tmp_path: Path, config: VaultConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = a_vault(tmp_path, config)
    now = datetime.now(UTC)
    answering(monkeypatch, lambda: {"started": (now + timedelta(seconds=2)).isoformat(), "vault": str(root)})
    recorder = Recorder()
    done = install.install(
        root / "raw", 9001, installer=recorder, tokens=Tokens(), environ={"PATH": "/usr/bin"}, system="Linux"
    )
    assert (done.vault, done.port, done.problem, done.how) == (root, 9001, None, "a test registration")
    assert userconfig.service() == userconfig.ServiceSettings(vault=root, port=9001, store="file")
    assert json.loads(install.env_file().read_text(encoding="utf-8")) == {"PATH": "/usr/bin"}
    assert recorder.launches[0].args[-2:] == ("--env-file", str(install.env_file()))


def test_install_names_the_macos_folder_permission(
    tmp_path: Path, config: VaultConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = a_vault(tmp_path, config)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path.parent))
    guarded = tmp_path.parent / "Documents"
    monkeypatch.setattr(Vault, "find", staticmethod(lambda _p: guarded / "vault"))
    answering(
        monkeypatch,
        lambda: {
            "started": datetime.now(UTC).isoformat(),
            "vault_problem": "can't read the vault: Operation not permitted",
        },
    )
    done = install.install(root, None, installer=Recorder(), tokens=Tokens(), environ={}, system="Darwin")
    assert done.problem is not None
    assert "Files and Folders" in done.problem
    assert done.problem.startswith("can't read the vault: Operation not permitted. macOS keeps")
    elsewhere = install.wait_for(
        root,
        port=1,
        token="t",  # noqa: S106 - an invented token
        began=datetime.now(UTC) - timedelta(minutes=1),
        python=Path("/x/python"),
        system="Darwin",
        wait_seconds=0,
        sleep=lambda _s: None,
    )
    assert elsewhere == "can't read the vault: Operation not permitted"
    on_linux = install.wait_for(
        guarded / "vault",
        port=1,
        token="t",  # noqa: S106 - an invented token
        began=datetime.now(UTC) - timedelta(minutes=1),
        python=Path("/x/python"),
        system="Linux",
        wait_seconds=0,
        sleep=lambda _s: None,
    )
    assert on_linux == "can't read the vault: Operation not permitted"


def test_install_reports_a_service_that_never_answers_or_isnt_docket(
    tmp_path: Path, config: VaultConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = a_vault(tmp_path, config)
    answering(monkeypatch, lambda: {"started": "2020-01-01T00:00:00+00:00"})
    old = install.install(
        root,
        None,
        installer=Recorder(),
        tokens=Tokens(),
        environ={},
        system="Linux",
        wait_seconds=1,
        sleep=lambda _s: None,
    )
    assert old.problem is not None
    assert "didn't answer in 1 seconds" in old.problem
    answering(monkeypatch, lambda: {}, status=401)
    other = install.install(
        root,
        None,
        installer=Recorder(),
        tokens=Tokens(),
        environ={},
        system="Linux",
        wait_seconds=0,
        sleep=lambda _s: None,
    )
    assert other.problem is not None
    assert "doesn't take Docket's token" in other.problem
    answering(monkeypatch, lambda: {"started": "not a time"})
    odd = install.install(
        root,
        None,
        installer=Recorder(),
        tokens=Tokens(),
        environ={},
        system="Linux",
        wait_seconds=0,
        sleep=lambda _s: None,
    )
    assert odd.problem is not None


def test_install_needs_a_vault(tmp_path: Path) -> None:
    with pytest.raises(DocketError, match="name the vault"):
        install.install(None, None, installer=Recorder(), tokens=Tokens(), environ={})
    with pytest.raises(DocketError, match="no vault at or above"):
        install.install(tmp_path, None, installer=Recorder(), tokens=Tokens(), environ={})


# -- stopping a Run key service, and status --------------------------------------------------------------------------


def test_a_run_key_service_is_ended_only_when_it_answers_with_its_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[int] = []
    monkeypatch.setattr(os, "kill", lambda pid, _sig: killed.append(pid))
    install.stop_recorded_pid("t")
    client.service_file().parent.mkdir(parents=True, exist_ok=True)
    client.service_file().write_text(json.dumps({"pid": 4242, "port": 9001}), encoding="utf-8")
    install.stop_recorded_pid(None)
    answering(monkeypatch, lambda: {"pid": 1717})
    install.stop_recorded_pid("t")
    assert killed == []
    answering(monkeypatch, lambda: {"pid": 4242})
    install.stop_recorded_pid("t")
    assert killed == [4242]


def test_status_running_with_its_record(monkeypatch: pytest.MonkeyPatch) -> None:
    Tokens().ensure()
    client.service_file().parent.mkdir(parents=True, exist_ok=True)
    client.service_file().write_text(json.dumps({"pid": 4242, "port": 9001}), encoding="utf-8")
    install.env_file().write_text(json.dumps({"PATH": "/usr/bin"}), encoding="utf-8")
    answering(monkeypatch, lambda: {"pid": 4242, "vault": "/v"})
    st = install.status(Recorder(), Tokens())
    assert (st.running, st.port, st.warning, st.environment) == (True, 9001, None, {"PATH": "/usr/bin"})
    assert st.line == "docket: running on 127.0.0.1:9001, vault /v"
    answering(monkeypatch, lambda: {"pid": 1717})
    assert install.status(Recorder(), Tokens()).warning == "what answers on port 9001 isn't the pid on record (4242)"


def test_status_not_running_or_answered_by_another_program(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "call", lambda *a, **k: None)
    assert install.status(Recorder(), Tokens()).environment == {}
    install.env_file().parent.mkdir(parents=True, exist_ok=True)
    install.env_file().write_text("[]", encoding="utf-8")
    monkeypatch.setattr(client, "call", lambda *a, **k: None)
    st = install.status(Recorder(), Tokens())
    assert not st.running
    assert st.store_problem is not None
    assert st.environment == {}
    assert st.line == "docket: not running and not installed; `docket service install --vault <folder>`"
    recorder = Recorder()
    recorder.launches.append(launch())
    assert install.status(recorder, Tokens()).line.startswith("docket: not running (installed as a test registration)")
    answering(monkeypatch, lambda: {}, status=401)
    assert install.status(Recorder(), Tokens()).warning == (
        f"something on port {client.DEFAULT_PORT} answers but doesn't take Docket's token"
    )


def test_a_service_status_line_is_one_line() -> None:
    st = ServiceStatus(None, False, 1, {}, "x", None, {}, None)
    assert "\n" not in st.line
