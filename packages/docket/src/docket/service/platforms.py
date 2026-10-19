"""How the service is registered to start at the user's logon on each system, never as a system service: the
recorder (phase 4) needs the user's audio devices, and the keychain, git credentials and GPU are the user's.

| System | Registration |
|---|---|
| Windows | a Task Scheduler task at the user's own logon; the per-user Run key when Task Scheduler refuses |
| macOS | a LaunchAgent in the user's GUI session |
| Linux | a systemd user unit |

Each installer is plain code over a runner (and a registry on Windows), so every system's runs in the tests of the
others. The names are parameters, so the live tests never touch a real registration.
"""

from __future__ import annotations

import importlib
import plistlib
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from xml.sax.saxutils import escape

from docket.errors import DocketError
from docket.service.system import Runner, run, windows_sid

TASK = "Docket"
LABEL = "io.github.serionist.docket"
UNIT = "docket.service"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# How long macOS gets to finish booting an agent out before it is bootstrapped again.
BOOTOUT_SECONDS = 10.0
DESCRIPTION = "Docket: the local service for Claude and the web app"


@dataclass(frozen=True)
class Launch:
    """What a registration starts: an interpreter and its arguments. The environment the service needs (PATH and
    the rest) is in the file `--env-file` names, which `docket serve` applies before anything else."""

    # Paths as the system writes them, kept as text so a registration renders the same on any machine.
    python: str
    args: tuple[str, ...]
    log_dir: str

    def argv(self) -> list[str]:
        return [self.python, *self.args]

    def log_file(self, name: str) -> str:
        separator = "\\" if "\\" in self.log_dir else "/"
        return f"{self.log_dir.rstrip(separator)}{separator}{name}"


class Installer(Protocol):
    def install(self, launch: Launch) -> str:
        """Register and start; says how it registered."""
        ...

    def stop(self) -> None: ...

    def uninstall(self) -> None: ...

    def installed(self) -> str | None:
        """How the service is registered, or None."""
        ...


# -- Windows -----------------------------------------------------------------------------------------------------------


class Registry(Protocol):
    """The per-user Run key's values."""

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class RunKey:  # pragma: no cover - winreg exists only on Windows, where test_service_install runs this
    """HKCU's Run key, through winreg."""

    def __init__(self) -> None:
        self.winreg: Any = importlib.import_module("winreg")

    def get(self, name: str) -> str | None:
        try:
            with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = self.winreg.QueryValueEx(key, name)
        except OSError:
            return None
        return str(value)

    def set(self, name: str, value: str) -> None:
        with self.winreg.CreateKey(self.winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            self.winreg.SetValueEx(key, name, 0, self.winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        try:
            with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, RUN_KEY, 0, self.winreg.KEY_SET_VALUE) as key:
                self.winreg.DeleteValue(key, name)
        except OSError:
            return


def task_xml(launch: Launch, sid: str, start: datetime, every_minutes: int = 5) -> str:
    """The task, in the form Task Scheduler exports: at the user's own logon, only while they're logged on, on
    battery too (both battery settings default to true, and a laptop on battery would never start it), and restarted
    after a failure. A service that exited comes back within 5 minutes: the restart setting alone doesn't restart a
    process that ran and ended, and a logon trigger's repetition only begins at the next logon, so a time trigger from
    `start` (install's moment, in local time) repeats every 5 minutes. IgnoreNew keeps either from starting a second
    one. The live test asks for every minute, Task Scheduler's shortest, so CI doesn't wait 5 for it."""
    begins = start.replace(microsecond=0, tzinfo=None).isoformat()
    command = escape(launch.python)
    arguments = escape(subprocess.list2cmdline(list(launch.args)))
    folder = escape(launch.log_dir)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{DESCRIPTION}</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{sid}</UserId>
    </LogonTrigger>
    <TimeTrigger>
      <Repetition>
        <Interval>PT{every_minutes}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{begins}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{sid}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{folder}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


class Windows:
    def __init__(
        self,
        *,
        runner: Runner = run,
        registry: Callable[[], Registry] = RunKey,
        start: Callable[[list[str]], None] | None = None,
        stop_pid: Callable[[], None] | None = None,
        work: Path,
        name: str = TASK,
        now: Callable[[], datetime] = datetime.now,
        every_minutes: int = 5,
    ) -> None:
        self.now = now
        self.every_minutes = every_minutes
        self.runner = runner
        self.registry = registry
        self.start = start or _start_detached
        self.stop_pid = stop_pid or (lambda: None)
        self.work = work
        self.name = name

    def _schtasks(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.runner(["schtasks", *args])

    def _has_task(self) -> bool:
        return self._schtasks("/Query", "/TN", self.name).returncode == 0

    def install(self, launch: Launch) -> str:
        # Task Scheduler won't start an action whose working folder doesn't exist yet.
        Path(launch.log_dir).mkdir(parents=True, exist_ok=True)
        xml = self.work / f"{self.name}-task.xml"
        xml.parent.mkdir(parents=True, exist_ok=True)
        xml.write_text(task_xml(launch, windows_sid(self.runner), self.now(), self.every_minutes), encoding="utf-16")
        try:
            created = self._schtasks("/Create", "/TN", self.name, "/XML", str(xml), "/F")
        finally:
            xml.unlink(missing_ok=True)
        if created.returncode == 0:
            self.registry().delete(self.name)
            self._schtasks("/End", "/TN", self.name)
            started = self._schtasks("/Run", "/TN", self.name)
            if started.returncode != 0:
                raise DocketError(f"Task Scheduler made the task {self.name} but didn't start it")
            return f"the Task Scheduler task {self.name}, at your logon"
        # A policy or a standard account can refuse a logon task; the Run key needs no rights.
        self.stop_pid()
        self.registry().set(self.name, subprocess.list2cmdline(launch.argv()))
        self.start(launch.argv())
        return (
            f"the Run key value {self.name}, at your logon (Task Scheduler refused a task: exit "
            f"{created.returncode}); it isn't restarted after a crash"
        )

    def stop(self) -> None:
        if self._has_task():
            self._schtasks("/End", "/TN", self.name)
            # Disabled, or the repetition would start it again; install enables it.
            self._schtasks("/Change", "/TN", self.name, "/DISABLE")
        if self.registry().get(self.name) is not None:
            self.stop_pid()
            self.registry().delete(self.name)

    def uninstall(self) -> None:
        self.stop()
        if self._has_task():
            self._schtasks("/Delete", "/TN", self.name, "/F")

    def installed(self) -> str | None:
        if self._has_task():
            return f"the Task Scheduler task {self.name}"
        if self.registry().get(self.name) is not None:
            return f"the Run key value {self.name}"
        return None


def _start_detached(argv: list[str]) -> None:  # pragma: no cover - starts a real service; the live test runs it
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(  # noqa: S603 - this Docket's own interpreter and arguments
        argv, creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


# -- macOS -------------------------------------------------------------------------------------------------------------


def agent_plist(launch: Launch, label: str = LABEL) -> bytes:
    """A LaunchAgent that starts at load and again after any exit but a clean one (a crash, or `serve`'s exit 1 when
    the token store can't be reached)."""
    return plistlib.dumps(
        {
            "Label": label,
            "ProgramArguments": launch.argv(),
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "WorkingDirectory": launch.log_dir,
            "StandardOutPath": launch.log_file("launchd.log"),
            "StandardErrorPath": launch.log_file("launchd.log"),
        }
    )


class Mac:
    def __init__(
        self,
        *,
        runner: Runner = run,
        home: Path,
        uid: int,
        label: str = LABEL,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runner = runner
        self.home = home
        self.domain = f"gui/{uid}"
        self.label = label
        self.sleep = sleep

    @property
    def plist(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{self.label}.plist"

    def _launchctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.runner(["launchctl", *args])

    def _boot_out(self) -> None:
        """Boot the agent out and wait until it is gone: bootout returns before it ends, and a bootstrap while it is
        still going fails."""
        self._launchctl("bootout", f"{self.domain}/{self.label}")
        waited = 0.0
        while self._launchctl("print", f"{self.domain}/{self.label}").returncode == 0:
            if waited >= BOOTOUT_SECONDS:
                raise DocketError(f"launchd didn't let go of {self.label} in {BOOTOUT_SECONDS:.0f} seconds")
            self.sleep(0.25)
            waited += 0.25

    def install(self, launch: Launch) -> str:
        self.plist.parent.mkdir(parents=True, exist_ok=True)
        Path(launch.log_dir).mkdir(parents=True, exist_ok=True)
        self._boot_out()
        self.plist.write_bytes(agent_plist(launch, self.label))
        loaded = self._launchctl("bootstrap", self.domain, str(self.plist))
        if loaded.returncode != 0:
            raise DocketError(f"launchctl couldn't load {self.plist}: {loaded.stderr.strip()}")
        return f"the LaunchAgent {self.label}, at your logon"

    def stop(self) -> None:
        self._boot_out()

    def uninstall(self) -> None:
        self._boot_out()
        self.plist.unlink(missing_ok=True)

    def installed(self) -> str | None:
        return f"the LaunchAgent {self.plist}" if self.plist.exists() else None


# -- Linux -------------------------------------------------------------------------------------------------------------


def unit_quote(arg: str) -> str:
    """One argument for ExecStart: quoted, with systemd's specifiers (%) and variables ($) escaped."""
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def unit_path(path: str) -> str:
    """A path for WorkingDirectory: the rest of the line, unquoted, with only specifiers (%) escaped; systemd
    expands no variables there and refuses quotes."""
    return path.replace("%", "%%")


def user_unit(launch: Launch) -> str:
    """A user unit retried every 5 seconds after any failure: systemd's own 100 ms would hit its start limit in a
    second and give up on an exit 1."""
    return f"""[Unit]
Description={DESCRIPTION}

[Service]
Type=simple
ExecStart={" ".join(unit_quote(a) for a in launch.argv())}
WorkingDirectory={unit_path(launch.log_dir)}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


class Linux:
    def __init__(self, *, runner: Runner = run, config_home: Path, unit: str = UNIT) -> None:
        self.runner = runner
        self.config_home = config_home
        self.unit = unit

    @property
    def path(self) -> Path:
        return self.config_home / "systemd" / "user" / self.unit

    def _systemctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.runner(["systemctl", "--user", *args])

    def install(self, launch: Launch) -> str:
        if self._systemctl("show-environment").returncode != 0:
            raise DocketError(
                "there is no systemd user instance here (WSL without systemd, a container); start `docket serve` "
                "from your own startup instead"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        Path(launch.log_dir).mkdir(parents=True, exist_ok=True)
        self.path.write_text(user_unit(launch), encoding="utf-8", newline="\n")
        for args in (("daemon-reload",), ("enable", self.unit), ("restart", self.unit)):
            done = self._systemctl(*args)
            if done.returncode != 0:
                raise DocketError(f"systemctl --user {' '.join(args)} failed: {done.stderr.strip()}")
        return f"the systemd user unit {self.unit}, at your logon"

    def stop(self) -> None:
        self._systemctl("stop", self.unit)

    def uninstall(self) -> None:
        self._systemctl("disable", "--now", self.unit)
        self.path.unlink(missing_ok=True)
        self._systemctl("daemon-reload")

    def installed(self) -> str | None:
        return f"the systemd user unit {self.path}" if self.path.exists() else None
