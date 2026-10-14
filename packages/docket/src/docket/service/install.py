"""`docket service install | stop | uninstall | status`: the service registered to start at the user's logon.

Install records the vault, the port and the token's store in the user's config, writes the environment the service
needs (the installing shell's PATH and the rest) into a file the registration names, registers through the system's
installer, and waits until the service answers with the right vault. Run again, it replaces the registration and
restarts the service: that is how a new Docket version starts serving.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from docket import userconfig
from docket.errors import DocketError
from docket.service import client
from docket.service.platforms import Installer, Launch, Linux, Mac, Windows
from docket.service.system import SYSTEM
from docket.service.token import Token, Tokens
from docket.vault.vault import Vault

ENV_FILE = "service-env.json"
WAIT_SECONDS = 30.0
# Folders macOS keeps from a program without the Files and Folders permission (or Full Disk Access).
GUARDED_FOLDERS = ("Documents", "Desktop", "Downloads", "Library/Mobile Documents")


def env_file() -> Path:
    return userconfig.config_path().parent / ENV_FILE


def captured_environment(environ: Mapping[str, str], system: str = SYSTEM) -> dict[str, str]:
    """What the service gets from the installing shell: its whole PATH (git, git-lfs and their helpers live where the
    user put them, and a LaunchAgent's own PATH has no Homebrew folder), the SSH agent's socket except on macOS (launchd
    hands an agent the session's own, and a path captured here goes stale at the next boot), and Docket's folders when
    they were moved."""
    names = ["PATH", "DOCKET_CONFIG_DIR", "DOCKET_STATE_DIR"]
    if system != "Darwin":
        names.append("SSH_AUTH_SOCK")
    return {name: environ[name] for name in names if environ.get(name)}


def apply_environment(path: Path) -> None:
    """`docket serve --env-file`: the environment install captured, applied before anything else reads it."""
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DocketError(f"can't read the service's environment from {path}: {type(e).__name__}") from None
    if not isinstance(data, dict):
        raise DocketError(f"{path} isn't a map of environment variables")
    for name, value in data.items():  # pyright: ignore[reportUnknownVariableType]
        if isinstance(name, str) and isinstance(value, str):
            os.environ[name] = value


def launch_for(system: str = SYSTEM, executable: str = sys.executable) -> Launch:
    """This Docket's own interpreter, without a console window on Windows."""
    python = Path(executable)
    if system == "Windows" and python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")
    args = ("-X", "utf8", "-m", "docket", "serve", "--env-file", str(env_file()))
    return Launch(python=str(python), args=args, log_dir=str(userconfig.log_dir()))


def installer_for(system: str = SYSTEM, *, stop_pid: Callable[[], None] | None = None) -> Installer:
    if system == "Windows":
        return Windows(work=userconfig.state_dir(), stop_pid=stop_pid)
    if system == "Darwin":
        uid: int = getattr(os, "getuid", lambda: 0)()
        return Mac(home=Path.home(), uid=uid)
    if system == "Linux":
        return Linux(config_home=Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"))
    raise DocketError(f"Docket can't register a service on {system}; run `docket serve` from your own startup")


@dataclass(frozen=True)
class Installed:
    how: str
    vault: Path
    port: int
    token: Token
    store: str
    # Why the service didn't answer as it should, or None when it did.
    problem: str | None


def install(
    vault: Path | None,
    port: int | None,
    *,
    installer: Installer,
    tokens: Tokens,
    environ: Mapping[str, str],
    system: str = SYSTEM,
    executable: str = sys.executable,
    wait_seconds: float = WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> Installed:
    settings = userconfig.service()
    target = vault or settings.vault
    if target is None:
        raise DocketError("name the vault the service serves: `docket service install --vault <folder>`")
    root = Vault.find(target)
    if root is None:
        raise DocketError(f"no vault at or above {target}; create one with `docket init`")
    chosen = port or settings.port or client.DEFAULT_PORT
    userconfig.set_service(vault=root, port=chosen)
    token = tokens.ensure()
    path = env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(captured_environment(environ, system), indent=2) + "\n", encoding="utf-8")
    began = datetime.now(UTC)
    launch = launch_for(system, executable)
    how = installer.install(launch)
    problem = wait_for(
        root,
        port=chosen,
        token=token.value,
        began=began,
        python=Path(launch.python),
        system=system,
        wait_seconds=wait_seconds,
        sleep=sleep,
    )
    return Installed(how, root, chosen, token, tokens.where(), problem)


def wait_for(
    root: Path,
    *,
    port: int,
    token: str,
    began: datetime,
    python: Path,
    system: str,
    wait_seconds: float,
    sleep: Callable[[float], None],
) -> str | None:
    """Wait until the service started now answers with the vault; None when it does, else what went wrong."""
    waited = 0.0
    last: client.Answer | None = None
    while waited <= wait_seconds:
        last = client.call("GET", "/api/status", token=token, at=port)
        if last is not None and last.status == 200 and _started_after(last.body, began):
            problem = last.body.get("vault_problem")
            if not problem:
                return None
            return _with_folder_hint(str(problem), root, python, system)
        sleep(0.5)
        waited += 0.5
    if last is not None and last.status == 401:
        return f"something on port {port} answers but doesn't take Docket's token; see `docket service status`"
    return f"the service didn't answer in {wait_seconds:.0f} seconds; see {userconfig.log_dir() / 'service.log'}"


def _started_after(body: dict[str, Any], began: datetime) -> bool:
    try:
        started = datetime.fromisoformat(str(body.get("started")))
    except ValueError:
        return False
    # The service's clock is this machine's; a second of slack covers the record's rounding.
    return started >= began - timedelta(seconds=1)


def _with_folder_hint(problem: str, root: Path, python: Path, system: str) -> str:
    if system != "Darwin" or "can't read the vault" not in problem:
        return problem
    home = Path.home()
    if not any(root.is_relative_to(home / folder) for folder in GUARDED_FOLDERS):
        return problem
    return (
        f"{problem}. macOS keeps ~/Documents, ~/Desktop, ~/Downloads and iCloud Drive from programs without "
        f"permission: in System Settings, Privacy & Security, Files and Folders (or Full Disk Access), allow "
        f"{python}, or move the vault elsewhere"
    )


def stop_recorded_pid(token: str | None) -> None:
    """End the service the Run key started: the pid in service.json, once /api/status answers with that same pid, so
    a number another program reused is never ended."""
    record = client.recorded()
    if record is None or token is None:
        return
    pid = record.get("pid")
    answer = client.call("GET", "/api/status", token=token, at=record.get("port"))
    if isinstance(pid, int) and answer is not None and answer.status == 200 and answer.body.get("pid") == pid:
        os.kill(pid, signal.SIGTERM)


@dataclass(frozen=True)
class ServiceStatus:
    installed: str | None
    running: bool
    port: int
    body: dict[str, Any]
    store: str
    store_problem: str | None
    environment: dict[str, str]
    warning: str | None

    @property
    def line(self) -> str:
        """One line, for a hook that shows it at a session's start."""
        if self.running:
            return f"docket: running on 127.0.0.1:{self.port}, vault {self.body.get('vault')}"
        if self.installed:
            return f"docket: not running (installed as {self.installed}); see `docket service status`"
        return "docket: not running and not installed; `docket service install --vault <folder>`"


def status(installer: Installer, tokens: Tokens) -> ServiceStatus:
    port = client.port()
    token: str | None = None
    store_problem: str | None = None
    try:
        token = tokens.read()
    except DocketError as e:
        store_problem = str(e)
    answer = client.call("GET", "/api/status", token=token, at=port)
    running = answer is not None and answer.status == 200
    warning: str | None = None
    record = client.recorded()
    if answer is not None and answer.status == 401:
        warning = f"something on port {port} answers but doesn't take Docket's token"
    elif running and answer is not None and record is not None and answer.body.get("pid") != record.get("pid"):
        warning = f"what answers on port {port} isn't the pid on record ({record.get('pid')})"
    return ServiceStatus(
        installed=installer.installed(),
        running=running,
        port=port,
        body=answer.body if running and answer is not None else {},
        store=tokens.where(),
        store_problem=store_problem,
        environment=_captured(),
        warning=warning,
    )


def _captured() -> dict[str, str]:
    """The environment install wrote for the service, for status to show."""
    try:
        loaded: Any = json.loads(env_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in cast(dict[str, Any], loaded).items()} if isinstance(loaded, dict) else {}
