"""The Recording page's audio (#159): the stored file, decrypted through the one reader (DR-0551) for each request and
served with byte ranges, so the browser's player seeks to a line's time.

| Request | Answer |
|---|---|
| no Range, or one the server may ignore (several ranges, or one that doesn't parse) | 200 and the whole file |
| `bytes=a-b`, `bytes=a-`, `bytes=-n` inside the file | 206 and exactly those bytes, with `Content-Range` |
| a range that starts past the end, or `bytes=-0` | 416 with `Content-Range: bytes */<size>` |

Every answer is `Cache-Control: no-store`: the browser keeps no copy of the audio. Only `ok` plays: any other state of
the reader answers 404 with its reason, and nothing about the audio, the rid included, is logged.
"""

from __future__ import annotations

import re
from typing import Literal

from starlette.responses import PlainTextResponse, Response

RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
HEADERS = {"Cache-Control": "no-store", "Accept-Ranges": "bytes", "Content-Disposition": "inline"}


def span(header: str | None, size: int) -> tuple[int, int] | Literal["unsatisfiable"] | None:
    """The bytes a Range header asks for, inclusive; None for the whole file; "unsatisfiable" for a range the file
    can't answer."""
    if header is None or "," in header:
        return None
    found = RANGE.match(header.strip())
    if found is None or (not found.group(1) and not found.group(2)):
        return None
    first, last = found.group(1), found.group(2)
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
    """The file, or the part a Range header asks for, exactly."""
    size = len(data)
    wanted = span(header, size)
    if isinstance(wanted, str):
        return Response(status_code=416, headers={**HEADERS, "Content-Range": f"bytes */{size}"})
    if wanted is None:
        return Response(data, media_type=media_type, headers=HEADERS)
    start, end = wanted
    return Response(
        data[start : end + 1],
        status_code=206,
        media_type=media_type,
        headers={**HEADERS, "Content-Range": f"bytes {start}-{end}/{size}"},
    )


def refused(why: str) -> Response:
    """No audio to play, and why, in the reader's words."""
    return PlainTextResponse(f"no audio: {why}", status_code=404, headers={"Cache-Control": "no-store"})
