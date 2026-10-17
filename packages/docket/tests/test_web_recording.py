"""The Recording page over HTTP, signed in, on the invented recording vault (DR-0544, DR-0545): the summary beside the
transcript with each anchor linked to the lines it points at, the URL's states, and the pages without a recording."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import make_record
from docket_webkit import Element, a11y_problems, html_problems, parse, signed_in
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
from docket.web.templating import PAGE_CSP


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return recording_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


def page(client: TestClient, path: str, status: int = 200) -> Element:
    """A state of the page: its status, the page CSP, no-store, and the checks every page passes."""
    answer = client.get(path)
    assert answer.status_code == status, answer.text
    assert answer.headers["Content-Security-Policy"] == PAGE_CSP
    assert answer.headers["Cache-Control"] == "no-store"
    assert not html_problems(answer.text), path
    assert not a11y_problems(answer.text), path
    return parse(answer.text)


def marked(tree: Element) -> list[str]:
    return [s.find("a", class_="time").text() for s in tree.find_all("li", class_="mark")]


def anchors(tree: Element) -> dict[str, str]:
    return {
        a.text(): a.attrs["href"] for a in tree.find("section", class_="summary-column").find_all("a", class_="anchor")
    }


def follow(client: TestClient, href: str) -> Element:
    """A link followed as a browser does: the fragment stays in the browser."""
    url, _, fragment = href.partition("#")
    tree = page(client, url)
    if fragment:
        assert tree.find(id=fragment), href
    return tree


def test_the_page_shows_the_summary_beside_the_transcript_with_plain_links(client: TestClient) -> None:
    tree = page(client, f"/recording?rid={CRANE}")
    assert tree.find("title").text() == "Harbour crane check - Recording - Docket"
    assert tree.find("h1").text() == "Harbour crane check"
    assert (
        tree.find("p", class_="eyebrow").text()
        == "recording plaud \N{MIDDLE DOT} 12 Oct 2026 10:30 \N{MIDDLE DOT} 03:59 long"
    )
    summary = tree.find("section", class_="summary-column")
    assert f"{MINUTES}, minutes, rev 1" in summary.text()
    transcript = tree.find("section", class_="transcript-column")
    assert transcript.find("p", class_="meta").text() == f"{POLISHED}, plaud/transaction_polish, 12 lines"
    assert len(transcript.find_all("li", class_="segment")) == 12
    assert "Also transcribed: plaud:rec-crane/transaction" in transcript.text()
    # Every state is its URL, and every link a plain one: the page needs no script to work.
    main = tree.find("main")
    assert not [el for el in main.walk() if any(name.startswith("hx-") for name in el.attrs)]
    assert all(a.attrs["href"].startswith("/") for a in main.find_all("a"))
    assert not marked(tree)
    nav = tree.find("nav", class_="nav")
    assert nav.find("a", href="/recording").attrs["aria-current"] == "page"


def test_the_page_says_why_there_is_no_audio(client: TestClient) -> None:
    audio = page(client, f"/recording?rid={CRANE}").find("section", class_="audio")
    assert audio.attrs["data-why"] == "not-fetched"
    assert audio.text() == f"Audio {NO_AUDIO['not-fetched']}"
    assert audio.text().startswith("Audio No audio stored yet")


def test_each_summary_line_links_to_the_transcript_lines_it_anchors(client: TestClient) -> None:
    tree = page(client, f"/recording?rid={CRANE}&doc={NOTE}")
    links = anchors(tree)
    # On the transcript shown an anchor reads as its time; the link keeps the page's rid and doc.
    assert links["@00:40-01:39"] == f"/recording?rid={CRANE}&doc={NOTE}&at=00:40-01:39#at"
    assert links["@02:40"] == f"/recording?rid={CRANE}&doc={NOTE}&at=02:40#at"
    # Another transcript of the call opens with it shown; another record opens on the Search page.
    assert links[f"[r:{RAW}@03:00-03:19]"] == f"/recording?rid={RAW}&doc={NOTE}&at=03:00-03:19#at"
    assert links[f"[r:{MAIL}]"] == "/search?hit=r:mail:%3Ccrane-1@harbour.example%3E"

    at = follow(client, links["@00:40-01:39"])
    assert marked(at) == ["00:40", "01:00", "01:20"]
    assert at.find("li", id="at").find("a", class_="time").text() == "00:40"
    cited = at.find("section", class_="summary-column").find_all("div", class_="cited")
    assert [line.attrs["data-line"] for line in cited] == ["18"]
    assert "points at the marked lines" in cited[0].text()
    assert at.find("span", class_="locator").text() == f"r:{CRANE}@00:40-01:39"

    point = follow(client, links["@02:40"])
    assert marked(point) == ["02:40"]
    assert [line.attrs["data-line"] for line in point.find_all("div", class_="cited")] == ["23"]

    other = follow(client, links[f"[r:{RAW}@03:00-03:19]"])
    transcript = other.find("section", class_="transcript-column")
    assert transcript.find("p", class_="meta").text().startswith(f"{RAW}, plaud/transaction")
    assert marked(other) == ["03:00"]
    assert anchors(other)["@03:00-03:19"] == f"/recording?rid={RAW}&doc={NOTE}&at=03:00-03:19#at"
    assert f"/recording?rid={POLISHED}&doc={NOTE}&at=03:00-03:19#at" in [
        a.attrs["href"] for a in transcript.find_all("a")
    ]


def test_a_transcript_line_links_to_itself_and_marks_what_points_at_it(client: TestClient) -> None:
    tree = page(client, f"/recording?rid={CRANE}&doc={NOTE}")
    line = tree.find_all("li", class_="segment")[4]
    href = line.find("a", class_="time").attrs["href"]
    assert href == f"/recording?rid={CRANE}&doc={NOTE}&at=01:20#at"
    there = follow(client, href)
    assert marked(there) == ["01:20"]
    assert [line.attrs["data-line"] for line in there.find_all("div", class_="cited")] == ["18"]


def test_the_doc_shown_is_the_summary_search_attaches_and_the_others_are_a_click_away(client: TestClient) -> None:
    tree = page(client, f"/recording?rid={CRANE}&at=01:40")
    chips = tree.find("nav", aria_label="Docs on this recording").find_all("a")
    assert [(a.text(), a.attrs["href"], a.attrs.get("aria-current")) for a in chips] == [
        (MINUTES, f"/recording?rid={CRANE}&at=01:40#at", "true"),
        (NOTE, f"/recording?rid={CRANE}&doc={NOTE}&at=01:40#at", None),
    ]
    # The minutes cover only the transcript, so their short anchor is a time on it.
    assert anchors(tree) == {"@01:40-02:19": f"/recording?rid={CRANE}&at=01:40-02:19#at"}
    assert [line.attrs["data-line"] for line in tree.find_all("div", class_="cited")] == ["14"]


def test_a_transcript_s_rid_or_a_locator_opens_the_same_call(client: TestClient) -> None:
    by_transcript = page(client, f"/recording?rid={POLISHED}")
    assert by_transcript.find("h1").text() == "Harbour crane check"
    assert f"{MINUTES}, minutes, rev 1" in by_transcript.find("section", class_="summary-column").text()
    located = page(client, f"/recording?rid={quote(f'r:{CRANE}@01:40')}")
    assert marked(located) == ["01:40"]
    assert located.find("span", class_="locator").text() == f"r:{CRANE}@01:40"
    bracketed = page(client, f"/recording?rid={quote(f'[r:{CRANE}@01:40]')}&at=02:00")
    assert marked(bracketed) == ["02:00"]


def test_a_recording_with_no_summary_still_shows_its_transcript(client: TestClient) -> None:
    tree = page(client, f"/recording?rid={LABELS}")
    summary = tree.find("section", class_="summary-column")
    assert summary.find("p", class_="empty-note").text() == "No summary covers this recording yet."
    assert not tree.find_all("nav", aria_label="Docs on this recording")
    assert len(tree.find_all("li", class_="segment")) == 2


def test_a_recording_with_no_transcript_yet_says_so(client: TestClient, tmp_path: Path) -> None:
    under = {"source": "plaud", "container": "plaud:rec-bare", "thread": "plaud:rec-bare", "author": None}
    bare = make_record(rid="plaud:rec-bare", kind="recording", title="Late notes", text="", **under)
    Vault.open(tmp_path / "vault").write_run("plaud", [bare])
    tree = page(client, "/recording?rid=plaud:rec-bare")
    assert tree.find("h1").text() == "Late notes"
    assert tree.find("p", class_="eyebrow").text() == "recording plaud \N{MIDDLE DOT} 14 Oct 2026 11:30"
    column = tree.find("section", class_="transcript-column")
    assert column.find("p", class_="empty-note").text() == "No transcript of this recording is in the raw log yet."
    assert not tree.find_all("li", class_="segment")


def test_a_legacy_note_stands_in_for_a_summary(client: TestClient) -> None:
    summary = page(client, f"/recording?rid={OLD}").find("section", class_="summary-column")
    assert f"{LEGACY}, legacy-note, rev 1" in summary.text()
    assert "A legacy note has no anchors" in summary.text()
    assert "The labels print too small." in summary.text()


@pytest.mark.parametrize("rid", [HELD, f"{HELD}/transaction_polish"])
def test_a_held_recording_shows_nothing_of_it(client: TestClient, rid: str) -> None:
    tree = page(client, f"/recording?rid={rid}")
    main = tree.find("main")
    assert f"{rid} is held: it has stretches you left out, so nothing of it is stored yet." in main.text()
    assert main.find("a", class_="button").attrs["href"] == "/staging?source=plaud"
    assert not main.find_all("section")
    assert not main.find_all("li", class_="segment")


def test_a_rid_the_raw_log_doesn_t_hold_or_that_isn_t_a_recording(client: TestClient) -> None:
    unknown = page(client, "/recording?rid=plaud:rec-nothing", status=404)
    assert "No recording plaud:rec-nothing in the raw log." in unknown.find("main").text()
    assert unknown.find("main").find("a", class_="button").attrs["href"] == "/search"
    mail = page(client, f"/recording?rid={quote(MAIL)}", status=404)
    assert f"{MAIL} is a mail, not a recording or a transcript." in mail.find("main").text()
    assert (
        mail.find("main").find("a", class_="button").attrs["href"] == "/search?hit=r:mail:%3Ccrane-1@harbour.example%3E"
    )
    # A rid of no source a hold list can name is simply unknown.
    page(client, "/recording?rid=odd.:x", status=404)


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
def test_a_value_the_url_can_t_hold_is_the_page_s_own_error(client: TestClient, query: str, named: str) -> None:
    tree = page(client, f"/recording?{query}", status=400)
    assert named in tree.find("div", class_="error").text()


def test_a_doc_that_isn_t_on_the_recording_says_so(client: TestClient) -> None:
    gone = page(client, f"/recording?rid={CRANE}&doc=minutes:2026-01-01-nothing")
    assert "no doc minutes:2026-01-01-nothing" in gone.find("div", class_="warn").text()
    assert not gone.find("section", class_="summary-column").find_all("p", class_="empty-note")
    elsewhere = page(client, f"/recording?rid={CRANE}&doc={LEGACY}")
    assert f"{LEGACY} has no lines on this recording" in elsewhere.find("div", class_="warn").text()
    assert "The labels print too small." in elsewhere.find("section", class_="summary-column").text()


def test_a_time_off_the_transcript_says_so(client: TestClient) -> None:
    after = page(client, f"/recording?rid={CRANE}&at=09:00")
    assert not marked(after)
    note = after.find("section", class_="transcript-column").find("div", class_="warn").text()
    assert note == f"09:00 is outside {POLISHED}, which runs from 00:00 to 03:59"
    # Between two lines: inside the transcript, on no line.
    gap = page(client, f"/recording?rid={CRANE}&at=00:19")
    assert gap.find("div", class_="warn").text() == f"no line of {POLISHED} is at 00:19"


def test_the_start_takes_a_rid_or_a_locator(client: TestClient) -> None:
    tree = page(client, "/recording")
    form = tree.find("form", class_="recording-open")
    assert (form.attrs["action"], form.attrs["method"]) == ("/recording", "get")
    assert form.find("input").attrs["name"] == "rid"
    # Without a rid there is nothing to be in: a doc or a time alone is the start.
    assert page(client, "/recording?at=02:00&doc=nope").find("form", class_="recording-open")


def test_the_index_being_built_waits_and_a_failure_is_the_error_page(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = client.app
    assert isinstance(app, ServiceApp)
    updater = app.service.updater
    app.service.updater = IndexUpdater(lambda: time.sleep(0.5), wait_seconds=0.01)
    waiting = page(client, f"/recording?rid={CRANE}", status=503)
    assert "the index is being built" in waiting.find("div", class_="error").text()
    app.service.updater = updater
    # A hold list that doesn't read stops the page, as it stops every ingest.
    (tmp_path / "vault" / "state" / "holds" / "plaud.txt").write_text("not an id\n", encoding="utf-8")
    broken = page(client, "/recording?rid=plaud:rec-nothing", status=400)
    assert "line 1 isn't an id" in broken.find("div", class_="error").text()

    def failing(*_args: object) -> object:
        raise RuntimeError("words from a record")

    monkeypatch.setattr(recording, "gather", failing)
    failed = client.get(f"/recording?rid={CRANE}")
    assert failed.status_code == 500
    assert "Something failed in Docket" in failed.text
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
    view = build(facts, RecordingState("calls:rec-1", at=Anchor(None, "time", 2)))
    assert view.summary is not None
    assert [(a.text, a.url, a.title) for line in view.summary.lines for a in line.anchors] == [
        # A short anchor on a doc covering two records points at neither.
        ("@00:01", None, ""),
        # A time on a recording without segments is placed on its transcript.
        ("@00:02", "/recording?rid=calls:rec-1&at=00:02#at", "[r:calls:rec-1@00:02]: the transcript at this time"),
        ("[r:calls:rec-1/t]", "/recording?rid=calls:rec-1", "[r:calls:rec-1/t]: the transcript"),
        ("[r:calls:rec-1/t#1]", "/search?hit=r:calls:rec-1/t%231", "Open r:calls:rec-1/t#1 on the Search page"),
    ]
    assert [line.cited for line in view.summary.lines] == [False, True, False, False]
    assert [s.at for s in view.transcripts[0].segments if s.mark] == ["00:00"]
