"""`docket sync recordings` (#267): each recording of a source that needs reading gets an agent of its own, the
recording written from raw into the sync folder as files the Read tool takes whole, and a prompt; one with a record
still in staging is skipped and said. Invented people and data only: the pilot's ledger of `test_sync_folder.py`."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docket_testkit import copy_vault, make_record, template_with
from forget_kit import forget
from ledger_kit import meeting, write_ops
from recording_kit import call, doc
from sync_kit import sync_template, sync_vault
from test_sync_folder import NOTED, NOW, POINT, WEEKLY, build_pilot

from docket import recall
from docket.cli import main
from docket.errors import DocketError
from docket.index import build as index_build
from docket.ledger import views
from docket.sync import folder, reconcile, recordings, start
from docket.vault import cursors
from docket.vault.cuts import CUT_MARKER
from docket.vault.gate import Gate
from docket.vault.processed import DocMeta, ProcessedDoc
from docket.vault.records import Segment
from docket.vault.vault import Vault

AT = NOW + timedelta(minutes=10)
PLACEHOLDER = re.compile(r"\{[A-Z_]+\}")
CALL = "plaud:rec-0103"
CALL_SAID = [("Cleo Test", "The quote and the PO come by Friday."), ("Ada Example", "Thanks, Cleo.")]


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_with(tmp_path_factory, build_pilot)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    v = copy_vault(template, tmp_path)
    index_build.rebuild(v)
    cursors.save(v, "slack", cursors.Cursor(mark=datetime(2026, 10, 19, 6, 2, tzinfo=UTC), lookback_days=1))
    return v


def read(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_a_recording_with_no_note_gets_its_files_its_prompt_and_its_line_in_the_source_s_list(vault: Vault) -> None:
    started = start.start(vault, [], NOW)
    sync = started.folder
    before = read(sync / "sync.json")
    wave = recordings.recordings(vault, started.info.sync, "plaud", AT)
    # The kickoff has its note and the idea is outside the window: only the weekly is read.
    (given,) = wave.given
    assert (given.rid, given.key, given.title, wave.earlier, wave.said) == (WEEKLY, "rec-0101", "Pilot weekly", 0, [])
    assert given.prompt == str(sync / "agents" / "plaud" / "rec-0101.md")
    assert given.block == str(sync / "blocks" / "plaud" / "rec-0101.txt")
    assert given.files == [str(sync / "recordings" / "plaud" / "rec-0101.1.txt")]
    assert read(given.files[0]).splitlines() == [
        "recording plaud:rec-0101 | Pilot weekly | 2026-10-19 10:30 Europe/Budapest | part 1 of 1",
        "== plaud:rec-0101/summary | vendor-summary | a paragraph a line, its locator r:plaud:rec-0101/summary#<its "
        "number>",
        "#1 ## Summary",
        "#2 The crane check.",
        "== plaud:rec-0101/transaction_polish | transcript | a segment a line, its locator "
        "r:plaud:rec-0101/transaction_polish@<its mm:ss>",
        "@00:00 Ben Sample: The pilot starts on the 28th.",
        "@00:20 Dan Example: I book the room.",
    ]
    prompt = read(given.prompt)
    assert not PLACEHOLDER.search(prompt)
    assert prompt.startswith("You are the agent of one plaud recording in a Docket sync.")
    assert f"| the ledger | {sync / 'context.md'} |" in prompt
    assert f"| your block | {given.block} |" in prompt
    assert "| the recording | plaud:rec-0101, Pilot weekly, from 2026-10-19 10:30 |" in prompt
    assert f"- {given.files[0]}\n" in prompt
    assert "Its meetings by time: M-0001 | 10-19 10:00-10:30 | Pilot weekly | Ben Sample. Its open points" in prompt
    assert "id `recording-note:2026-10-19-<slug>`" in prompt and "`by: claude, sync 2026-10-20T09:15`" in prompt
    assert f"--vault {vault.root}" in prompt and "| owed since | the run's window |" in prompt
    assert f"| the note's layout | {recordings.LAYOUT} |" in prompt
    assert "| the owner | Ada Example (ada@example.invalid): whose asks and waits you find |" in prompt
    listed = json.loads(read(sync / "recordings" / "plaud.json"))
    assert listed["source"] == "plaud" and [r["rid"] for r in listed["recordings"]] == [WEEKLY]
    # sync.json stays as the start wrote it; a source with no recording writes no list.
    assert read(sync / "sync.json") == before
    assert recordings.recordings(vault, started.info.sync, "slack", AT).given == []
    assert not (sync / "recordings" / "slack.json").exists()
    assert recordings.lines(wave) == [f"recording plaud/rec-0101: {given.prompt}"]


def test_a_second_call_gives_only_what_is_new_and_each_source_keeps_its_own_list(vault: Vault) -> None:
    started = start.start(vault, [], NOW)
    first = recordings.recordings(vault, started.info.sync, "plaud", AT)
    again = recordings.recordings(vault, started.info.sync, "plaud", AT)
    assert (again.given, again.earlier) == ([], 1)
    assert recordings.lines(again) == ["no recording of plaud to read now; 1 given an agent earlier in this sync"]
    vault.write_run("plaud", call(CALL, "Sensor quote", CALL_SAID, 19, "transaction_polish"))
    later = recordings.recordings(vault, started.info.sync, "plaud", AT)
    assert [r.rid for r in later.given] == [CALL] and later.earlier == 1
    assert [r.rid for r in folder.recordings(started.folder, "plaud")] == [first.given[0].rid, CALL]
    assert recordings.lines(recordings.recordings(vault, started.info.sync, "slack", AT)) == [
        "no recording of slack to read now"
    ]


def legacy(slug: str, covers: list[str]) -> str:
    """A legacy note as `docket import notes` writes one, tied to the records its head names."""
    head = DocMeta(
        id=f"legacy-note:{slug}", type="legacy-note", covers=covers, by="docket import", old_paths=[f"notes/{slug}.md"]
    )
    return ProcessedDoc(head, "# The weekly\n\nThe 28th.\n", 0).render()


def test_a_legacy_note_counts_as_a_recording_s_note_and_the_summary_names_a_sync_s_note_first(vault: Vault) -> None:
    """A note from before Docket, tied to the recording by its import, is its note: the fourth rehearsal read two such
    recordings again (#286). Where an agent's note covers it too, that note is the one the summary names."""
    recall.doc_put(vault, legacy("pilot-weekly", [WEEKLY]))
    recall.doc_put(vault, legacy("pilot-kickoff", [f"{NOTED}/transaction_polish"]))
    started = start.start(vault, [], NOW)
    wave = recordings.recordings(vault, started.info.sync, "plaud", AT)
    assert (wave.given, recordings.lines(wave)) == ([], ["no recording of plaud to read now"])
    assert index_build.noted(vault, [WEEKLY, NOTED]) == {
        WEEKLY: "legacy-note:pilot-weekly", NOTED: "recording-note:2026-10-18-pilot-kickoff",
    }  # fmt: skip


def test_a_digest_covering_a_recording_isn_t_its_note(vault: Vault) -> None:
    """A digest, a brief or a correction covers records too, and would hide a recording no note covers."""
    recall.doc_put(vault, doc("digest:pilot-week", [f"{WEEKLY}/transaction_polish"], ["- The 28th. @00:00"]))
    index_build.update(vault)
    assert index_build.noted(vault, [WEEKLY]) == {}
    started = start.start(vault, [], NOW)
    assert [r.rid for r in recordings.recordings(vault, started.info.sync, "plaud", AT).given] == [WEEKLY]


def test_a_recording_with_a_record_still_in_staging_is_skipped_and_said(vault: Vault) -> None:
    """Its note would come from the summary alone and then cover the recording for good."""
    recording, transcript, summary = call(CALL, "Sensor quote", CALL_SAID, 19, "transaction_polish")
    vault.write_run("plaud", [recording, summary])
    # Another source's record waiting there holds up no recording of Plaud's.
    Gate(vault).stage([transcript, make_record(rid="calls:rec-0103", source="calls", kind="recording")])
    started = start.start(vault, [], NOW)
    wave = recordings.recordings(vault, started.info.sync, "plaud", AT)
    assert [r.rid for r in wave.given] == [WEEKLY]
    assert wave.said == [
        f"skipped {CALL}: {CALL}/transaction_polish waits in staging, and a note without it would cover the "
        "recording for good; the next sync reads it once it is all in raw"
    ]
    assert not list((started.folder / "recordings" / "plaud").glob("rec-0103*"))


def test_the_files_hold_only_what_raw_keeps_a_forgotten_record_left_out(vault: Vault) -> None:
    forget(vault, rids=[f"{WEEKLY}/summary"])
    started = start.start(vault, [], NOW)
    (given,) = recordings.recordings(vault, started.info.sync, "plaud", AT).given
    text = read(given.files[0])
    assert "summary" not in text and "The crane check." not in text
    assert "@00:00 Ben Sample: The pilot starts on the 28th." in text


def test_a_cut_stretch_shows_as_raw_keeps_it(vault: Vault) -> None:
    recording, transcript, summary = call(CALL, "Sensor quote", CALL_SAID, 19, "transaction_polish")
    cut = [Segment(start_ms=0, end_ms=19_000, text=CUT_MARKER), *(transcript.segments or [])[1:]]
    vault.write_run("plaud", [recording, transcript.model_copy(update={"segments": cut}), summary])
    started = start.start(vault, [], NOW)
    wave = recordings.recordings(vault, started.info.sync, "plaud", AT)
    text = read(next(r for r in wave.given if r.rid == CALL).files[0])
    assert "@00:00 [cut]" in text and "quote and the PO" not in text
    assert "@00:20 Ada Example: Thanks, Cleo." in text


def test_a_long_recording_splits_into_parts_between_lines_and_a_long_line_wraps(vault: Vault) -> None:
    said = [("Ben Sample", f"Point {n}: the pilot's sensors need another look. " * 4) for n in range(600)]
    said[10] = ("Cleo Test", "word " * 500)
    vault.write_run("plaud", call(CALL, "Pilot review", said, 19, "transaction_polish"))
    started = start.start(vault, [], NOW)
    wave = recordings.recordings(vault, started.info.sync, "plaud", AT)
    files = next(r for r in wave.given if r.rid == CALL).files
    assert len(files) > 2
    texts = [read(f) for f in files]
    for n, text in enumerate(texts, start=1):
        assert len(text.encode("utf-8")) <= recordings.PART_BYTES
        assert text.startswith(f"recording {CALL} | Pilot review | 2026-10-19 10:30 Europe/Budapest | part {n} of ")
        assert all(len(line) <= recordings.LINE_CHARS for line in text.splitlines())
    assert texts[1].splitlines()[1].endswith("@<its mm:ss> (continued)")
    joined = [line for text in texts for line in text.splitlines()[1:] if not line.startswith("==")]
    assert sum(1 for line in joined if line.startswith("@")) == 600
    wrapped = joined.index(next(line for line in joined if line.startswith("@03:20 Cleo Test:")))
    assert joined[wrapped + 1].startswith("  word")


def test_the_meetings_by_time_leave_out_a_cancelled_one(vault: Vault) -> None:
    # The vendor call, cancelled, runs 23:30 to 00:30 on the 22nd in Budapest.
    late = [
        r.model_copy(update={"ts": datetime(2026, 10, 22, 21, 35, tzinfo=UTC)})
        for r in call(CALL, "Late", CALL_SAID, 22)
    ]
    vault.write_run("plaud", late)
    started = start.start(vault, [], NOW + timedelta(days=3))
    wave = recordings.recordings(vault, started.info.sync, "plaud", NOW + timedelta(days=3))
    prompt = read(next(r for r in wave.given if r.rid == CALL).prompt)
    assert f"Its meetings by time: {recordings.NONE_BY_TIME}." in prompt


def test_a_recording_in_two_meetings_time_names_both_and_one_with_nothing_under_it_says_so(vault: Vault) -> None:
    review = meeting(40, None, number=5, start="2026-10-19T10:20+02:00", end="2026-10-19T11:00+02:00",
                     subject="Design review", organiser="Gail Example")  # fmt: skip
    vault.commit(write_ops(vault.root, [review]), "A review over the weekly's end")
    bare = make_record(rid=CALL, source="plaud", kind="recording", container=CALL, thread=CALL, text="",
                       ts=datetime(2026, 10, 19, 13, 0, tzinfo=UTC))  # fmt: skip
    vault.write_run("plaud", [bare])
    started = start.start(vault, [], NOW)
    weekly, nothing = recordings.recordings(vault, started.info.sync, "plaud", AT).given
    assert (
        "Its meetings by time: M-0001 | 10-19 10:00-10:30 | Pilot weekly | Ben Sample; M-0005 | 10-19 10:20-11:00 | "
        "Design review | Gail Example. The one its content names takes the minutes and goes on the NOTES line."
        in read(weekly.prompt)
    )
    assert read(nothing.files[0]).splitlines()[1:] == ["nothing of it is in raw but the recording itself"]


def test_a_source_s_list_that_doesn_t_read_is_refused(vault: Vault) -> None:
    started = start.start(vault, [], NOW)
    (started.folder / "recordings" / "plaud.json").write_text("{", encoding="utf-8")
    with pytest.raises(DocketError, match=re.escape("plaud.json doesn't read; start another sync")):
        recordings.recordings(vault, started.info.sync, "plaud", AT)


@pytest.mark.parametrize(
    ("rid", "expected"),
    [
        ("plaud:rec-0101", "rec-0101"),
        ("plaud:0f3a9c", "0f3a9c"),
        ("plaud:Rec 1/a", None),
        ("plaud:" + "a" * 65, None),
        ("plaud:x0123456789abcdef", None),
    ],
)
def test_a_key_is_the_recording_s_own_id_when_a_file_name_can_hold_it(rid: str, expected: str | None) -> None:
    found = recordings.key(rid)
    if expected is None:
        assert re.fullmatch(r"x[0-9a-f]{16}", found) and found == recordings.key(rid)
    else:
        assert found == expected


def test_a_source_not_of_the_sync_is_refused(vault: Vault) -> None:
    started = start.start(vault, ["slack"], NOW)
    with pytest.raises(
        DocketError, match=re.escape(f"plaud isn't a source of {started.info.sync}; its sources: slack")
    ):
        recordings.recordings(vault, started.info.sync, "plaud", AT)


def test_a_recording_whose_agent_wrote_no_block_is_said_by_the_reconcile(vault: Vault) -> None:
    started = start.start(vault, [], NOW)
    for source in ("plaud", "slack"):
        (started.folder / "blocks" / f"{source}.txt").write_text(f"RUN:      {source} | none | -\n", encoding="utf-8")
    (given,) = recordings.recordings(vault, started.info.sync, "plaud", AT).given
    done = reconcile.reconcile(vault, started.info.sync, NOW)
    assert done.said == [f"blocks/plaud/{given.key}.txt isn't there: no block from {WEEKLY}'s agent"]


@pytest.mark.parametrize("written", [f"{WEEKLY} | 10:30 | 11:53 | M-0001", f"{WEEKLY} | 10:30 | 11:53", "- | 10:30"])
def test_a_recording_s_notes_line_with_no_note_id_still_links_the_recording_to_its_meeting(
    vault: Vault, written: str
) -> None:
    """The third rehearsal's recording agent wrote its NOTES line with the rid first and no note id, and the reconcile
    dropped it, the recording's link with it. The block's file names the recording, and its time or the line's M- ref
    the meeting, so the link is drafted and the missing id said (#272)."""
    started = start.start(vault, [], NOW)
    for source in ("plaud", "slack"):
        (started.folder / "blocks" / f"{source}.txt").write_text(f"RUN:      {source} | none | -\n", encoding="utf-8")
    (given,) = recordings.recordings(vault, started.info.sync, "plaud", AT).given
    Path(given.block).write_text(f"NOTES:    {written}\n", encoding="utf-8")
    done = reconcile.reconcile(vault, started.info.sync, NOW)
    ops = [json.loads(line) for line in done.path.read_text(encoding="utf-8").splitlines() if line.startswith("{")]
    assert {"op": "material", "item": "M-0001", "ref": WEEKLY, "label": "recording"} in ops
    assert done.dropped == [] and done.unsure == []
    assert done.said == [
        f"blocks/plaud/{given.key}.txt:1 NOTES: {written}: no note id, so the link is drafted from {WEEKLY}, the "
        "block's recording; the note's id is missing from the line"
    ]


def test_the_command_prints_a_line_per_recording(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The command reads the clock: held at the fixtures' day.
    monkeypatch.setattr(views, "now", lambda: AT)
    started = start.start(vault, [], NOW)
    assert main(["sync", "--vault", str(vault.root), "recordings", started.info.sync, "plaud"]) == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert line == f"recording plaud/rec-0101: {started.folder / 'agents' / 'plaud' / 'rec-0101.md'}"
    assert main(["sync", "recordings", "sync-1", "plaud", "--vault", str(vault.root)]) == 2
    assert "isn't a sync" in capsys.readouterr().err


@pytest.mark.perf
def test_a_recording_of_1500_segments_takes_under_a_second(vault: Vault) -> None:
    said = [("Ben Sample" if n % 2 else "Cleo Test", f"Line {n} about the pilot's sensors.") for n in range(1500)]
    vault.write_run("plaud", call(CALL, "Pilot review", said, 19, "transaction_polish"))
    index_build.update(vault)
    best = float("inf")
    for _ in range(3):
        started = start.start(vault, ["plaud"], NOW)
        began = time.perf_counter()
        wave = recordings.recordings(vault, started.info.sync, "plaud", AT)
        best = min(best, time.perf_counter() - began)
        assert CALL in [r.rid for r in wave.given]
    print(f"docket sync recordings, a recording of 1,500 segments: {best:.2f} s")
    assert best < 1.0


def test_a_note_s_layout_is_the_one_its_source_s_sync_md_gives(
    tmp_path_factory: pytest.TempPathFactory, tmp_path: Path
) -> None:
    vault = sync_vault(sync_template(tmp_path_factory), tmp_path)
    when = datetime(2026, 10, 19, 8, 30, tzinfo=UTC)
    vault.write_run(
        "memo", [make_record(rid="memo:m-1", source="memo", kind="recording", container="memo:m-1", ts=when)]
    )
    index_build.rebuild(vault)
    sync_md = vault.root.parent / ".claude" / "skills" / "sources" / "memo" / "sync.md"
    sync_md.write_text("# memo\n\nA note's layout: the decisions first.\n", encoding="utf-8")
    started = start.start(vault, [], NOW)
    (given,) = recordings.recordings(vault, started.info.sync, "memo", AT).given
    assert f"| the note's layout | the one {sync_md.resolve()} gives for a recording note, else " in read(given.prompt)


# -- the worked rehearsal --------------------------------------------------------------------------------------------


def test_a_rehearsal_of_two_recordings_reads_each_once_and_the_reconcile_drafts_both_blocks(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pilot weekly at 10:30, in M-0001, long enough for two parts, and a call with Cleo at 15:00 in no meeting.
    The Plaud agent's block is its RUN line alone; each recording's agent writes its own block, here as an agent would.
    The weekly's outcome holds Cleo's quote and PO, which also gets its WAITING line; the ask to put the pilot calls in
    the team's calendars names Ben's running item, A-0001, and is drafted unsure with its add beside it."""
    weekly_said = [
        ("Ben Sample", f"Point {n}: the pilot starts on the 28th, with the team on site. " * 3) for n in range(250)
    ]
    weekly_said[1] = ("Cleo Test", "I send the quote and the PO for the site visit by Friday.")
    vault.write_run("plaud", call(WEEKLY, "Pilot weekly", weekly_said, 19, "transaction_polish"))
    call_said = [("Ada Example", "Ben, here are the pilot dates: the 28th to the 30th."), ("Ben Sample", "Thanks.")]
    at_three = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
    vault.write_run(
        "plaud",
        [
            r.model_copy(update={"ts": at_three})
            for r in call(CALL, "Dates for Ben", call_said, 19, "transaction_polish")
        ],
    )
    started = start.start(vault, [], NOW)
    sync = started.folder
    (sync / "blocks" / "plaud.txt").write_text(
        "RUN:      plaud | run-1 | captures 9 | ready 6, pending 0 | skipped 1 | mark moved yes | -\n", encoding="utf-8"
    )
    (sync / "blocks" / "slack.txt").write_text("RUN:      slack | none | no new messages\n", encoding="utf-8")
    wave = recordings.recordings(vault, started.info.sync, "plaud", AT)
    weekly, dates = wave.given
    assert (weekly.rid, dates.rid) == (WEEKLY, CALL)
    assert len(weekly.files) == 2 and len(dates.files) == 1
    assert "M-0001 | 10-19 10:00-10:30 | Pilot weekly" in read(weekly.prompt)
    assert f"Its meetings by time: {recordings.NONE_BY_TIME}." in read(dates.prompt)
    transcript = f"r:{WEEKLY}/transaction_polish"
    Path(weekly.block).write_text(
        "\n".join([
            f"MINUTES:  M-0001 | {POINT} | answered | the 28th, with the team on site | {transcript}@00:00",
            f"OUTCOME:  M-0001 | The pilot starts on the 28th. / Cleo sends the quote and the PO by Friday. | "
            f"{transcript}@00:20",
            f"WAITING:  Cleo Test | {transcript}@00:20 | 2026-10-19 10:30 | the quote and the PO for the site visit | "
            "pilot | - | 2026-10-23",
            f"OWED:     Ben Sample | {transcript}@00:00 | 2026-10-19 10:30 | Put the pilot calls in the team's "
            "calendars | not yet | this week | task | pilot | A-0001 | -",
            f"NOTES:    recording-note:2026-10-19-pilot-weekly | {WEEKLY} | 10:30 | 11:53",
        ]) + "\n",
        encoding="utf-8",
    )  # fmt: skip
    Path(dates.block).write_text(
        f"CLOSES:   A-0001 | done | r:{CALL}/transaction_polish@00:00 | Ada gave Ben the dates on the call\n"
        f"NOTES:    recording-note:2026-10-19-dates-for-ben | {CALL} | 15:00 | 15:01\n",
        encoding="utf-8",
    )
    done = reconcile.reconcile(vault, started.info.sync, NOW)
    lines = done.path.read_text(encoding="utf-8").splitlines()
    found = [json.loads(line) for line in lines if not line.startswith("#")]
    by_op = [(op["op"], op.get("item")) for op in found]
    assert by_op == [
        ("done", "A-0001"), ("add", None), ("log", "A-0001"), ("material", "M-0001"), ("point_set", "M-0001"),
        ("set", "M-0001"), ("rollover", None), ("archive", None),
    ]  # fmt: skip
    close, wait, calendars = found[:3]
    assert close["note"] == f"r:{CALL}/transaction_polish@00:00"
    assert wait["fields"]["title"] == "the quote and the PO for the site visit" and "unsure" not in wait
    assert calendars["unsure"].startswith("it continues A-0001, but its title doesn't look like that item's")
    k = lines.index(json.dumps(calendars, ensure_ascii=False))
    assert lines[k - 2] == (
        f"# ? blocks/plaud/rec-0101.txt:4 OWED: Ben Sample | {transcript}@00:00 | 2026-10-19 10:30 | Put the pilot "
        "calls in the team's calendars | not yet | this week | task | pilot | A-0001 | -"
    )
    assert lines[k + 1].startswith('# {"op": "add", "fields": {"kind": "task", "title": "Put the pilot calls')
    assert found[3] == {"op": "material", "item": "M-0001", "ref": WEEKLY, "label": "recording"}
    assert found[5]["fields"] == {
        "outcome": "The pilot starts on the 28th.\nCleo sends the quote and the PO by Friday."
    }
    assert done.unsure == [k + 1]
    assert done.said == [
        f"blocks/plaud/rec-0103.txt:2 NOTES: recording-note:2026-10-19-dates-for-ben | {CALL} | 15:00 | 15:01: no "
        "meeting 10 minutes either side of its start, so no link"
    ]
    # Settled as Claude would: the calendars are new work, so the add takes the log's place.
    settled = [*lines[:k], lines[k + 1][2:], *lines[k + 2 :]]
    done.path.write_text("\n".join(settled) + "\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["ledger", "batch", str(done.path), "--by", "sync", "--dry-run", "--vault", str(vault.root)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "dry run: 7 op(s) would be written; nothing written"
