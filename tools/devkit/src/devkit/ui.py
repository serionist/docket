"""`uv run devkit ui <page>`: the screenshot loop's tooling (DR-0712).

It seeds an invented world into a folder of its own, starts a throwaway service on a free port with config, state and
models folders of its own, a file token and the failing keyring, and drives the built app in Chrome: each of the
page's states, light and dark, at 1440x900 and 390x844, with the clock, zone and motion fixed. A state opens a URL,
then presses keys, clicks or fills a field, and may undo its own write once shot, so the next shot finds the world as
it was; one may ask for another world, such as the empty one. The shots and the machine checks go to
`.local/ui/<page>/r<n>/`, a new round each run.

| Check | Fails on |
|---|---|
| axe | any violation of WCAG 2.1 A and AA or axe's best practices |
| console | an error in the console, or a page error |
| csp | a securitypolicyviolation event |
| sideways | the document scrolling sideways |
| targets | a control smaller than 24 px, or 44 on a coarse pointer, inline text links aside |

It never reaches the owner's service, port 8741, Docket's folders or the keychain: everything it runs gets
`environment()`, which a test holds. Signing in goes to `localhost`, in a browser profile of Playwright's own, so the
owner's session cookie on 127.0.0.1 is never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from devkit.repo import KitError

OWNERS_PORT = 8741
WORLD_SCRIPT = "packages/docket/tests/ui_world.py"
KITS = "packages/docket/tests"
AXE = "packages/docket/web/node_modules/axe-core/axe.min.js"
ROUNDS = ".local/ui"
ZONE = "Europe/Budapest"
# The clock every shot is taken at: a Tuesday morning in the invented week.
CLOCK = datetime(2026, 10, 20, 9, 30, tzinfo=ZoneInfo(ZONE))
GITCONFIG = (
    "[user]\n\tname = UI Example\n\temail = ui@example.invalid\n[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n"
)
START_SECONDS = 60
SIZES: dict[str, tuple[int, int]] = {"wide": (1440, 900), "narrow": (390, 844)}
THEMES: tuple[Literal["light", "dark"], ...] = ("light", "dark")
TARGET = 24
TARGET_COARSE = 44
# Variables of the caller's that would point a child at the owner's things.
DROPPED_PREFIXES = ("DOCKET_", "GIT_")
DROPPED = frozenset({"PYTHON_KEYRING_BACKEND", "PYTHONPATH"})


# What a state's step does: press a key, click a selector, or fill a field, `selector|text`.
VERBS = ("press", "click", "fill")
# What Chrome logs for a refusal's 400, which a state that shows a refusal brings about on purpose.
REFUSED = "the server responded with a status of 400"


@dataclass(frozen=True)
class State:
    """One state of a page: where it opens, then what is pressed, clicked or filled, in order; `only` limits it to a
    size, `world` serves it another world than the page's, and `after` runs once it is shot, such as an undo of the
    write it shows. `expects` names console lines the state brings about on purpose, such as the 400 of the refusal
    it shows, which the console check then leaves out."""

    name: str
    url: str
    steps: tuple[tuple[str, str], ...] = ()
    only: str | None = None
    world: str | None = None
    after: tuple[tuple[str, str], ...] = ()
    expects: tuple[str, ...] = ()


@dataclass(frozen=True)
class Page:
    world: str
    states: tuple[State, ...]


PAGES: dict[str, Page] = {
    "design": Page(
        world="week",
        states=(
            State("list", "/design"),
            State("panel", "/design?open=A-0012"),
            State("calls", "/design", (("press", "x"), ("press", "j"), ("press", "x"), ("press", "1"))),
            State("tokens", "/design?part=tokens"),
            State("components", "/design?part=components"),
            State("select", "/design?part=components", (("click", "#stream"),)),
            State("keys", "/design", (("press", "?"),)),
            State("session", "/design?part=components", (("click", "text=End the session here"),)),
            State("sheet", "/design", (("click", "[aria-label='Open the pages']"),), only="narrow"),
            State("theme", "/design", (("click", "button:has-text('Theme')"),), only="wide"),
            State(
                "sheet-theme",
                "/design",
                (("click", "[aria-label='Open the pages']"), ("click", "button:has-text('Theme')")),
                only="narrow",
            ),
        ),
    ),
    "ledger": Page(
        world="week",
        states=(
            State("list", "/ledger"),
            State("panel", "/ledger?open=A-0005"),
            State("meeting", "/ledger?open=A-0002"),
            State("ticked", "/ledger", (("press", "j"), ("press", "x"), ("press", "j"), ("press", "x"))),
            State("notice", "/ledger", (("press", "j"), ("press", "2")), after=(("press", "u"),)),
            State(
                "refusal",
                "/ledger?open=A-0003&drop=1",
                (("click", '#close-A-0003 >> text="Drop"'),),
                expects=(REFUSED,),
            ),
            State("viewer", "/ledger?open=A-0005&text=notes/plan.md"),
            # The date picker over each of its fields: the panel's due, the snooze's own day, the new item's due.
            State("due", "/ledger?open=A-0003", (("click", "#f-A-0003-due"),)),
            State("snooze", "/ledger?open=A-0003", (("click", "#until-A-0003"),)),
            State(
                "new-due",
                "/ledger",
                (("press", "n"), ("fill", "#new-title|Book the train for the visit"), ("click", "#new-due")),
            ),
            State(
                "alike",
                "/ledger",
                (
                    ("press", "n"),
                    ("fill", "#new-title|Send Ada the dates again"),
                    ("fill", "#new-who|Ada Example"),
                    ("fill", "#new-stream|pilot"),
                    ("click", 'role=dialog >> text="Add"'),
                ),
                expects=(REFUSED,),
            ),
            State("empty", "/ledger", world="empty"),
            # The who field's names under the panel's field and the new item's, and the kinds' tabs on every row.
            State("who", "/ledger?open=A-0003", (("fill", "#f-A-0003-who|e"),)),
            State("new-who", "/ledger", (("press", "n"), ("fill", "#new-who|Cl"))),
        ),
    ),
    "queue": Page(
        world="week",
        states=(
            State("list", "/queue"),
            State("panel", "/queue?open=A-0022"),
            State("ticked", "/queue", (("press", "j"), ("press", "j"), ("press", "x"), ("press", "j"), ("press", "x"))),
            State("notice", "/queue", (("press", "j"), ("press", "j"), ("press", "2")), after=(("press", "u"),)),
            State(
                "refusal",
                "/queue?open=A-0003&drop=1",
                (("click", '#close-A-0003 >> text="Drop"'),),
                expects=(REFUSED,),
            ),
            State("who", "/queue?open=A-0003", (("fill", "#f-A-0003-who|Ada E"),)),
            State("filtered", "/queue?stream=pilot"),
            State("empty", "/queue", world="empty"),
        ),
    ),
}

CHECKS = ("axe", "console", "csp", "sideways", "targets")

# The page-side half of the checks: CSP violations as they happen, from before the app's first script.
WATCH_CSP = (
    "window.__csp = [];"
    "document.addEventListener('securitypolicyviolation', e => "
    "window.__csp.push(`${e.violatedDirective} ${e.blockedURI || 'inline'}`));"
)
# A modal (a dialog, an open listbox or menu) hides the page behind it from assistive technology, as it should; axe
# then reads that page as missing its landmarks, so a modal state is checked inside the modal on top, the last opened.
AXE_RUN = """async () => {
  const modal = [...document.querySelectorAll(
    '[role=dialog][data-state=open], [role=alertdialog][data-state=open], [role=listbox][data-state=open], ' +
    '[role=menu][data-state=open]')].pop();
  const found = await axe.run(modal ?? document, {
    runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']},
  });
  return found.violations.map(v => `${v.id} (${v.impact}): ${v.nodes.length} node(s), ` +
    v.nodes.slice(0, 3).map(n => n.target.join(' ')).join(' | '));
}"""
MEASURE = """(least) => {
  const coarse = matchMedia('(pointer: coarse)').matches;
  const floor = coarse ? Math.max(least, 44) : least;
  const out = [];
  const seen = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=checkbox], ' +
    '[role=switch], [role=tab], [role=menuitem], [role=menuitemradio], [role=option]';
  for (const el of document.querySelectorAll(seen)) {
    // A control hidden from everyone, such as the native select a Select keeps for a form's autofill.
    if (el.closest('[aria-hidden="true"]')) continue;
    const box = el.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'inline' || style.clipPath !== 'none') continue;
    if (box.width < floor || box.height < floor) {
      const name = (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 40);
      out.push(`${el.tagName.toLowerCase()} "${name}" ${Math.round(box.width)}x${Math.round(box.height)}`);
    }
  }
  const root = document.documentElement;
  return {coarse, small: out, sideways: root.scrollWidth > root.clientWidth};
}"""


@dataclass
class Shot:
    state: str
    theme: str
    size: str
    file: str
    problems: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    @property
    def ok(self) -> bool:
        return not any(self.problems.values())


def environment(work: Path, base: Mapping[str, str], root: Path) -> dict[str, str]:
    """What every process devkit ui runs gets: the caller's environment without Docket's and git's own variables,
    then folders under `work` for Docket's config, state and models, git's config, the failing keyring, and the
    kits on the path for the world script."""
    env = {k: v for k, v in base.items() if not k.startswith(DROPPED_PREFIXES) and k not in DROPPED}
    env |= {
        "DOCKET_CONFIG_DIR": str(work / "config"),
        "DOCKET_STATE_DIR": str(work / "state"),
        "DOCKET_MODELS_DIR": str(work / "models"),
        "DOCKET_MACHINE": "ui-box",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
        "GIT_CONFIG_GLOBAL": str(work / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "PYTHONPATH": str(root / KITS),
        "PYTHONUTF8": "1",
    }
    return env


def next_round(root: Path, page: str) -> Path:
    """`.local/ui/<page>/r<n>/`, n one past the highest there: every round's shots are kept."""
    folder = root / ROUNDS / page
    taken = [int(p.name[1:]) for p in folder.glob("r*") if p.is_dir() and p.name[1:].isdigit()]
    out = folder / f"r{max(taken, default=0) + 1}"
    out.mkdir(parents=True)
    return out


def checked_port(port: int) -> int:
    """The throwaway service's port, which is never the owner's."""
    if port == OWNERS_PORT:
        raise KitError(f"the throwaway service got port {OWNERS_PORT}, the owner's; stopping")
    return port


def shot_name(state: str, theme: str, size: str) -> str:
    width, height = SIZES[size]
    return f"{state}-{theme}-{width}x{height}.png"


def summary(shots: Sequence[Shot]) -> list[str]:
    """The checks' lines: one per problem, by shot, then a count."""
    lines: list[str] = []
    for shot in shots:
        for check in CHECKS:
            lines += [f"{shot.file}: {check}: {p}" for p in shot.problems.get(check, [])]
    bad = sum(1 for s in shots if not s.ok)
    lines.append(f"{len(shots)} shots, {bad} with problems" if bad else f"{len(shots)} shots, every check passed")
    return lines


def _run(cmd: list[str], env: dict[str, str], cwd: Path) -> None:
    proc = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if proc.returncode != 0:
        raise KitError(f"{' '.join(cmd[:3])}: {(proc.stderr or proc.stdout).strip()[-2000:]}")


def remove(folder: Path) -> None:
    """The throwaway folders, gone: git keeps its objects read-only, which Windows deletes only once made writable,
    and a process just stopped can hold a file a moment longer."""

    def writable(func: Callable[[str], object], path: str, _error: BaseException) -> None:
        Path(path).chmod(stat.S_IWRITE | stat.S_IREAD)
        func(path)

    try:
        shutil.rmtree(folder, onexc=writable)
    except OSError:
        time.sleep(1)
        shutil.rmtree(folder, onexc=writable)


@contextmanager
def service(root: Path, world: str) -> Generator[tuple[int, str]]:
    """A throwaway service over a new invented world: its port and its token, stopped and its folders removed after."""
    work = Path(tempfile.mkdtemp(prefix="docket-ui-"))
    env = environment(work, os.environ, root)
    (work / "gitconfig").write_text(GITCONFIG, encoding="utf-8")
    vault = work / "vault"
    said = work / "service.err"
    proc: subprocess.Popen[bytes] | None = None
    try:
        _run([sys.executable, str(root / WORLD_SCRIPT), str(vault), world], env, root)
        token = (work / "config" / "service-token").read_text(encoding="utf-8").strip()
        cmd = [sys.executable, "-m", "docket", "serve", "--vault", str(vault), "--port", "0"]
        with said.open("wb") as err:
            proc = subprocess.Popen(cmd, cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=err)
        record = work / "state" / "service.json"
        deadline = time.monotonic() + START_SECONDS
        while not record.is_file():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise KitError(f"the throwaway service didn't start: {said.read_text(errors='replace')[-2000:]}")
            time.sleep(0.1)
        port = checked_port(int(json.loads(record.read_text(encoding="utf-8"))["port"]))
        yield port, token
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(15)
        remove(work)


def sign_in_link(port: int, token: str, after: str) -> str:
    """A one-time link that opens `after`, on localhost: never 127.0.0.1, whose session cookie is the owner's."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/session",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310 - the throwaway service on this machine
        url = str(json.loads(answer.read())["url"])
    code = url.split("code=", 1)[1]
    return f"http://localhost:{port}/session?code={code}&next={urllib.parse.quote(after, safe='')}"


def unexpected(lines: Sequence[str], expects: Sequence[str]) -> list[str]:
    """A state's console lines, but for those it brings about on purpose."""
    return [line for line in lines if not any(expected in line for expected in expects)]


def _steps(page: Any, steps: Sequence[tuple[str, str]]) -> None:
    for verb, arg in steps:
        if verb == "press":
            page.keyboard.press(arg)
        elif verb == "fill":
            selector, _, text = arg.partition("|")
            page.fill(selector, text)
        else:
            page.click(arg)
        page.wait_for_timeout(150)


def _shoot(page: Any, state: State, out: Path, shot: Shot) -> None:
    _steps(page, state.steps)
    page.wait_for_timeout(250)
    page.screenshot(path=str(out / shot.file))
    measured = page.evaluate(MEASURE, TARGET)
    shot.problems["targets"] = list(measured["small"])
    shot.problems["sideways"] = ["the document scrolls sideways"] if measured["sideways"] else []
    shot.problems["csp"] = list(page.evaluate("window.__csp"))
    shot.problems["axe"] = list(page.evaluate(AXE_RUN))
    if state.after:
        _steps(page, state.after)
        page.wait_for_timeout(250)


class Console:
    """What a tab's console said in error, and the page errors it threw."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def message(self, message: Any) -> None:
        if message.type == "error":
            self.lines.append(str(message.text))

    def error(self, error: Any) -> None:
        self.lines.append(str(error))


def shoot(browser: Any, states: Sequence[State], port: int, token: str, *, out: Path, axe: Path) -> list[Shot]:
    """Every state, at each size, in each theme, each in a browser context of its own: its own cookies, so each signs
    in afresh through a one-time link."""
    shots: list[Shot] = []
    for size, (width, height) in SIZES.items():
        for theme in THEMES:
            for state in states:
                if state.only not in (None, size):
                    continue
                shot = Shot(state.name, theme, size, shot_name(state.name, theme, size))
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    color_scheme=theme,
                    reduced_motion="reduce",
                    timezone_id=ZONE,
                    locale="en-GB",
                    has_touch=size == "narrow",
                    is_mobile=size == "narrow",
                    device_scale_factor=1,
                )
                context.add_init_script(WATCH_CSP)
                context.add_init_script(path=str(axe))
                tab = context.new_page()
                errors = Console()
                tab.on("console", errors.message)
                tab.on("pageerror", errors.error)
                tab.clock.set_fixed_time(CLOCK)
                tab.goto(sign_in_link(port, token, state.url))
                # A query may hold a slash, such as a material's path, which one star doesn't match.
                tab.wait_for_url(f"**{state.url.split('?')[0]}**")
                tab.wait_for_selector("#main")
                # The page's reads answered, so a key pressed next finds its rows.
                tab.wait_for_load_state("networkidle")
                tab.evaluate("document.fonts.ready")
                _shoot(tab, state, out, shot)
                shot.problems["console"] = unexpected(errors.lines, state.expects)
                shots.append(shot)
                context.close()
    return shots


@contextmanager
def chrome() -> Generator[Any]:
    """The machine's own Chrome, driven by Playwright in a profile of its own; nothing is downloaded."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 - only this command drives a browser

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        try:
            yield browser
        finally:
            browser.close()


def run(
    page_name: str,
    *,
    root: Path,
    browser: Callable[[], AbstractContextManager[Any]] = chrome,
    serve: Callable[[Path, str], AbstractContextManager[tuple[int, str]]] = service,
) -> int:
    """A round of the page: its shots and checks in a new round folder; 1 when a check failed."""
    if page_name not in PAGES:
        raise KitError(f"no page {page_name!r} to shoot; the pages are {', '.join(PAGES)}")
    page = PAGES[page_name]
    axe = root / AXE
    if not axe.is_file():
        raise KitError(f"{AXE} is missing: run `npm ci` in packages/docket/web first")
    out = next_round(root, page_name)
    shots: list[Shot] = []
    worlds = list(dict.fromkeys(state.world or page.world for state in page.states))
    with browser() as driven:
        for world in worlds:
            states = [state for state in page.states if (state.world or page.world) == world]
            with serve(root, world) as (port, token):
                shots += shoot(driven, states, port, token, out=out, axe=axe)
    lines = summary(shots)
    (out / "checks.json").write_text(
        json.dumps([s.__dict__ | {"ok": s.ok} for s in shots], indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (out / "checks.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    for line in lines:
        print(line)
    print(f"shots and checks in {out.relative_to(root).as_posix()}")
    return 0 if all(s.ok for s in shots) else 1
