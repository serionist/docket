"""The four calls the MCP tools and the REST API share, so one place decides how each runs: what goes through the
writer, what waits for the index, and the compact text that goes with each result.

Text from docs and records comes back as data; nothing here acts on what it says.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Literal
from zoneinfo import ZoneInfo

from docket import index, recall, render
from docket.index import SearchResult
from docket.recall import ReadResult, VerifyResult
from docket.service.state import Service
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


async def doc_put(service: Service, text: str) -> tuple[PutResult, str]:
    """Through the writer, one at a time; the index is brought up to date after, without waiting. A refused doc
    raises `DocRefusedError` with every problem."""
    result = await service.writer.run(lambda: recall.doc_put(service.vault(), text))
    service.updater.poke()
    return result, render.text(render.put(result))
