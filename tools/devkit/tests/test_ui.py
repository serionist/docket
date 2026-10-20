from __future__ import annotations

import contextlib
import io
import json
import tempfile
import urllib.request
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from devkit import cli, ui
from devkit.repo import KitError


def test_everything_it_runs_gets_folders_of_its_own_and_the_failing_keyring(tmp_path: Path) -> None:
    owner = {
        "PATH": "/usr/bin",
        "DOCKET_VAULT": "/home/ada/vault",
        "DOCKET_CONFIG_DIR": "/home/ada/.config/docket",
        "DOCKET_STATE_DIR": "/home/ada/.local/state/docket",
        "DOCKET_TOKEN": "the-owner's",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.Windows.WinVaultKeyring",
        "GIT_CONFIG_GLOBAL": "/home/ada/.gitconfig",
        "GIT_DIR": "/home/ada/vault/.git",
        "PYTHONPATH": "/home/ada/lib",
    }
    env = ui.environment(tmp_path / "work", owner, tmp_path / "repo")
    assert env["PATH"] == "/usr/bin"
    assert "DOCKET_VAULT" not in env and "DOCKET_TOKEN" not in env and "GIT_DIR" not in env
    assert env["PYTHON_KEYRING_BACKEND"] == "keyring.backends.fail.Keyring"
    for name in ("DOCKET_CONFIG_DIR", "DOCKET_STATE_DIR", "DOCKET_MODELS_DIR", "GIT_CONFIG_GLOBAL"):
        assert Path(env[name]).is_relative_to(tmp_path / "work"), name
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["PYTHONPATH"] == str(tmp_path / "repo" / ui.KITS)
    assert not any("/home/ada" in value for name, value in env.items() if name != "PATH")


def test_each_run_takes_the_next_round(tmp_path: Path) -> None:
    assert ui.next_round(tmp_path, "design") == tmp_path / ui.ROUNDS / "design" / "r1"
    (tmp_path / ui.ROUNDS / "design" / "notes").mkdir()
    assert ui.next_round(tmp_path, "design").name == "r2"
    assert ui.next_round(tmp_path, "design").name == "r3"


def test_the_owner_s_port_is_never_taken() -> None:
    assert ui.checked_port(61234) == 61234
    with pytest.raises(KitError, match="8741"):
        ui.checked_port(ui.OWNERS_PORT)


def test_the_shots_and_their_checks_are_named_and_summed() -> None:
    assert ui.shot_name("panel", "dark", "narrow") == "panel-dark-390x844.png"
    good = ui.Shot("list", "light", "wide", "list-light-1440x900.png", {"axe": [], "csp": []})
    bad = ui.Shot("list", "dark", "wide", "list-dark-1440x900.png", {"targets": ["button 16x16"], "csp": []})
    assert good.ok and not bad.ok
    assert ui.summary([good, bad]) == [
        "list-dark-1440x900.png: targets: button 16x16",
        "2 shots, 1 with problems",
    ]
    assert ui.summary([good]) == ["1 shots, every check passed"]


def test_every_page_s_states_open_a_path_and_fit_a_size() -> None:
    for name, page in ui.PAGES.items():
        assert page.world in ("empty", "week"), name
        assert len({s.name for s in page.states}) == len(page.states)
        for state in page.states:
            assert state.url.startswith(f"/{name}"), state.name
            assert state.only in (None, *ui.SIZES), state.name
            assert state.world in (None, "empty", "week"), state.name
            assert all(verb in ui.VERBS for verb, _ in (*state.steps, *state.after)), state.name
            assert all("|" in arg for verb, arg in state.steps if verb == "fill"), state.name


def test_signing_in_goes_to_localhost_never_127_0_0_1(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[Any] = []

    def answer(request: Any, timeout: float) -> Any:
        asked.append(request)
        body = json.dumps({"url": "http://127.0.0.1:61234/session?code=abc123"}).encode()
        return _Answer(body)

    monkeypatch.setattr(urllib.request, "urlopen", answer)
    link = ui.sign_in_link(61234, "t0ken", "/design?part=tokens")
    assert link == "http://localhost:61234/session?code=abc123&next=%2Fdesign%3Fpart%3Dtokens"
    assert asked[0].get_header("Authorization") == "Bearer t0ken"


def test_a_page_it_doesn_t_know_or_a_missing_axe_stops_it(tmp_path: Path) -> None:
    with pytest.raises(KitError, match="no page"):
        ui.run("outbox", root=tmp_path)
    with pytest.raises(KitError, match="npm ci"):
        ui.run("design", root=tmp_path)


class _Answer(io.BytesIO):
    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class _Tab:
    """A page as Playwright drives it, answering the checks' scripts as a clean page would, but for one target."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.handlers: dict[str, Any] = {}
        self.keyboard = self
        self.clock = self

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    def set_fixed_time(self, when: object) -> None:
        self.log.append(f"clock {when}")

    def goto(self, url: str) -> None:
        self.log.append(f"goto {url}")
        self.handlers["console"](type("Message", (), {"type": "warning", "text": "a warning"})())

    def wait_for_url(self, pattern: str) -> None:
        self.log.append(f"url {pattern}")

    def wait_for_selector(self, selector: str) -> None:
        self.log.append(f"wait {selector}")

    def wait_for_load_state(self, state: str) -> None:
        self.log.append(f"load {state}")

    def fill(self, selector: str, text: str) -> None:
        self.log.append(f"fill {selector} {text}")

    def wait_for_timeout(self, ms: int) -> None:
        pass

    def press(self, key: str) -> None:
        self.log.append(f"press {key}")

    def click(self, selector: str) -> None:
        self.log.append(f"click {selector}")
        self.handlers["pageerror"](RuntimeError("the click threw"))

    def screenshot(self, path: str) -> None:
        Path(path).write_bytes(b"\x89PNG")

    def evaluate(self, script: str, arg: object = None) -> Any:
        if script == ui.MEASURE:
            return {"coarse": False, "small": [], "sideways": False}
        if script == "window.__csp":
            return []
        if script == ui.AXE_RUN:
            return []
        return None


class _Browser:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.contexts: list[dict[str, Any]] = []

    def new_context(self, **options: Any) -> _Browser:
        self.contexts.append(options)
        return self

    def add_init_script(self, script: str | None = None, path: str | None = None) -> None:
        self.log.append("init axe" if path else "init csp")

    def new_page(self) -> _Tab:
        return _Tab(self.log)

    def close(self) -> None:
        self.log.append("close")


def test_a_round_shoots_each_state_at_each_size_and_theme_and_writes_its_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ui.AXE).parent.mkdir(parents=True)
    (tmp_path / ui.AXE).write_text("window.axe = {};", encoding="utf-8")
    browser = _Browser()
    served: list[str] = []

    @contextlib.contextmanager
    def serve(root: Path, world: str) -> Generator[tuple[int, str]]:
        served.append(world)
        yield 61234, "t0ken"

    @contextlib.contextmanager
    def driven() -> Generator[Any]:
        yield browser

    monkeypatch.setattr(ui, "sign_in_link", lambda port, token, after: f"http://localhost:{port}/session?next={after}")
    code = ui.run("design", root=tmp_path, browser=driven, serve=serve)
    out = tmp_path / ui.ROUNDS / "design" / "r1"
    states = ui.PAGES["design"].states
    shots = sum(s.only in (None, size) for s in states for size in ui.SIZES)
    assert served == ["week"]
    assert len(browser.contexts) == 2 * shots
    assert {c["color_scheme"] for c in browser.contexts} == {"light", "dark"}
    assert {(c["viewport"]["width"], c["is_mobile"]) for c in browser.contexts} == {(1440, False), (390, True)}
    assert all(c["reduced_motion"] == "reduce" and c["timezone_id"] == ui.ZONE for c in browser.contexts)
    assert (out / "list-light-1440x900.png").is_file() and (out / "sheet-dark-390x844.png").is_file()
    assert not (out / "sheet-dark-1440x900.png").exists(), "the sheet is a narrow window's only"
    assert (out / "theme-light-1440x900.png").is_file() and not (out / "theme-light-390x844.png").exists()
    checks = json.loads((out / "checks.json").read_text(encoding="utf-8"))
    clicked = [c for c in checks if c["state"] == "select"]
    assert clicked and all(c["problems"]["console"] == ["the click threw"] for c in clicked)
    assert code == 1, "a page error fails the round"
    assert "with problems" in (out / "checks.txt").read_text(encoding="utf-8")
    assert "shots and checks in .local/ui/design/r1" in capsys.readouterr().out


def test_a_state_fills_undoes_its_write_once_shot_and_may_ask_for_another_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ui.AXE).parent.mkdir(parents=True)
    (tmp_path / ui.AXE).write_text("window.axe = {};", encoding="utf-8")
    browser = _Browser()
    served: list[str] = []

    @contextlib.contextmanager
    def serve(root: Path, world: str) -> Generator[tuple[int, str]]:
        served.append(world)
        yield 61234, "t0ken"

    @contextlib.contextmanager
    def driven() -> Generator[Any]:
        yield browser

    monkeypatch.setattr(ui, "sign_in_link", lambda port, token, after: f"http://localhost:{port}/session?next={after}")
    ui.run("ledger", root=tmp_path, browser=driven, serve=serve)
    # One service a world: the empty world's one state after the week's.
    assert served == ["week", "empty"]
    assert "fill #new-title Send Ada the dates again" in browser.log
    assert browser.log.count("load networkidle") == 2 * len(ui.SIZES) * len(ui.PAGES["ledger"].states)
    # The notice's write is undone once it is shot: the undo's key comes after the shot's checks.
    notice = browser.log.index("press 2")
    assert browser.log[notice + 1] == "press u"
    checks = json.loads((tmp_path / ui.ROUNDS / "ledger" / "r1" / "checks.json").read_text(encoding="utf-8"))
    assert {c["state"] for c in checks} == {s.name for s in ui.PAGES["ledger"].states}


def test_a_state_s_expected_console_lines_are_left_out_of_its_check() -> None:
    refusal = next(s for s in ui.PAGES["ledger"].states if s.name == "refusal")
    assert refusal.expects == (ui.REFUSED,)
    line = f"Failed to load resource: {ui.REFUSED} (Bad Request)"
    assert ui.unexpected([line, "TypeError: boom"], refusal.expects) == ["TypeError: boom"]
    assert ui.unexpected([line], ()) == [line], "a state that expects nothing counts every line"


def test_the_throwaway_service_runs_on_folders_of_its_own_and_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[3]
    work = tmp_path / "work"

    def made(prefix: str) -> str:
        work.mkdir()
        return str(work)

    monkeypatch.setattr(tempfile, "mkdtemp", made)
    with ui.service(root, "empty") as (port, token):
        assert port != ui.OWNERS_PORT
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/health")
        with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310 - the throwaway service
            assert json.loads(answer.read()) == {"service": "docket"}
        assert ui.sign_in_link(port, token, "/design").startswith(f"http://localhost:{port}/session?code=")
        assert (work / "config" / "service-token").read_text(encoding="utf-8").strip() == token
        assert json.loads((work / "state" / "service.json").read_text(encoding="utf-8"))["port"] == port
    assert not work.exists(), "its folders go with it"


def test_a_service_that_doesn_t_start_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[3]
    work = tmp_path / "work"

    def made(prefix: str) -> str:
        work.mkdir()
        return str(work)

    monkeypatch.setattr(tempfile, "mkdtemp", made)
    monkeypatch.setattr(ui, "START_SECONDS", 0)
    with pytest.raises(KitError, match="didn't start"), ui.service(root, "empty"):
        pass
    assert not work.exists()
    with pytest.raises(KitError, match="no world"), ui.service(root, "month"):
        pass


def test_the_command_names_a_page_it_can_t_shoot(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["ui", "outbox"]) == 2
    assert "no page 'outbox'" in capsys.readouterr().err
