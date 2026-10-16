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
from urllib.parse import unquote

from docket.vault.config import NeverStore
from docket.vault.records import RawRecord

ListName = Literal["people", "containers", "keywords"]

_ADDRESS = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Typographic apostrophes (U+2018, U+2019, U+02BC) and dashes (U+2010 to U+2015) read as the plain ones, so a name
# typed with a straight apostrophe or a hyphen matches the curly or the non-breaking one a mail client wrote.
_PLAIN = str.maketrans({chr(c): "'" for c in (0x2018, 0x2019, 0x02BC)} | {chr(c): "-" for c in range(0x2010, 0x2016)})
_NOT_A_WORD = re.compile(r"[\W_]+")
_PERCENT_U = re.compile(r"%u([0-9a-fA-F]{4})")
_BACKSLASH_U = re.compile(r"\\u([0-9a-fA-F]{4})")
# Decoding stops when a round changes nothing; a link wrapped more often than this is beyond any mail client seen.
DECODE_ROUNDS = 4


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


def _unicode_escape(m: re.Match[str]) -> str:
    return chr(int(m.group(1), 16))


def decoded_forms(text: str) -> list[str]:
    """The forms a name can hide behind in a link or a payload, each held to the rule as well as the text: percent
    encoding, once or wrapped again (an Outlook Safe Link turns %20 into %2520), behind HTML entities (&#37;20), in
    Latin-1 (Kov%E1cs), and as %uXXXX or \\uXXXX escapes (JavaScript's escape(), JSON). Every round's form is kept,
    at most DECODE_ROUNDS of them."""
    forms: list[str] = []
    current = text
    for _ in range(DECODE_ROUNDS):
        decoded = unquote(html.unescape(current))
        decoded = _BACKSLASH_U.sub(_unicode_escape, _PERCENT_U.sub(_unicode_escape, decoded))
        if decoded == current:
            break
        forms.append(decoded)
        current = decoded
    for form in (text, *forms):
        latin = unquote(form, encoding="latin-1")
        if latin != form and latin not in forms:
            forms.append(latin)
    return forms


def _says(texts: list[str], keywords: list[str]) -> bool:
    keywords = [k for k in keywords if k.strip()]
    texts = [t for t in texts if t]
    texts += [form for t in texts for form in decoded_forms(t)]
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


def _orders(value: str) -> list[str]:
    """A value by its words, folded, one space between them, and for two words also the other way round: a list may
    write a name family name first, and `Minta, Dóra` or a doubled space is still the name."""
    words = _words(value).split()
    return [" ".join(words), *([f"{words[1]} {words[0]}"] if len(words) == 2 else [])]


def listed(value: str, lists: NeverStore) -> ListName | None:
    """The list a value an entity holds (a name, an alias, an id, a role, a note) is on, or None. The people list
    is held to it as to a record's author, and by its words in both orders; the keywords list, as text, in both orders
    too."""
    people = {form for p in lists.people for form in (*_forms(p), *_orders(p))}
    if (_forms(value) | set(_orders(value))) & people:
        return "people"
    return "keywords" if _says([value, *_orders(value)[1:]], lists.keywords) else None


def match(record: RawRecord, lists: NeverStore) -> ListName | None:
    """The list a record matches, or None."""
    people = set[str]().union(*(_forms(p) for p in lists.people)) if lists.people else set[str]()
    segments = record.segments or []
    if people:
        named = [s for s in (record.author, *record.to, *(seg.speaker for seg in segments)) if s]
        named += list(strings(record.native))
        if any(_forms(s) & people for s in named):
            return "people"
    containers = {fold(c.strip()) for c in lists.containers if c.strip()}
    for where in (record.container, record.thread):
        if where and (fold(where) in containers or fold(where.split(":", 1)[-1]) in containers):
            return "containers"
    if any(k.strip() for k in lists.keywords):
        texts = [t for t in (record.title, record.text, *(a.name for a in record.attachments)) if t]
        texts += [t for seg in segments for t in (seg.speaker, seg.text) if t]
        if _says([*texts, *strings(record.native)], lists.keywords):
            return "keywords"
    return None
