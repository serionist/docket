"""The browser's way in: a one-time code `docket open` asks the service for, swapped for a session cookie.

The code is 32 random bytes, good once and for 60 seconds. It reaches the browser through a redirect page only the
user can read, never through a process's arguments, which any account on Linux reads from `/proc/<pid>/cmdline`, and
`docket open` also prints the link, for a browser that can't open a file in a hidden folder. Sessions live in the
service's memory for 12 hours at most and end when it stops.
"""

from __future__ import annotations

import html
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from docket import userconfig
from docket.service.system import SYSTEM, Runner, run, write_private

CODE_SECONDS = 60
SESSION_SECONDS = 12 * 3600
COOKIE = "docket_session"
REDIRECT_FILE = "docket-open.html"
# What `open_command` keeps as it is in a path: nothing a shell acts on inside double quotes.
OPEN_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~/?=&%+:@,")


@dataclass
class Sessions:
    clock: Callable[[], float] = time.monotonic
    _codes: dict[str, float] = field(default_factory=dict[str, float])
    _sessions: dict[str, float] = field(default_factory=dict[str, float])

    def new_code(self) -> str:
        self._forget_expired()
        code = secrets.token_urlsafe(32)
        self._codes[code] = self.clock() + CODE_SECONDS
        return code

    def redeem(self, code: str) -> str | None:
        """A new session for a code that is good, which is then used up; None for any other."""
        self._forget_expired()
        if self._codes.pop(code, None) is None:
            return None
        session = secrets.token_urlsafe(32)
        self._sessions[session] = self.clock() + SESSION_SECONDS
        return session

    def valid(self, session: str | None) -> bool:
        if not session:
            return False
        ends = self._sessions.get(session)
        if ends is None:
            return False
        if self.clock() >= ends:
            del self._sessions[session]
            return False
        return True

    def _forget_expired(self) -> None:
        now = self.clock()
        self._codes = {c: t for c, t in self._codes.items() if t > now}
        self._sessions = {s: t for s, t in self._sessions.items() if t > now}


def safe_next(target: str | None) -> str:
    """A path on this origin to go on to after the session starts; anything else goes to the start page."""
    if not target or not target.startswith("/") or target.startswith(("//", "/\\")) or any(c in target for c in "\r\n"):
        return "/"
    return target


def open_command(wanted: str) -> str:
    """The command that opens a page after signing in: `docket open "<path>"`, or `docket open` for the start page.
    The path is double-quoted, and whatever a shell could act on inside the quotes (`$`, a backquote, `!`, `"`, a
    backslash) is percent-encoded, as is anything else a URL path or query wouldn't carry as it is."""
    path = safe_next(wanted)
    if path in ("/", "/search"):
        return "docket open"
    encoded = "".join(ch if ch in OPEN_SAFE else "".join(f"%{b:02X}" for b in ch.encode()) for ch in path)
    return f'docket open "{encoded}"'


def moving_on(target: str, title: str = "Docket") -> str:
    """A small page that moves the browser on by itself, with a link for when it doesn't."""
    href = html.escape(target, quote=True)
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
        f'<meta http-equiv="refresh" content="0; url={href}"></head>'
        f'<body><p><a href="{href}">Open Docket</a></p></body></html>\n'
    )


def write_redirect(url: str, *, system: str = SYSTEM, runner: Runner = run) -> Path:
    """`docket open`'s page, written so only this user can read it, over the one the last `docket open` left."""
    path = userconfig.state_dir() / REDIRECT_FILE
    write_private(path, moving_on(url), system=system, runner=runner)
    return path
