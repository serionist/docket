"""What a document's text comes as: a file's format, its parts (a pdf's pages, a deck's slides, a workbook's sheets)
and what the reader found. Light on purpose: the scan, `read` and the extraction's runner import it, and none of them
load a reader's libraries (DR-0485)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Format = Literal["pdf", "docx", "xlsx", "pptx", "md", "txt"]
FORMATS: dict[str, Format] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".md": "md",
    ".txt": "txt",
}
# Read in the scan's own process, decoded and never parsed (DR-0485).
PLAIN: frozenset[Format] = frozenset({"md", "txt"})
PartKind = Literal["page", "slide", "sheet"]


@dataclass(frozen=True)
class Part:
    kind: PartKind
    n: int
    # The part's own words: `page 3`, `slide 4`, `sheet Costs`; the record adds the file's name.
    title: str
    text: str
    hidden: bool = False


@dataclass(frozen=True)
class Extracted:
    """What a file says: one text for docx, md and txt, a part each for a pdf's pages, a deck's slides and a workbook's
    sheets, and the people its core properties and comments name."""

    format: Format
    engine: str
    text: str = ""
    parts: list[Part] = field(default_factory=list[Part])
    creator: str | None = None
    last_modified_by: str | None = None
    authors: list[str] = field(default_factory=list[str])
    note: str | None = None


def format_of(name: str) -> Format | None:
    dot = name.rfind(".")
    return FORMATS.get(name[dot:].lower()) if dot >= 0 else None
