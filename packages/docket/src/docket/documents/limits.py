"""The caps a document is read under, and the error that keeps a file out whole when it passes one (DR-0485).

A file is stored whole or not at all: one past a cap, damaged or protected gets a record saying why and no text, so
the never-store check always covers all the text a file has. A `NotReadError`'s detail is Docket's own words and
numbers, never a value from the file.
"""

from __future__ import annotations

from dataclasses import dataclass

MIB = 1024 * 1024


@dataclass(frozen=True)
class Limits:
    file_bytes: int = 64 * MIB
    pages: int = 2_000
    slides: int = 1_000
    sheets: int = 100
    rows: int = 10_000
    columns: int = 256
    part_text: int = 1 * MIB
    file_text: int = 4 * MIB
    zip_members: int = 10_000
    member_bytes: int = 64 * MIB
    package_bytes: int = 256 * MIB
    # A member over this many bytes may be compressed at most `ratio` to 1.
    ratio_from: int = 1 * MIB
    ratio: int = 100


LIMITS = Limits()


class NotReadError(Exception):
    """A file kept out whole: `reason` is a short code, `detail` says why in Docket's words."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def mib(n: int) -> str:
    """Bytes as whole MB, rounded up, so a file just over a cap never reads as equal to it."""
    return f"{-(-n // MIB):,} MB"


def over(what: str, n: int, cap: int) -> NotReadError:
    return NotReadError(what, f"{n:,} {what}, over the cap of {cap:,}")
