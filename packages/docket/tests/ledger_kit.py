"""Builders the ledger's tests share: ops made by hand, in the shape the writer makes them, and op files on disk."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from docket.ledger import ops
from docket.ledger.ops import Line, Op

NOW = datetime(2026, 10, 18, 9, 14, 2, tzinfo=UTC)
DAY = date(2026, 10, 18)
ADD_FIELDS = {"kind": "reply", "title": "Send Ada the dates", "who": "Ada Example", "stream": "pilot",
              "source": "chat:C0TEST/1791000001.000100"}  # fmt: skip


def ulid(n: int, ms: int = 1_790_000_000_000) -> str:
    """An id with a known order: the nth one of a millisecond."""
    return ops.encode((ms << ops.RANDOM_BITS) + n)


def op(kind: str, n: int, item: int | None = None, **keys: Any) -> Op:
    """The op with id ulid(n), on the item whose add is ulid(item), by Claude on desk."""
    base: dict[str, Any] = {
        "schema_version": 1, "id": ulid(n), "item": ulid(n if item is None else item), "op": kind, "day": DAY,
        "machine": "desk", "by": "claude", "via": "cli",
    }  # fmt: skip
    if kind == "add":
        base |= {"number": n, "fields": dict(ADD_FIELDS)}
    return Op(**(base | keys))


def line(o: Op, n: int = 1) -> Line:
    return Line(o, o.text(), ops.rel_of(o), n)


def lines(*made: Op) -> list[Line]:
    return [line(o, n) for n, o in enumerate(made, start=1)]


def write_ops(root: Path, made: Iterable[Op]) -> list[str]:
    """Append ops to their files under root, as the writer lays them out; the files written."""
    by_file: dict[str, list[str]] = {}
    for o in made:
        by_file.setdefault(ops.rel_of(o), []).append(o.text())
    for rel, texts in by_file.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(before + "".join(f"{t}\n" for t in texts), encoding="utf-8", newline="\n")
    return sorted(by_file)
