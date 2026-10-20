"""The compact text of Docket's answers: the lines the `docket doc` and `docket search` commands print, and the text an
MCP tool returns for Claude to read beside its structured result.

Every line goes through `printable`: raw text from a source can carry terminal codes (OSC 52 sets the clipboard), and
both a terminal and Claude Code's screen would act on them.
"""

from __future__ import annotations

from collections.abc import Iterable
from zoneinfo import ZoneInfo

from docket.briefs import BriefsReport
from docket.curate.batch import BatchView, Measure, Status
from docket.curate.propose import UNSEEN, CardSpelling
from docket.decisions import DecisionsReport, DecisionState
from docket.index import SearchResult
from docket.plaintext import printable
from docket.questions import EvalReport, Score
from docket.recall import LocatorRead, ReadResult, StaleReport, VerifyResult
from docket.reconcile.spots import Listing, SpotsReport
from docket.vault.anchors import format_time
from docket.vault.curatelog import Line
from docket.vault.docstore import PutResult, RedactReport
from docket.vault.entitystore import Written
from docket.vault.processed import DocProblem
from docket.vault.spans import Span


def text(lines: Iterable[str]) -> str:
    """Lines as one text, each made printable."""
    return "\n".join(printable(line) for line in lines) + "\n"


def span(s: Span, indent: str = "") -> list[str]:
    head = f"{indent}{s.anchor}"
    if s.ts is not None:
        head += f"  {s.kind} {s.ts:%Y-%m-%d %H:%M}Z"
    if s.author:
        head += f" {s.author}"
    if s.versions > 1 and s.note is None:
        head += f" ({s.versions} versions; this is the latest)"
    lines = [head]
    if s.note is not None:
        lines.append(f"{indent}  ({s.note})")
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
    lines += [f"note: {note}" for note in result.notes]
    if result.redactions:
        where = ", ".join(str(n) for n in result.redacted_lines)
        counts = ", ".join(f"{n} {rule}" for rule, n in result.redactions.items())
        lines.append(f"redacted on line{'s' if len(result.redacted_lines) > 1 else ''} {where}: {counts}")
    if result.pinned_lines:
        where = ", ".join(str(n) for n in result.pinned_lines)
        lines.append(f"pinned the doc-line refs on line{'s' if len(result.pinned_lines) > 1 else ''} {where}")
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


def read(result: LocatorRead) -> list[str]:
    lines: list[str] = []
    if result.resolved is not None and result.resolved != result.locator.strip():
        lines.append(f"read as {result.resolved}")
    lines += [f"! {problem}" for problem in result.problems]
    if result.doc is not None:
        lines.append(f"{result.doc.id} rev {result.doc.rev}  {result.doc.path}")
        if result.doc.status is not None:
            state = "current" if result.doc.current else "not current"
            lines.append(f"a {result.doc.status} decision, {state}")
        if result.doc.superseded_by:
            lines.append(f"superseded by {', '.join(result.doc.superseded_by)}")
        for line in result.doc.lines:
            lines.append(f"{line.n:>4}  {line.text}")
            lines += [f"        ! {flag}" for flag in line.flags]
    elif result.view != "raw" and not result.lines and not result.legacy:
        lines.append("no current doc points at it yet")
    lines += [f"{cited.doc} rev {cited.rev} line {cited.n}: {cited.text}" for cited in result.lines]
    lines += [f"{ref.id}, a legacy note, covers it: {', '.join(ref.old_paths)}" for ref in result.legacy]
    for s in result.spans:
        lines += span(s)
    return lines


def reads(result: ReadResult) -> list[str]:
    """A read of several locators (DR-0725): each read under a `== <locator>` line, in order, and last the locators
    left past the room, to ask for again."""
    lines: list[str] = []
    for one in (result, *result.more):
        lines += [f"== {one.locator}", *read(one)]
    if result.later:
        lines.append(f"past the room, not read: ask again with more for {', '.join(result.later)}")
    return lines


def verify(result: VerifyResult) -> list[str]:
    lines = [f"{result.doc} rev {result.rev}"]
    for checked in result.lines:
        lines.append(f"{checked.n:>4}  {checked.text}")
        lines += [f"        ! {problem}" for problem in checked.problems]
        for s in checked.spans:
            lines += span(s, "        ")
        for ref in checked.refs:
            if ref.problem is not None:
                lines.append(f"        {ref.ref}  ! {ref.problem}")
                continue
            lines.append(f"        {ref.ref}  line {ref.n} now: {ref.text}")
            for s in ref.spans:
                lines += span(s, "          ")
    return lines


def stale(report: StaleReport) -> list[str]:
    """What `docket doc stale` found: lines citing a line that changed, corrections not applied, damaged anchors."""
    if not (report.flagged or report.unapplied or report.damaged):
        return ["nothing flagged: every doc line cites a line that still reads the same"]
    lines: list[str] = []
    for title, found in (("cite a line that changed", report.flagged), ("corrections not applied", report.unapplied)):
        if found:
            lines.append(f"{len(found)} line(s) {title}:" if found is report.flagged else f"{len(found)} {title}:")
            for one in found:
                lines += [f"  {one.doc} line {one.n}: {one.text}", f"    {one.why}"]
    if report.damaged:
        lines.append(f"{len(report.damaged)} line(s) whose anchors an older Docket's redaction wrote inside:")
        for hurt in report.damaged:
            where = (
                f"restore them from rev {hurt.restore_from}" if hurt.restore_from else "no earlier rev has them whole"
            )
            lines.append(f"  {hurt.doc} line {hurt.n}: {hurt.token}; {where}")
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
    if result.rerank.state == "skipped" and result.rerank.why:
        lines.append(f"not reranked: {result.rerank.why}")
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
            place = f"  {hit.place}" if hit.place else ""
            lines.append(f"  {hit.rank:>3}. {hit.locator}{doc_lines}{place}{at}{f'  {who}' if who else ''}")
            lines.append(f"       {hit.snippet}")
            lines += [f"       ! {mark}" for mark in hit.marks]
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
    reranked = report.rerank
    if reranked is not None and reranked.model is None:
        lines.append(f"reranker  off: {reranked.off}")
    elif reranked is not None:
        lines.append(f"reranker  {reranked.model}: the top {reranked.pairs}, with no time budget")
        if reranked.skipped:
            lines.append(f"          {reranked.skipped} question(s) ran without it: {reranked.why}")
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
    lines += [f"{line.id}  {line.op:<6} {line.entity or '-'}  {_part(line)}" for line in written.lines]
    if written.only_logged:
        lines.append(f"only logged, the file no longer held what they added: {', '.join(written.only_logged)}")
    if written.already:
        lines.append(f"already there: {written.already}")
    lines.append(f"committed {written.commit[:12]}" if written.commit else "nothing to write")
    lines += list(written.rendered)
    return lines


def curate_refused(problems: list[str]) -> list[str]:
    return ["refused; nothing was written", *(f"  {p}" for p in problems)]


def curate_log(
    lines: list[Line], undone_by: dict[str, str], problems: tuple[str, ...], unfinished: set[str] | None = None
) -> list[str]:
    def marks(line: Line) -> str:
        undone = f"  undone by {undone_by[line.id]}" if line.id in undone_by else ""
        return undone + ("  not finished: the next write commits it" if line.id in (unfinished or set()) else "")

    out = [
        f"{line.at:%Y-%m-%d %H:%M}Z  {line.id}  {line.op:<6} {line.entity or '-'}  {_part(line)}  ({line.by}, "
        f"{line.via}){marks(line)}"
        for line in lines
    ]
    if not lines:
        out.append("no changes logged yet")
    return out + [f"! {problem}" for problem in problems]


ADDS = {"name": "the name", "plain": "alias", "asr": "asr alias", "no": "found already"}
TRUST_SHOWN = {UNSEEN: "other order, not seen"}


def _spellings(spellings: list[CardSpelling]) -> str:
    return "  ".join(f"{s.text} ({TRUST_SHOWN.get(s.trust, s.trust)}, {ADDS[s.add]})" for s in spellings)


def curate_round(view: BatchView) -> list[str]:
    states = [c.state for c in view.cards]
    lines = [
        f"round    {view.round}, started {view.started:%Y-%m-%d %H:%M}Z: {len(states)} card(s), "
        f"{states.count('open')} open"
    ]
    if view.left_out:
        counts = sorted(view.left_out.items(), key=lambda kv: (-kv[1], kv[0]))
        lines.append("left out " + ", ".join(f"{n} {why}" for why, n in counts))
    lines += [
        f"applied  by itself: {line.id}  {line.op} {line.entity or '-'}  {_part(line)}"
        + ("  not finished: the next write commits it" if line.id in view.unfinished else "")
        for line in view.applied
    ]
    lines += [f"taken back by itself: {what}" for what in view.taken_back]
    lines += [f"skipped  by itself: {why}" for why in view.skipped]
    lines += [f"tier 1   {view.tier1}"] if view.tier1 else []
    for shown in view.cards:
        c = shown.card
        what = (
            f"new {c.type}  {c.name} ({c.entity})"
            if c.kind == "new"
            else f"id for {c.name} ({c.entity}): {c.fact[0]} {c.fact[1]}"
            if c.fact is not None
            else f"alias for {c.name} ({c.entity})"
        )
        gap = (
            f"missed in {c.missed_chunks} chunk(s) of {c.missed_containers} container(s)"
            if c.missed_containers
            else f"found already, in {c.chunks} chunk(s) of {c.containers} container(s)"
        )
        lines.append(f"{c.n:>2}  {c.id}  {what}  {gap}, newest {c.newest[:10]}  {shown.state}")
        lines.append(f"      {_spellings(c.spellings)}")
        for snippet in shown.snippets:
            text = snippet.text
            if snippet.mark is not None:
                a, b = snippet.mark
                text = f"{text[:a]}»{text[a:b]}«{text[b:]}"
            lines.append(f"      ... {text} ...  {snippet.locator}")
    if not view.cards:
        lines.append("no cards this round")
    if view.before is not None:
        lines += curate_measures(view.before, view.after)
    return lines


def _score(pair: tuple[float, float]) -> str:
    return f"{pair[0]:.2f}/{pair[1]:.2f}"


def curate_measures(before: Measure | None, after: Measure | None) -> list[str]:
    """Your question set's recall at 10 and MRR, before the round and after it."""
    if before is None:
        return []
    if after is None:
        return [f"questions before {_score(before.all)}, after: when the round's last card is decided"]
    kinds = ", ".join(
        f"{kind} {_score(score)} -> {_score(after.kinds.get(kind, score))}" for kind, score in before.kinds.items()
    )
    return [f"questions {_score(before.all)} -> {_score(after.all)} (recall@10/MRR); {kinds}"]


def curate_status(status: Status, config: object) -> list[str]:
    when = f", the last started {status.last:%Y-%m-%d}" if status.last is not None else ""
    lines = [f"due      {'yes' if status.due else 'no'}{when}"]
    if status.round is not None:
        lines.append(f"round    {status.round}: {status.cards} card(s), {status.open} open")
    weeks = list(status.minutes.items())[:4]
    lines.append("review   " + (", ".join(f"{week} {m:.0f} min" for week, m in weeks) or "no rounds yet"))
    lines.append(f"stoplist {status.stoplist} key(s)")
    if status.questions is None:
        lines.append(f"questions set curate.questions in {config} to a question file to measure a round")
    else:
        lines += curate_measures(status.before, status.after) or [f"questions {status.questions}, measured at a round"]
    return lines


def decisions(found: DecisionsReport) -> list[str]:
    """What `docket decision list` and the `decisions` tool say: each decision's standing and title, then its links.
    A proposed one never reads as decided: its standing says proposed (DR-0565)."""
    lines: list[str] = []
    for one in found.decisions:
        standing = "current" if one.current else "superseded" if one.status == "confirmed" else one.status
        lines.append(f"{standing:<10} {one.id}  {one.title}")
        lines.append(f"           on {one.subject}, rev {one.rev}")
        lines += [f"           {what} {', '.join(ids)}" for what, ids in _links(one) if ids]
        if one.flagged:
            lines.append(f"           {one.flagged} line(s) cite lines that changed")
        if one.note:
            lines.append(f"           {one.note}")
    if not found.decisions:
        lines.append("no decisions yet")
    if found.hidden:
        lines.append(f"hidden     {found.hidden} decision(s) on an entity a never-store list holds")
    return lines


def _links(one: DecisionState) -> list[tuple[str, list[str]]]:
    superseded = [one.superseded_by] if one.superseded_by else []
    return [
        ("replaces", one.replaces),
        ("contradicts", one.contradicts),
        ("supersedes", one.supersedes),
        ("superseded by", superseded),
        ("contradicted by", one.contradicted_by),
    ]


def briefs(found: BriefsReport) -> list[str]:
    """What `docket brief stale` and the `briefs` tool say: each brief's state and why, then what is hidden or
    missing. Ids, names, counts and rids only (DR-0556)."""
    lines: list[str] = []
    for one in found.briefs:
        head = f"{one.state:<9} {one.id}  {one.name}" + (f" ({one.type})" if one.type else "") + f", rev {one.rev}"
        lines.append(head)
        if one.why:
            lines.append(f"          {one.why}")
        if one.new.count:
            lines.append(f"          {one.new.count} new record(s): {', '.join(one.new.newest)}")
        if one.flagged:
            lines.append(f"          {one.flagged} line(s) cite lines that changed")
        if one.state == "orphaned":
            lines.append(
                f"          its entity is gone; {one.orphaned} holds its name now"
                if one.orphaned
                else "          its entity is gone, and no one entity holds its name"
            )
    if not found.briefs:
        lines.append("nothing stale: every brief is current")
    if found.hidden:
        lines.append(f"hidden    {found.hidden} brief(s) whose entity a never-store list holds")
    lines += [f"no brief  {entity}" for entity in found.missing]
    return lines


def spots(found: SpotsReport) -> list[str]:
    """What `docket reconcile spots` says: the pair and its fixes doc, the meeting's people, then each spot with the
    recorder's line and Whisper's words for it, the fix lines, and what was left out, counted."""
    doc = f"{found.doc.id}, rev {found.doc.rev}" if found.doc.rev is not None else f"{found.doc.id}, none yet"
    lines = [f"{found.rid}: {found.transcript} beside {found.whisper}", f"doc        {doc}"]
    if found.why is not None:
        return [*lines, f"no spots   {found.why}"]
    for i, person in enumerate(found.people):
        aliases = ", ".join(f"{a.text} (asr)" if a.asr else a.text for a in person.aliases)
        said = f"{person.id} ({person.type}) {person.name}" + (f"; {aliases}" if aliases else "")
        lines.append(f"{'people' if i == 0 else '':<10} {said}")
    for spot in found.spots:
        head = f'{spot.kind:<10} {spot.id}  "{spot.left}" / "{spot.right}"'
        head += f"  near {', '.join(spot.near)}" if spot.near else ""
        head += f"  settled; turned down: {', '.join(spot.rejected)}" if spot.rejected else ""
        lines += [
            head,
            f"           {spot.recorder_anchor} {spot.speaker or '?'}: {spot.line}",
            f"           {spot.whisper_anchor} Whisper: {spot.heard}",
        ]
    for fix in found.fixes:
        said = f'"{fix.left}" / "{fix.right}" -> "{fix.chosen}"' if fix.left is not None else "its texts not shown"
        lines.append(f"fix        line {fix.n} {fix.state}: {said}" + (f"; {fix.why}" if fix.why else ""))
    if not found.spots:
        lines.append("no spots   the two transcripts agree, or every spot is decided")
    out = found.left_out
    capped = ", ".join(f"{n} {kind}" for kind, n in out.cap.items() if n)
    parts = [f"{sum(out.cap.values())} past the cap ({capped})"] if capped else []
    parts += [f"{out.heard_differently} heard differently"] if out.heard_differently else []
    parts += [f"{out.never_store} near a never-store value"] if out.never_store else []
    return lines + ([f"left out   {'; '.join(parts)}"] if parts else [])


def reconcile(found: Listing, tz: ZoneInfo) -> list[str]:
    """What `docket reconcile list` says: each recording waiting, newest first, with its counts; then how many the
    screen kept out."""
    lines: list[str] = []
    for one in found.recordings:
        lines.append(f"{one.rid}  {one.ts.astimezone(tz):%Y-%m-%d %H:%M}" + (f"  {one.title}" if one.title else ""))
        counts = ", ".join(f"{n} {kind}" for kind, n in one.spots.items() if n) or "none"
        lines.append(f"           spots: {counts}" + (f"; {one.waiting} fix(es) wait for you" if one.waiting else ""))
    if not found.recordings:
        lines.append("nothing to reconcile: no recording here has spots no fix decides")
    if found.hidden:
        lines.append(f"hidden     {found.hidden} recording(s) a never-store list or your decisions keep out")
    return lines
