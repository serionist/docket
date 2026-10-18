"""The Recording page: `/recording` is always a whole page, and its links are plain links (DR-0544).

The page reads the raw log, the hold list and the docs in this process, through recall's own reads, and writes
nothing: GET only, as Search's routes are. The summary search attaches comes from the index, so the page waits for an
update as a search does. A value the URL can't hold, or a `DocketError`, is the page's own error state; anything else is
logged as its type and frames and answered with the error page, as on the other pages (DR-0170).
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from starlette.responses import Response

from docket import index, recall
from docket.errors import DocketError
from docket.media import crypto, layout, reader
from docket.recall import DocView
from docket.service.speech import line as speech_line
from docket.service.state import Service
from docket.service.writer import IndexBuildingError
from docket.vault import forgotten, holds
from docket.vault.records import RID, is_native_id, owning_id, source_name_problem
from docket.vault.spans import RawLookup, is_transcript
from docket.vault.vault import Vault
from docket.web import audio, templating
from docket.web.recording_state import RecordingState, RecordingStateError
from docket.web.recording_view import Facts, NoAudio, RecordingView, build, is_recording
from docket.web.shell import shell


def _held(vault: Vault, rid: str) -> bool:
    source = rid.partition(":")[0]
    if source_name_problem(source) is not None:
        return False
    return owning_id(rid, source, holds.read(vault.root, source)) is not None


def gather(vault: Vault, state: RecordingState) -> Facts:
    """Everything the page shows, read now: the record, the recording it is or is under and its transcripts, the hold
    list when the raw log doesn't hold it, and the docs with lines on it."""
    rid = state.rid or ""
    tz = ZoneInfo(vault.config.owner.time_zone)
    with recall.raw_lookup(vault) as lookup:
        record = lookup.latest(rid)
        if record is None:
            return Facts(rid, tz, held=_held(vault, rid), forgotten=forgotten.load(vault.root, vault.home).whole(rid))
        # A transcript is read with the recording it is under (DR-0391); one under none is a recording of its own.
        above = lookup.recordings(record) if is_transcript(record) else []
        recording = (lookup.latest(above[0]) if above else None) or record
        transcripts = ((recording,) if recording.segments else ()) + tuple(lookup.transcripts(recording.rid))
        if not is_recording(record, transcripts):
            return Facts(rid, tz, record=record)
        audio = _audio(vault, recording.rid, lookup)
    ref = index.summary_for(vault, recording.rid)
    on = recall.read(vault, recording.rid, view="summary")
    attached = ref.id if ref is not None else None
    cited: list[str] = [line.doc for line in on.lines]
    docs = tuple(dict.fromkeys([attached, *cited] if attached else cited))
    legacy = tuple(note.id for note in on.legacy)
    chosen = state.doc or attached or (legacy[0] if legacy else None)
    summary: DocView | None = None
    problems: list[str] = []
    if state.doc is not None and state.doc not in (*docs, *legacy):
        problems.append(f"{state.doc} has no lines on this recording")
    if chosen is not None:
        try:
            summary = recall.read(vault, chosen, view="summary").doc
        except DocketError as e:
            problems = [str(e)]
    return Facts(
        rid,
        tz,
        record=record,
        recording=recording,
        transcripts=transcripts,
        attached=attached,
        docs=docs,
        legacy=legacy,
        summary=summary,
        problems=tuple(problems),
        audio=audio,
    )


# The media reader's state, as the page says it: `ok` is stored, and plays once the page has a player.
AUDIO: dict[reader.State, NoAudio] = {
    "ok": "stored",
    "forgotten": "forgotten",
    "not-fetched": "not-fetched",
    "staged": "staged",
    "held": "held",
    "withheld": "withheld",
    "recut": "recut",
    "cut-by-characters": "cut-by-characters",
    "not-downloaded": "not-downloaded",
    "no-key": "no-key",
}


def _with_speech(service: Service, facts: Facts) -> Facts:
    """The facts with the service's Whisper job for the recording, when it queued or ran one."""
    found = service.speech.job(facts.recording.rid) if facts.recording is not None else None
    return dataclasses.replace(facts, speech=f"Whisper: {speech_line(found)}") if found is not None else facts


def _audio_of(vault: Vault, rid: str, plain: audio.Plain, *, whole: bool) -> tuple[bytes | None, int, str] | str:
    """A recording's audio for the player: its bytes (none for HEAD), its size and its type; or why not. The reader's
    state comes first, every time, so kept bytes never outlive it."""
    state = reader.state_of(vault, rid)
    if state != "ok":
        return state
    # A file that reads ok is the one stored file, here and no LFS pointer.
    path = layout.stored_files(vault.root, rid)[0]
    ext = layout.ext_of(path)
    if ext not in layout.AUDIO:
        return "it isn't audio"
    media_type = layout.TYPES[ext]
    if not whole:
        kept = plain.size(path)
        return None, kept if kept is not None else crypto.plain_size(path), media_type
    data = plain.get(path, lambda: reader.read(vault, rid).data)
    if data is None:
        # Its state changed between the two reads.
        return "it no longer reads ok"
    return data, len(data), media_type


def _audio(vault: Vault, rid: str, lookup: RawLookup) -> NoAudio:
    return AUDIO[reader.state_of(vault, rid, raw=lookup)]


def _page(
    context: dict[str, Any], view: RecordingView | None, state: RecordingState, problem: str = "", *, status: int = 200
) -> Response:
    page = context | {"view": view, "state": state, "problem": problem}
    return templating.page("recording/page.html", page, status=status)


def router(service: Service) -> APIRouter:
    web = APIRouter()

    plain = audio.Plain()

    @web.api_route("/recording/audio", methods=["GET", "HEAD"], include_in_schema=False)
    async def recording_audio(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        """The recording's stored audio for the page's player (#159, DR-0576): only while the reader says ok, read
        again on every request; decrypted through it, kept a minute for the next range; served by byte range with no
        caching. The browser's session only: the token never fetches it. Nothing of it is logged."""
        if request.headers.get("authorization") is not None:
            return audio.token_refused()
        rid = request.query_params.get("rid", "")
        if RID.match(rid) is None or not is_native_id(rid.partition(":")[2]):
            return audio.refused("give a recording's rid")
        whole = request.method == "GET"
        try:
            found = await asyncio.to_thread(_audio_of, service.vault(), rid, plain, whole=whole)
        except DocketError as e:
            return audio.refused(str(e))
        if isinstance(found, str):
            return audio.refused(found)
        data, size, media_type = found
        if data is None:
            return audio.head(size, media_type)
        return audio.ranged(data, media_type, request.headers.get("range"))

    @web.get("/recording", include_in_schema=False)
    async def recording_page(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            context = shell(service, request, "Recording", current="recording")
            try:
                state = RecordingState.read(request.query_params)
            except RecordingStateError as e:
                return _page(context, None, RecordingState(), str(e), status=400)
            if state.rid is None:
                return _page(context, RecordingView(mode="start"), state)
            try:
                await service.updater.fresh()
                facts = await asyncio.to_thread(gather, service.vault(), state)
            except IndexBuildingError as e:
                return _page(context, None, state, str(e), status=503)
            except DocketError as e:
                return _page(context, None, state, str(e), status=400)
            view = build(_with_speech(service, facts), state)
            context["title"] = f"{view.title} - Recording"
            return _page(context, view, state, status=404 if view.mode in ("missing", "other") else 200)

        def failed() -> Response:
            context = shell(None, request, "Something failed", current="recording")
            return templating.page("error.html", context, status=500)

        return await templating.safely(request, "/recording", answer, failed)

    return web
