"""Text as Docket keeps it for a document (DR-0483): NFC, LF line ends, no control characters but the tab, paragraphs
apart by one blank line, and none over 500 words.

md and txt are decoded here too, with no parser: UTF-8 with or without a BOM, UTF-16 by its BOM, else Windows-1252.
That is why the scan may read them in its own process (DR-0485).
"""

from __future__ import annotations

import codecs
import re
import unicodedata
from collections.abc import Iterable

PARAGRAPH_WORDS = 500
WINDOWS_1252 = "decoded as Windows-1252"

# C0 and C1 controls and DEL, less the tab and the line feed; vertical tab and form feed are line breaks first.
_CONTROL = re.compile("[\\x00-\\x08\\x0e-\\x1f\\x7f-\\x9f]")
_BREAKS = re.compile("\\r\\n|[\\r\\x0b\\x0c\\x1c-\\x1e\\x85\\u2028\\u2029]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"\S+\s*")


def clean(text: str) -> str:
    """NFC, every kind of line break as LF, no control characters but the tab, no spaces at the ends of lines."""
    text = _CONTROL.sub("", _BREAKS.sub("\n", unicodedata.normalize("NFC", text)))
    return "\n".join(line.rstrip(" \t\N{NO-BREAK SPACE}") for line in text.split("\n"))


def one_line(text: str) -> str:
    """A cell's or a row's text on one line: its line breaks as spaces, its runs of spaces as one."""
    return " ".join(clean(text).split())


def _words(text: str) -> int:
    return len(text.split())


def _pieces(sentence: str, limit: int) -> list[str]:
    """A sentence cut at the last space before every `limit`th word."""
    words = [m.group(0) for m in _WORD.finditer(sentence)]
    return ["".join(words[k : k + limit]).rstrip() for k in range(0, len(words), limit)]


def split_long(paragraph: str, limit: int = PARAGRAPH_WORDS) -> list[str]:
    """A paragraph of at most `limit` words as it is; a longer one cut at sentence ends into paragraphs of at most
    `limit` words, and a sentence longer than that at the last space before the limit."""
    if _words(paragraph) <= limit:
        return [paragraph]
    out: list[str] = []
    current: list[str] = []
    count = 0
    for sentence in _SENTENCE_END.split(paragraph):
        for piece in _pieces(sentence, limit):
            n = _words(piece)
            if current and count + n > limit:
                out.append(" ".join(current))
                current, count = [], 0
            current.append(piece)
            count += n
    if current:
        out.append(" ".join(current))
    return out


def paragraphs(blocks: Iterable[str], *, split: bool = True) -> list[str]:
    """Blocks as paragraphs: each cleaned, its blank lines dropped so it stays one paragraph, empty ones left out, a
    long one split unless `split` is off (a sheet's row, which must stay one paragraph)."""
    out: list[str] = []
    for block in blocks:
        lines = [line for line in clean(block).split("\n") if line.strip(" \t\N{NO-BREAK SPACE}")]
        if not lines:
            continue
        joined = "\n".join(lines)
        out.extend(split_long(joined) if split else [joined])
    return out


def join(paras: Iterable[str]) -> str:
    return "\n\n".join(paras)


def blocks_of(text: str) -> list[str]:
    """A plain text's paragraphs: its runs of non-blank lines, once `clean` has emptied its blank ones."""
    return re.split(r"\n{2,}", clean(text))


def decode(data: bytes) -> tuple[str, str | None]:
    """An md or txt file's text, and a note when it wasn't UTF-8: UTF-16 by its BOM, else Windows-1252."""
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8) :].decode("utf-8", "replace"), None
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", "replace"), None
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace"), WINDOWS_1252
