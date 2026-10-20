"""`docket sync recordings <sync> <source>` (#267): each recording of a source that needs reading gets an agent of its
own, which reads it once, from files this command writes, and writes both its block lines and its note.

Run as each source's agent comes back, after its promotion: the recordings it lists are the source's in raw, in its
window, that no current note covers, the index brought up to date first. A recording given an agent in this sync isn't
given another, so a second call gives only what is new. One with a record still in staging (pending, ready or held) is
skipped and said: its note would come from what raw holds alone, and would then cover the recording for good.

For each, under the sync folder, `<key>` the recording's own id when it is a short lowercase name, else `x` and 16 hex
of its rid's sha256, since a file name can't hold every id:

| File | What |
|---|---|
| `recordings/<source>/<key>.<n>.txt` | the recording as raw keeps it, in parts the Read tool takes whole |
| `agents/<source>/<key>.md` | its agent's prompt, the package's `recording.md` filled |
| `blocks/<source>/<key>.txt` | written by its agent, DR-0722's block |
| `recordings/<source>.json` | the source's recordings given agents, so two sources' calls never lose each other's |

A part opens with a head line, then holds each record at its latest version: a transcript a segment a line as
`@mm:ss Speaker: text`, notes a paragraph a line as `#n text`. It is at most 40,000 bytes, split between lines, under
the Read tool's 25,000 tokens, and a line is at most 1,000 characters, under the 2,000 at which Read cuts one.

What the files hold comes from raw through the lookup `read` uses, under the forgotten list in force: promoted records
only, their cuts made, nothing held or private. A forget's apply removes the whole sync folder (DR-0746).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from docket import recall
from docket.errors import DocketError
from docket.index import build as index_build
from docket.ledger import views
from docket.sources import runner
from docket.sync import folder, reconcile, start
from docket.vault.anchors import encode_rid, format_time, paragraphs
from docket.vault.records import RawRecord
from docket.vault.segments import segments
from docket.vault.staging import Staging
from docket.vault.vault import Vault

PART_BYTES = 40_000
LINE_CHARS = 1_000
# A recording's own id as its key: short, lowercase, and not the shape a hashed key takes.
KEY = re.compile(r"^(?!x[0-9a-f]{16}$)[a-z0-9_-]{1,64}$")
LAYOUT = "a title, who spoke, the notes with every number, date, name and commitment said, and the actions at the end"
NONE_BY_TIME = (
    "none: no meeting 10 minutes either side of its start, so no MINUTES, no OUTCOME, and no M- ref on the NOTES line"
)


@dataclass(frozen=True)
class Wave:
    """What a call gave: the recordings given agents now, how many had theirs from an earlier call, and what was said
    instead of an agent."""

    source: str
    given: list[folder.Recording]
    earlier: int
    said: list[str]


def key(rid: str) -> str:
    """A recording's key in the sync folder's file names: its own id, else `x` and 16 hex of its rid's sha256."""
    native = rid.partition(":")[2]
    return native if KEY.match(native) else "x" + hashlib.sha256(rid.encode("utf-8")).hexdigest()[:16]


def recordings(vault: Vault, sync: str, source: str, at: datetime) -> Wave:
    """Give each of a source's recordings that needs reading an agent of its own: its files, its prompt, and its line
    in the source's list. Refused for a source that isn't one of the sync's."""
    path, info = folder.find(sync, vault.root)
    window = next((s for s in info.sources if s.source == source), None)
    if window is None:
        named = ", ".join(s.source for s in info.sources)
        raise DocketError(f"{source} isn't a source of {info.sync}; its sources: {named}")
    index_build.update(vault)
    found = index_build.unnoted(vault, {source: (window.since, max(window.until, at))})
    listed = folder.recordings(path, source)
    known = {r.rid for r in listed}
    waiting = _staged(vault, source)
    said: list[str] = []
    todo: list[index_build.Unnoted] = []
    for unnoted in found:
        if unnoted.rid in known:
            continue
        if unnoted.rid in waiting:
            said.append(
                f"skipped {unnoted.rid}: {waiting[unnoted.rid]} waits in staging, and a note without it would cover "
                "the recording for good; the next sync reads it once it is all in raw"
            )
            continue
        todo.append(unnoted)
    earlier = sum(1 for u in found if u.rid in known)
    if not todo:
        return Wave(source, [], earlier, said)
    tz = ZoneInfo(info.zone)
    meetings = _meetings(vault, tz, at, [u.ts for u in todo])
    owner = reconcile.Owner.of(vault)
    prompt = start.template("recording.md")
    layout = _layout(vault, source)
    given: list[folder.Recording] = []
    with recall.Lookup(vault) as lookup:
        for unnoted in todo:
            records = _records(lookup, unnoted.rid)
            k = key(unnoted.rid)
            local = unnoted.ts.astimezone(tz)
            title = views.as_field(unnoted.title or "-")
            head = f"recording {unnoted.rid} | {title} | {local:%Y-%m-%d %H:%M} {info.zone}"
            files = _write_parts(path / folder.RECORDINGS / source, k, head, records)
            agent = path / folder.AGENTS / source / f"{k}.md"
            block_file = path / folder.BLOCKS / source / f"{k}.txt"
            # The agent's Write lands in a folder that is there.
            block_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            values = {
                "SOURCE": source, "VAULT": str(vault.root), "TODAY": info.today.isoformat(), "ZONE": info.zone,
                "OWED_SINCE": info.owed_since, "CONTEXT": str(path / folder.CONTEXT), "BLOCK": str(block_file),
                "RECORDING": unnoted.rid, "TITLE": views.as_field(unnoted.title or "no title"),
                "START": f"{local:%Y-%m-%d %H:%M}", "DAY": f"{local:%Y-%m-%d}",
                "FILES": "\n".join(f"- {file}" for file in files),
                "MEETINGS": _meetings_line(reconcile.recorded_in(meetings, unnoted.ts, owner), info),
                "LAYOUT": layout, "SYNC_AT": f"{info.made.astimezone(tz):%Y-%m-%dT%H:%M}",
            }  # fmt: skip
            _write(agent, start.fill(prompt, values))
            made = folder.Recording(
                rid=unnoted.rid, key=k, title=unnoted.title, start=unnoted.ts, prompt=str(agent),
                block=str(block_file), files=[str(f) for f in files],
            )  # fmt: skip
            given.append(made)
    listing = folder.Recordings(source=source, recordings=[*listed, *given])
    _write(folder.recordings_file(path, source), listing.model_dump_json(indent=2) + "\n")
    return Wave(source, given, earlier, said)


def lines(wave: Wave) -> list[str]:
    """What `docket sync recordings` prints: a line per recording given an agent, with its prompt's path, then what was
    said instead of one."""
    out = [f"recording {wave.source}/{r.key}: {r.prompt}" for r in wave.given]
    out += wave.said
    if not wave.given:
        before = f"; {wave.earlier} given an agent earlier in this sync" if wave.earlier else ""
        out.append(f"no recording of {wave.source} to read now{before}")
    return out


# -- what is read ------------------------------------------------------------------------------------------------------


def _staged(vault: Vault, source: str) -> dict[str, str]:
    """Each rid a record of the source still in staging is or is under, and that record's rid: a recording whose
    transcript waits there."""
    out: dict[str, str] = {}
    for entry in Staging(vault.root, vault.home).scan().entries:
        record = entry.staged.record
        if record.source != source:
            continue
        for rid in (record.rid, record.thread, record.container):
            if rid:
                out.setdefault(rid, record.rid)
    return out


def _meetings(vault: Vault, tz: ZoneInfo, at: datetime, starts: Sequence[datetime]) -> list[views.MeetingFacts]:
    window = (min(starts) - reconcile.RECORDED, max(starts) + reconcile.RECORDED)
    with index_build.ledger_tables(vault) as conn:
        return views.sync_read(conn, tz, at, window).meetings


def _meetings_line(found: list[views.MeetingFacts], info: folder.Info) -> str:
    if not found:
        return NONE_BY_TIME
    said = "; ".join(views.short_meeting(m.row, info.today) for m in found)
    if len(found) == 1:
        return f"{said}. Its open points are under it in the ledger file"
    return f"{said}. The one its content names takes the minutes and goes on the NOTES line"


def _records(lookup: recall.Lookup, rid: str) -> list[RawRecord]:
    """The recording and the records under it at their latest versions: itself, its notes, then its transcripts."""
    under = lookup.under(rid)
    found = [r for r in [lookup.record(rid)] if r is not None]
    return [*found, *(r for r in under if not segments(r)), *(r for r in under if segments(r))]


def _layout(vault: Vault, source: str) -> str:
    """The note's layout: the one the source's `sync.md` gives, when it has one (DR-0750), else Docket's."""
    try:
        sync_md = runner.skill_folder(runner.consuming_repo(vault.root), vault.root, source) / "sync.md"
    except DocketError:
        return LAYOUT
    return f"the one {sync_md} gives for a recording note, else {LAYOUT}" if sync_md.is_file() else LAYOUT


# -- the files ---------------------------------------------------------------------------------------------------------


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _wrapped(line: str) -> list[str]:
    """A line of at most 1,000 characters, its rest on lines of its own, indented, broken at a space where there is
    one: Claude Code's Read cuts a line at 2,000."""
    out: list[str] = []
    rest = line
    while len(rest) > LINE_CHARS:
        cut = rest.rfind(" ", LINE_CHARS // 2, LINE_CHARS)
        cut = cut if cut > 0 else LINE_CHARS
        out.append(rest[:cut])
        rest = "  " + rest[cut:].lstrip(" ")
    return [*out, rest]


def _section(record: RawRecord) -> tuple[str, list[str]]:
    """A record's head line and its lines: a segment a line by its start, or a paragraph a line by its number."""
    said = segments(record)
    rid = encode_rid(record.rid)
    if said:
        head = f"== {record.rid} | {record.kind} | a segment a line, its locator r:{rid}@<its mm:ss>"
        body = [
            f"@{format_time(s.start_ms // 1000)} {f'{s.speaker}: ' if s.speaker else ''}{_one_line(s.text)}"
            for s in said
        ]
    else:
        head = f"== {record.rid} | {record.kind} | a paragraph a line, its locator r:{rid}#<its number>"
        body = [f"#{n} {_one_line(p)}" for n, p in enumerate(paragraphs(record.text), start=1)]
    return head, [part for line in body for part in _wrapped(line)]


def parts(head: str, records: Sequence[RawRecord]) -> list[str]:
    """The recording's text in parts of at most 40,000 bytes of UTF-8, each opening with its head line and `part n of
    m`, split between lines; a record that runs on into the next part has its head line again there."""
    room = PART_BYTES - len(f"{head} | part 999 of 999\n".encode())
    chunks: list[list[str]] = [[]]
    used = 0
    for record in records:
        title, body = _section(record)
        if not body:
            continue
        for k, line in enumerate([title, *body]):
            size = len(line.encode("utf-8")) + 1
            if chunks[-1] and used + size > room:
                chunks.append([] if k == 0 else [f"{title} (continued)"])
                used = sum(len(x.encode("utf-8")) + 1 for x in chunks[-1])
            chunks[-1].append(line)
            used += size
    if not chunks[-1]:
        chunks[-1].append("nothing of it is in raw but the recording itself")
    total = len(chunks)
    return ["\n".join([f"{head} | part {n} of {total}", *chunk]) + "\n" for n, chunk in enumerate(chunks, start=1)]


def _write_parts(where: Path, k: str, head: str, records: Sequence[RawRecord]) -> list[Path]:
    files: list[Path] = []
    for n, text in enumerate(parts(head, records), start=1):
        file = where / f"{k}.{n}.txt"
        _write(file, text)
        files.append(file)
    return files


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
