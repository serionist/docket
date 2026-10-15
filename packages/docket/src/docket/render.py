"""The compact text of Docket's answers: the lines the `docket doc` and `docket search` commands print, and the text an
MCP tool returns for Claude to read beside its structured result.

Every line goes through `printable`: raw text from a source can carry terminal codes (OSC 52 sets the clipboard), and
both a terminal and Claude Code's screen would act on them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from zoneinfo import ZoneInfo

from docket.index import SearchResult
from docket.recall import ReadResult, VerifyResult
from docket.vault.anchors import format_time
from docket.vault.docstore import PutResult
from docket.vault.processed import DocProblem
from docket.vault.spans import Span

# C0 and C1 control characters and DEL, but newline and tab.
CONTROL = re.compile("[\\x00-\\x08\\x0b-\\x1f\\x7f-\\x9f]")


def printable(text: str) -> str:
    return CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


def text(lines: Iterable[str]) -> str:
    """Lines as one text, each made printable."""
    return "\n".join(printable(line) for line in lines) + "\n"


def span(s: Span, indent: str = "") -> list[str]:
    head = f"{indent}{s.anchor}"
    if s.ts is not None:
        head += f"  {s.kind} {s.ts:%Y-%m-%d %H:%M}Z"
    if s.author:
        head += f" {s.author}"
    if s.versions > 1:
        head += f" ({s.versions} versions; this is the latest)"
    lines = [head]
    if s.problem is not None:
        lines.append(f"{indent}  ! {s.problem}")
    elif s.segments:
        lines += [
            f"{indent}  {format_time(seg.start_ms // 1000)}  {seg.speaker or '?'}: {seg.text}" for seg in s.segments
        ]
    else:
        lines += [f"{indent}  {line}" for line in s.text.split("\n")]
    return lines


def put(result: PutResult) -> list[str]:
    lines = [f"a hand edit went in first, as rev {result.taken_in}"] if result.taken_in else []
    lines.append(f"{'put' if result.changed else 'unchanged'} {result.id} rev {result.rev}  {result.path}")
    if result.redactions:
        where = ", ".join(str(n) for n in result.redacted_lines)
        counts = ", ".join(f"{n} {rule}" for rule, n in result.redactions.items())
        lines.append(f"redacted on line{'s' if len(result.redacted_lines) > 1 else ''} {where}: {counts}")
    return lines


def refused(problems: list[DocProblem]) -> list[str]:
    return [*(str(problem) for problem in problems), f"refused: {len(problems)} problem(s)"]


def read(result: ReadResult) -> list[str]:
    lines: list[str] = []
    if result.resolved is not None and result.resolved != result.locator.strip():
        lines.append(f"read as {result.resolved}")
    lines += [f"! {problem}" for problem in result.problems]
    if result.doc is not None:
        lines.append(f"{result.doc.id} rev {result.doc.rev}  {result.doc.path}")
        if result.doc.superseded_by:
            lines.append(f"superseded by {', '.join(result.doc.superseded_by)}")
        lines += [f"{line.n:>4}  {line.text}" for line in result.doc.lines]
    elif result.view != "raw" and not result.lines and not result.legacy:
        lines.append("no current doc points at it yet")
    lines += [f"{cited.doc} rev {cited.rev} line {cited.n}: {cited.text}" for cited in result.lines]
    lines += [f"{ref.id}, a legacy note, covers it: {', '.join(ref.old_paths)}" for ref in result.legacy]
    for s in result.spans:
        lines += span(s)
    return lines


def verify(result: VerifyResult) -> list[str]:
    lines = [f"{result.doc} rev {result.rev}"]
    for checked in result.lines:
        lines.append(f"{checked.n:>4}  {checked.text}")
        lines += [f"        ! {problem}" for problem in checked.problems]
        for s in checked.spans:
            lines += span(s, "        ")
    return lines


def search(result: SearchResult, tz: ZoneInfo) -> list[str]:
    lines: list[str] = []
    reading = result.query
    if reading.since is not None or reading.until is not None:
        ends = [
            f"{word} {at.astimezone(tz):%Y-%m-%d %H:%M}"
            for word, at in (("from", reading.since), ("to", reading.until))
            if at is not None
        ]
        said = f" ({', '.join(reading.time_words)})" if reading.time_words else ""
        lines.append(" ".join(ends) + said)
    lines += [f"{found.words!r} is {found.id}" for found in reading.entities]
    if reading.unknown:
        lines.append(f"not found as a person or entity: {', '.join(reading.unknown)}")
    if reading.left_out:
        lines.append(f"too common to search for: {', '.join(reading.left_out)}")
    if not result.groups:
        lines.append("nothing found")
    for group in result.groups:
        when = f"  {group.first.astimezone(tz):%Y-%m-%d}" if group.first else ""
        lines.append(f"\n{group.kind}  {group.title}  ({group.source or 'docs'}{when})")
        if group.summary is not None:
            lines.append(f"  summary  {group.summary.id} rev {group.summary.rev}: {group.summary.title}")
        for hit in group.hits:
            at = f"  {hit.ts.astimezone(tz):%Y-%m-%d %H:%M}" if hit.ts else ""
            who = ", ".join(hit.speakers or hit.authors)
            doc_lines = f"  lines {hit.lines[0]}-{hit.lines[1]}" if hit.lines else ""
            lines.append(f"  {hit.rank:>3}. {hit.locator}{doc_lines}{at}{f'  {who}' if who else ''}")
            lines.append(f"       {hit.snippet}")
            lines += [f"       -> {anchor}" for anchor in hit.anchors]
            if hit.also:
                lines.append(f"       also {', '.join(hit.also)}")
    return lines
