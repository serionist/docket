"""`docket sync check <sync> <block>` and `docket sync learned <sync>` (#305).

The check reads one agent's block as the reconcile will, and says each field it would have to fix by hand: a line of
the wrong shape, a kind, a stream the ledger doesn't hold or none, a day or a due that isn't one, a who the ledger
writes another way, a locator that doesn't open. The agent fixes each line it names with an Edit, so its block is
written once and the draft needs no hand edits. It reads only.

`learned` lists the sync's LEARNED lines by source, each with the file it goes in: the source's `SKILL.md` for a line
about the route, its `sync.md` for the rest, and `sync.md` under `Route` for a route line of a `SKILL.md` copied from
Docket's examples, which an upgrade copies over (DR-0750). Claude folds them in after the summary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from docket import recall
from docket.errors import DocketError
from docket.index import build as index_build
from docket.ledger.ops import STREAM_FIELDS
from docket.sources import runner
from docket.sync import block, folder, reconcile
from docket.vault.vault import Vault

NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*(?:/[a-z0-9_-]+)?$")
ITEM_REF = re.compile(r"^A-\d{4,}$")
# The title an example's SKILL.md carries, which an upgrade copies over.
EXAMPLE = re.compile(r"^# .*(Docket's reference source|a Docket example source)\s*$", re.MULTILINE)
KINDS = {"OWED": ("reply", "decision", "task")}
# Each label's stream field, its due field and its day field, by place.
STREAM_AT = {"OWED": 7, "WAITING": 4, "INVITES": 4}
DUE_AT = {"OWED": 9, "WAITING": 6}
DAY_AT = {"OWED": 2, "WAITING": 2}
WHO = ("OWED", "WAITING", "INVITES")


def _block_file(path: Path, info: folder.Info, name: str) -> Path:
    """`slack` names the source's block, `plaud/rec-0101` a recording's."""
    source = name.partition("/")[0]
    if NAME.match(name) is None or source not in {s.source for s in info.sources}:
        raise DocketError(f"{name!r} isn't a block of {info.sync}: give a source, or <source>/<key> for a recording")
    file = path / folder.BLOCKS / f"{name}.txt"
    if not file.is_file():
        raise DocketError(f"no block {folder.BLOCKS}/{name}.txt yet: write it first")
    return file


def _is_day(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == len("YYYY-MM-DD")


def check(vault: Vault, sync: str, name: str) -> tuple[str, list[str]]:
    """The block's path in the sync folder and each problem the reconcile would leave to a hand edit, a line each."""
    path, info = folder.find(sync, vault.root)
    file = _block_file(path, info, name)
    rel = file.relative_to(path).as_posix()
    lines = block.parse(file.read_bytes().decode("utf-8", "replace"))
    with index_build.ledger_tables(vault) as conn:
        streams = {s for (s,) in conn.execute("SELECT name FROM streams")}
        names = sorted({w for (w,) in conn.execute("SELECT DISTINCT who FROM items")})
    out: list[str] = []
    with recall.Lookup(vault) as lookup:
        for line in lines:
            said = [f"{rel}:{line.n} {line.label}: {p}" for p in _problems(line, streams, names, lookup)]
            out += said
    return rel, out


def _problems(line: block.Line, streams: set[str], names: list[str], lookup: recall.Lookup) -> list[str]:
    if line.problem is not None:
        return [line.problem]
    f = line.fields
    out: list[str] = []
    at = reconcile.LOCATOR.get(line.label)
    if at is not None and at < len(f):
        if f[at] in ("", "-"):
            # The reconcile drops a line with no locator, as one whose locator doesn't open.
            out.append("it has no locator: give the one read opens, or drop the line")
        elif not lookup.opens(f[at]):
            out.append(f"its locator, {f[at]}, doesn't open: give the one read opens, or drop the line")
    kinds = KINDS.get(line.label)
    if kinds is not None and len(f) > 6 and f[6] not in kinds:
        out.append(f"its kind, {f[6]!r}, is one of {', '.join(kinds)}")
    k = STREAM_AT.get(line.label)
    if k is not None and k < len(f):
        if f[k] in ("", "-"):
            out.append(f"it names no stream: one of {', '.join(sorted(streams)) or 'none yet'}")
        elif f[k] not in streams:
            out.append(f"the stream {f[k]!r} isn't one the ledger holds: one of {', '.join(sorted(streams))}")
    k = DUE_AT.get(line.label)
    if k is not None and k < len(f) and f[k] not in ("", "-") and not _is_day(f[k]):
        out.append(f"its due, {f[k]!r}, is YYYY-MM-DD or -")
    k = DAY_AT.get(line.label)
    if k is not None and not _is_day(f[k][:10]):
        out.append(f"the day asked, {f[k]!r}, is YYYY-MM-DD, its HH:MM after it")
    if line.label in WHO and f[0] not in names:
        same = [n for n in names if reconcile.same_person(n, f[0])]
        if len(same) == 1:
            out.append(f"who: the ledger writes {same[0]!r}, not {f[0]!r}")
    if line.label == "CLOSES":
        if not ITEM_REF.match(f[0]):
            out.append(f"{f[0]!r} isn't an item's A- ref")
        if f[1] not in ("done", "moot"):
            out.append(f"{f[1]!r} is done or moot")
    if line.label == "STREAMS":
        if f[0] not in streams:
            out.append(f"the stream {f[0]!r} isn't one the ledger holds: one of {', '.join(sorted(streams))}")
        pairs = [p.strip() for p in f[1].split(";") if p.strip()]
        bad = [p for p in pairs if p.partition("=")[0].strip() not in STREAM_FIELDS or "=" not in p]
        out += [f"{p!r} isn't field=value with a field of {', '.join(STREAM_FIELDS)}" for p in bad]
    if line.label == "MINUTES" and f[2] not in reconcile.MINUTES:
        out.append(f"{f[2]!r} is answered, dropped or open")
    return out


def check_lines(rel: str, problems: list[str]) -> list[str]:
    """What `docket sync check` prints: each problem, or that the block reads clean."""
    return problems or [f"{rel}: every line reads as the reconcile takes it"]


# -- LEARNED -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Learned:
    """A LEARNED line: its block line, the file it goes in, and the part of that file."""

    where: str
    text: str
    file: Path | None
    part: str | None


def learned(vault: Vault, sync: str) -> list[tuple[str, list[Learned]]]:
    """The sync's LEARNED lines by source, in the sources' order, each with the file it goes in."""
    path, info = folder.find(sync, vault.root)
    found, _ = reconcile.read_blocks(path, info)
    by_source: dict[str, list[Learned]] = {}
    for f in found:
        if f.line.label != "LEARNED":
            continue
        source = f.file.removeprefix(f"{folder.BLOCKS}/").removesuffix(".txt").partition("/")[0]
        try:
            skills = runner.skill_folder(runner.consuming_repo(vault.root), vault.root, source)
        except DocketError:
            skills = None
        file, part = _target(skills, f.field(0))
        by_source.setdefault(source, []).append(Learned(f.where, f.field(1), file, part))
    return [(s.source, by_source[s.source]) for s in info.sources if s.source in by_source]


def _target(skills: Path | None, said: str) -> tuple[Path | None, str | None]:
    if skills is None:
        return None, None
    skill = skills / "SKILL.md"
    if "SKILL" not in said.upper():
        return skills / "sync.md", None
    try:
        copied = EXAMPLE.search(skill.read_text(encoding="utf-8")) is not None
    except OSError:
        copied = False
    return (skills / "sync.md", "Route") if copied else (skill, None)


def learned_lines(found: list[tuple[str, list[Learned]]]) -> list[str]:
    """What `docket sync learned` prints: a head line per source, then each line with where it goes."""
    if not found:
        return ["no block holds a LEARNED line"]
    out: list[str] = []
    for source, lines in found:
        out.append(f"{source} ({len(lines)})")
        for line in lines:
            where = "no skill folder" if line.file is None else str(line.file) + (
                f", part {line.part}" if line.part else "")  # fmt: skip
            out.append(f"  {where}: {line.text}  [{line.where}]")
    return out
