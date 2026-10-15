"""This clone's machine name, which every run file and cursor carries."""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

from docket.errors import DocketError
from docket.vault.home import Home, make_private

NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
_ENDS_IN_NUMBER = re.compile(r"-[0-9]+$")


def validate(name: str) -> str:
    """A machine name: lowercase letters, digits and inner hyphens, at most 40 characters, not ending in `-<digits>`."""
    if not NAME.match(name):
        raise DocketError(f"machine name {name!r}: use lowercase letters, digits and hyphens, at most 40 characters")
    if _ENDS_IN_NUMBER.search(name):
        raise DocketError(f"machine name {name!r} can't end in a hyphen and digits; try {name.rsplit('-', 1)[0]}")
    return name


def from_host(host: str) -> str:
    """A valid name made from a host name: `DESKTOP-4K2J9QX` becomes `desktop-4k2j9qx`, `LAPTOP-2` `laptop-2-m`."""
    name = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")[:40].strip("-") or "machine"
    if _ENDS_IN_NUMBER.search(name):
        name = f"{name[:38]}-m"
    return name


def machine_file(private: Path) -> Path:
    """Where the wizard keeps this clone's name: in Docket's private folder under the git dir, so it is never
    committed or shared."""
    return private / "machine"


def resolve(private: Path | None = None) -> str:
    """This machine's name: `DOCKET_MACHINE`, else the clone's own file, else the host name."""
    env = os.environ.get("DOCKET_MACHINE")
    if env:
        return validate(env.strip())
    if private is not None:
        path = machine_file(private)
        if path.is_file():
            return validate(path.read_text(encoding="utf-8").strip())
    return from_host(socket.gethostname())


def remember(home: Home, name: str) -> None:
    path = machine_file(make_private(home.git_dir, home.private))
    path.write_text(validate(name) + "\n", encoding="utf-8")
