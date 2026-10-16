"""A folder source's records (DR-0482, DR-0483, DR-0487): their rids, kinds, titles, `native`, `ts`, and gone versions.

| Record | rid | kind | title | text |
|---|---|---|---|---|
| a file | `<source>:<id>` | `document` | its path in the folder | docx, md, txt: all of it; a file not read: why |
| a pdf page | `<file rid>/page3` | `page` | `<name>, page 3` | the page |
| a slide | `<file rid>/slide4` | `slide` | `<name>, slide 4` | the slide, then its speaker notes |
| a sheet | `<file rid>/sheet2` | `sheet` | `<name>, sheet Costs` | a paragraph per row, row n as paragraph n |

`<id>` is the first 12 hex digits of the sha256 of the file's path from the folder's root, NFC and /-separated. The
rid shape is stored in raw, anchors and the ledger, so it never changes. A file's identity in `native` is its bytes'
sha256 and size for pdf, docx, xlsx and pptx, and the sha256 of its redacted text for md and txt: nothing derived
from text before redaction reaches raw. A gone version keeps the title and says in one line since when the file or
part wasn't read, with `native.gone`.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Collection
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from docket.documents.parts import Part
from docket.vault.records import RawRecord

VIA = "docket-scan"
FILE_KIND = "document"
PART_KINDS = frozenset({"page", "slide", "sheet"})
ID_HEX = 12
GONE_FILE = "Not read from the folder since {day}."
GONE_PART = "Not read from the file since {day}."
# A file's identity in native: its bytes' hash for the formats whose bytes the text can't rebuild, else its redacted
# text's.
BYTES_HASH = "sha256"
TEXT_HASH = "redacted_sha256"
_PART_RID = re.compile(r"/(page|slide|sheet)([1-9][0-9]*)$")
# A folder source's rid: a file's 12 hex digits, and a part's kind and number under it (DR-0482).
FOLDER_RID = re.compile(r"^([^:]+):[0-9a-f]{12}(?:/(page|slide|sheet)([1-9][0-9]*))?$")


def file_id(rel: str) -> str:
    """A file's id: from its path inside the folder, so every machine and every place the folder moves to agree."""
    return hashlib.sha256(unicodedata.normalize("NFC", rel).encode("utf-8")).hexdigest()[:ID_HEX]


def file_rid(source: str, rel: str) -> str:
    return f"{source}:{file_id(rel)}"


def part_rid(rid: str, kind: str, n: int) -> str:
    return f"{rid}/{kind}{n}"


def file_of(rid: str) -> str:
    """The file record a part belongs to; a file's own rid for a file."""
    return _PART_RID.sub("", rid)


def is_folder_rid(rid: str, sources: Collection[str]) -> bool:
    """Whether a rid has a folder source's shape and its source is one of `sources`, the folder sources vault.yaml
    names: a mapper's record of kind `page` or `document` is neither."""
    m = FOLDER_RID.match(rid)
    return m is not None and m.group(1) in sources


def is_folder(record: RawRecord) -> bool:
    """A record the scan wrote: by its `via` and its rid's shape."""
    return record.via == VIA and FOLDER_RID.match(record.rid) is not None


def is_gone(record: RawRecord) -> bool:
    return is_folder(record) and "gone" in record.native


def is_not_read(record: RawRecord) -> bool:
    """A folder file whose newest bytes weren't read: past a cap, damaged or protected, its text the line saying why."""
    return is_folder(record) and record.kind == FILE_KIND and "problem" in record.native


def part_order(rid: str) -> tuple[str, int]:
    """A part's place among its file's parts: by kind, then by its number as a number, so page 2 comes before page
    12."""
    m = _PART_RID.search(rid)
    return (m.group(1), int(m.group(2))) if m else ("", 0)


def identity(record: RawRecord) -> str | None:
    """A file record's identity hash as raw holds it; None for a gone version, a part, or a file with none."""
    value = record.native.get(BYTES_HASH, record.native.get(TEXT_HASH))
    return value if isinstance(value, str) and not is_gone(record) else None


def readable(versions: list[RawRecord]) -> RawRecord | None:
    """The last version with text, neither gone nor not read: what `read` shows beside the newest when that one is,
    and where anchors on it resolve (DR-0491)."""
    return next((v for v in reversed(versions) if not (is_gone(v) or is_not_read(v))), None)


def name_of(rel: str) -> str:
    return PurePosixPath(rel).name


def file_record(
    source: str, rel: str, *, ts: datetime, fetched: datetime, text: str, engine: str | None, native: dict[str, Any]
) -> RawRecord:
    return RawRecord(
        rid=file_rid(source, rel),
        source=source,
        kind=FILE_KIND,
        ts=ts,
        title=rel,
        text=text,
        native=native,
        fetched=fetched,
        via=VIA,
        engine=engine,
    )


def part_record(file: RawRecord, part: Part, *, ts: datetime, fetched: datetime, engine: str | None) -> RawRecord:
    native: dict[str, Any] = {"hidden": True} if part.hidden else {}
    return RawRecord(
        rid=part_rid(file.rid, part.kind, part.n),
        source=file.source,
        kind=part.kind,
        container=file.rid,
        thread=file.rid,
        ts=ts,
        title=f"{name_of(file.title or '')}, {part.title}",
        text=part.text,
        native=native,
        fetched=fetched,
        via=VIA,
        engine=engine,
    )


def gone_version(newest: RawRecord, *, at: datetime, day: str, moved_to: str | None = None) -> RawRecord:
    """A file's or a part's gone version: its title kept, its text one line, no identity, at the scan's time."""
    part = newest.kind in PART_KINDS
    native: dict[str, Any] = {"gone": day}
    if "format" in newest.native:
        native["format"] = newest.native["format"]
    if moved_to is not None:
        native["moved_to"] = moved_to
    return RawRecord(
        rid=newest.rid,
        source=newest.source,
        kind=newest.kind,
        container=newest.container,
        thread=newest.thread,
        ts=at,
        title=newest.title,
        text=(GONE_PART if part else GONE_FILE).format(day=day),
        native=native,
        fetched=at,
        via=VIA,
    )


def place(kind: str, title: str | None, first: int | None = None, last: int | None = None) -> str | None:
    """Where a hit sits in its file, for search: `page 3`, `slide 4`, and a sheet's rows in A1 notation, `Costs!41:80`
    (row n is paragraph n); None for anything else."""
    if kind not in PART_KINDS or not title:
        return None
    if kind != "sheet":
        # `<name>, page 3`: the part's own words come last.
        return title.rsplit(", ", 1)[-1]
    sheet = title.rsplit(", sheet ", 1)[-1].removesuffix(" (hidden)")
    if first is None or last is None:
        return f"sheet {sheet}"
    quoted = sheet if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", sheet) else "'" + sheet.replace("'", "''") + "'"
    return f"{quoted}!{first}:{last}"


def resolving(versions: list[RawRecord]) -> RawRecord:
    """The version an anchor on a record resolves against: its latest, but for a folder record whose latest is gone
    or not read, the last one with text (DR-0491). `versions` is never empty."""
    newest = versions[-1]
    return (readable(versions) or newest) if is_gone(newest) or is_not_read(newest) else newest


def gone_note(gone: RawRecord) -> str:
    """What a span read from an earlier version says about the newest, gone or not read, naming where the file moved
    (DR-0491)."""
    moved = gone.native.get("moved_to")
    where = f" It moved to {moved}." if isinstance(moved, str) else ""
    return f"the last version with text; the latest says: {gone.text}{where}"
