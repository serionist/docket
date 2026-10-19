"""The calls the MCP tools and the REST API share, so one place decides how each runs: what goes through the writer,
what waits for the index, and the compact text that goes with each result.

Text from docs and records comes back as data; nothing here acts on what it says.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from docket import briefs, decisions, index, recall, render
from docket.briefs import BriefsReport
from docket.decisions import DecisionsReport
from docket.index import SearchResult
from docket.ledger import batch, store
from docket.ledger.batch import BatchResult
from docket.ledger.ops import By
from docket.media import put as media
from docket.media.keystore import KeyStore
from docket.recall import ReadResult, VerifyResult
from docket.service.speech import SpeechJob
from docket.service.speech import line as speech_line
from docket.service.state import Service
from docket.speech import job
from docket.vault.docstore import PutResult

View = Literal["summary", "raw", "both"]


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
    service: Service, locator: str, view: View = "summary", around: int = 0, rev: int | None = None
) -> tuple[ReadResult, str]:
    def run() -> tuple[ReadResult, str]:
        result = recall.read(service.vault(), locator, view=view, around=around, rev=rev)
        return result, render.text(render.read(result))

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


async def ledger_batch(
    service: Service, ops: list[dict[str, Any]], by: By = "claude", dry_run: bool = False
) -> tuple[BatchResult, str]:
    """One batch through the writer, one at a time with every other write, via tool (DR-0637, DR-0639); the index is
    brought up to date after a write, without waiting. A refused change refuses the batch, and a dry run lists every
    refusal it can."""
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
