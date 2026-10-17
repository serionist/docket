"""The console-code marker (DR-0574): a file only an admin or root can make, which sends this machine back to the
console code where a prompt is set up. It is the one way out of a refusal, on every system.

| System | Path |
|---|---|
| Windows | `Docket\\console-code` under ProgramData, from `SHGetKnownFolderPath`, never the environment |
| macOS | `/Library/Application Support/Docket/console-code` |
| Linux | `/etc/docket/console-code` |

It counts only when no process of the owner's could have put it there or can replace it. The question is whether an
entry on the path can be replaced, not whether the owner can add beside it: stock `C:\\` and `C:\\ProgramData` let
users make folders, and neither lets them replace these.

- Each entry, from the drive or `/` down to the file, is owned by root (Windows: Administrators, SYSTEM or
  TrustedInstaller) and isn't a symlink or a reparse point.
- The owner can replace none of them: on Windows the token lacks `DELETE`, `WRITE_DAC` and `WRITE_OWNER` on it and
  `FILE_DELETE_CHILD` on its folder (`AccessCheck`); elsewhere its folder isn't writable by the owner unless sticky
  (`access(2)`, so ACLs count).
- The marker's own folder gives the owner no right to add a file, and the file has one link.
- Windows judges with the owner's unelevated token, the linked one when Docket runs elevated, and refuses the marker
  first when that token is restricted, below Medium integrity, an AppContainer, or has deny-only groups beyond the ones
  UAC filters for an admin: a copy of the owner's token with Users deny-only would hide a right the owner has.
- With UAC off, or as root, every process of the owner is an admin: only the owner and link checks apply, the root
  floor.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath

FILE = "console-code"
MACOS = PurePath("/Library/Application Support/Docket", FILE)
LINUX = PurePath("/etc/docket", FILE)
MEDIUM = 0x2000
# The owners Windows trusts for the marker's path: Administrators, SYSTEM, TrustedInstaller.
TRUSTED = frozenset(
    {"S-1-5-32-544", "S-1-5-18", "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"}
)  # fmt: skip
# The groups UAC turns to deny-only in an admin's filtered token, as Microsoft lists them: the builtin admin-like
# groups, the local-admin account group, and the domain's admin groups by their relative ids.
UAC_BUILTIN = frozenset(
    {f"S-1-5-32-{rid}" for rid in (544, 547, 548, 549, 550, 551, 553, 554, 556, 569)} | {"S-1-5-114"}
)  # fmt: skip
UAC_DOMAIN_RIDS = frozenset({"498", "512", "516", "517", "518", "519", "520", "521"})
# The rights that replace an entry, and the folder rights that replace or add a child.
DELETE, WRITE_DAC, WRITE_OWNER = 0x00010000, 0x00040000, 0x00080000
FILE_ADD_FILE, FILE_DELETE_CHILD = 0x0002, 0x0040


@dataclass(frozen=True)
class Entry:
    """One entry of the marker's path, as the system describes it to the owner."""

    path: str
    trusted: bool
    link: bool = False
    replaceable: bool = False
    can_add: bool = False
    links: int = 1


@dataclass(frozen=True)
class Token:
    """What Windows says about the token the marker is judged with."""

    restricted: bool = False
    integrity: int = MEDIUM
    app_container: bool = False
    deny_only: tuple[str, ...] = ()


@dataclass(frozen=True)
class Verdict:
    path: str
    present: bool = False
    counts: bool = False
    why: str = ""


def uac_filters(sid: str) -> bool:
    if sid in UAC_BUILTIN:
        return True
    return sid.startswith("S-1-5-21-") and sid.rsplit("-", 1)[1] in UAC_DOMAIN_RIDS


def token_problem(token: Token) -> str | None:
    """Why a token can't vouch for what the owner could change, or None."""
    if token.restricted:
        return "Docket runs with a restricted token, which can't tell what you could change"
    if token.integrity < MEDIUM:
        return "Docket runs below Medium integrity, which can't tell what you could change"
    if token.app_container:
        return "Docket runs in an AppContainer, which can't tell what you could change"
    extra = [sid for sid in token.deny_only if not uac_filters(sid)]
    if extra:
        turned = ", ".join(extra)
        return f"Docket's token has {turned} turned off, which UAC doesn't, so it can't tell what you could change"
    return None


def judge(path: str, entries: Sequence[Entry] | None, *, owner: str, problem: str | None = None) -> Verdict:
    """The verdict on the marker at `path` from its entries, root first and the file last (None when it isn't there).
    `owner` names who must own each entry; `problem` is the token's, which refuses before anything is judged."""
    if entries is None:
        return Verdict(path)
    if problem is not None:
        return Verdict(path, present=True, why=problem)
    for entry in entries:
        if entry.link:
            return Verdict(path, present=True, why=f"{entry.path} is a link")
        if not entry.trusted:
            return Verdict(path, present=True, why=f"{entry.path} isn't owned by {owner}")
        if entry.replaceable:
            return Verdict(path, present=True, why=f"you could replace {entry.path}")
    folder = entries[-2] if len(entries) > 1 else entries[-1]
    if folder.can_add:
        return Verdict(path, present=True, why=f"{folder.path} lets you add files: run the README's lines again")
    if entries[-1].links != 1:
        return Verdict(path, present=True, why=f"{entries[-1].path} has more than one link")
    return Verdict(path, present=True, counts=True)


def windows_entries(path: PureWindowsPath, facts: Sequence[tuple[str, bool, int, int] | None]) -> list[Entry] | None:
    """The entries from each one's facts (owner SID, reparse point, links, rights granted), root first. The facts of
    the admin floor carry no rights, so nothing reads as replaceable."""
    if any(f is None for f in facts):
        return None
    known = [f for f in facts if f is not None]
    parts = [path.anchor, *(str(p) for p in reversed(path.parents[:-1])), str(path)]
    out: list[Entry] = []
    for i, (where, (sid, link, links, granted)) in enumerate(zip(parts, known, strict=True)):
        above = known[i - 1][3] if i else 0
        out.append(
            Entry(
                where,
                trusted=sid in TRUSTED,
                link=link,
                replaceable=bool(granted & (DELETE | WRITE_DAC | WRITE_OWNER)) or bool(above & FILE_DELETE_CHILD),
                can_add=bool(granted & FILE_ADD_FILE),
                links=links,
            )
        )
    return out


def posix_entries(path: PurePath, *, root: bool) -> list[Entry] | None:  # pragma: no cover - macOS's and Linux's
    """The entries from `lstat` and `access(2)`, root first; None when one isn't there. As root nothing is replaceable:
    the root floor."""
    parts = [Path(path.anchor), *reversed(Path(path).parents[:-1]), Path(path)]
    out: list[Entry] = []
    for i, where in enumerate(parts):
        try:
            info = os.lstat(where)
        except FileNotFoundError:
            return None
        above = parts[i - 1] if i else None
        writable_above = (
            above is not None
            and os.access(above, os.W_OK | os.X_OK)
            and not os.lstat(above).st_mode & stat.S_ISVTX
        )  # fmt: skip
        out.append(
            Entry(
                str(where),
                trusted=info.st_uid == 0,
                link=stat.S_ISLNK(info.st_mode),
                replaceable=not root and writable_above,
                can_add=not root and stat.S_ISDIR(info.st_mode) and os.access(where, os.W_OK | os.X_OK),
                links=info.st_nlink,
            )
        )
    return out


def here() -> Verdict:
    """The marker on this machine. A check that fails leaves the prompt in place: it never counts."""
    try:
        return _here()
    except Exception as e:  # any failure of the check keeps the prompt, never gives the code
        return Verdict("the marker", present=True, why=f"it couldn't be checked: {e}")


def _here() -> Verdict:  # pragma: no cover - one per system; test_osprompt_system runs each on its own
    if sys.platform == "win32":
        return windows_verdict()
    path = MACOS if sys.platform == "darwin" else LINUX
    return judge(str(path), posix_entries(path, root=os.geteuid() == 0), owner="root")


def windows_verdict(token: int | None = None) -> Verdict:  # pragma: no cover - Windows' own API
    """The marker under ProgramData, judged with `token` (a test's) or the owner's own."""
    if sys.platform != "win32":
        raise OSError("ProgramData is Windows'")
    from docket.osprompt import win32  # noqa: PLC0415

    path = PureWindowsPath(win32.program_data(), "Docket", FILE)
    if not os.path.lexists(path):
        return Verdict(str(path))
    owned = token is None
    floor = False
    if token is None:
        token, floor = win32.own_token()
    try:
        problem = None if floor or token is None else token_problem(Token(*win32.token_facts(token)))
        parts = [path.anchor, *(str(p) for p in reversed(path.parents[:-1])), str(path)]
        facts = [win32.entry_facts(part, None if floor else token) for part in parts]
        return judge(str(path), windows_entries(path, facts), owner="Administrators, SYSTEM or TrustedInstaller",
                     problem=problem)  # fmt: skip
    finally:
        if owned and token is not None:
            win32.close_handle(token)
