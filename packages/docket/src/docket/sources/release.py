"""Releasing held recordings (#31): a held recording goes into staging already cut, only after its owner types back a
code at a console, and leaves the hold list after its release is committed.

- `show()`: a held recording as the source's mapper reads it from an import folder, that one hold lifted in memory,
  each record through the gate's intake checks (DR-0204). It writes nothing.
- `release()`: checks each proposal, shows its screen, asks for the code, and on it commits the release line, then
  the hold's removal, then stages the records through the gate, which cuts them (DR-0202, DR-0203).
- `load_proposals()`: the proposals file the cuts skill writes (DR-0206).

The question and its checks live here, and `release()` has no parameter that answers it, so a Python caller, a pipe
or Claude Code's shell gets a refusal, not a release. None of this stops a process running as the user on purpose: it
makes a release a deliberate act, which the cuts skill forbids Claude.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from docket import console
from docket.errors import DocketError
from docket.sources import runner
from docket.sources.contract import Manifest
from docket.sources.ingest import map_folder
from docket.vault import cuts as cutting
from docket.vault import holds, releaselog
from docket.vault.config import UniqueKeyLoader
from docket.vault.cuts import Cut, merge, parse, removes, widen
from docket.vault.gate import Gate
from docket.vault.git import toplevel
from docket.vault.records import RawRecord, Segment, is_native_id
from docket.vault.releaselog import Kept, LoggedCut, Release
from docket.vault.segments import segments
from docket.vault.vault import Vault

EDGE = 2
PROPOSAL_KEYS = frozenset({"id", "cuts", "keep", "note", "held"})


@dataclass(frozen=True)
class Proposal:
    """What to release, as the cuts skill proposes it or the command line gives it: never a confirmation."""

    id: str
    cuts: tuple[str, ...] = ()
    keep: tuple[str, ...] = ()
    note: str | None = None
    # Why the skill left it held: the recording is shown as held, with this reason.
    held: str | None = None


@dataclass(frozen=True)
class Viewed:
    """A record as the mapper made it, and what intake makes of it: the record to stage, or why intake drops it."""

    record: RawRecord
    view: RawRecord | str


@dataclass
class ReleaseReport:
    released: list[str] = field(default_factory=list[str])
    # Held again after a release (a merge brought the hold back), and taken off.
    taken_off: list[str] = field(default_factory=list[str])
    # Released and off the hold already, with nothing staged (a crash after the hold's commit): staged now.
    restaged: list[str] = field(default_factory=list[str])
    held: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    refused: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    stopped: bool = False


@dataclass(frozen=True)
class _Checked:
    proposal: Proposal
    records: list[RawRecord]
    views: dict[str, RawRecord | str]
    # Each cut as typed, and the merged spans they widen to.
    asked: list[tuple[str, Cut]]
    spans: list[Cut]

    def kept(self) -> list[RawRecord]:
        return [v for rid in self.proposal.keep if isinstance(v := self.views[rid], RawRecord)]


# -- the console --------------------------------------------------------------------------------------------------

# The question and its checks are docket.console's (DR-0518). These two names stay, each only a call through, for the
# reference source's tests a project copied, which stand in for the owner under them.


def _console_problem() -> str | None:
    return console.problem(console.RELEASE)


def _ask(prompt: str) -> str:
    return console.ask(prompt)


# -- reading a held recording ----------------------------------------------------------------------------------------


def records_under(
    vault: Vault, source: str, folder: Path, ids: set[str], held: frozenset[str]
) -> dict[str, list[RawRecord]]:
    """Each id's records as the source's mapper reads them from an import folder, with those ids' holds lifted in
    memory: the mapper's holds input is the list less them, so contract 1 doesn't change."""
    mapper = runner.find_mapper(vault, source)
    if not folder.is_dir():
        raise DocketError(f"{folder} isn't a folder")
    run = f"hold-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    manifest = Manifest(run=run, source=source, captures=0, final=True, folder=str(folder))
    mapped, _, _ = map_folder(mapper, source, manifest, [], folder=folder, held=frozenset(held - ids))
    wanted = frozenset(ids)
    out: dict[str, list[RawRecord]] = {i: [] for i in ids}
    for record in mapped.records:
        found = next(
            (h for ref in (record.rid, record.thread, record.container) if (h := holds.holder(ref, source, wanted))),
            None,
        )
        if found is not None:
            out[found].append(record)
    return {i: sorted(rs, key=lambda r: r.rid) for i, rs in out.items()}


def show(vault: Vault, source: str, native: str, folder: Path) -> list[Viewed]:
    """A held recording's records, each with what intake makes of it. Nothing is written. An id released already and
    held again is refused: its cuts are on record, and showing it would print the stretch the release took out."""
    held = holds.read(vault.root, source)
    if native not in held:
        raise DocketError(f"{native} isn't held in {holds.HOLDS}/{source}.txt; docket hold list names what is")
    if native in releaselog.read(vault.root, source):
        raise DocketError(
            f"{native} was released already and is held again (a merge brought the hold back); its cuts are on "
            f"record, so take the hold off with docket hold release {source} {native} --from <folder>"
        )
    records = records_under(vault, source, folder, {native}, held)[native]
    if not records:
        raise DocketError(f"{native} isn't in {folder}")
    gate = Gate(vault)
    return [Viewed(r, gate.intake_view(r)) for r in records]


def clock(ms: int, *, up: bool = False) -> str:
    """A moment as m:ss or h:mm:ss, rounded down, or up for an end, so a range read off it covers the utterance."""
    seconds = -(-ms // 1000) if up else ms // 1000
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def _span(s: Segment | Cut) -> str:
    start, end = (s.start_ms, s.end_ms) if isinstance(s, Segment) else (s.start, s.end)
    return _range(start, end)


def _range(start: int, end: int) -> str:
    return f"{clock(start)}-{clock(end, up=True)}"


def _utterance(s: Segment) -> str:
    words = " ".join(s.text.split())
    return f"{s.speaker}: {words}" if s.speaker else words


def show_lines(viewed: Sequence[Viewed]) -> list[str]:
    """What `docket hold show` prints: each record, a transcript one utterance a line with its times."""
    lines: list[str] = []
    for item in viewed:
        view = item.view
        if isinstance(view, str):
            lines.append(f"{item.record.rid}  {item.record.kind}  dropped at intake: {view}")
            continue
        said = segments(view)
        if said:
            whole = _range(min(s.start_ms for s in said), max(s.end_ms for s in said))
            lines.append(f"{view.rid}  {view.kind}  {whole}, {len(said)} utterances")
            lines += [f"  [{_span(s)}] {_utterance(s)}" for s in said]
            continue
        lines.append(f"{view.rid}  {view.kind}")
        lines += _record_text(view, "  ")
    return lines


def _record_text(view: RawRecord, indent: str) -> list[str]:
    lines = [f"{indent}title     {view.title}"] if view.title else []
    text = view.text.splitlines() or ["(none)"]
    lines.append(f"{indent}text      {text[0]}")
    lines += [f"{indent}          {line}" for line in text[1:]]
    return lines


# -- checking a proposal -------------------------------------------------------------------------------------------


def _check(gate: Gate, proposal: Proposal, records: list[RawRecord], in_raw: bool) -> _Checked | str:
    """The proposal checked against the recording, or why it stays held."""
    if not records:
        return "not in this folder"
    if in_raw:
        return "a record under it is already in raw, uncut, so it stays held until docket forget"
    transcripts = [r for r in records if r.segments]
    if not transcripts:
        return "no transcript to cut by time"
    if not proposal.cuts:
        return "no cut: a held recording goes in only with its left-out stretches cut"
    said = [s for r in transcripts for s in r.segments or []]
    end = max(s.end_ms for s in said)
    asked: list[tuple[str, Cut]] = []
    for spec in proposal.cuts:
        try:
            cut = parse(spec)
        except DocketError as e:
            return f"cut {spec}: {e}"
        if cut.unit != "ms":
            return f"cut {spec} is characters; a transcript is cut by time, such as 12:30-14:05"
        if cut.start >= end:
            return f"cut {spec} starts after the transcript ends ({clock(end, up=True)})"
        wide = widen(said, cut)
        if not removes(said, wide):
            return f"cut {spec} falls between utterances and removes none"
        asked.append((spec, wide))
    views = {r.rid: gate.intake_view(r) for r in records}
    for rid in proposal.keep:
        record = next((r for r in records if r.rid == rid), None)
        if record is None:
            return f"keep {rid}: not a record of {proposal.id} in this folder"
        if record.segments:
            return f"keep {rid}: a transcript is cut, not kept"
        if isinstance(views[rid], str):
            return f"keep {rid}: intake drops it ({views[rid]})"
    # Spans widened to a fixed point stay one when merged: an utterance touching their union touches one of them.
    return _Checked(proposal, records, views, asked, merge([wide for _, wide in asked]))


def _screen(checked: _Checked, place: str) -> list[str]:
    proposal = checked.proposal
    lines = [f"{proposal.id}  ({place})"]
    if proposal.note:
        lines.append(f"  note        {proposal.note}")
    for record in checked.records:
        view = checked.views[record.rid]
        label = f"  {record.kind:<11} {record.rid}"
        if isinstance(view, str):
            lines.append(f"{label}: dropped at intake ({view})")
        elif view.segments:
            said = segments(view)
            lines.append(f"{label}, {len(said)} utterances")
            for span in checked.spans:
                lines += _cut_lines(checked, span, said)
        elif record.rid in proposal.keep:
            lines.append(f"{label}: kept as it reads here")
            lines += _record_text(view, "    ")
        else:
            lines.append(f"{label}: left out")
    return lines


def _cut_lines(checked: _Checked, span: Cut, said: list[Segment]) -> list[str]:
    specs = [spec for spec, wide in checked.asked if span.start <= wide.start and wide.end <= span.end]
    gone = removes(said, span)
    lines = [f"    cut {', '.join(specs)}, widened to {_span(span)}, removes {len(gone)} utterances"]
    before = [s for s in said if s.end_ms <= span.start and s not in gone]
    after = [s for s in said if s.start_ms >= span.end and s not in gone]
    if before:
        lines.append(f"        {_span(before[-1])}  {_utterance(before[-1])}")
    shown = gone if len(gone) <= 2 * EDGE else [*gone[:EDGE], None, *gone[-EDGE:]]
    for s in shown:
        lines.append(
            f"      - ... {len(gone) - 2 * EDGE} more" if s is None else f"      - {_span(s)}  {_utterance(s)}"
        )
    if after:
        lines.append(f"        {_span(after[0])}  {_utterance(after[0])}")
    return lines


# -- releasing -----------------------------------------------------------------------------------------------------


def release(vault: Vault, source: str, folder: Path, proposals: Sequence[Proposal]) -> ReleaseReport:
    """Release held recordings, one question each. Refused, before anything is read, without the owner at a
    console."""
    problem = _console_problem()
    if problem is not None:
        raise DocketError(problem)
    report = ReleaseReport()
    # A line or a list a crash left on disk uncommitted reaches git before anything else is decided on it, each read
    # first, so one that doesn't parse is refused, never committed. A conflict in the vault refuses here, before the
    # owner sees a screen or types a code: the release's own commits would refuse it after (DR-0528).
    with vault.writing():
        vault.refuse_unmerged()
        releaselog.read(vault.root, source)
        holds.read(vault.root, source)
        releaselog.commit_pending(vault, source)
        holds.commit_pending(vault, source)
    held = holds.read(vault.root, source)
    granted = releaselog.read(vault.root, source)
    wanted = {p.id for p in proposals if p.held is None and (p.id in held or p.id in granted)}
    records = records_under(vault, source, folder, wanted, held) if wanted else {}
    raw_rids = vault.rawlog.rids() if wanted else set[str]()
    gate = Gate(vault)
    for n, proposal in enumerate(proposals, start=1):
        place = f"{n} of {len(proposals)}"
        native = proposal.id
        if proposal.held is not None:
            report.held.append((native, proposal.held))
        elif native in held and native in granted:
            if not _take_off_again(vault, source, granted[native], place, report):
                return report
            _stage(vault, source, native, records[native])
        elif native in granted:
            if not records[native]:
                report.refused.append((native, "released already, and not in this folder"))
                continue
            _stage(vault, source, native, records[native])
            report.restaged.append(native)
        elif native not in held:
            report.refused.append((native, f"not held in {holds.HOLDS}/{source}.txt"))
        else:
            in_raw = any(holds.holder(rid, source, frozenset({native})) for rid in raw_rids)
            checked = _check(gate, proposal, records[native], in_raw)
            if isinstance(checked, str):
                report.held.append((native, checked))
                console.say(f"{native}  ({place})  stays held: {checked}")
                continue
            if not _release_one(vault, source, checked, place, report):
                return report
    return report


def _question(lines: list[str], what: str, native: str) -> str:
    """Show the screen and ask: "yes" for the code typed back, "no", or "stop"."""
    return console.typed_back(lines, f"{what} {native}", "keep it held", f"{native} stays held", asker=_ask)


def _release_one(vault: Vault, source: str, checked: _Checked, place: str, report: ReleaseReport) -> bool:
    """One recording's screen, question and release. False when the owner stops."""
    native = checked.proposal.id
    answer = _question(_screen(checked, place), "release", native)
    if answer == "stop":
        report.stopped = True
        report.held.append((native, "stopped before it"))
        return False
    if answer == "no":
        report.held.append((native, "not confirmed"))
        return True
    line = Release(
        id=native,
        cuts=[LoggedCut(start=c.start, end=c.end) for c in checked.spans],
        keep=[Kept(rid=v.rid, v=v.v) for v in checked.kept()],
        at=datetime.now(UTC),
        machine=vault.machine,
    )
    shown = _shown(Gate(vault), checked)
    with vault.writing():
        if native not in holds.read(vault.root, source) or native in releaselog.read(vault.root, source):
            report.held.append((native, "its hold or release changed since the screen; run the release again"))
            return True
        releaselog.add(vault, source, line)
        holds.remove(vault, source, [native])
    _stage(vault, source, native, checked.records, shown)
    report.released.append(native)
    return True


def _shown(gate: Gate, checked: _Checked) -> frozenset[tuple[str, str]]:
    """The versions the screen showed, as the gate will stage them: each transcript cut by the release, each record
    kept as it reads. Only these are classified work by the owner."""
    out: set[tuple[str, str]] = set()
    for record in checked.records:
        if record.segments:
            try:
                view = gate.intake_view(cutting.apply(record, checked.spans))
            except DocketError:
                # A transcript the times don't fit; the gate leaves it out.
                continue
        elif record.rid in checked.proposal.keep:
            view = checked.views[record.rid]
        else:
            continue
        if isinstance(view, RawRecord):
            out.add((view.rid, view.v))
    return frozenset(out)


def _take_off_again(vault: Vault, source: str, granted: releaselog.Granted, place: str, report: ReleaseReport) -> bool:
    first = granted.first
    lines = [
        f"{granted.id}  ({place})",
        f"  released {first.at:%Y-%m-%d %H:%M} UTC on {first.machine}, and held again: a merge brought the hold back",
    ]
    answer = _question(lines, "take the hold off", granted.id)
    if answer == "stop":
        report.stopped = True
        return False
    if answer == "yes":
        holds.remove(vault, source, [granted.id])
        report.taken_off.append(granted.id)
    else:
        report.held.append((granted.id, "not confirmed"))
    return True


def _stage(
    vault: Vault, source: str, native: str, records: list[RawRecord], shown: frozenset[tuple[str, str]] = frozenset()
) -> None:
    """Stage a released recording: the gate cuts it by its release and leaves out what the release doesn't keep.
    Only the versions this run showed the owner are classified work by them; anything else under the id (a version
    staged elsewhere or earlier, or one staged again after a crash or a merge) waits as pending for a classification."""
    if native in holds.read(vault.root, source):
        return
    gate = Gate(vault)
    gate.stage(records)
    if not shown:
        return
    for entry in gate.list(source=source, state="pending").entries:
        record = entry.staged.record
        if (record.rid, record.v) in shown:
            gate.classify(entry.key, "work", by="user")


# -- the proposals file --------------------------------------------------------------------------------------------


def load_proposals(path: Path, source: str) -> list[Proposal]:
    """The cuts skill's proposals file: refused inside any git work tree, where a commit could take it. Errors name an
    entry by its place and id, never its note."""
    if toplevel(path.resolve().parent) is not None:
        raise DocketError(
            f"{path} is inside a git work tree, where a commit could take it; keep the proposals file in a temp folder "
            "outside any repo"
        )
    try:
        data: Any = yaml.load(path.read_bytes().decode("utf-8-sig"), Loader=UniqueKeyLoader)  # noqa: S506 - SafeLoader
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise DocketError(f"{path}: can't read it as YAML ({type(e).__name__})") from None
    if not isinstance(data, dict) or set(cast(dict[str, Any], data)) - {"source", "recordings"}:
        raise DocketError(f"{path}: a proposals file is source and recordings, and nothing else")
    top = cast(dict[str, Any], data)
    if top.get("source") != source:
        raise DocketError(f"{path} proposes for another source than {source}")
    items = top.get("recordings")
    if not isinstance(items, list) or not items:
        raise DocketError(f"{path}: recordings is a list of what to release")
    return [_proposal(path, n, item) for n, item in enumerate(cast(list[Any], items), start=1)]


def _proposal(path: Path, n: int, item: Any) -> Proposal:
    where = f"{path}: recording {n}"
    entry = cast(dict[str, Any], item) if isinstance(item, dict) else {}
    native = entry.get("id")
    if not isinstance(native, str) or not is_native_id(native):
        raise DocketError(f"{where} has no id")
    where += f" ({native})"
    if set(entry) - PROPOSAL_KEYS:
        raise DocketError(f"{where} has a key a proposal doesn't know; it takes id, cuts, keep, note or held")
    if ("cuts" in entry) == ("held" in entry):
        raise DocketError(f"{where}: give it cuts to release, or held to say why it stays held")
    cuts = _strings(entry.get("cuts", []), f"{where}: cuts", times=True)
    keep = _strings(entry.get("keep", []), f"{where}: keep", times=False)
    for name in ("note", "held"):
        if entry.get(name) is not None and not isinstance(entry[name], str):
            raise DocketError(f"{where}: {name} is text")
    return Proposal(native, tuple(cuts), tuple(keep), entry.get("note"), entry.get("held"))


def _strings(value: Any, where: str, *, times: bool) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in cast(list[Any], value)):
        hint = '; write each time quoted, such as "12:30-14:05": YAML reads an unquoted 12:30 as a number'
        raise DocketError(f"{where} is a list of text{hint if times else ''}")
    return cast(list[str], value)
