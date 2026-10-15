"""The folders `docket ingest` read on this machine (DR-0248), for the folder rule a live run's close follows (DR-0250).

`<private>/sync/folders.json` in the vault's private folder under the git dir: per machine, never committed, since a
folder's path belongs to one machine and can name its user. A run's folder is taken only when its real path is one of
these, or sits under one, and outside the vault. It is no wall against a process running as the user; it keeps the MCP
route from reading a folder nobody imported by hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from docket.errors import DocketError
from docket.vault.home import make_private
from docket.vault.staging import write_replacing
from docket.vault.vault import Vault

FOLDERS = "sync/folders.json"


def _path(vault: Vault) -> Path:
    return vault.home.private / FOLDERS


def imported(vault: Vault) -> dict[str, list[str]]:
    """The folders read per source; a file that doesn't read counts as none, since it only ever widens what a run may
    read."""
    try:
        data: Any = json.loads(_path(vault).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    folders: Any = cast(dict[str, Any], data).get("folders") if isinstance(data, dict) else None
    if not isinstance(folders, dict):
        return {}
    out: dict[str, list[str]] = {}
    for source, paths in cast(dict[Any, Any], folders).items():
        if isinstance(source, str) and isinstance(paths, list):
            out[source] = [p for p in cast(list[Any], paths) if isinstance(p, str)]
    return out


def record(vault: Vault, source: str, folder: Path) -> None:
    """Remember that `docket ingest` read this folder for a source."""
    real = str(folder.resolve())
    folders = imported(vault)
    known = folders.setdefault(source, [])
    if real in known:
        return
    known.append(real)
    path = _path(vault)
    # Under the git dir, so through make_private: it refuses a git dir that went missing rather than make one (DR-0270).
    make_private(vault.home.git_dir, path.parent)
    text = json.dumps({"schema_version": 1, "folders": folders}, indent=2, sort_keys=True) + "\n"
    write_replacing(vault.home, path, text, "sync-folders")


def allowed(vault: Vault, source: str, folder: str) -> Path:
    """A live run's folder: refused unless `docket ingest` read it, or a folder it sits under, for this source on this
    machine, and unless it stays outside the vault. A link given as the folder itself is refused."""
    given = Path(folder)
    if not given.is_absolute():
        raise DocketError(f"a run's folder is an absolute path; {folder} isn't one")
    try:
        if given.is_symlink() or given.is_junction():
            raise DocketError(f"{folder} is a link; name the folder it leads to")
        real = given.resolve()
        is_dir = real.is_dir()
    except (OSError, ValueError):
        # An embedded NUL, a path past the system's limit: no folder this machine can name.
        raise DocketError(f"{folder!r} can't be read as a folder on this machine") from None
    if not is_dir:
        raise DocketError(f"{folder} isn't a folder")
    if real.is_relative_to(vault.root.resolve()):
        raise DocketError(f"{folder} is inside the vault; a run reads only folders outside it")
    for known in imported(vault).get(source, []):
        if real.is_relative_to(Path(known)):
            return real
    raise DocketError(
        f"{folder} wasn't imported on this machine; run docket ingest {source} {folder} in your own terminal first, "
        "then close the run with it"
    )
