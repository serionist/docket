"""`docket transcribe <rid>` (DR-0569, DR-0570): a recording's stored audio made a Whisper transcript, staged at the
gate.

| Step | What |
|---|---|
| 0 | the machine's speech lock: one job at a time on this machine; a second waits, or is told whose pid holds it |
| 1 | under the vault's lock: the family checks media's screen runs (`Gate.family_plan`) and the reader's state `ok` |
| 2 | the stored file decrypted into this process's plain folder and decoded to 16 kHz float32 by the child |
| 3 | the speech worker, one process for the job, with its time limit |
| 4 | under the vault's lock again: step 1's checks; segments in the cut ranges dropped, `[cut]` put there; staged |

Both plain files go on every path. A job on the same stored file with the same engine as a version staged or in raw
stages nothing. Nothing here logs a segment, a name or the prompt.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout

from docket import recall, userconfig
from docket.errors import DocketError
from docket.media import crypto, cutter, layout, reader
from docket.media.ranges import Range
from docket.speech import client, prompt, setup
from docket.speech.setup import Speech
from docket.vault import cursors
from docket.vault.entities import load_entities
from docket.vault.gate import Gate, StageResult
from docket.vault.records import RID, TRANSCRIBED_VIA, RawRecord, Segment, is_native_id
from docket.vault.segments import normalize, render
from docket.vault.spans import RawLookup
from docket.vault.staging import Staging, rid_hash
from docket.vault.vault import Vault

SUFFIX = "/whisper"
LOCK = "speech.lock"
PID = "speech.pid"
CUT_MARKER = "[cut]"
# The job's time limit by the audio's length: CUDA ran large-v3 at 26 times the audio's speed and a CPU about as fast
# as the audio, so these leave room; never under ten minutes, for the model's load.
FACTOR = {"cuda": 0.5, "cpu": 3.0}
LEAST_LIMIT = 600.0
# How many recordings the service queues by itself after an index update, newest first, on CUDA only (DR-0571).
AUTO = 20
# Why a recording's audio can't be transcribed, by the reader's state (DR-0551).
WHY: dict[reader.State, str] = {
    "not-fetched": "no audio is stored for it",
    "staged": "its audio waits at the gate; it is stored at the next promotion",
    "held": "a hold covers it",
    "withheld": "nothing of its family may be kept now",
    "recut": "its stored audio was cut before its family's latest cuts; the next run puts it again, cut",
    "cut-by-characters": "a record of it was cut by characters, so its audio can't be cut to match",
    "not-downloaded": "its stored audio isn't downloaded to this machine; git lfs pull fetches it",
    "no-key": "this machine has no key to open its stored audio",
}


class SpeechBusyError(DocketError):
    """Another transcription holds the machine's speech lock."""


@dataclass(frozen=True)
class Family:
    """What a job reads of a recording at step 1 and again at step 4."""

    recording: RawRecord
    key: str
    ranges: tuple[Range, ...]
    # The family's records other than machine transcripts, at their latest versions, staged ones first.
    records: list[RawRecord] = field(default_factory=list[RawRecord])

    @property
    def people(self) -> list[str]:
        """The recording's recipients and its transcripts' speakers, sorted: the Whisper record's `to` (DR-0570)."""
        speakers = {s.speaker for r in self.records for s in r.segments or [] if s.speaker}
        return sorted({*self.recording.to, *speakers})

    def language(self, languages: Sequence[str]) -> str | None:
        """The recorder's transcript's language, else the vault's only one, else None: Whisper detects it."""
        found = next((r.lang for r in self.records if r.segments and r.lang), None)
        return found or (languages[0] if len(languages) == 1 else None)


@dataclass(frozen=True)
class Outcome:
    rid: str
    staged: bool
    line: str


@contextmanager
def machine_lock(*, wait: bool) -> Generator[None]:
    """The machine's speech lock, in the state folder: a job holds it from its checks to its staging."""
    folder = userconfig.state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(folder / LOCK), timeout=-1 if wait else 0)
    try:
        lock.acquire()
    except Timeout:
        try:
            pid = (folder / PID).read_text(encoding="utf-8").strip()
        except OSError:
            pid = "unknown"
        raise SpeechBusyError(
            f"a transcription is running on this machine (pid {pid}); let it end, then run docket transcribe again"
        ) from None
    try:
        (folder / PID).write_text(str(os.getpid()), encoding="utf-8")
        yield
    finally:
        (folder / PID).unlink(missing_ok=True)
        lock.release()


def checked(rid: str) -> str:
    """A recording's rid as a job takes it, or why not: a rid, and not a Whisper transcript's."""
    if RID.match(rid) is None or not is_native_id(rid.partition(":")[2]):
        raise DocketError(f"{rid!r} isn't a rid: <source>:<id>")
    if rid.endswith(SUFFIX):
        raise DocketError(f"{rid} is a Whisper transcript; give its recording's rid")
    return rid


def whisper_rid(rid: str) -> str:
    return f"{rid}{SUFFIX}"


def due(vault: Vault, limit: int = AUTO) -> list[str]:
    """The newest recordings, at most `limit`, whose stored audio reads ok and that have no Whisper transcript staged
    or in raw, from the sources this machine ingests: what the service queues by itself after an index update on CUDA
    (DR-0571). Raw is read at its places, and a recording without a stored file costs no read."""
    ingested = cursors.read(vault.root, vault.machine)
    staged = {e.staged.record.rid for e in Staging(vault.root, vault.home).scan().entries}
    found: list[tuple[datetime, str]] = []
    with recall.raw_lookup(vault) as lookup:
        for source in sorted(s for s in ingested if s in vault.config.sources):
            folder = vault.root / layout.STORED / source
            stored: set[str] = set()
            if folder.is_dir():
                stored = {p.name.split(".", 1)[0] for p in folder.glob(f"*/*/*{layout.SUFFIX}")}
            taken = 0
            for rid in lookup.of_kind(source, "recording"):
                if taken >= limit:
                    break
                made = whisper_rid(rid)
                if rid_hash(rid) not in stored or made in staged or lookup.has(made):
                    continue
                latest = lookup.latest(rid)
                if latest is None or reader.state_of(vault, rid, raw=lookup) != "ok":
                    continue
                found.append((latest.ts, rid))
                taken += 1
    return [rid for _, rid in sorted(found, reverse=True)[:limit]]


def transcribe(
    vault: Vault,
    rid: str,
    *,
    speech: Speech | None = None,
    wait: bool = False,
    say: Callable[[str], None] = print,
    worker: Sequence[str] | None = None,
    decoder: Sequence[str] | None = None,
) -> Outcome:
    """One recording transcribed and staged, or why nothing was staged. `worker` and `decoder` are a test's commands."""
    checked(rid)
    chosen = speech if speech is not None else setup.chosen()
    if isinstance(chosen, str):
        raise DocketError(chosen)
    source = layout.source_of(rid)
    if source not in cursors.read(vault.root, vault.machine):
        # The withheld marks the checks read are this clone's alone, as for media_put (DR-0547).
        raise DocketError(f"this machine hasn't ingested {source}; transcribe its recordings where it runs")
    engine = chosen.model.engine
    with machine_lock(wait=wait):
        first = _family(vault, rid)
        if _made(vault, rid, first.key, engine):
            return Outcome(rid, False, f"{rid}'s stored audio was transcribed by {engine} already; nothing staged")
        found = _run(vault, rid, first, chosen, say, worker=worker, decoder=decoder)
        with vault.lock:
            again = _family(vault, rid)
            if again.key != first.key:
                return Outcome(rid, False, f"{rid}'s stored audio changed while it was transcribed; nothing staged")
            if _made(vault, rid, again.key, engine):
                return Outcome(rid, False, f"{rid}'s stored audio was transcribed by {engine} already; nothing staged")
            record = _record(again, found, engine, vault.config.owner.languages)
            if record is None:
                return Outcome(rid, False, f"Whisper heard no speech in {rid}'s stored audio; nothing staged")
            return _staged(record, Gate(vault).stage([record]))


def _family(vault: Vault, rid: str) -> Family:
    """Step 1's checks, and step 4's: the family's, media's screen reads them (DR-0569), and the reader's state."""
    gate = Gate(vault)
    planned = gate.family_plan(rid)
    if isinstance(planned, str):
        raise DocketError(f"nothing transcribed: {planned}")
    state = reader.state_of(vault, rid, gate.keys)
    if state != "ok":
        raise DocketError(f"nothing transcribed: {WHY[state]}")
    stored = layout.stored_files(vault.root, rid)
    lookup = RawLookup(vault.root)
    staged = [e.staged.record for e in Staging(vault.root, vault.home).scan().entries]
    family: dict[str, RawRecord] = {}
    for record in staged:
        if layout.in_family(rid, record.rid):
            family[record.rid] = record
    for other in [*lookup.nested(rid), *(t.rid for t in lookup.transcripts(rid))]:
        latest = lookup.latest(other)
        if latest is not None and other not in family:
            family[other] = latest
    recording = family.pop(rid)
    kept = [r for r in family.values() if not r.via.startswith(TRANSCRIBED_VIA)]
    return Family(recording, crypto.key_id(stored[0]), planned.ranges, kept)


def _made(vault: Vault, rid: str, key: str, engine: str) -> bool:
    """Whether a version of the recording's Whisper transcript, staged or in raw, came from this file and engine."""
    made = whisper_rid(rid)
    staged = [e.staged.record for e in Staging(vault.root, vault.home).scan().entries if e.staged.record.rid == made]
    via = f"{TRANSCRIBED_VIA} {key}"
    return any(r.via == via and r.engine == engine for r in [*staged, *RawLookup(vault.root).versions(made)])


def _run(
    vault: Vault,
    rid: str,
    family: Family,
    speech: Speech,
    say: Callable[[str], None],
    *,
    worker: Sequence[str] | None,
    decoder: Sequence[str] | None,
) -> client.Result:
    """Steps 2 and 3: the stored file decrypted and decoded, the worker run; both plain files gone on every path."""
    plain: Path | None = None
    pcm: Path | None = None
    try:
        media = reader.read(vault, rid)
        if media.data is None or media.ext is None:
            # Its state changed since step 1's check, which ran under the vault's lock.
            raise DocketError(f"nothing transcribed: {WHY.get(media.state, 'its audio is unreadable')}")
        plain = cutter.plain_file(vault.home, f".{media.ext}")
        plain.write_bytes(media.data)
        decoded = cutter.decode_pcm(vault.home, plain, media.ext, command=decoder)
        pcm = decoded.path
        cutter.done_with(plain)
        plain = None
        # The settings in force now, as the gate reads them, so a never-store value added since the vault was opened
        # keeps its name out of the prompt.
        config = Gate(vault).settings()
        language = family.language(config.owner.languages)
        entities = load_entities(vault.root).entities
        vocabulary = prompt.vocabulary(language, [family.recording, *family.records], entities, config)
        limit = max(LEAST_LIMIT, decoded.duration_ms / 1000 * FACTOR[speech.device])
        say(f"transcribing {_clock(decoded.duration_ms)} of audio with whisper {speech.model.name} on {speech.device}")

        def progress(ms: int) -> None:
            say(f"  {min(100, ms * 100 // max(1, decoded.duration_ms))}%")

        return client.run_job(
            worker or client.command(speech.python),
            setup.spec(speech, pcm, language=language, prompt=vocabulary),
            time_limit=limit,
            progress=progress,
        )
    finally:
        cutter.done_with(plain, pcm)


def _record(family: Family, found: client.Result, engine: str, languages: Sequence[str]) -> RawRecord | None:
    """The Whisper record (DR-0570): its segments outside the cut ranges, `[cut]` over each range; None when no
    segment is left."""
    recording = family.recording
    kept = [
        Segment(start_ms=s.start_ms, end_ms=max(s.start_ms, s.end_ms), text=s.text.strip())
        for s in found.segments
        if _said(s.text) and not any(_overlaps(s.start_ms, s.end_ms, r) for r in family.ranges)
    ]
    if not kept:
        return None
    marks = [Segment(start_ms=start, end_ms=end, text=CUT_MARKER) for start, end in family.ranges]
    segments = normalize([*kept, *marks])
    return RawRecord(
        rid=whisper_rid(recording.rid),
        source=recording.source,
        kind="transcript",
        container=recording.rid,
        thread=recording.rid,
        ts=recording.ts,
        author=recording.author,
        to=family.people,
        segments=segments,
        text=render(segments),
        fetched=datetime.now(UTC),
        via=f"{TRANSCRIBED_VIA} {family.key}",
        fidelity="derived",
        engine=engine,
        lang=found.language or family.language(languages),
    )


def _said(text: str) -> bool:
    # A segment of punctuation alone is what Whisper puts in a silence, not a word.
    return any(c.isalnum() for c in text)


def _clock(ms: int) -> str:
    hours, rest = divmod(ms // 1000, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _overlaps(start: int, end: int, cut: Range) -> bool:
    # As a cut takes a segment (cuts.py): a segment with no length still sits at its moment.
    return start < cut[1] and max(end, start + 1) > cut[0]


def _staged(record: RawRecord, result: StageResult) -> Outcome:
    n = sum(1 for s in record.segments or [] if s.text != CUT_MARKER)
    made = f"{n} segment(s), {record.lang or 'language unknown'}"
    if result.ready:
        return Outcome(record.rid, True, f"staged {record.rid}: {made}; it goes in at the next promotion")
    if result.pending:
        return Outcome(record.rid, True, f"staged {record.rid}: {made}; it waits for you: docket staging list")
    if result.on_hold:
        return Outcome(record.rid, False, f"{record.rid}: a hold covers it, so nothing was staged")
    if result.skipped:
        return Outcome(record.rid, False, f"{record.rid}: the same transcript is staged or in raw already")
    why = ", ".join(sorted(result.dropped)) or "the gate's rules"
    return Outcome(record.rid, False, f"{record.rid}: the gate dropped it ({why}); nothing staged")
