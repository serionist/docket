"""The calls the MCP tools and the REST API share, so one place decides how each runs: what goes through the writer,
what waits for the index, and the compact text that goes with each result.

Text from docs and records comes back as data; nothing here acts on what it says.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from docket import briefs, decisions, index, recall, render
from docket.briefs import BriefsReport
from docket.decisions import DecisionsReport
from docket.errors import DocketError
from docket.index import SearchResult
from docket.index.build import ledger_tables
from docket.ledger import batch, store, views
from docket.ledger import day as ledger_day
from docket.ledger.batch import BatchResult
from docket.ledger.ops import By
from docket.media import put as media
from docket.media.keystore import KeyStore
from docket.recall import ReadResult, VerifyResult
from docket.service.speech import SpeechJob
from docket.service.speech import line as speech_line
from docket.service.state import Service
from docket.speech import job
from docket.sync import folder as sync_folder
from docket.vault.docstore import PutResult

View = Literal["summary", "raw", "both"]
# The most characters a read of several locators or a suggest for several meetings answers with, its text and its
# structured content's JSON counted together: under the 50,000 past which Claude Code saves a tool's result to a file
# and hands Claude a note instead (capture.SAVED_TO_FILE, DR-0725).
ROOM = 40_000


def size(model: BaseModel, lines: Iterable[str]) -> int:
    """A tool's result as the room counts it: its text, the lines as the tool gives them, and its model's JSON."""
    return len(render.text(lines)) + len(model.model_dump_json())


async def search(
    service: Service,
    query: str,
    *,
    people: Sequence[str] = (),
    entities: Sequence[str] = (),
    sources: Sequence[str] = (),
    since: str | None = None,
    until: str | None = None,
    k: int = 20,
) -> tuple[SearchResult, str]:
    await service.updater.fresh()

    def run() -> tuple[SearchResult, str]:
        vault = service.vault()
        result = index.search(
            vault,
            query,
            people=people,
            entities=entities,
            sources=sources,
            since=index.parse_when(since, "since"),
            until=index.parse_when(until, "until"),
            k=k,
            models=service.models.for_search(),
        )
        return result, render.text(render.search(result, ZoneInfo(vault.config.owner.time_zone)))

    return await asyncio.to_thread(run)


async def read(
    service: Service,
    locator: str,
    view: View = "summary",
    around: int = 0,
    rev: int | None = None,
    *,
    more: Sequence[str] = (),
) -> tuple[ReadResult, str]:
    """A read, or with `more` a read of several in one call, as many as fit the room, the rest in `later` (DR-0725)."""
    if more and rev is not None:
        raise DocketError("rev is one doc's earlier revision: read it alone, without more")

    def run() -> tuple[ReadResult, str]:
        vault = service.vault()
        if not more:
            result = recall.read(vault, locator, view=view, around=around, rev=rev)
            return result, render.text(render.read(result))
        result = recall.read_many(
            vault, locator, more, view=view, around=around, fits=lambda r: size(r, render.reads(r)) <= ROOM
        )
        return result, render.text(render.reads(result))

    return await asyncio.to_thread(run)


async def verify(
    service: Service, doc: str, lines: Sequence[int] | None = None, window: int = 60
) -> tuple[VerifyResult, str]:
    def run() -> tuple[VerifyResult, str]:
        result = recall.verify(service.vault(), doc, lines=list(lines) if lines else None, window=window)
        return result, render.text(render.verify(result))

    return await asyncio.to_thread(run)


async def decision_states(
    service: Service, entities: list[str] | None = None, status: str | None = None
) -> tuple[DecisionsReport, str]:
    """The decisions and where each stands, read-only, after the index is brought up to date (DR-0565). The tool's
    schema holds `status` to the three a decision has."""

    def run() -> tuple[DecisionsReport, str]:
        found = decisions.report(service.vault(), entities, status, update=False)
        return found, render.text(render.decisions(found))

    await service.updater.fresh()
    return await asyncio.to_thread(run)


async def brief_states(
    service: Service, entities: list[str] | None = None, stale_only: bool = True
) -> tuple[BriefsReport, str]:
    """The briefs' states, read-only, after the index is brought up to date (DR-0556)."""

    def run() -> tuple[BriefsReport, str]:
        found = briefs.report(service.vault(), entities, stale_only=stale_only, update=False)
        return found, render.text(render.briefs(found))

    await service.updater.fresh()
    return await asyncio.to_thread(run)


async def doc_put(service: Service, text: str) -> tuple[PutResult, str]:
    """Through the writer, one at a time; the index is brought up to date after, without waiting. A refused doc
    raises `DocRefusedError` with every problem."""
    result = await service.writer.run(lambda: recall.doc_put(service.vault(), text))
    service.updater.poke()
    return result, render.text(render.put(result))


async def transcribe(service: Service, rid: str) -> tuple[SpeechJob, str]:
    """Queued in the service and answered at once with the job's place (DR-0571); its outcome shows in the service's
    status and on the recording's page. A rid the job would refuse is refused now."""
    job.checked(rid)
    found = await asyncio.to_thread(service.speech.add, rid)
    return found, f"{rid}: {speech_line(found)}"


async def ledger(service: Service, ask: views.Ask) -> tuple[views.LedgerRead, str]:
    """One read of the ledger's tables, brought up to date first, beside the writer: a read never waits in a write's
    queue, and the index's lock keeps an update from moving the tables while it asks them. With no index, or one it
    can't have within a second, the op files are folded in memory (DR-0619, DR-0638). A suggest for several meetings
    answers as many as fit the room, the rest in `later` (DR-0725)."""

    def run() -> tuple[views.LedgerRead, str]:
        vault = service.vault()
        tz = ZoneInfo(vault.config.owner.time_zone)
        with ledger_tables(vault) as conn:
            found = views.read(conn, ask, tz, views.now(), fits=lambda r: size(r, views.lines(r, tz)) <= ROOM)
        return found, render.text(views.lines(found, tz))

    return await asyncio.to_thread(run)


async def day(service: Service, room: int) -> ledger_day.DayAnswer:
    """The owner's day in at most `room` lines, read beside the writer as any ledger read is, the sources with a run
    open never due (DR-0670 to DR-0672)."""
    running = {run.source for run in service.runs.snapshot()}

    def run() -> ledger_day.DayAnswer:
        vault = service.vault()
        found = ledger_day.read(vault, views.now(), running)
        return ledger_day.answer(found, room, ledger_day.repo_of(vault))

    return await asyncio.to_thread(run)


async def ledger_batch(
    service: Service, ops: list[dict[str, Any]], by: By = "claude", dry_run: bool = False, file: str | None = None
) -> tuple[BatchResult, str]:
    """One batch through the writer, one at a time with every other write, via tool (DR-0637, DR-0639); the index is
    brought up to date after a write, without waiting. A refused change refuses the batch, and a dry run lists every
    refusal it can. With `file`, the ops are a batch file's in a sync folder of the service's vault, and `ops` is []
    (DR-0724)."""
    if file is not None:
        if ops:
            raise DocketError("give ops or file, not both: with file, ops is []")
        root = service.vault().root
        ops = await asyncio.to_thread(lambda: batch.parse(sync_folder.batch_file(file, root)))
    written = await service.writer.run(lambda: store.write(service.vault(), ops, by=by, via="tool", dry_run=dry_run))
    if written.commit is not None:
        service.updater.poke()
    found = batch.result(ops, written, dry_run=dry_run)
    return found, render.text(batch.lines(found))


class MediaPut(BaseModel):
    """What `media_put` staged: the rid, the type its first bytes say, and its size in bytes."""

    rid: str
    ext: str
    bytes: int


async def media_put(service: Service, rid: str, *, path: str | None, url: str | None) -> tuple[MediaPut, str]:
    """The checks and the staging on the writer, one at a time with every other write; the download beside it, so a
    slow host never holds the queue (DR-0547)."""
    keys = KeyStore(service.vault().home)
    needed = await service.writer.run(lambda: media.check(service.vault(), rid, keys))
    fetched = await asyncio.to_thread(lambda: media.fetch(service.vault(), rid, path=path, url=url, needed=needed))
    try:
        stored = await service.writer.run(lambda: media.finish(service.vault(), fetched, keys))
    finally:
        fetched.tmp.unlink(missing_ok=True)
    return MediaPut(rid=stored.rid, ext=stored.ext, bytes=stored.bytes), stored.line
