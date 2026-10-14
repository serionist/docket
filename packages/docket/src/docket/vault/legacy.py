"""Legacy notes by their old paths: the paths other files cite them by, from before the notes came into the vault.

A path locator is looked up in these steps, stopping at the first with exactly one match (the old-path locator, a
one-way door):

- exact: an old path, character for character after normalizing;
- case-folded: an old path compared case-folded, when only one matches;
- suffix: the longest old path the locator ends with, case-folded; after a `:` any old path, after a `/` only one of
  two parts or more, so another folder's README.md never resolves to the root one (a one-part match is named instead);
- file name: a locator with no `/` after any `<source>:`, against each old path's last part, case-folded.

Normalizing makes `\\` a `/`, drops a leading `./` and repeated `/`, and composes to NFC; a `..` part is refused. Exact
and case-folded take a relative path only: a drive, a leading `/` or a `<source>:` prefix takes part in the suffix step
alone. Nothing here touches the file system: an old path is matched as a string, never resolved, so an absolute path
from another machine reads the same as on this one.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from docket.errors import DocketError
from docket.vault.processed import ProcessedDoc

_DRIVE = re.compile(r"^[A-Za-z]:/")
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9._-]*:")
_SLASHES = re.compile(r"/{2,}")


@dataclass(frozen=True)
class OldPath:
    """One old path a legacy note keeps, and the note."""

    path: str
    doc: str


def old_paths(docs: Iterable[ProcessedDoc]) -> list[OldPath]:
    """Every old path the legacy notes keep."""
    return [OldPath(path, d.meta.id) for d in docs if d.meta.type == "legacy-note" for path in d.meta.old_paths]


def normalize(text: str) -> str:
    """A locator's path as old paths are kept: `/` separators, no leading `./` or repeated `/`, NFC."""
    value = _SLASHES.sub("/", unicodedata.normalize("NFC", text.strip()).replace("\\", "/"))
    while value.startswith("./"):
        value = value[2:]
    if ".." in value.split("/"):
        raise DocketError(f"{text} has a .. part; an old path is matched as written, never resolved")
    return value


def find(entries: list[OldPath], locator: str) -> str:
    """The legacy note a path names, by its old paths. Nothing, or several equally good, is a DocketError naming the
    candidates: a guess would put words in someone's mouth."""
    norm = normalize(locator)
    folded = norm.casefold()

    def exact(path: str) -> bool:
        return path == norm

    def case_folded(path: str) -> bool:
        return path.casefold() == folded

    if not (norm.startswith("/") or _DRIVE.match(norm) or ":" in norm):
        for same in (exact, case_folded):
            hit = _one(entries, same, locator)
            if hit is not None:
                return hit
    best: dict[str, set[str]] = {}
    named: set[str] = set()
    for entry in entries:
        kept = entry.path.casefold()
        if len(kept) >= len(folded) or not folded.endswith(kept):
            continue
        boundary = folded[len(folded) - len(kept) - 1]
        if boundary == "/" and "/" not in kept:
            named.add(f"{entry.path} ({entry.doc})")
        elif boundary in "/:":
            best.setdefault(entry.path.casefold(), set()).add(entry.doc)
    if best:
        longest = max(best, key=len)
        docs = {d for path, ids in best.items() if len(path) == len(longest) for d in ids}
        if len(docs) == 1:
            return docs.pop()
        raise DocketError(f"{locator} could be any of {', '.join(sorted(docs))}; give more of its path")
    name = _SOURCE.sub("", norm, count=1).casefold()

    def same_name(path: str) -> bool:
        return path.rsplit("/", 1)[-1].casefold() == name

    if "/" not in name and not _DRIVE.match(norm):
        hit = _one(entries, same_name, locator)
        if hit is not None:
            return hit
    hint = f"; a note at the root of its folder ends it: {', '.join(sorted(named))}" if named else ""
    raise DocketError(f"no legacy note has the old path {locator}{hint}")


def _one(entries: list[OldPath], test: Callable[[str], bool], locator: str) -> str | None:
    matches = {e.doc for e in entries if test(e.path)}
    if len(matches) > 1:
        raise DocketError(f"{locator} could be any of {', '.join(sorted(matches))}; give more of its path")
    return matches.pop() if matches else None
