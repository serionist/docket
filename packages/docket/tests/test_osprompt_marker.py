"""The console-code marker's checks (DR-0574), with the system's answers faked: who owns each entry, whether the owner
could replace it, the marker's folder, links, and the token it is judged with. Invented paths only."""

from __future__ import annotations

import ast
import sys
from pathlib import Path, PureWindowsPath

import pytest

from docket.osprompt import marker
from docket.osprompt.marker import Entry, Token, Verdict

ADMINS, SYSTEM, TRUSTED_INSTALLER = (
    "S-1-5-32-544",
    "S-1-5-18",
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
)
OWNER = "S-1-5-21-1111111111-2222222222-3333333333-1001"
PATH = "C:\\ProgramData\\Docket\\console-code"
READ = 0x1200A9
# What a stock Windows grants a user on C:\ and C:\ProgramData: folders and files may be added, nothing replaced.
STOCK_DRIVE = READ | 0x4
STOCK_PROGRAM_DATA = READ | 0x1 | 0x2 | 0x4 | 0x10 | 0x100


def entries(
    *, folder: int = READ, file: int = READ, owner: str = ADMINS, links: int = 1, link: bool = False
) -> list[Entry]:
    facts = [
        (TRUSTED_INSTALLER, False, 1, STOCK_DRIVE),
        (SYSTEM, False, 1, STOCK_PROGRAM_DATA),
        (ADMINS, False, 1, folder),
        (owner, link, links, file),
    ]
    found = marker.windows_entries(PureWindowsPath(PATH), facts)
    assert found is not None
    return found


def judged(found: list[Entry] | None, problem: str | None = None) -> Verdict:
    return marker.judge(PATH, found, owner="Administrators, SYSTEM or TrustedInstaller", problem=problem)


def test_a_marker_the_readmes_lines_made_counts_on_a_stock_windows() -> None:
    """Stock C:\\ and C:\\ProgramData let users add folders, but not replace these: the marker still counts (O6)."""
    found = entries()
    assert [e.path for e in found] == ["C:\\", "C:\\ProgramData", "C:\\ProgramData\\Docket", PATH]
    assert judged(found) == Verdict(PATH, present=True, counts=True)


def test_no_marker_is_no_verdict() -> None:
    assert marker.windows_entries(PureWindowsPath(PATH), [(SYSTEM, False, 1, READ), None]) is None
    assert judged(None) == Verdict(PATH)


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"owner": OWNER}, f"{PATH} isn't owned by Administrators, SYSTEM or TrustedInstaller"),
        ({"link": True}, f"{PATH} is a link"),
        ({"links": 2}, f"{PATH} has more than one link"),
        ({"file": READ | marker.DELETE}, f"you could replace {PATH}"),
        ({"file": READ | marker.WRITE_DAC}, f"you could replace {PATH}"),
        ({"file": READ | marker.WRITE_OWNER}, f"you could replace {PATH}"),
        ({"folder": READ | marker.FILE_DELETE_CHILD}, f"you could replace {PATH}"),
        ({"folder": READ | marker.DELETE}, "you could replace C:\\ProgramData\\Docket"),
        (
            {"folder": READ | marker.FILE_ADD_FILE},
            "C:\\ProgramData\\Docket lets you add files: run the README's lines again",
        ),
    ],
)
def test_a_marker_the_owner_could_have_made_or_can_replace_doesnt_count(change: dict[str, object], why: str) -> None:
    verdict = judged(entries(**change))  # pyright: ignore[reportArgumentType]
    assert verdict == Verdict(PATH, present=True, why=why)


def test_a_docket_folder_the_owner_made_first_fails_the_owner_check() -> None:
    facts = [
        (TRUSTED_INSTALLER, False, 1, STOCK_DRIVE),
        (SYSTEM, False, 1, STOCK_PROGRAM_DATA),
        (OWNER, False, 1, 0x1F01FF),
        (ADMINS, False, 1, READ),
    ]
    verdict = judged(marker.windows_entries(PureWindowsPath(PATH), facts))
    assert verdict.why == "C:\\ProgramData\\Docket isn't owned by Administrators, SYSTEM or TrustedInstaller"


def test_the_admin_floor_judges_owners_and_links_only() -> None:
    """With UAC off every process of the owner is an admin: no rights are asked, so nothing reads as replaceable."""
    facts = [(TRUSTED_INSTALLER, False, 1, 0), (SYSTEM, False, 1, 0), (ADMINS, False, 1, 0), (ADMINS, False, 1, 0)]
    assert judged(marker.windows_entries(PureWindowsPath(PATH), facts)).counts


def test_a_single_entry_path_is_judged_as_its_own_folder() -> None:
    assert marker.judge("/m", [Entry("/m", trusted=True)], owner="root").counts
    assert not marker.judge("/m", [Entry("/m", trusted=True, can_add=True)], owner="root").counts


@pytest.mark.parametrize(
    ("token", "problem"),
    [
        (Token(restricted=True), "Docket runs with a restricted token, which can't tell what you could change"),
        (Token(integrity=0x1000), "Docket runs below Medium integrity, which can't tell what you could change"),
        (Token(app_container=True), "Docket runs in an AppContainer, which can't tell what you could change"),
        (
            Token(deny_only=("S-1-5-32-544", "S-1-5-32-545")),
            "Docket's token has S-1-5-32-545 turned off, which UAC doesn't, so it can't tell what you could change",
        ),
    ],
)
def test_a_token_that_hides_the_owners_rights_refuses_the_marker_before_it_is_judged(
    token: Token, problem: str
) -> None:
    """A copy of the owner's token with Users deny-only would hide a right the owner has (O8)."""
    assert marker.token_problem(token) == problem
    assert judged(entries(), problem) == Verdict(PATH, present=True, why=problem)


def test_the_groups_uac_filters_for_an_admin_are_no_problem() -> None:
    uac = ("S-1-5-32-544", "S-1-5-114", "S-1-5-32-551", "S-1-5-21-1111111111-2222222222-3333333333-512")
    assert marker.token_problem(Token(deny_only=uac)) is None
    assert marker.token_problem(Token(integrity=0x3000)) is None
    assert not marker.uac_filters("S-1-5-21-1111111111-2222222222-3333333333-1001")


def test_a_check_that_fails_keeps_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> Verdict:
        raise OSError("access denied")

    monkeypatch.setattr(marker, "_here", broken)
    assert marker.here() == Verdict("the marker", present=True, why="it couldn't be checked: access denied")


@pytest.mark.skipif(sys.platform == "win32", reason="lstat and access(2) are macOS's and Linux's")
def test_a_marker_under_folders_the_owner_owns_doesnt_count(tmp_path: Path) -> None:
    folder = tmp_path / "Docket"
    folder.mkdir()
    (folder / marker.FILE).write_text("", encoding="utf-8")
    found = marker.posix_entries(folder / marker.FILE, root=False)
    verdict = marker.judge(str(folder / marker.FILE), found, owner="root")
    assert not verdict.counts
    assert verdict.why.endswith("isn't owned by root")
    assert marker.posix_entries(tmp_path / "none" / marker.FILE, root=False) is None


def test_every_windows_dll_loads_from_system32_only_and_nothing_reads_a_variable() -> None:
    """A DLL planted beside Python is never loaded: win32.dll is the one place a DLL loads, with System32's flag."""
    source = Path(sys.modules["docket.osprompt.marker"].__file__ or "").with_name("win32.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    loads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"WinDLL", "CDLL"}
    ]
    assert len(loads) == 1
    assert any(
        k.arg == "winmode" and isinstance(k.value, ast.Name) and k.value.id == "SYSTEM32" for k in loads[0].keywords
    )
    for path in source.parent.glob("*.py"):
        module = ast.parse(path.read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(module) if isinstance(n, ast.Attribute)}
        names |= {n.id for n in ast.walk(module) if isinstance(n, ast.Name)}
        assert not names & {"windll", "oledll", "cdll", "LoadLibrary", "LoadLibraryW", "LoadLibraryExW"}, path.name
        # And no variable chooses anything here: the prompt is the system's answer alone (DR-0574).
        assert not names & {"environ", "getenv"}, path.name
