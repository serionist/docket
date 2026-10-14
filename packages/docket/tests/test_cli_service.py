from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from docket import userconfig
from docket.cli import main
from docket.service import client, install, server
from docket.service.install import Installed, ServiceStatus
from docket.service.platforms import Launch
from docket.service.token import Token, Tokens


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


class Registration:
    """An installer that records what it was asked to do."""

    def __init__(self, installed: str | None = None) -> None:
        self.done: list[str] = []
        self.registered = installed

    def install(self, launch: Launch) -> str:
        self.done.append(f"install {launch.args[0]}")
        return "a test registration"

    def stop(self) -> None:
        self.done.append("stop")

    def uninstall(self) -> None:
        self.done.append("uninstall")

    def installed(self) -> str | None:
        return self.registered


@pytest.fixture
def registration(monkeypatch: pytest.MonkeyPatch) -> Registration:
    found = Registration()
    monkeypatch.setattr(install, "installer_for", lambda system=None, *, stop_pid=None: found)
    return found


# -- serve -------------------------------------------------------------------------------------------------------------


def test_serve_applies_the_environment_file_then_serves(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        server, "serve", lambda vault, port, *, said: calls.append((vault, port, os.environ.get("X_TEST")))
    )
    env = tmp_path / "env.json"
    env.write_text(json.dumps({"X_TEST": "set"}), encoding="utf-8")
    monkeypatch.delenv("X_TEST", raising=False)
    code, _, _ = run(capsys, "serve", "--vault", str(tmp_path), "--port", "9001", "--env-file", str(env))
    assert code == 0
    assert calls == [(tmp_path.resolve(), 9001, "set")]
    monkeypatch.delenv("X_TEST")
    run(capsys, "serve")
    assert calls[-1] == (None, None, None)


# -- install, stop, uninstall ------------------------------------------------------------------------------------------


def installed(problem: str | None = None, token: Token | None = None) -> Installed:
    return Installed(
        how="a test registration",
        vault=Path("/v"),
        port=8741,
        token=token or Token("t", "file", written=True),
        store="/c/service-token",
        problem=problem,
    )


@pytest.mark.parametrize(
    ("token", "says"),
    [
        (Token("t", "file", written=True), "token    made, in /c/service-token"),
        (Token("t", "file"), "token    in /c/service-token"),
        (Token("t", "file", written=True, moved_from="keyring"), "token    moved from the keyring store"),
    ],
)
def test_install_says_how_and_where(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    registration: Registration,
    token: Token,
    says: str,
) -> None:
    seen: list[tuple[Any, ...]] = []

    def fake(vault: Path | None, port: int | None, **kwargs: Any) -> Installed:
        seen.append((vault, port, kwargs["installer"]))
        return installed(token=token)

    monkeypatch.setattr(install, "install", fake)
    code, out, _ = run(capsys, "service", "install", "--port", "9001")
    assert code == 0
    assert "installed as a test registration" in out
    assert says in out
    assert out.rstrip().endswith("running")
    assert seen == [(None, 9001, registration)]


def test_install_with_a_problem_exits_1(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install, "install", lambda *a, **k: installed("the service didn't answer"))
    code, out, _ = run(capsys, "service", "install", "--vault", ".")
    assert code == 1
    assert "! the service didn't answer" in out


def test_stop_and_uninstall(capsys: pytest.CaptureFixture[str], registration: Registration) -> None:
    assert run(capsys, "service", "stop")[1] == "stopped\n"
    assert run(capsys, "service", "uninstall")[1] == "uninstalled\n"
    assert registration.done == ["stop", "uninstall"]


def test_the_installer_ends_a_run_key_service_with_the_recorded_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from docket import cli

    tokens: list[str | None] = []
    monkeypatch.setattr(install, "stop_recorded_pid", tokens.append)
    handed: list[Any] = []
    monkeypatch.setattr(install, "installer_for", lambda system=None, *, stop_pid=None: handed.append(stop_pid))
    cli._installer(Tokens())  # pyright: ignore[reportPrivateUsage]
    handed[0]()
    made = Tokens().ensure()
    handed[0]()
    assert tokens == [None, made.value]


# -- status ------------------------------------------------------------------------------------------------------------


def a_status(running: bool, **fields: Any) -> ServiceStatus:
    base: dict[str, Any] = {
        "installed": "a test registration",
        "running": running,
        "port": 8741,
        "body": {"pid": 4242, "version": "0.0.0", "vault": "/v", "vault_problem": "can't read it"} if running else {},
        "store": "the system keychain",
        "store_problem": None,
        "environment": {"PATH": "/usr/bin"},
        "warning": None,
    }
    base.update(fields)
    return ServiceStatus(**base)


def test_status_in_full(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, registration: Registration
) -> None:
    monkeypatch.setattr(install, "status", lambda installer, tokens: a_status(True, warning="what answers isn't ours"))
    code, out, _ = run(capsys, "service", "status")
    assert code == 0
    for line in ("installed  a test registration", "running    yes, port 8741", "pid        4242", "! can't read it"):
        assert line in out
    assert "PATH       /usr/bin" in out
    assert "! what answers isn't ours" in out
    assert f"log        {userconfig.log_dir() / 'service.log'}" in out


def test_status_not_running_exits_1_and_quiet_is_one_line(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, registration: Registration
) -> None:
    monkeypatch.setattr(
        install,
        "status",
        lambda installer, tokens: a_status(False, installed=None, store_problem="no token", environment={}),
    )
    code, out, _ = run(capsys, "service", "status")
    assert code == 1
    assert "installed  no" in out
    assert "! no token" in out
    code, out, _ = run(capsys, "service", "status", "--quiet")
    assert code == 1
    assert out == "docket: not running and not installed; `docket service install --vault <folder>`\n"


# -- token -------------------------------------------------------------------------------------------------------------


def test_token_prints_the_token_and_the_header(capsys: pytest.CaptureFixture[str]) -> None:
    made = Tokens().ensure()
    assert run(capsys, "service", "token")[1] == f"{made.value}\n"
    assert json.loads(run(capsys, "service", "token", "--header")[1]) == {"Authorization": f"Bearer {made.value}"}


def test_token_without_a_store_names_the_way_out(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "service", "token")
    assert code == 2
    assert "docket service install" in err


def test_rotate_restarts_an_installed_service_or_says_to(
    capsys: pytest.CaptureFixture[str], registration: Registration
) -> None:
    first = Tokens().ensure()
    code, out, _ = run(capsys, "service", "token", "--rotate")
    assert (code, out) == (0, "a new token; restart `docket serve` to use it\n")
    assert Tokens().read() != first.value
    registration.registered = "a test registration"
    code, out, _ = run(capsys, "service", "token", "--rotate")
    assert out == "a new token; the service restarted with it\n"
    assert registration.done == ["install -X"]


def test_rotate_says_when_the_store_moved(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, registration: Registration
) -> None:
    import keyring

    from docket import cli

    Tokens().ensure()
    Tokens.file().unlink()
    Tokens.file().mkdir()  # a folder where the token file was: the recorded store is gone for good
    items: dict[tuple[str, str], str] = {}

    class Working:
        def get_password(self, service: str, username: str) -> str | None:
            return items.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            items[(service, username)] = password

    monkeypatch.setattr(keyring, "get_keyring", Working)
    # As on macOS, where the keychain needs no session bus.
    monkeypatch.setattr(cli, "_tokens", lambda: Tokens(system="Darwin"))
    code, out, _ = run(capsys, "service", "token", "--rotate")
    assert code == 0
    assert out.startswith("the file store was gone; the token now lives in the system keychain\n")
    assert userconfig.service().store == "keyring"


# -- open --------------------------------------------------------------------------------------------------------------


def test_open_writes_the_redirect_opens_it_and_prints_the_link(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    Tokens().ensure()
    url = "http://127.0.0.1:8741/session?code=one-time-code"
    monkeypatch.setattr(client, "call", lambda method, path, *, token=None, at=None: client.Answer(200, {"url": url}))
    opened: list[str] = []

    def browser(target: str) -> bool:
        opened.append(target)
        return True

    monkeypatch.setattr("webbrowser.open", browser)
    code, out, _ = run(capsys, "open")
    assert code == 0
    assert out == f"opened Docket in your browser; the link, good once and for a minute: {url}\n"
    page = userconfig.state_dir() / "docket-open.html"
    assert opened == [page.as_uri()]
    assert "one-time-code" in page.read_text(encoding="utf-8")
    assert all("one-time-code" not in target for target in opened)
    monkeypatch.setattr("webbrowser.open", lambda target: False)
    code, out, _ = run(capsys, "open")
    assert out == f"the link, good once and for a minute: {url}\n"


def test_open_without_a_service_says_so(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    Tokens().ensure()
    monkeypatch.setattr(client, "call", lambda *a, **k: None)
    code, _, err = run(capsys, "open")
    assert code == 2
    assert "the service isn't answering" in err
