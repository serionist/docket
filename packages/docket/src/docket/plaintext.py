"""Small text helpers that import nothing of Docket's: the words and the fold the index and the curate log's keys share,
and text made safe to print. The session-start hook reads the curate log through them without loading the index.
"""

from __future__ import annotations

import re
import unicodedata

WORD = re.compile(r"[^\W_]+")
# C0 and C1 control characters and DEL, but newline and tab.
CONTROL = re.compile("[\\x00-\\x08\\x0b-\\x1f\\x7f-\\x9f]")


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def words(text: str) -> list[str]:
    """The words of a text, lowercased, accents kept: the stemmers need them."""
    return [w.lower() for w in WORD.findall(nfc(text))]


def fold(word: str) -> str:
    """A word as FTS5 stores it: lowercased, diacritics removed."""
    decomposed = unicodedata.normalize("NFD", word)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def printable(text: str) -> str:
    """Control characters written out (`\\x1b`): raw text from a source can carry terminal codes (OSC 52 sets the
    clipboard), and a terminal or Claude Code's screen would act on them."""
    return CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text)
