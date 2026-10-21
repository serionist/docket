"""The Recording page's read, signed in, on the invented recording vault (DR-0545, DR-0709, DR-0776): the summary beside
the transcript with each anchor linked to the lines it points at, the URL's states, and the pages without a recording.
The page itself is the app's document; its links are the URLs these answers carry."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import make_record
from docket_webkit import signed_in
from recording_kit import CRANE, HELD, LABELS, LEGACY, MAIL, MINUTES, NOTE, OLD, POLISHED, RAW, recording_template
from starlette.testclient import TestClient

from docket.recall import DocLine, DocView
from docket.service.app import ServiceApp
from docket.service.writer import IndexUpdater
from docket.vault.anchors import Anchor
from docket.vault.vault import Vault
from docket.web import recording
from docket.web.recording_state import RecordingState, RecordingStateError
from docket.web.recording_view import NO_AUDIO, Facts, build

API = "/api/web/recording"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return recording_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


def answer(client: TestClient, query: str, status: int = 200) -> dict[str, Any]:
    got = client.get(f"{API}?{query}" if query else API)
    assert got.status_code == status, got.text
    return got.json()


def view(client: TestClient, query: str) -> dict[str, Any]:
    found = answer(client, query)["view"]
    assert found is not None
    return found


def lines(found: dict[str, Any]) -> list[dict[str, Any]]:
    return found["transcripts"][0]["lines"]


def marked(found: dict[str, Any]) -> list[str]:
    return [line["at"] for line in lines(found) if line["mark"]]


def anchors(found: dict[str, Any]) -> dict[str, str | None]:
    return {a["text"]: a["url"] for line in found["summary"]["lines"] for a in line["anchors"]}


def cited(found: dict[str, Any]) -> list[int]:
    return [line["n"] for line in found["summary"]["lines"] if line["cited"]]


def follow(client: TestClient, href: str) -> dict[str, Any]:
    """A link followed as the app follows it: its query string read, its `#at` naming the line it lands on."""
    url, _, fragment = href.partition("#")
    found = view(client, url.partition("?")[2])
    if fragment:
        assert fragment == "at" and [line["target"] for line in lines(found)].count(True) == 1, href
    return found


def test_the_page_is_the_app_s_and_shows_the_summary_beside_the_transcript(client: TestClient) -> None:
    assert '<div id="root">' in client.get(f"/recording?rid={CRANE}").text
    found = view(client, f"rid={CRANE}")
    assert (found["mode"], found["title"]) == ("recording", "Harbour crane check")
    assert found["meta"] == ["plaud", "12 Oct 2026 10:30", "03:59 long"]
    assert (found["summary"]["id"], found["summary"]["type"], found["summary"]["rev"]) == (MINUTES, "minutes", 1)
    transcript = found["transcripts"][0]
    assert transcript["meta"] == f"{POLISHED}, plaud/transaction_polish, 12 lines"
    assert len(transcript["lines"]) == 12
    assert [o["rid"] for o in found["others"]] == ["plaud:rec-crane/transaction"]
    assert found["others"][0]["url"] == "/recording?rid=plaud:rec-crane/transaction"
    assert not marked(found)
    assert found["locator"] == f"r:{CRANE}"


def test_the_page_says_why_there_is_no_audio(client: TestClient) -> None:
    found = view(client, f"rid={CRANE}")
    assert found["audio"] == {"why": "not-fetched", "message": NO_AUDIO["not-fetched"], "speech": None}
    assert found["player"] is None


def test_each_summary_line_links_to_the_transcript_lines_it_anchors(client: TestClient) -> None:
    links = anchors(view(client, f"rid={CRANE}&doc={NOTE}"))
    # On the transcript shown an anchor reads as its time; the link keeps the page's rid and doc.
    assert links["@00:40-01:39"] == f"/recording?rid={CRANE}&doc={NOTE}&at=00:40-01:39#at"
    assert links["@02:40"] == f"/recording?rid={CRANE}&doc={NOTE}&at=02:40#at"
    # Another transcript of the call opens with it shown; another record opens on the Search page.
    assert links[f"[r:{RAW}@03:00-03:19]"] == f"/recording?rid={RAW}&doc={NOTE}&at=03:00-03:19#at"
    assert links[f"[r:{MAIL}]"] == "/search?hit=r:mail:%3Ccrane-1@harbour.example%3E"

    at = follow(client, str(links["@00:40-01:39"]))
    assert marked(at) == ["00:40", "01:00", "01:20"]
    assert next(line for line in lines(at) if line["target"])["at"] == "00:40"
    assert cited(at) == [18]
    assert at["locator"] == f"r:{CRANE}@00:40-01:39"

    point = follow(client, str(links["@02:40"]))
    assert (marked(point), cited(point)) == (["02:40"], [23])

    other = follow(client, str(links[f"[r:{RAW}@03:00-03:19]"]))
    assert other["transcripts"][0]["meta"].startswith(f"{RAW}, plaud/transaction")
    assert marked(other) == ["03:00"]
    assert anchors(other)["@03:00-03:19"] == f"/recording?rid={RAW}&doc={NOTE}&at=03:00-03:19#at"
    # The transcript it was shown beside is a link that keeps the time.
    assert [o["url"] for o in other["others"]] == [f"/recording?rid={POLISHED}&doc={NOTE}&at=03:00-03:19#at"]


def test_a_transcript_line_links_to_itself_and_marks_what_points_at_it(client: TestClient) -> None:
    line = lines(view(client, f"rid={CRANE}&doc={NOTE}"))[4]
    assert line["url"] == f"/recording?rid={CRANE}&doc={NOTE}&at=01:20#at"
    assert line["start"] == 80.0
    there = follow(client, line["url"])
    assert (marked(there), cited(there)) == (["01:20"], [18])


def test_the_doc_shown_is_the_summary_search_attaches_and_the_others_are_a_click_away(client: TestClient) -> None:
    found = view(client, f"rid={CRANE}&at=01:40")
    assert found["docs"] == [
        {"id": MINUTES, "url": f"/recording?rid={CRANE}&at=01:40#at", "on": True},
        {"id": NOTE, "url": f"/recording?rid={CRANE}&doc={NOTE}&at=01:40#at", "on": False},
    ]
    # The minutes cover only the transcript, so their short anchor is a time on it.
    assert anchors(found) == {"@01:40-02:19": f"/recording?rid={CRANE}&at=01:40-02:19#at"}
    assert cited(found) == [14]


def test_a_transcript_s_rid_or_a_locator_opens_the_same_call(client: TestClient) -> None:
    by_transcript = view(client, f"rid={POLISHED}")
    assert by_transcript["title"] == "Harbour crane check"
    assert by_transcript["summary"]["id"] == MINUTES
    located = view(client, f"rid={quote(f'r:{CRANE}@01:40')}")
    assert marked(located) == ["01:40"]
    assert located["locator"] == f"r:{CRANE}@01:40"
    assert answer(client, f"rid={quote(f'r:{CRANE}@01:40')}")["canonical"] == f"/recording?rid={CRANE}&at=01:40"
    assert marked(view(client, f"rid={quote(f'[r:{CRANE}@01:40]')}&at=02:00")) == ["02:00"]


def test_a_recording_with_no_summary_still_shows_its_transcript(client: TestClient) -> None:
    found = view(client, f"rid={LABELS}")
    assert (found["summary"], found["no_summary"], found["docs"]) == (None, "No summary covers this recording yet.", [])
    assert len(lines(found)) == 2


def test_a_recording_with_no_transcript_yet_says_so(client: TestClient, tmp_path: Path) -> None:
    under = {"source": "plaud", "container": "plaud:rec-bare", "thread": "plaud:rec-bare", "author": None}
    bare = make_record(rid="plaud:rec-bare", kind="recording", title="Late notes", text="", **under)
    Vault.open(tmp_path / "vault").write_run("plaud", [bare])
    found = view(client, "rid=plaud:rec-bare")
    assert (found["title"], found["meta"]) == ("Late notes", ["plaud", "14 Oct 2026 11:30"])
    assert (found["transcripts"], found["pair"]) == ([], None)
    assert found["no_transcript"] == "No transcript of this recording is in the raw log yet."


def test_a_legacy_note_stands_in_for_a_summary(client: TestClient) -> None:
    found = view(client, f"rid={OLD}")
    assert (found["summary"]["id"], found["summary"]["type"]) == (LEGACY, "legacy-note")
    assert "A legacy note has no anchors" in found["note"]
    assert any(line["body"] == "The labels print too small." for line in found["summary"]["lines"])


@pytest.mark.parametrize("rid", [HELD, f"{HELD}/transaction_polish"])
def test_a_held_recording_shows_nothing_of_it(client: TestClient, rid: str) -> None:
    found = view(client, f"rid={rid}")
    assert found["mode"] == "held"
    assert found["message"].startswith(f"{rid} is held: it has stretches you left out, so nothing of it is stored yet.")
    assert found["link"] == "/staging?source=plaud"
    assert found["link_label"] == "Held plaud recordings on the Staging page"
    assert (found["summary"], found["transcripts"], found["pair"], found["audio"]) == (None, [], None, None)


def test_a_rid_the_raw_log_doesn_t_hold_or_that_isn_t_a_recording(client: TestClient) -> None:
    unknown = view(client, "rid=plaud:rec-nothing")
    assert (unknown["mode"], unknown["message"]) == ("missing", "No recording plaud:rec-nothing in the raw log.")
    assert unknown["link"] == "/search"
    mail = view(client, f"rid={quote(MAIL)}")
    assert (mail["mode"], mail["message"]) == ("other", f"{MAIL} is a mail, not a recording or a transcript.")
    assert mail["link"] == "/search?hit=r:mail:%3Ccrane-1@harbour.example%3E"
    # A rid of no source a hold list can name is simply unknown.
    assert view(client, "rid=odd.:x")["mode"] == "missing"


@pytest.mark.parametrize(
    ("query", "named"),
    [
        ("rid=not%20a%20rid", "rid is a recording's rid or its locator"),
        ("rid=minutes:2026-10-12-harbour-crane-check", "rid is a recording's rid or its locator"),
        ("rid=plaud:a//b", "rid is a recording's rid or its locator"),
        (f"rid={quote(f'r:{MAIL}#2')}", "a place in a recording is a time"),
        (f"rid={CRANE}&at=soon", "at is a time or a stretch"),
        (f"rid={CRANE}&at=03:00-02:00", "at is a time or a stretch"),
        (f"rid={CRANE}&doc=plaud:rec-crane", "doc is a doc's id"),
        (f"rid={CRANE}&doc=not+a+doc", "doc is a doc's id"),
    ],
)
def test_a_value_the_url_can_t_hold_is_refused(client: TestClient, query: str, named: str) -> None:
    assert named in answer(client, query, status=400)["error"]


def test_a_doc_that_isn_t_on_the_recording_says_so(client: TestClient) -> None:
    gone = view(client, f"rid={CRANE}&doc=minutes:2026-01-01-nothing")
    assert any("no doc minutes:2026-01-01-nothing" in p for p in gone["problems"])
    assert gone["no_summary"] == ""
    elsewhere = view(client, f"rid={CRANE}&doc={LEGACY}")
    assert f"{LEGACY} has no lines on this recording" in elsewhere["problems"]
    assert any(line["body"] == "The labels print too small." for line in elsewhere["summary"]["lines"])


def test_a_time_off_the_transcript_says_so(client: TestClient) -> None:
    after = view(client, f"rid={CRANE}&at=09:00")
    assert not marked(after)
    assert after["transcripts"][0]["problem"] == f"09:00 is outside {POLISHED}, which runs from 00:00 to 03:59"
    # Between two lines: inside the transcript, on no line.
    gap = view(client, f"rid={CRANE}&at=00:19")
    assert gap["transcripts"][0]["problem"] == f"no line of {POLISHED} is at 00:19"


def test_the_start_takes_a_rid_or_a_locator(client: TestClient) -> None:
    assert answer(client, "") == {"canonical": "/recording", "view": view(client, ""), "waiting": ""}
    assert view(client, "")["mode"] == "start"
    # Without a rid there is nothing to be in: a doc or a time alone is the start.
    assert view(client, "at=02:00&doc=nope")["mode"] == "start"


def test_the_index_being_built_waits_and_a_failure_says_no_words(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = client.app
    assert isinstance(app, ServiceApp)
    updater = app.service.updater
    app.service.updater = IndexUpdater(lambda: time.sleep(0.5), wait_seconds=0.01)
    waiting = answer(client, f"rid={CRANE}")
    assert waiting["view"] is None and "the index is being built" in waiting["waiting"]
    app.service.updater = updater
    # A hold list that doesn't read stops the read, as it stops every ingest.
    (tmp_path / "vault" / "state" / "holds" / "plaud.txt").write_text("not an id\n", encoding="utf-8")
    assert "line 1 isn't an id" in answer(client, "rid=plaud:rec-nothing", status=400)["error"]

    def failing(*_args: object) -> object:
        raise RuntimeError("words from a record")

    monkeypatch.setattr(recording, "gather", failing)
    failed = client.get(f"{API}?rid={CRANE}")
    assert failed.status_code == 500
    assert "Something failed in Docket" in failed.json()["error"]
    assert "words from a record" not in failed.text


def test_the_url_reads_and_writes_one_canonical_form() -> None:
    state = RecordingState.read({"at": "1:02:03-1:02:09", "doc": NOTE, "rid": CRANE, "other": "kept out"})
    assert state == RecordingState(CRANE, NOTE, Anchor(None, "time", 3723, 3729))
    assert state.url() == f"/recording?rid={CRANE}&doc={NOTE}&at=1:02:03-1:02:09"
    assert state.link() == f"{state.url()}#at"
    assert RecordingState(CRANE).link() == f"/recording?rid={CRANE}"
    assert RecordingState.read({"rid": MAIL}).url() == "/recording?rid=mail:%3Ccrane-1@harbour.example%3E"
    assert RecordingState().url() == "/recording"
    with pytest.raises(RecordingStateError, match="at is a time"):
        RecordingState.read({"rid": CRANE, "at": "02:61"})


def test_an_anchor_links_where_its_record_is_shown_else_to_search() -> None:
    under = {"source": "calls", "container": "calls:rec-1", "thread": "calls:rec-1", "author": None}
    call = make_record(rid="calls:rec-1", kind="recording", title="A call", text="", **under)
    said = [{"start_ms": 0, "end_ms": 9000, "speaker": "Ada Example", "text": "Hello."}]
    transcript = make_record(rid="calls:rec-1/t", kind="transcript", text="Ada Example: Hello.", segments=said, **under)
    body = [
        "- Short, with two covers. @00:01",
        "- On the recording. [r:calls:rec-1@00:02]",
        "- The whole transcript. [r:calls:rec-1/t]",
        "- A paragraph of it. [r:calls:rec-1/t#1]",
    ]
    numbered = enumerate(["---", "id: recording-note:x", "---", *body], start=1)
    doc = DocView(
        id="recording-note:x", type="recording-note", rev=1, path="processed/x.md", by="claude-test",
        covers=["calls:rec-1", "calls:rec-1/t"], supersedes=[], entities=[], superseded_by=[], text="",
        lines=[DocLine(n=n, text=text) for n, text in numbered],
    )  # fmt: skip
    facts = Facts(
        "calls:rec-1", ZoneInfo("Europe/Budapest"), record=call, recording=call, transcripts=(transcript,),
        attached=doc.id, docs=(doc.id,), summary=doc,
    )  # fmt: skip
    found = build(facts, RecordingState("calls:rec-1", at=Anchor(None, "time", 2)))
    assert found.summary is not None
    assert [(a.text, a.url, a.title) for line in found.summary.lines for a in line.anchors] == [
        # A short anchor on a doc covering two records points at neither.
        ("@00:01", None, ""),
        # A time on a recording without segments is placed on its transcript.
        ("@00:02", "/recording?rid=calls:rec-1&at=00:02#at", "[r:calls:rec-1@00:02]: the transcript at this time"),
        ("[r:calls:rec-1/t]", "/recording?rid=calls:rec-1", "[r:calls:rec-1/t]: the transcript"),
        ("[r:calls:rec-1/t#1]", "/search?hit=r:calls:rec-1/t%231", "Open r:calls:rec-1/t#1 on the Search page"),
    ]
    assert [line.cited for line in found.summary.lines] == [False, True, False, False]
    assert [s.at for s in found.transcripts[0].lines if s.mark] == ["00:00"]
