"""A ledger batch as Claude and the command line give it and read it back (DR-0639, DR-0645): the ops from a JSON array
or JSON lines, as todo.py read them, and what a write wrote, as the `ledger_batch` tool's result and as lines. It
writes nothing: the one writer is `store.write`.

    1. add A-0302 added: Send Ada the dates
    2. triage A-0302 triage: chase
    3. set M-0040: nothing to change
    $x = A-0302
    2 op(s) written, batch 01JAB3W8Q4X5M2Z7K9R0T6YVNC, commit 1a2b3c4

Text in these lines is the ledger's own, as the writer stored it; a record's text never shows here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, cast

from pydantic import BaseModel

from docket.ledger.fold import describe
from docket.ledger.ops import Op
from docket.ledger.store import LedgerError, Written

# A log line's or a note's words cut to this, so a batch's lines stay one line each.
WIDTH = 60


class BatchChange(BaseModel):
    """One change of a batch, by its place: the lines of what it wrote, what the writer says of it, or why it was
    refused and, when it hangs on a refused change's name, that change."""

    n: int
    op: str
    wrote: list[str]
    said: list[str] = []
    refused: str | None = None
    hangs_on: int | None = None


class BatchResult(BaseModel):
    """What `ledger_batch` wrote, or with a dry run would write: how many ops, the batch's id (its first op's, for an
    undo of the whole), the commit, each change, each `as` name as it reads and its id, and the renumbers a write
    starts with."""

    dry_run: bool
    ops: int
    batch: str | None
    commit: str | None
    changes: list[BatchChange]
    names: dict[str, str]
    ids: dict[str, str]
    renumbered: list[str] = []


def parse(text: str) -> list[dict[str, Any]]:
    """A batch's ops: a JSON array, or one JSON object per line, blank lines and `#` comments skipped."""
    text = text.removeprefix("﻿").strip()
    if not text:
        return []
    found: object
    if text.startswith("["):
        try:
            found = json.loads(text)
        except json.JSONDecodeError as e:
            raise LedgerError(f"the batch isn't a JSON array: {e.msg} at line {e.lineno}") from None
    else:
        rows: list[object] = []
        for n, row in enumerate(text.splitlines(), start=1):
            if not row.strip() or row.lstrip().startswith("#"):
                continue
            try:
                rows.append(json.loads(row))
            except json.JSONDecodeError as e:
                raise LedgerError(f"line {n} of the batch isn't a JSON object: {e.msg}") from None
        found = rows
    if not isinstance(found, list) or not all(isinstance(op, dict) for op in cast(list[object], found)):
        raise LedgerError('every op of a batch is a JSON object, {"op": ...}')
    return cast(list[dict[str, Any]], found)


def result(changes: Sequence[Mapping[str, Any]], written: Written, *, dry_run: bool) -> BatchResult:
    """The tool's result for a write of these changes."""
    labels = written.labels
    said: dict[int, list[str]] = {}
    for n, remark in written.remarks:
        said.setdefault(n, []).append(remark)
    refused = {r.n: r for r in written.refused}
    out: list[BatchChange] = []
    for n, (change, wrote) in enumerate(zip(changes, written.changes, strict=True), start=1):
        why = refused.get(n)
        shown = [op_line(op, labels) for op in wrote]
        out.append(BatchChange(n=n, op=str(change.get("op")), wrote=shown, said=said.get(n, [])))
        if why is not None:
            out[-1].refused, out[-1].hangs_on = why.why, why.on
    mine = {op.id for ops in written.changes for op in ops}
    return BatchResult(
        dry_run=dry_run, ops=len(written.ops), batch=written.ops[0].batch if written.ops else None,
        commit=written.commit, changes=out, names={name: labels[i] for name, i in written.names.items()},
        ids=dict(written.names), renumbered=[op_line(op, labels) for op in written.ops if op.id not in mine],
    )  # fmt: skip


def op_line(op: Op, labels: Mapping[str, str]) -> str:
    """One op as a batch's line says it: what it names, then what it did."""
    if op.op == "point":
        what = labels.get(op.id, op.item)
    elif op.op == "point_set":
        what = labels.get(str(op.point), op.item)
    else:
        what = labels.get(op.item, op.item)
    fields = op.fields or {}
    if op.op in ("add", "meeting", "stream"):
        words = f"added: {_cut(str(fields.get('title') or fields.get('subject') or fields.get('name') or ''))}"
    elif op.op == "undo":
        words = f"undo of {op.of}"
    elif op.op == "log":
        words = f"log: {_cut(op.note or '')}"
    else:
        words = describe(op)
    return f"{what} {words}"


def lines(found: BatchResult) -> list[str]:
    """The result as the tool's text and the command's output: a line or more per change, the names, the total."""
    out = [f"renumbered: {line}" for line in found.renumbered]
    for change in found.changes:
        head = f"{change.n}. {change.op}"
        if change.refused is not None:
            out.append(f"{head}: refused, {change.refused}")
        elif not change.wrote:
            out.append(f"{head}: nothing to change")
        else:
            out.append(f"{head}: {change.wrote[0]}")
            out += [f"   {line}" for line in change.wrote[1:]]
        out += [f"   {line}" for line in change.said]
    out += [f"${name} = {label}" for name, label in found.names.items()]
    refused = sum(1 for change in found.changes if change.refused is not None)
    if found.dry_run:
        if refused:
            out.append(f"dry run: {refused} change(s) refused; nothing written")
        else:
            out.append(f"dry run: {found.ops} op(s) would be written; nothing written")
    elif found.commit is not None:
        out.append(f"{found.ops} op(s) written, batch {found.batch}, commit {found.commit[:7]}")
    else:
        out.append("nothing to write")
    return out


def _cut(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= WIDTH else one[: WIDTH - 1] + "…"
