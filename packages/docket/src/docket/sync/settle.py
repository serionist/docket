"""`docket sync settle <sync> <line>=<choice>... --rev <n>` (#272): the draft's unsure lines settled in one call, by the
line numbers `docket sync reconcile` printed and the Read tool shows, so Claude never writes the draft out again. It
rewrites `draft.jsonl` and nothing else: the batch stays Claude's, by `ledger_batch` with `file`, dry run first.

The draft's head line carries its revision, `rev 1` as the reconcile writes it. A settle names the revision its line
numbers were read at and refuses any other, since a settle before it moved the lines; it raises the revision by one.

| Choice | The line becomes |
|---|---|
| `keep` | the op, its `unsure` taken out, and the lines that go with it kept |
| `alt`, `alt2`, ... | its first, second, ... alternative, its `# ` taken off; the lines that go with the op go |
| `keep+alt2`, `alt+alt2` | those ops in that order: a merge kept and its other ask added too |
| `drop` | nothing, and the lines that go with it go too |

An unsure op's block in the draft is its `# ?` lines above it, the op, its `# {...}` alternatives below it, and the ops
that go with it after those, each marked unsure with `GOES_WITH`; a settled op's block becomes its ops alone, so the
file holds only what is still open. A line that goes with an op may be named itself, to keep or drop it alone. Every
choice is checked before anything is written: one that doesn't apply refuses the call and changes nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from docket.errors import DocketError
from docket.sync import folder
from docket.sync.reconcile import DRAFT, GOES_WITH
from docket.vault.vault import Vault

CHOICE = re.compile(r"^(?P<n>\d{1,6})=(?P<choice>[a-z0-9+]+)$")
PART = re.compile(r"^(?:keep|alt(?P<k>[1-9]\d{0,2})?)$")
HEAD = re.compile(r"^# docket sync reconcile (?P<sync>\S+): \d+ ops, \d+ unsure, rev (?P<rev>\d{1,6})$")
PREP = "# prep:"


@dataclass(frozen=True)
class Block:
    """An unsure op and what goes with it, by their places in the draft, counted from 0: its `# ?` lines from
    `first`, the op at `at`, its alternatives' JSON, and the lines that go with it, up to `end`, not taken."""

    first: int
    at: int
    op: dict[str, Any]
    alternatives: list[str]
    then: list[int]
    end: int


@dataclass(frozen=True)
class Settled:
    """What a settle did: the draft's path and its new revision, how many lines it settled and how, the ops and the
    unsure lines left by their new numbers, and the `# prep` line's."""

    path: Path
    rev: int
    kept: int
    swapped: int
    dropped: int
    ops: int
    unsure: list[int]
    prep: int | None


def _op(line: str) -> dict[str, Any] | None:
    """A line's op, when it is one: a JSON object on a line that isn't a comment."""
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    try:
        found: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    return cast(dict[str, Any], found) if isinstance(found, dict) else None


def _blocks(lines: list[str]) -> dict[int, Block]:
    """Each unsure op's block by the op's place; a line that goes with an op is in its block, never one of its own."""
    out: dict[int, Block] = {}
    k = 0
    while k < len(lines):
        op = _op(lines[k])
        if op is None or "unsure" not in op or op["unsure"] == GOES_WITH:
            k += 1
            continue
        first = k
        while first > 0 and lines[first - 1].startswith("# ? "):
            first -= 1
        end = k + 1
        alternatives: list[str] = []
        while end < len(lines) and lines[end].startswith("# {"):
            alternatives.append(lines[end][2:])
            end += 1
        then: list[int] = []
        while end < len(lines) and (follows := _op(lines[end])) is not None and follows.get("unsure") == GOES_WITH:
            then.append(end)
            end += 1
        out[k] = Block(first, k, op, alternatives, then, end)
        k = end
    return out


def _bare(op: dict[str, Any]) -> str:
    return json.dumps({key: value for key, value in op.items() if key != "unsure"}, ensure_ascii=False)


def _parts(choice: str, alternatives: int) -> tuple[list[str], str | None]:
    """A choice's parts in order, `keep` or an alternative's index from 1, or why it doesn't apply."""
    if choice == "drop":
        return [], None
    parts = choice.split("+")
    if len(set(parts)) != len(parts):
        return [], f"{choice} names a part twice"
    out: list[str] = []
    for part in parts:
        found = PART.match(part)
        if found is None:
            return [], f"{part} isn't keep, alt, alt2 and so on, or drop"
        k = int(found["k"] or 1) if part != "keep" else 0
        if k > alternatives:
            had = f"{alternatives} alternative{'s' if alternatives != 1 else ''}" if alternatives else "no alternative"
            return [], f"{part} names an alternative the line doesn't have: it has {had}"
        out.append(part if part == "keep" else str(k))
    return out, None


def settle(vault: Vault, sync: str, choices: list[str], *, rev: int) -> Settled:
    """Settle the draft's unsure lines as `choices` say, `<line>=<choice>` each, numbered as the draft read at `rev`,
    and rewrite it at the next revision; refused, writing nothing, when the draft is at another revision or has no head
    line, a choice doesn't apply, a line is named twice, or the sync is another vault's."""
    path, _ = folder.find(sync, vault.root)
    draft = path / DRAFT
    if not draft.is_file():
        raise DocketError(f"no draft in {path.name}: docket sync reconcile writes it")
    lines = draft.read_bytes().decode("utf-8").splitlines()
    head = HEAD.match(lines[0]) if lines else None
    if head is None:
        raise DocketError(
            "nothing settled: the draft's head line, with its rev, isn't its first line; run the reconcile again"
        )
    now = int(head["rev"])
    if now != rev:
        raise DocketError(
            f"nothing settled: the draft is at rev {now}, not {rev}, so its lines moved since they were read: read it "
            f"again and give the numbers it shows now, with --rev {now}"
        )
    blocks = _blocks(lines)
    owner = {line: block for block in blocks.values() for line in block.then}
    problems: list[str] = []
    asked: dict[int, str] = {}
    for given in choices:
        found = CHOICE.match(given.strip())
        if found is None:
            problems.append(f"{given!r} isn't <line>=<choice>, such as 12=keep")
            continue
        k = int(found["n"]) - 1
        if k in asked:
            problems.append(f"line {k + 1} is named twice")
            continue
        asked[k] = found["choice"]
    if not choices:
        problems.append("name the lines to settle, such as 12=keep 16=alt 18=drop")
    plans: dict[int, list[str]] = {}
    for k, choice in sorted(asked.items()):
        if k in owner:
            if choice not in ("keep", "drop"):
                problems.append(f"line {k + 1} goes with the op on line {owner[k].at + 1}: keep it or drop it")
            continue
        block = blocks.get(k)
        if block is None:
            problems.append(f"line {k + 1} isn't an unsure op: name the line of an op that carries unsure")
            continue
        parts, why = _parts(choice, len(block.alternatives))
        if why is not None:
            problems.append(f"line {k + 1}: {why}")
            continue
        plans[k] = parts
    if problems:
        raise DocketError("nothing settled: " + "; ".join(problems))
    starts = {block.first: block for block in blocks.values() if block.at in plans}
    out: list[str] = []
    kept = swapped = dropped = 0
    k = 0
    while k < len(lines):
        block = starts.get(k)
        if block is None:
            if k in owner and k in asked:
                out += [_bare(_op(lines[k]) or {})] if asked[k] == "keep" else []
            else:
                out.append(lines[k])
            k += 1
            continue
        parts = plans[block.at]
        out += [_bare(block.op) if part == "keep" else block.alternatives[int(part) - 1] for part in parts]
        if not parts:
            dropped += 1
        elif parts == ["keep"]:
            kept += 1
        else:
            swapped += 1
        for line in block.then:
            mine = asked.get(line)
            if mine == "keep" or (mine is None and "keep" in parts):
                out.append(_bare(_op(lines[line]) or {}))
        k = block.end
    ops = [n for n, line in enumerate(out, start=1) if _op(line) is not None]
    unsure = [n for n in ops if "unsure" in (_op(out[n - 1]) or {})]
    out[0] = f"# docket sync reconcile {head['sync']}: {len(ops)} ops, {len(unsure)} unsure, rev {now + 1}"
    prep = next((n for n, line in enumerate(out, start=1) if line.startswith(PREP)), None)
    temporary = draft.with_name(f".{DRAFT}.settling")
    temporary.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(draft)
    return Settled(draft, now + 1, kept, swapped, dropped, len(ops), unsure, prep)


def lines(done: Settled) -> list[str]:
    """What `docket sync settle` prints: how many lines it settled and how, the draft with the unsure lines left by
    their new numbers, and the `# prep` line's, where the prep points go."""
    settled = done.kept + done.swapped + done.dropped
    out = [f"settled {settled} line(s): {done.kept} kept, {done.swapped} alternative(s) taken, {done.dropped} dropped"]
    left = f", lines {', '.join(map(str, done.unsure))}" if done.unsure else ""
    out.append(f"draft: {done.path} rev {done.rev}: {done.ops} ops, {len(done.unsure)} unsure{left}")
    if done.prep is not None:
        out.append(f"prep: line {done.prep}")
    return out
