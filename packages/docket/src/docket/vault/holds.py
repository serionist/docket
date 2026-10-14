"""Hold lists: records Docket must not stage yet, held by id in the vault, one list per source.

`state/holds/<source>.txt` is committed, so a hold reaches every machine with git: one native id a line, read as UTF-8
with or without a BOM, lines stripped, blank lines skipped, any order and duplicates allowed. A hold on `<id>` covers a
record whose rid, thread or container is `<source>:<id>` or sits under `<source>:<id>/`. The list fails closed: a line
that isn't an id, or a list that is a link, stops every ingest and every gate call until it is fixed, because skipping
it would stage what the user held back. Docket only adds ids; releasing one is a separate step (#31).

Two machines can add at once, so the lists merge by union (`state/holds/*.txt merge=union`), and the next add sorts the
list and drops duplicates.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from docket.errors import DocketError
from docket.vault.records import RawRecord, is_native_id, source_name_problem
from docket.vault.staging import retry
from docket.vault.vault import Vault

HOLDS = "state/holds"
UNION = "state/holds/*.txt merge=union"


@dataclass(frozen=True)
class Holds:
    """Every source's held ids, as read on one call."""

    ids: dict[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])

    def of(self, source: str) -> frozenset[str]:
        return self.ids.get(source, frozenset())

    def covers(self, record: RawRecord) -> bool:
        ids = self.of(record.source)
        refs = (record.rid, record.thread, record.container)
        return bool(ids) and any(covered(ref, record.source, ids) for ref in refs)


def holder(ref: str | None, source: str, ids: frozenset[str]) -> str | None:
    """The held id a rid of a source is, or sits under: `plaud:<id>` and `plaud:<id>/summary`, never `plaud:<id>x`."""
    prefix = f"{source}:"
    if ref is None or not ref.startswith(prefix):
        return None
    parts = ref[len(prefix) :].split("/")
    return next(("/".join(parts[:n]) for n in range(1, len(parts) + 1) if "/".join(parts[:n]) in ids), None)


def covered(ref: str | None, source: str, ids: frozenset[str]) -> bool:
    return holder(ref, source, ids) is not None


def parse(text: str, where: str) -> list[str]:
    """The ids in a list's text. A bad line is named by its number only: its text could be anything, a title too."""
    ids: list[str] = []
    for n, line in enumerate(text.splitlines(), start=1):
        value = line.strip()
        if not value:
            continue
        if not is_native_id(value):
            raise DocketError(f"{where}: line {n} isn't an id; fix or delete it (a hold list fails closed)")
        ids.append(value)
    return ids


def read_file(path: Path, where: str) -> list[str]:
    """A file of ids to hold, such as an import's hold.txt: the list's own reading rules."""
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocketError(f"{where} isn't UTF-8") from None
    except OSError as e:
        raise DocketError(f"{where}: can't read it: {e.strerror or type(e).__name__}") from None
    return parse(text, where)


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _folder(root: Path) -> Path:
    folder = root / HOLDS
    if _linked(folder):
        raise DocketError(f"{HOLDS} is a link; hold lists are read and written only as plain files in the vault")
    return folder


def list_path(root: Path, source: str) -> Path:
    """A source's list, from a checked name, never through a link."""
    problem = source_name_problem(source)
    if problem is not None:
        raise DocketError(problem)
    path = _folder(root) / f"{source}.txt"
    if _linked(path):
        raise DocketError(f"{HOLDS}/{source}.txt is a link; a hold list is a plain file in the vault")
    return path


def read(root: Path, source: str) -> frozenset[str]:
    path = list_path(root, source)
    if not path.exists():
        return frozenset()
    return frozenset(read_file(path, f"{HOLDS}/{source}.txt"))


def read_all(root: Path) -> Holds:
    """Every source's list. A `.txt` there whose name isn't a source's fails closed like a bad line."""
    folder = _folder(root)
    if not folder.is_dir():
        return Holds()
    ids: dict[str, frozenset[str]] = {}
    for path in sorted(folder.iterdir()):
        if path.suffix != ".txt" or path.is_dir():
            continue
        problem = source_name_problem(path.stem)
        if problem is not None:
            raise DocketError(f"{HOLDS}/{path.name}: {problem}; rename or delete it (a hold list fails closed)")
        ids[path.stem] = read(root, path.stem)
    return Holds(ids)


def add(vault: Vault, source: str, ids: Iterable[str]) -> int:
    """Hold more ids of a source: the list sorted, without duplicates, committed with a message that names none.
    Returns how many were new."""
    new = set(ids)
    if not all(is_native_id(i) for i in new):
        raise DocketError("an id to hold isn't an id")
    with vault.writing():
        path = list_path(vault.root, source)
        current = read(vault.root, source)
        added = new - current
        if not added:
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace(vault.root, path, "".join(f"{i}\n" for i in sorted(current | added)))
        paths = [f"{HOLDS}/{source}.txt"]
        if _union_line(vault.root):
            paths.append(".gitattributes")
        vault.commit(paths, f"Holds: {source}, {len(added)} added on {vault.machine}")
        return len(added)


def _replace(root: Path, path: Path, text: str) -> None:
    """Write through a temp file in .git and a rename, so a crash leaves the old list or the new, and a link planted
    at the list's path is replaced, never written through."""
    tmp_dir = root / ".git" / "docket" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"holds-{path.stem}.{os.getpid()}.tmp"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        retry(lambda: tmp.replace(path))
    finally:
        tmp.unlink(missing_ok=True)


def _union_line(root: Path) -> bool:
    """Add the merge rule to the vault's .gitattributes when a vault made before hold lists lacks it; True if added."""
    attrs = root / ".gitattributes"
    text = attrs.read_text(encoding="utf-8") if attrs.is_file() else ""
    if UNION in (line.strip() for line in text.splitlines()):
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    note = "# Hold lists from several machines merge by keeping both sides' ids."
    attrs.write_text(f"{text}{note}\n{UNION}\n", encoding="utf-8", newline="\n")
    return True
