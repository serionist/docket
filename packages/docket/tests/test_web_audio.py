"""The Recording page's player (#159): the stored audio served by exact byte ranges with no caching, only while the
reader says ok; every other state refused with its reason; the player's start at the line `at` marks; and the
recorder's transcript beside Whisper's, aligned by time, a cut stretch cut in both. The audio is a tone made here and
the records are invented."""

from __future__ import annotations

import io
import logging
import os
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import make_record
from docket_webkit import TOKEN, Element, a11y_problems, html_problems, parse, signed_in
from recording_kit import TOUR, TOUR_POLISHED, WHISPER, recording_template, with_audio
from starlette.datastructures import QueryParams
from starlette.testclient import TestClient

from docket.errors import DocketError
from docket.media import crypto, layout, reader
from docket.vault import holds
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.vault import Vault
from docket.web import audio
from docket.web.recording_state import RecordingState
from docket.web.recording_view import NO_AUDIO, Facts, build
from docket.web.templating import PAGE_CSP

AUDIO = f"/recording/audio?rid={TOUR.replace(':', '%3A')}"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return recording_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


@pytest.fixture
def stored(client: TestClient, tmp_path: Path) -> Vault:
    """The tour's audio stored and Whisper's transcript in raw, in the vault the client's service reads."""
    vault = Vault.open(tmp_path / "vault")
    with_audio(vault)
    return vault


def tree(client: TestClient, path: str) -> Element:
    answer = client.get(path)
    assert answer.status_code == 200, answer.text
    assert answer.headers["Content-Security-Policy"] == PAGE_CSP and "media-src 'self';" in PAGE_CSP
    assert not html_problems(answer.text), html_problems(answer.text)
    assert not a11y_problems(answer.text), a11y_problems(answer.text)
    return parse(answer.text)


@pytest.mark.parametrize(
    ("header", "size", "wanted"),
    [
        (None, 10, None),
        ("bytes=0-3", 10, (0, 3)),
        ("bytes=4-", 10, (4, 9)),
        ("bytes=-3", 10, (7, 9)),
        ("bytes=-30", 10, (0, 9)),
        ("bytes=5-500", 10, (5, 9)),
        ("bytes=9-9", 10, (9, 9)),
        ("bytes=10-", 10, "unsatisfiable"),
        ("bytes=-0", 10, "unsatisfiable"),
        ("bytes=-5", 0, "unsatisfiable"),
        ("bytes=0-1,4-5", 10, None),
        ("bytes=5-2", 10, None),
        ("bytes=-", 10, None),
        ("items=0-3", 10, None),
        ("bytes=" + "9" * 5000 + "-", 10, None),
        ("bytes=0-" + "9" * 21, 10, None),
    ],
)
def test_a_range_is_read_as_the_rfc_reads_it(header: str | None, size: int, wanted: object) -> None:
    assert audio.span(header, size) == wanted


def test_the_audio_is_served_by_exact_byte_ranges_and_never_cached(client: TestClient, stored: Vault) -> None:
    data = reader.read(stored, TOUR).data
    assert data is not None and len(data) > 200
    whole = client.get(AUDIO)
    assert whole.status_code == 200 and whole.content == data
    assert whole.headers["Content-Type"] == "audio/webm" and whole.headers["Accept-Ranges"] == "bytes"
    assert whole.headers["Cache-Control"] == "no-store" and whole.headers["X-Content-Type-Options"] == "nosniff"
    size = len(data)
    for header, start, end in (
        ("bytes=0-99", 0, 99),
        ("bytes=100-", 100, size - 1),
        ("bytes=-50", size - 50, size - 1),
    ):
        part = client.get(AUDIO, headers={"Range": header})
        assert part.status_code == 206, header
        assert part.content == data[start : end + 1] and part.headers["Content-Length"] == str(end - start + 1)
        assert part.headers["Content-Range"] == f"bytes {start}-{end}/{size}"
        assert part.headers["Cache-Control"] == "no-store"
    huge = client.get(AUDIO, headers={"Range": "bytes=" + "9" * 5000 + "-"})
    assert huge.status_code == 200 and huge.content == data, (
        "a field past Python's digits is a Range that doesn't parse"
    )
    past = client.get(AUDIO, headers={"Range": f"bytes={size}-"})
    assert past.status_code == 416 and past.headers["Content-Range"] == f"bytes */{size}" and past.content == b""


@pytest.mark.parametrize(
    "state",
    [
        "forgotten",
        "not-fetched",
        "staged",
        "held",
        "withheld",
        "recut",
        "cut-by-characters",
        "not-downloaded",
        "no-key",
    ],
)
def test_every_state_but_ok_plays_nothing_and_says_why(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    monkeypatch.setattr(reader, "state_of", lambda *a, **k: state)
    for refused in (client.get(AUDIO), client.head(AUDIO)):
        assert refused.status_code == 404 and refused.headers["Cache-Control"] == "no-store"
    assert client.get(AUDIO).text == f"no audio: {state}"


def test_a_rid_a_file_that_isn_t_audio_and_a_reader_s_refusal_are_named(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/recording/audio?rid=not%20a%20rid").text == "no audio: give a recording's rid"
    monkeypatch.setattr(reader, "state_of", lambda *a, **k: "ok")
    monkeypatch.setattr(layout, "stored_files", lambda *a, **k: [Path("media/plaud/2026/10/x.pdf.age")])
    assert client.get(AUDIO).text == "no audio: it isn't audio"

    def two(*_: object, **__: object) -> reader.State:
        raise DocketError(f"{TOUR} has 2 stored media files; one of them is a leftover to remove by hand")

    monkeypatch.setattr(reader, "state_of", two)
    assert (
        client.get(AUDIO).text
        == f"no audio: {TOUR} has 2 stored media files; one of them is a leftover to remove by hand"
    )


def test_a_file_that_stops_reading_ok_between_the_reader_s_two_looks_plays_nothing(
    client: TestClient, stored: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reader, "read", lambda *a, **k: reader.Media("held"))
    assert client.get(AUDIO).text == "no audio: it no longer reads ok"


def test_the_token_never_fetches_the_audio(client: TestClient, stored: Vault) -> None:
    bearer = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get(AUDIO, headers=bearer).status_code == 403, "with the session too"
    client.cookies.clear()
    refused = client.get(AUDIO, headers=bearer)
    assert refused.status_code == 403 and refused.text == "the audio plays in the browser, with its session"


def test_one_file_is_decrypted_once_for_its_ranges_and_its_state_is_read_every_time(
    client: TestClient, stored: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = crypto.decrypt
    opened: list[int] = []

    def counted(data: bytes, identity: str) -> bytes:
        opened.append(len(data))
        return real(data, identity)

    monkeypatch.setattr(crypto, "decrypt", counted)
    first = client.get(AUDIO, headers={"Range": "bytes=0-99"})
    second = client.get(AUDIO, headers={"Range": "bytes=100-199"})
    assert (first.status_code, second.status_code) == (206, 206)
    size = int(first.headers["Content-Range"].rpartition("/")[2])
    # The stored file is larger than what it decrypts to; a key file is a few hundred bytes.
    assert len([n for n in opened if n > size]) == 1, "the file decrypted once for both ranges"
    head = client.head(AUDIO)
    assert head.status_code == 200 and head.headers["Content-Length"] == str(size) and head.content == b""
    holds.add(stored, "plaud", ["rec-tour"])
    assert client.get(AUDIO, headers={"Range": "bytes=200-299"}).text == "no audio: held", "kept bytes never outlive it"


def test_kept_bytes_go_with_their_file_s_change_and_after_their_time(tmp_path: Path) -> None:
    path = tmp_path / "a.webm.age"
    path.write_bytes(b"one")
    reads: list[str] = []

    def read() -> bytes:
        reads.append("read")
        return path.read_bytes()

    kept = audio.Plain(seconds=0.05)
    assert kept.get(path, read) == b"one" and kept.get(path, read) == b"one" and reads == ["read"]
    assert kept.size(path) == 3
    path.write_bytes(b"two!")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
    assert kept.size(path) is None and kept.get(path, read) == b"two!" and len(reads) == 2
    deadline = time.monotonic() + 5
    while kept.size(path) is not None:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert kept.get(path, lambda: None) is None and kept.size(path) is None


@pytest.mark.parametrize("size", [0, 1, 65_535, 65_536, 65_537, 200_000])
def test_a_file_s_size_is_read_from_its_age_framing(tmp_path: Path, size: int) -> None:
    secret, recipient = crypto.new_identity()
    path = tmp_path / "f.age"
    data = os.urandom(size)
    with path.open("wb") as out:
        crypto.encrypt(io.BytesIO(data), out, recipient)
    assert crypto.plain_size(path) == size and crypto.decrypt(path.read_bytes(), secret) == data


def test_the_audio_needs_the_session_and_leaves_nothing_in_the_log(
    client: TestClient, stored: Vault, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    assert client.get(AUDIO, headers={"Range": "bytes=0-9"}).status_code == 206
    said = [r.getMessage() for r in caplog.records if r.name.startswith("docket")]
    # The request's line names the route's template and its status, never the rid, the range or a byte.
    assert [m for m in said if "audio" in m] == [m for m in said if re.fullmatch(r"GET /recording/audio 206 \d+ ms", m)]
    assert not [m for m in said if "tour" in m or "rid" in m or "bytes" in m], said
    client.cookies.clear()
    assert client.get(AUDIO).status_code != 200


def test_withheld_audio_doesn_t_play_and_the_page_says_why(client: TestClient, stored: Vault) -> None:
    path = stored.root / CONFIG_FILE
    config = VaultConfig.load(path)
    config.never_store.keywords.append("yard tour")
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    assert client.get(AUDIO).text == "no audio: withheld"
    shown = tree(client, f"/recording?rid={TOUR}").find("section", class_="audio")
    assert shown.attrs["data-why"] == "withheld" and NO_AUDIO["withheld"] in shown.text()
    assert shown.find_all("audio") == []


def test_the_player_starts_at_the_line_at_marks_and_every_spoken_line_plays(client: TestClient, stored: Vault) -> None:
    page = tree(client, f"/recording?rid={TOUR}&at=00:20")
    player = page.find("audio", id="player")
    assert player is not None and player.attrs["src"] == f"{AUDIO}#t=20.000" and player.attrs["preload"] == "none"
    assert NO_AUDIO["stored"] in page.find("section", class_="audio").text()
    lines = page.find_all("li", class_="segment")
    spoken = [li for li in lines if "cut" not in li.attrs["class"].split()]
    assert spoken and all(li.attrs.get("data-start") for li in spoken)
    assert all("data-start" not in li.attrs for li in lines if "cut" in li.attrs["class"].split())
    plain = tree(client, f"/recording?rid={TOUR}").find("audio", id="player")
    assert plain is not None and plain.attrs["src"] == AUDIO


def test_the_two_transcripts_sit_side_by_side_by_time_and_a_cut_is_cut_in_both(
    client: TestClient, stored: Vault
) -> None:
    pair = tree(client, f"/recording?rid={TOUR}").find("table", class_="aligned")
    assert pair is not None
    heads = [th.text() for th in pair.find_all("th")]
    assert heads[0].startswith("The recorder's") and TOUR_POLISHED in heads[0]
    assert heads[1].startswith("Whisper's") and WHISPER in heads[1]
    rows = [
        [td.find_all("li", class_="segment") for td in tr.find_all("td")] for tr in pair.find("tbody").find_all("tr")
    ]
    shape = [(len(left), len(right)) for left, right in rows]
    # The recorder's first line holds two of Whisper's; the cut stretch is one row, cut in both.
    assert shape == [(1, 2), (1, 1), (1, 1), (1, 1)]
    cut = rows[2]
    assert all("cut" in li.attrs["class"].split() for side in cut for li in side)
    assert "Thanks for coming." in rows[0][1][1].text()


def test_a_whisper_transcript_waiting_at_the_gate_isn_t_shown(client: TestClient, tmp_path: Path) -> None:
    vault = Vault.open(tmp_path / "vault")
    staged = make_record(
        rid=f"{TOUR}/whisper", source="plaud", kind="transcript", container=TOUR, thread=TOUR,
        segments=[{"start_ms": 0, "end_ms": 9000, "text": "Welcome."}], text="Welcome.",
        via="docket transcribe 0f", engine=WHISPER, fidelity="derived",
    )  # fmt: skip
    Gate(vault).stage([staged])
    page = tree(client, f"/recording?rid={TOUR}")
    assert page.find_all("table", class_="aligned") == [] and page.find_all("audio") == []
    assert "No audio stored yet" in page.find("section", class_="audio").text()


# -- the pair, from the facts alone ---------------------------------------------------------------------------------

AT = datetime(2026, 10, 14, tzinfo=UTC)
CALL = "plaud:rec-x"


def said(rid: str, segs: list[tuple[int, int, str]], **fields: Any) -> RawRecord:
    lines = [{"start_ms": a, "end_ms": b, "text": t, "speaker": "Ada Example"} for a, b, t in segs]
    return make_record(
        rid=rid, source="plaud", kind="transcript", container=CALL, thread=CALL, segments=lines, ts=AT, **fields
    )


RECORDING = make_record(rid=CALL, source="plaud", kind="recording", container=CALL, thread=CALL, text="", ts=AT)
LEFT = said(f"{CALL}/t", [(0, 9000, "one"), (10_000, 19_000, "two")])
RIGHT = said(
    f"{CALL}/whisper",
    [(500, 8000, "one"), (9200, 9800, "between"), (30_000, 31_000, "late")],
    via="docket transcribe 0f",
)


def view_of(rid: str, transcripts: tuple[RawRecord, ...], query: str = "") -> Any:
    facts = Facts(rid, ZoneInfo("UTC"), record=RECORDING, recording=RECORDING, transcripts=transcripts, audio="stored")
    return build(facts, RecordingState.read(QueryParams(f"rid={rid}{query}")))


def test_whisper_s_lines_in_a_gap_get_their_own_rows_and_the_rid_may_name_either_transcript() -> None:
    for rid in (CALL, f"{CALL}/whisper"):
        view = view_of(rid, (LEFT, RIGHT))
        assert view.pair is not None and view.pair.left.rid == LEFT.rid and view.pair.right.rid == RIGHT.rid
        rows = [([s.text for s in r.left], [s.text for s in r.right]) for r in view.pair.rows]
        assert rows == [(["one"], ["one"]), ([], ["between"]), (["two"], []), ([], ["late"])]
        assert all(s.speaker == "" for r in view.pair.rows for s in r.right), "Whisper names no speaker"
    late = view_of(CALL, (LEFT, RIGHT), "&at=00:30")
    assert late.player == "/recording/audio?rid=plaud%3Arec-x"
    marked = [s for r in late.pair.rows for s in r.right if s.mark]
    assert [s.text for s in marked] == ["late"] and marked[0].target, "a time only Whisper has is marked on its side"
    alone = view_of(f"{CALL}/whisper", (RIGHT,))
    assert alone.pair is None and [t.rid for t in alone.transcripts] == [RIGHT.rid]
