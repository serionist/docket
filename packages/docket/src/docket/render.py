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
from docket.questions import EvalReport, Score
from docket.recall import ReadResult, VerifyResult
from docket.vault.anchors import format_time
from docket.vault.curatelog import Line
from docket.vault.docstore import PutResult, RedactReport
from docket.vault.entitystore import Written
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


def redacted(report: RedactReport, storage: str) -> list[str]:
    """What `docket doc redact` did or would do: the docs and their markers, what it left and why, and where the old
    text still is. Paths, ids, line numbers and rule names only, never a value."""
    would = "would redact" if report.dry_run else "redacted"
    if not report.docs:
        lines = [f"{would:<9} nothing: every doc is as the settings would have it"]
    else:
        counts = ", ".join(f"{n} {rule}" for rule, n in report.counts.items())
        lines = [f"{would:<9} {len(report.docs)} doc{'s' if len(report.docs) > 1 else ''}: {counts}"]
        lines += [
            f"          {doc.id} rev {doc.rev}{', a hand edit taken in' if doc.by_hand else ''}" for doc in report.docs
        ]
    if report.left:
        lines.append(f"left      {len(report.left)} problem(s); each file is left as it is:")
        lines += [f"          {why}" for why in report.left]
        lines.append("          edit or delete each, then run docket doc redact again")
    if report.docs and not report.dry_run:
        where = (
            "the history of the repo the vault is a folder of, which Docket never rewrites"
            if storage == "folder"
            else "this vault's git history"
        )
        lines.append(f"history   earlier revs still hold what was redacted, in {where}")
    if report.dry_run:
        lines.append("dry run: nothing written")
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
    if reading.absent:
        lines.append(f"in no chunk: {', '.join(reading.absent)}")
    if result.models.state == "skipped" and result.models.why:
        lines.append(f"no vectors in this search: {result.models.why}")
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


def _score_row(name: str, s: Score) -> str:
    return f"{name:<14}{s.questions:>4}{s.recall:>12.2f}{s.mrr:>7.2f}"


def eval_report(report: EvalReport) -> list[str]:
    """`docket eval`'s table: a row per kind, per language and for all, then each question missed at k."""
    lines = [f"eval      {report.file}: {report.all.questions} questions, k {report.k}"]
    if report.behind:
        lines.append(report.behind)
    seen = report.vectors
    if seen is not None:
        lines.append(f"vectors   {seen.model}: {seen.have} of {seen.total} chunks")
        if seen.skipped:
            lines.append(f"          {seen.skipped} question(s) ran without them: {seen.why}")
    lines.append(f"{'kind':<14}{'n':>4}{f'recall@{report.k}':>12}{'MRR':>7}")
    lines += [_score_row(kind, s) for kind, s in report.kinds.items()]
    lines.append("")
    lines += [_score_row(lang, s) for lang, s in report.langs.items()]
    lines.append(_score_row("all", report.all))
    missed = [r for r in report.questions if r.recall < 1]
    if missed:
        lines += ["", f"missed at {report.k}"]
    for r in missed:
        wanted = ", ".join(w for w, rank in zip(r.want, r.ranks, strict=True) if rank is None)
        why = f"  (search refused: {r.refused})" if r.refused else ""
        lines.append(f"  {r.n:>3}. {r.kind}  {r.lang}  {r.q}  ->  {wanted}{why}")
    if report.unknown:
        lines += ["", "wanted, but naming no record, doc or thread in the index:"]
        lines += [f"  {text}" for text in report.unknown]
    return lines


def _part(line: Line) -> str:
    """What one op changed, in a few words."""
    change = line.change
    if change is None:
        return f"undoes {line.of}" if line.op == "undo" else line.key
    if change.entity is not None:
        return f"{change.entity.type}, {'; '.join(sorted(change.entity.name.values()))}"
    if change.alias is not None:
        return f"alias {change.alias.text}{' (asr)' if change.alias.kind == 'asr' else ''}"
    if change.name is not None:
        return f"name {change.name.lang} {change.name.text}"
    if change.id is not None:
        return f"id {change.id.key} {change.id.value}"
    related = change.related
    return f"related {related.type} {related.target}" if related is not None else ""


def curate_written(written: Written) -> list[str]:
    lines: list[str] = []
    if written.finished:
        lines.append(f"finished first, what a stopped write left: {', '.join(written.finished)}")
    lines += [f"a hand edit of {rel} went in first, on its own" for rel in written.taken_in]
    lines += [f"{line.id}  {line.op:<6} {line.entity}  {_part(line)}" for line in written.lines]
    if written.only_logged:
        lines.append(f"only logged, the file no longer held what they added: {', '.join(written.only_logged)}")
    if written.already:
        lines.append(f"already there: {written.already}")
    lines.append(f"committed {written.commit[:12]}" if written.commit else "nothing to write")
    return lines


def curate_refused(problems: list[str]) -> list[str]:
    return ["refused; nothing was written", *(f"  {p}" for p in problems)]


def curate_log(lines: list[Line], undone_by: dict[str, str], problems: tuple[str, ...]) -> list[str]:
    out = [
        f"{line.at:%Y-%m-%d %H:%M}Z  {line.id}  {line.op:<6} {line.entity or '-'}  {_part(line)}  ({line.by}, "
        f"{line.via}){f'  undone by {undone_by[line.id]}' if line.id in undone_by else ''}"
        for line in lines
    ]
    if not lines:
        out.append("no changes logged yet")
    return out + [f"! {problem}" for problem in problems]
