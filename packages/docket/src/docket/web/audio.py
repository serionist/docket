"""The Recording page's audio (#159, DR-0576): the stored file, decrypted through the one reader (DR-0551) and served
with byte ranges, so the browser's player seeks to a line's time.

| Request | Answer |
|---|---|
| no Range, or one the server may ignore (several ranges, or one that doesn't parse) | 200 and the whole file |
| `bytes=a-b`, `bytes=a-`, `bytes=-n` inside the file | 206 and exactly those bytes, with `Content-Range` |
| a range that starts past the end, or `bytes=-0` | 416 with `Content-Range: bytes */<size>` |
| HEAD | the headers alone, the size from the file's age framing, nothing decrypted |

Every answer is `Cache-Control: no-store`: the browser keeps no copy of the audio. The reader's state is read again on
every request, so a file that stops reading ok stops playing at once; only then are its decrypted bytes taken from
`Plain`, which keeps one file's for a minute after its last request, so a player's run of range requests decrypts the
file once. pyrage decrypts a whole stream or nothing, so the bytes a range needs can't be decrypted alone. Nothing about
the audio, the rid included, is logged.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from starlette.responses import PlainTextResponse, Response

RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
# A byte position never needs more digits; a longer one is a Range that doesn't parse, never a number to build.
DIGITS = 20
HEADERS = {"Cache-Control": "no-store", "Accept-Ranges": "bytes", "Content-Disposition": "inline"}
KEEP_SECONDS = 60.0


def span(header: str | None, size: int) -> tuple[int, int] | Literal["unsatisfiable"] | None:
    """The bytes a Range header asks for, inclusive; None for the whole file; "unsatisfiable" for a range the file
    can't answer."""
    if header is None or "," in header:
        return None
    found = RANGE.match(header.strip())
    if found is None or (not found.group(1) and not found.group(2)):
        return None
    first, last = found.group(1), found.group(2)
    if len(first) > DIGITS or len(last) > DIGITS:
        return None
    if not first:
        tail = int(last)
        return "unsatisfiable" if tail == 0 or size == 0 else (max(0, size - tail), size - 1)
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size:
        return "unsatisfiable"
    if end < start:
        return None
    return start, end


def ranged(data: bytes, media_type: str, header: str | None) -> Response:
    """The file, or the part a Range header asks for, exactly; a part is a view of the bytes, never a copy."""
    size = len(data)
    wanted = span(header, size)
    if isinstance(wanted, str):
        return Response(status_code=416, headers={**HEADERS, "Content-Range": f"bytes */{size}"})
    if wanted is None:
        return Response(data, media_type=media_type, headers=HEADERS)
    start, end = wanted
    return Response(
        memoryview(data)[start : end + 1],
        status_code=206,
        media_type=media_type,
        headers={**HEADERS, "Content-Range": f"bytes {start}-{end}/{size}"},
    )


def head(size: int, media_type: str) -> Response:
    """HEAD: what GET would say of the whole file, without its bytes."""
    return Response(b"", media_type=media_type, headers={**HEADERS, "Content-Length": str(size)})


def refused(why: str) -> Response:
    """No audio to play, and why, in the reader's words."""
    return PlainTextResponse(f"no audio: {why}", status_code=404, headers={"Cache-Control": "no-store"})


def token_refused() -> Response:
    """The token is Docket's API, Claude's too: the audio plays in the browser, with its session (DR-0576)."""
    return PlainTextResponse("the audio plays in the browser, with its session", status_code=403)


class Plain:
    """One stored file's decrypted bytes, kept in memory a short while: one file at most, keyed by its path and its
    modification time, and dropped `seconds` after its last request."""

    def __init__(self, seconds: float = KEEP_SECONDS) -> None:
        self.seconds = seconds
        self._lock = threading.Lock()
        self._key: tuple[str, int] | None = None
        self._data: bytes | None = None
        self._timer: threading.Timer | None = None

    @staticmethod
    def _key_of(path: Path) -> tuple[str, int]:
        return str(path), path.stat().st_mtime_ns

    def get(self, path: Path, read: Callable[[], bytes | None]) -> bytes | None:
        """The file's bytes: kept ones while its path and mtime hold, else `read`'s, kept in their place."""
        key = self._key_of(path)
        with self._lock:
            if self._key == key and self._data is not None:
                self._keep()
                return self._data
        data = read()
        with self._lock:
            self._key, self._data = (key, data) if data is not None else (None, None)
            self._keep()
        return data

    def size(self, path: Path) -> int | None:
        """The kept bytes' length when they are this file's."""
        with self._lock:
            return len(self._data) if self._data is not None and self._key == self._key_of(path) else None

    def _keep(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.seconds, self.drop)
        self._timer.daemon = True
        self._timer.start()

    def drop(self) -> None:
        with self._lock:
            self._key, self._data = None, None
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
