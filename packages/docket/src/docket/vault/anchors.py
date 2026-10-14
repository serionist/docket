"""The anchor grammar: how a line of a processed doc says which raw it came from, and the locators `read` takes.

A content line ends with its anchors, separated by spaces or tabs:

- `@12:08`, `@1:02:15`, `@14:30-14:52`: a time or a stretch of the one record a doc covers;
- `[r:<rid>@12:08]`: a time or a stretch of any record the doc covers;
- `[r:<rid>#2]`, `[r:<rid>#2-4]`: paragraphs of a record's text, counted from 1;
- `[r:<rid>]`: the whole record.

Times are offsets from the record's start in whole seconds: `m:ss` or `mm:ss` (minutes up to 99), or `h:mm:ss`. In an
anchor a rid is written with `%` as `%25` and `]` as `%5D`, and its last `@` or `#` as `%40` or `%23` only when the
rid's own tail would read as a position; the parser splits at the last `@` or `#` whose rest is a whole position, then
decodes those four codes and nothing else. Docs in users' vaults depend on all of this: it is a one-way door (DR-0025).

Lines and paragraphs are counted the same way for a doc and a record: CRLF and a lone CR read as LF, a line is the text
between two LFs, a blank line holds only spaces, tabs and no-break spaces, and a paragraph is a run of non-blank lines.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from docket.vault.records import RID

Kind = Literal["time", "part", "record"]

_TIME = r"(?:\d{1,2}:[0-5]\d:[0-5]\d|\d{1,2}:[0-5]\d)"
_TIME_PARTS = re.compile(r"(?:(\d{1,2}):([0-5]\d):([0-5]\d)|(\d{1,2}):([0-5]\d))")
_TIMES = re.compile(rf"({_TIME})(?:-({_TIME}))?")
_PARTS = re.compile(r"([1-9]\d*)(?:-([1-9]\d*))?")
_DECODE = re.compile(r"%(25|5[dD]|40|23)")
_CODES = {"25": "%", "5d": "]", "5D": "]", "40": "@", "23": "#"}
_RESERVED = re.compile(r"\[[a-z]+:\S*\]")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_SEPARATOR = re.compile(r"[\\/]")

# The characters a blank line may hold. The no-break space is one, because HTML mail turned into text writes its
# empty lines with it.
BLANK = frozenset(" \t\N{NO-BREAK SPACE}")


class AnchorError(ValueError):
    """A token that starts like an anchor but isn't one."""


@dataclass(frozen=True)
class Anchor:
    """Where a line points: a record (None for the short form, meaning the doc's one covered record) and a position
    in it. `start` is seconds for a time and the first paragraph for a part; `end` closes a stretch or a range."""

    rid: str | None
    kind: Kind
    start: int = 0
    end: int | None = None
    token: str = ""

    @property
    def last(self) -> int:
        return self.start if self.end is None else self.end

    @property
    def text(self) -> str:
        """The anchor as Docket writes it."""
        return format_anchor(self.rid, self.kind, self.start, self.end)


@dataclass(frozen=True)
class Trailing:
    """A line split into its text and the anchors at its end; `errors` are anchor-like tokens that don't parse."""

    text: str
    anchors: list[Anchor]
    errors: list[str]


@dataclass(frozen=True)
class Locator:
    """What `read` was asked for: a doc by id, a doc by path, or a record with an optional position."""

    kind: Literal["doc", "path", "record"]
    value: str
    anchor: Anchor | None = None


# -- counting -----------------------------------------------------------------------------------------------------


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_lines(text: str) -> list[str]:
    """Lines between LFs only: not str.splitlines(), which also splits on U+2028, form feeds and \\x1c to \\x1e."""
    return normalize_newlines(text).split("\n")


def is_blank(line: str) -> bool:
    return all(ch in BLANK for ch in line)


def paragraphs(text: str) -> list[str]:
    """Runs of non-blank lines; a run of blank lines is one break, and blank lines at either end don't count."""
    out: list[str] = []
    run: list[str] = []
    for line in split_lines(text):
        if is_blank(line):
            if run:
                out.append("\n".join(run))
                run = []
        else:
            run.append(line)
    if run:
        out.append("\n".join(run))
    return out


# -- times and positions ------------------------------------------------------------------------------------------


def parse_time(text: str) -> int:
    """Seconds from `m:ss`, `mm:ss` or `h:mm:ss`."""
    m = _TIME_PARTS.fullmatch(text)
    if m is None:
        raise AnchorError(f"{text!r} isn't a time: m:ss, mm:ss or h:mm:ss, with minutes and seconds 00-59")
    if m.group(1) is not None:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return int(m.group(4)) * 60 + int(m.group(5))


def format_time(seconds: int) -> str:
    """`mm:ss` under an hour, `h:mm:ss` from an hour."""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _is_position(mark: str, rest: str) -> bool:
    return (_TIMES if mark == "@" else _PARTS).fullmatch(rest) is not None


def _position(mark: str, rest: str) -> tuple[Kind, int, int | None]:
    if mark == "@":
        m = _TIMES.fullmatch(rest)
        if m is None:
            raise AnchorError(f"{rest!r} isn't a time (m:ss, mm:ss or h:mm:ss, minutes and seconds 00-59) or a-b")
        start = parse_time(m.group(1))
        end = parse_time(m.group(2)) if m.group(2) else None
        if end is not None and end < start:
            raise AnchorError(f"the stretch {rest} ends before it starts")
        return "time", start, end
    # A paragraph position is only read where _tail found a whole one, so it parses.
    first_text, _, last_text = rest.partition("-")
    first = int(first_text)
    last = int(last_text) if last_text else None
    if last is not None and last < first:
        raise AnchorError(f"the range {rest} ends before it starts")
    return "part", first, last


# -- rids in anchors ----------------------------------------------------------------------------------------------


def _tail(text: str) -> int:
    """Where a position starts in an anchor's content, or -1: the last `@` or `#` whose rest is a whole position."""
    i = max(text.rfind("@"), text.rfind("#"))
    return i if i > 0 and _is_position(text[i], text[i + 1 :]) else -1


def encode_rid(rid: str) -> str:
    text = rid.replace("%", "%25").replace("]", "%5D")
    i = _tail(text)
    if i > 0:
        text = text[:i] + ("%40" if text[i] == "@" else "%23") + text[i + 1 :]
    return text


def decode_rid(text: str) -> str:
    return _DECODE.sub(lambda m: _CODES[m.group(1)], text)


def format_anchor(rid: str | None, kind: Kind = "record", start: int = 0, end: int | None = None) -> str:
    """An anchor as Docket writes it: the short form when there is no rid, else `[r:<rid><position>]`."""
    if kind == "time":
        position = "@" + format_time(start) + ("" if end is None else "-" + format_time(end))
    elif kind == "part":
        position = f"#{start}" + ("" if end is None else f"-{end}")
    else:
        position = ""
    if rid is None:
        if kind != "time":
            raise ValueError("a short anchor is a time: @mm:ss")
        return position
    return f"[r:{encode_rid(rid)}{position}]"


def parse_anchor(token: str) -> Anchor:
    if token.startswith("@"):
        kind, start, end = _position("@", token[1:])
        return Anchor(None, kind, start, end, token)
    if not (token.startswith("[r:") and token.endswith("]") and len(token) > 4):
        raise AnchorError("isn't an anchor: @mm:ss, or [r:<rid>] with an optional @mm:ss or #n")
    content = token[3:-1]
    if "]" in content or any(ch.isspace() for ch in content):
        raise AnchorError("a rid in an anchor writes ] as %5D, % as %25, and has no spaces")
    i = _tail(content)
    rid = decode_rid(content if i < 0 else content[:i])
    if not RID.match(rid):
        raise AnchorError(f"{rid!r} isn't a rid: <source>:<id>")
    if i < 0:
        return Anchor(rid, "record", token=token)
    kind, start, end = _position(content[i], content[i + 1 :])
    return Anchor(rid, kind, start, end, token)


# -- lines --------------------------------------------------------------------------------------------------------


def _anchor_like(token: str) -> bool:
    return (
        (len(token) > 1 and token[0] == "@" and token[1] in "0123456789")
        or token.startswith("[r:")
        or _RESERVED.fullmatch(token) is not None
    )


def trailing_anchors(line: str) -> Trailing:
    """The anchors at the end of a line. A table row puts them at the end of its last cell. A token at the end that
    starts like an anchor but doesn't parse is an error, and so is `[<letters>:...]`, kept for later anchor kinds."""
    body = line.rstrip(" \t")
    if body.lstrip(" \t").startswith("|") and body.endswith("|") and len(body.strip(" \t")) > 1:
        body = body[:-1].rstrip(" \t")
    anchors: list[Anchor] = []
    errors: list[str] = []
    while body:
        cut = max(body.rfind(" "), body.rfind("\t"))
        token = body[cut + 1 :]
        if not _anchor_like(token):
            break
        if token.startswith(("@", "[r:")):
            try:
                anchors.append(parse_anchor(token))
            except AnchorError as e:
                errors.append(f"{token}: {e}")
        else:
            errors.append(f"{token}: only [r:...] is an anchor; other [<letters>:...] forms are kept for later")
        body = body[: cut + 1].rstrip(" \t") if cut >= 0 else ""
    anchors.reverse()
    errors.reverse()
    return Trailing(body, anchors, errors)


# -- locators -----------------------------------------------------------------------------------------------------


def parse_locator(text: str, doc_types: Collection[str]) -> Locator:
    """A doc id (`<type>:<slug>`); a doc's path (ends in .md, no colon before its first separator, or a drive);
    `r:` and a record part, or the same in brackets; else a bare rid, taken exactly as written."""
    value = text.strip()
    if value.startswith(("[", "r:")):
        anchor = parse_anchor(value if value.startswith("[") else f"[{value}]")
        return Locator("record", anchor.rid or "", anchor)
    first = _SEPARATOR.split(value, maxsplit=1)[0]
    if value.endswith(".md") and (":" not in first or _DRIVE.match(value)):
        return Locator("path", value)
    if value.split(":", 1)[0] in doc_types:
        return Locator("doc", value)
    if not RID.match(value):
        raise AnchorError(f"{value!r} is neither a doc id, a doc's path, nor a rid")
    return Locator("record", value, Anchor(value, "record", token=value))
