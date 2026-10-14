"""The never-store lists: whether a record names someone, sits somewhere or says something the user listed.

Matching fails closed. Too broad drops a work record; too narrow stores a private one, so case, accents and the
`<source>:` prefix never decide. The answer names the list, never the entry, so nothing downstream can log what matched.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterator
from functools import lru_cache
from typing import Any, Literal, cast

from docket.vault.config import NeverStore
from docket.vault.records import RawRecord

ListName = Literal["people", "containers", "keywords"]

_ADDRESS = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Typographic apostrophes (U+2018, U+2019, U+02BC) and dashes (U+2010 to U+2015) read as the plain ones, so a name
# typed with a straight apostrophe or a hyphen matches the curly or the non-breaking one a mail client wrote.
_PLAIN = str.maketrans({chr(c): "'" for c in (0x2018, 0x2019, 0x02BC)} | {chr(c): "-" for c in range(0x2010, 0x2016)})
_NOT_A_WORD = re.compile(r"[\W_]+")


def fold(text: str) -> str:
    """Case- and accent-folded, with typographic apostrophes and dashes made plain: `Kovács` and `KOVACS` are one
    name, and so is O'Brien written with a curly apostrophe."""
    decomposed = unicodedata.normalize("NFKD", text).translate(_PLAIN)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _split_case(text: str) -> str:
    """A space where lower case turns upper: `DoraExample` is `Dora Example`."""
    out: list[str] = []
    prev = ""
    for ch in text:
        if prev.islower() and ch.isupper():
            out.append(" ")
        out.append(ch)
        prev = ch
    return "".join(out)


def _words(text: str) -> str:
    """Words only, padded with spaces so a word is found whole: HTML entities read as what they stand for, and every
    run of characters that aren't letters or digits is one space (`dora-example`, `dora_example`, `call_with_dora`,
    `Project&nbsp;Phoenix`)."""
    return f" {_NOT_A_WORD.sub(' ', fold(html.unescape(text))).strip()} "


def _forms(value: str) -> set[str]:
    """How a person can be named by one string: the string itself, and every address in it (`Name <addr>`)."""
    forms = {fold(value.strip())}
    forms.update(fold(a) for a in _ADDRESS.findall(value))
    forms.discard("")
    return forms


def strings(value: Any) -> Iterator[str]:
    """Every string in a `native` payload, keys included, at any depth."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in cast(dict[str, Any], value).items():
            yield str(k)
            yield from strings(v)
    elif isinstance(value, list):
        for v in cast(list[Any], value):
            yield from strings(v)


@lru_cache(maxsize=1024)
def _keyword(keyword: str) -> re.Pattern[str]:
    """The keyword as written, folded: it still catches a keyword of punctuation alone (`++`)."""
    words = fold(keyword).split()
    return re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?!\w)")


@lru_cache(maxsize=1024)
def _keyword_shapes(keyword: str) -> tuple[frozenset[str], bool]:
    """Every whole-token shape a keyword can take, and whether it has several words. The shapes are its words as
    written, split at case changes, and run together (`dora example` is also the one token `doraexample`)."""
    written, cased = _words(keyword), _words(_split_case(keyword))
    shapes = {written, cased} | {f" {shape.replace(' ', '')} " for shape in (written, cased)}
    return frozenset(s for s in shapes if s.strip()), len(cased.split()) > 1


def _says(texts: list[str], keywords: list[str]) -> bool:
    keywords = [k for k in keywords if k.strip()]
    texts = [t for t in texts if t]
    if not keywords or not texts:
        return False
    folded = [fold(t) for t in texts]
    if any(_keyword(k).search(t) for k in keywords for t in folded):
        return True
    anywhere: set[str] = set()
    # Only a keyword of several words is looked for in text split at case changes too: `DoraExample` holds `dora
    # example`, but `Max` must not drop every `maxRetries`, nor `Jo` every `JoAnn`.
    split_too: set[str] = set()
    for keyword in keywords:
        shapes, several = _keyword_shapes(keyword)
        anywhere |= shapes
        if several:
            split_too |= shapes
    for text in texts:
        written = _words(text)
        if any(shape in written for shape in anywhere):
            return True
        if split_too:
            cased = _words(_split_case(text))
            if any(shape in cased for shape in split_too):
                return True
    return False


def matches_text(text: str, lists: NeverStore) -> bool:
    """Whether a plain string (a path, a slug, an id) says something on the keyword list, by the same rule a record's
    text is held to."""
    return _says([text], lists.keywords)


def match(record: RawRecord, lists: NeverStore) -> ListName | None:
    """The list a record matches, or None."""
    people = set[str]().union(*(_forms(p) for p in lists.people)) if lists.people else set[str]()
    if people:
        named = [s for s in (record.author, *record.to) if s] + list(strings(record.native))
        if any(_forms(s) & people for s in named):
            return "people"
    containers = {fold(c.strip()) for c in lists.containers if c.strip()}
    for where in (record.container, record.thread):
        if where and (fold(where) in containers or fold(where.split(":", 1)[-1]) in containers):
            return "containers"
    if any(k.strip() for k in lists.keywords):
        texts = [t for t in (record.title, record.text, *(a.name for a in record.attachments)) if t]
        if _says([*texts, *strings(record.native)], lists.keywords):
            return "keywords"
    return None
