"""The never-store lists: whether a record names someone, sits somewhere or says something the user listed.

Matching fails closed. Too broad drops a work record; too narrow stores a private one, so case, accents and the
`<source>:` prefix never decide. The answer names the list, never the entry, so nothing downstream can log what matched.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from functools import lru_cache
from typing import Any, Literal, cast

from docket.vault.config import NeverStore
from docket.vault.records import RawRecord

ListName = Literal["people", "containers", "keywords"]

_ADDRESS = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def fold(text: str) -> str:
    """Case- and accent-folded: `Kovács` and `KOVACS` are one name."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


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
    words = fold(keyword).split()
    return re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?!\w)")


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
    keywords = [k for k in lists.keywords if k.strip()]
    if keywords:
        texts = [record.title or "", record.text, *(a.name for a in record.attachments), *strings(record.native)]
        folded = [fold(t) for t in texts if t]
        if any(_keyword(k).search(t) for k in keywords for t in folded):
            return "keywords"
    return None
