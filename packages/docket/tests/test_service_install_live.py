"""The service installed for real, killed, back again, stopped and uninstalled, on the system the tests run on.

Only with DOCKET_TEST_LIVE_SERVICE=1: it registers a logon task, a LaunchAgent or a user unit under a name of its own,
on a temp vault with temp config and state folders, and removes it in a finally block whatever happens. It never
touches the user's own registration, vault or keychain (the token lives in a temp file).
"""

from __future__ import annotations

import os
import signal
import socket
import time
import uuid
from pathlib import Path

import pytest

from docket import userconfig
from docket.service import client, install, server
from docket.service.platforms import Installer, Linux, Mac, Windows
from docket.service.system import SYSTEM, run
from docket.service.token import Tokens
from docket.vault.config import VaultConfig
from docket.vault.vault import Vault

pytestmark = pytest.mark.skipif(
    os.environ.get("DOCKET_TEST_LIVE_SERVICE") != "1", reason="registers a real service: DOCKET_TEST_LIVE_SERVICE=1"
)
# The Windows task's repetition is the slowest way back: every minute here, Task Scheduler's shortest, so the job
# doesn't wait up to 5 minutes for a user's; the same trigger brings the service back either way.
BACK_SECONDS = 3 * 60


def a_test_installer(tag: str, tmp_path: Path) -> Installer:
    if SYSTEM == "Windows":
        return Windows(
            work=tmp_path,
            name=f"DocketTest-{tag}",
            stop_pid=lambda: install.stop_recorded_pid(_token()),
            every_minutes=1,
        )
    if SYSTEM == "Darwin":
        uid: int = getattr(os, "getuid", lambda: 0)()
        return Mac(home=Path.home(), uid=uid, label=f"io.github.serionist.docket-test-{tag}")
    if run(["systemctl", "--user", "show-environment"]).returncode != 0:
        pytest.skip("this runner has no systemd user instance")
    return Linux(
        config_home=Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"),
        unit=f"docket-test-{tag}.service",
    )


def _token() -> str | None:
    try:
        return Tokens().read()
    except Exception:
        return None


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_until(check: object, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if callable(check) and check():
            return True
        time.sleep(1)
    return False


def why_not_back(installer: Installer) -> str:
    """What a service that didn't come back is read by: on Windows the task's own state (its status, last run, last
    result and next run), and everywhere the end of the service's log, which holds no content (DR-0155)."""
    said = [f"registered as: {installer.installed()}"]
    if isinstance(installer, Windows):
        query = run(["schtasks", "/Query", "/TN", installer.name, "/V", "/FO", "LIST"])
        said += [line.strip() for line in (query.stdout + query.stderr).splitlines() if line.strip()]
    log = userconfig.log_dir() / server.LOG_FILE
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:] if log.is_file() else ["(no log)"]
    return "\n".join([*said, f"the end of {log.name}:", *lines])


def test_installed_killed_back_stopped_and_uninstalled(tmp_path: Path, config: VaultConfig) -> None:
    root = Vault.init(tmp_path / "vault", config, "test-box").root
    port = free_port()
    installer = a_test_installer(uuid.uuid4().hex[:8], tmp_path / "work")
    try:
        done = install.install(root, port, installer=installer, tokens=Tokens(), environ=os.environ)
        assert done.problem is None, done.problem
        token = done.token.value
        first = client.call("GET", "/api/status", token=token, at=port)
        assert first is not None
        assert first.status == 200
        assert first.body["vault"] == str(root)
        pid = int(first.body["pid"])

        # A crash, not a clean stop: every system brings the service back.
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))

        def back() -> bool:
            answer = client.call("GET", "/api/status", token=token, at=port)
            return answer is not None and answer.status == 200 and answer.body.get("pid") != pid

        assert wait_until(back, BACK_SECONDS), (
            f"the service didn't come back after it was killed\n{why_not_back(installer)}"
        )

        installer.stop()
        assert wait_until(lambda: client.call("GET", "/api/health", at=port) is None, 30)
        time.sleep(10)
        assert client.call("GET", "/api/health", at=port) is None, "the service came back after a stop"
    finally:
        installer.uninstall()
    assert installer.installed() is None
