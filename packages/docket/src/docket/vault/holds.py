"""Hold lists (DR-0121): records Docket must not stage yet, held by id in the vault, one list per source.

`state/holds/<source>.txt` is committed, so a hold reaches every machine with git: one native id a line, read as UTF-8
with or without a BOM, lines stripped, blank lines skipped, any order and duplicates allowed. A hold on `<id>` covers a
record whose rid, thread or container is `<source>:<id>` or sits under `<source>:<id>/`. The list fails closed: a line
that isn't an id, or a list that is a link, stops every ingest and every gate call until it is fixed, because skipping
it would stage what the user held back. Docket adds ids from an import's hold files, and takes one off only when the
owner releases it with its cuts (DR-0200); an add leaves released ids out (DR-0205).

Two machines can add at once, so the lists merge by union (`state/holds/*.txt merge=union`), and the next add sorts the
list and drops duplicates. A release merged against another machine's add can bring an id back, which fails safe: the
recording stays held until the next add, or the release run again, takes it off.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from docket.errors import DocketError
from docket.vault import releaselog
from docket.vault.records import RawRecord, is_native_id, owning_id, source_name_problem
from docket.vault.staging import write_replacing
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
    return owning_id(ref, source, ids)


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
    """Hold more ids of a source: the list sorted, without duplicates and without the ids the owner released, committed
    with a message that names none. Returns how many were new. A released id is never held again by an import's hold
    file, and one a merge brought back leaves the list here, even when nothing new is added (DR-0205)."""
    new = set(ids)
    if not all(is_native_id(i) for i in new):
        raise DocketError("an id to hold isn't an id")
    with vault.writing():
        path = list_path(vault.root, source)
        # The log first: a release line a failed commit left on disk reaches git before its id comes off the list, or
        # the add stops here with the list as it was. No machine may see a hold gone without its release. It is read
        # before it is committed: a log that doesn't parse is refused here, never committed for every machine to choke
        # on.
        released = set(releaselog.read(vault.root, source))
        releaselog.commit_pending(vault, source)
        current = read(vault.root, source)
        added = new - current - released
        if added or current & released:
            path.parent.mkdir(parents=True, exist_ok=True)
            _replace(vault, path, sorted((current | added) - released))
        if not path.exists():
            return 0
        _union_line(vault.root)
        what = f"{len(added)} added" if added else "the list committed"
        commit_pending(vault, source, f"Holds: {source}, {what} on {vault.machine}")
        return len(added)


def remove(vault: Vault, source: str, ids: Iterable[str]) -> int:
    """Take released ids off a source's list, committed with a message that names none. The release log is committed
    first, and an id with no line in it is refused, so a hold never comes off in git before its release. Returns how
    many came off."""
    gone = set(ids)
    with vault.writing():
        path = list_path(vault.root, source)
        granted = set(releaselog.read(vault.root, source))
        releaselog.commit_pending(vault, source)
        unreleased = sorted(gone - granted)
        if unreleased:
            raise DocketError(
                f"{', '.join(unreleased)}: no release line, so the hold stays; a hold comes off only through docket "
                "hold release"
            )
        current = read(vault.root, source)
        off = current & gone
        if off:
            _replace(vault, path, sorted(current - off))
        commit_pending(vault, source, f"Holds: {source}, {len(off)} released on {vault.machine}")
        return len(off)


def commit_pending(vault: Vault, source: str, message: str | None = None) -> str | None:
    """Commit the list, and the merge rule when .gitattributes gained it, whenever either differs from HEAD: a change
    whose commit failed (another git held the index) left it on disk, and a re-run that finds nothing new must still
    commit it, or it never reaches another machine. A list an ignore rule hides counts too, so its add fails loudly
    instead of the list staying out of git unseen."""
    rel = f"{HOLDS}/{source}.txt"
    if not (vault.root / rel).exists():
        return None
    git = vault.git
    pending = git.untracked(rel) or git.untracked(rel, ignored=True) or git.changed_against_head(rel)
    paths = [rel] if pending else []
    if _union_uncommitted(vault):
        paths.append(".gitattributes")
    if not paths:
        return None
    return vault.commit(paths, message or f"Holds: {source}, the list committed on {vault.machine}")


def _replace(vault: Vault, path: Path, ids: list[str]) -> None:
    write_replacing(vault.home.tmp_dir, path, "".join(f"{i}\n" for i in ids), f"holds-{path.stem}")


def _has_union(text: str) -> bool:
    return UNION in (line.strip() for line in text.splitlines())


def _union_line(root: Path) -> None:
    """Add the merge rule to the vault's .gitattributes when a vault made before hold lists lacks it."""
    attrs = root / ".gitattributes"
    text = attrs.read_text(encoding="utf-8") if attrs.is_file() else ""
    if _has_union(text):
        return
    if text and not text.endswith("\n"):
        text += "\n"
    note = "# Hold lists from several machines merge by keeping both sides' ids."
    attrs.write_text(f"{text}{note}\n{UNION}\n", encoding="utf-8", newline="\n")


def _union_uncommitted(vault: Vault) -> bool:
    """Whether the merge rule is in .gitattributes but not in HEAD's: only then does Docket commit that file, so a
    user's own edits there stay theirs."""
    # ./ reads the vault's own file: in a folder of the user's repo, HEAD:.gitattributes is the repo's.
    committed = vault.git.run("show", "HEAD:./.gitattributes", check=False).stdout
    return not _has_union(committed) and _has_union((vault.root / ".gitattributes").read_text(encoding="utf-8"))
