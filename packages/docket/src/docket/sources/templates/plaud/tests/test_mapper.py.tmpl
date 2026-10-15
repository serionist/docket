"""The Plaud mapper on its own: plain JSON in, plain JSON out, no Docket needed."""

from __future__ import annotations

import json
from types import ModuleType
from typing import Any

from plaud_payloads import (
    CALL,
    CALL_PAGE_1,
    CALL_PAGE_2,
    HELD,
    PRIVATE_TITLE,
    SHORT,
    UNREADY,
    Run,
    live_call,
    meta,
    segment,
    structured,
)


def by_rid(output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["rid"]: r for r in output["records"]}


def test_results_are_read_from_structured_content_or_the_first_json_in_their_text(mapper: ModuleType) -> None:
    payload = {"files": [1]}
    assert mapper.unwrap(structured(payload)) == payload
    texts = [{"type": "text", "text": 'Plaud says: {"files": [1]} and that is all.'}]
    assert mapper.unwrap({"content": texts}) == payload
    assert mapper.unwrap(texts) == payload
    assert mapper.unwrap("preamble [1, 2] trailing") == [1, 2]
    assert mapper.unwrap("no json here { at all") is None
    assert mapper.unwrap(payload) == payload
    assert mapper.unwrap(None) is None


def test_a_live_run_gives_three_records_under_the_name_plaud_gave_with_its_notes(mapper: ModuleType) -> None:
    out = mapper.map_run(live_call(Run()).payload())
    records = by_rid(out)
    rid = f"plaud:{CALL}"
    assert set(records) == {rid, f"{rid}/transaction_polish", f"{rid}/summary"}
    for record in records.values():
        assert (record["container"], record["thread"], record["source"]) == (rid, rid, "plaud")
        # Zone-less times are UTC.
        assert record["ts"] == "2026-10-14T09:30:00Z"
    recording = records[rid]
    assert recording["title"] == "Supplier call"
    assert recording["kind"] == "recording"
    assert sorted(recording["native"]) == ["created_at", "duration", "id", "name", "serial_number", "start_at"]
    transcript = records[f"{rid}/transaction_polish"]
    assert transcript["engine"] == "plaud/transaction_polish"
    assert "text" not in transcript
    assert [(s["start_ms"], s["speaker"], s["text"]) for s in transcript["segments"]] == [
        (0, "Ben Sample", "Morning, Cleo."),
        (3000, "Cleo Test", "Morning. Shall we start with the price?"),
        (728_000, "Ben Sample", "So ten million, for the whole quarter."),
    ]
    assert len(transcript["native"]["segments"]) == 3
    assert "data_link" not in json.dumps(out)
    summary = records[f"{rid}/summary"]
    # Tabs by type, then title: the order Plaud shows them in can't make a new version.
    assert summary["text"] == "## Summary\n\nPrice agreed.\n\n## Action items\n\n- Cleo sends it"
    assert summary["engine"] == "plaud"
    assert out["waiting"] == []
    assert out["set_aside"] == []


def test_signed_links_go_at_any_depth(mapper: ModuleType) -> None:
    detail = meta(SHORT, "Quick sync", presigned_url="https://example.invalid/audio?token=x")
    detail["source_list"] = [{"data_type": "transaction", "data_link": "https://example.invalid/x", "data_path": "a/b"}]
    out = mapper.map_run(Run(final=True).add("get_file", structured(detail), file_id=SHORT).payload())
    assert [r["kind"] for r in out["records"]] == ["recording"]
    assert "example.invalid" not in json.dumps(out)
    assert mapper.scrub({"a": [{"presigned_url": 1, "b": {"data_path": 2, "c": 3}}]}) == {"a": [{"b": {"c": 3}}]}


def test_a_recording_waits_for_its_notes_and_is_stored_once_under_the_new_name(mapper: ModuleType) -> None:
    first = mapper.map_run(
        Run()
        .listing(meta(SHORT, "Recording 2026-10-14"))
        .add("get_file", structured(meta(SHORT, "Recording 2026-10-14")), file_id=SHORT)
        .payload()
    )
    assert first["records"] == []
    assert first["waiting"] == [{"id": SHORT, "for": "summary"}]
    later = Run().listing(meta(SHORT, "Recording 2026-10-14")).note(SHORT).listing(meta(SHORT, "Quick sync"))
    records = by_rid(mapper.map_run(later.payload()))
    assert records[f"plaud:{SHORT}"]["title"] == "Quick sync"


def test_a_recording_only_listed_waits_to_be_fetched_inside_the_window_and_gives_nothing_outside(
    mapper: ModuleType,
) -> None:
    listed = meta(SHORT, "Recording 2026-10-14")
    inside = {"since": "2026-10-08T00:00:00Z", "until": "2026-10-15T09:12:00Z"}
    outside = {"since": "2026-10-15T00:00:00Z", "until": "2026-10-15T09:12:00Z"}
    assert mapper.map_run(Run(window=inside).listing(listed).payload())["waiting"] == [{"id": SHORT, "for": "fetch"}]
    for run in (Run(window=outside), Run(), Run(window={"since": None, "until": "2026-10-13T00:00:00Z"})):
        out = mapper.map_run(run.listing(listed).payload())
        assert (out["records"], out["waiting"]) == ([], [])


def test_a_final_run_stores_what_it_fetched_by_id_and_nothing_it_only_listed(mapper: ModuleType) -> None:
    fetched = Run(final=True).add("get_file", structured(meta(SHORT, "Recording 2026-10-14")), file_id=SHORT)
    out = mapper.map_run(fetched.payload())
    assert [(r["kind"], r["title"]) for r in out["records"]] == [("recording", "Recording 2026-10-14")]
    assert out["waiting"] == []
    window = {"since": "2026-10-08T00:00:00Z", "until": "2026-10-15T09:12:00Z"}
    listed = mapper.map_run(Run(final=True, window=window).listing(meta(CALL, "Supplier call")).payload())
    assert listed["records"] == []


def test_an_untranscribed_recording_stages_nothing_until_its_transcript_comes(mapper: ModuleType) -> None:
    run = (
        Run()
        .listing(meta(UNREADY, "Recording 2026-10-14"))
        .transcript(UNREADY, [], result=structured([]))
        .transcript(UNREADY, [], block="transaction")
    )
    out = mapper.map_run(run.payload())
    assert out["records"] == []
    assert {"id": UNREADY, "for": "transcript"} in out["waiting"]
    ready = Run().listing(meta(UNREADY, "Standup")).transcript(UNREADY, CALL_PAGE_2)
    kinds = [r["kind"] for r in mapper.map_run(ready.payload())["records"]]
    assert kinds == ["transcript"]
    final = Run(final=True).listing(meta(UNREADY, "Standup")).transcript(UNREADY, [], result=structured([]))
    assert [r["kind"] for r in mapper.map_run(final.payload())["records"]] == ["recording"]


def test_a_broken_page_chain_is_set_aside_never_staged(mapper: ModuleType) -> None:
    run = Run().listing(meta(CALL, "Supplier call")).transcript(CALL, CALL_PAGE_1, next_cursor="c2").note(CALL)
    out = mapper.map_run(run.payload())
    assert {"id": CALL, "reason": "broken-chain", "tool": "get_transcript", "key": "transaction_polish"} in out[
        "set_aside"
    ]
    assert [r["kind"] for r in out["records"]] == ["vendor-summary", "recording"]


def test_the_raw_block_stands_in_when_the_polished_one_is_empty(mapper: ModuleType) -> None:
    run = (
        Run(final=True)
        .listing(meta(CALL, "Supplier call"))
        .transcript(CALL, [])
        .transcript(CALL, CALL_PAGE_2, block="transaction")
    )
    rids = [r["rid"] for r in mapper.map_run(run.payload())["records"]]
    assert f"plaud:{CALL}/transaction" in rids


def test_a_held_recording_is_never_parsed_or_named(mapper: ModuleType) -> None:
    run = (
        Run()
        .listing(meta(HELD, PRIVATE_TITLE), meta(SHORT, "Quick sync"))
        .add("get_transcript", "not json at all, and private", file_id=HELD)
        .note(HELD)
        .backfill(HELD)
    )
    out = mapper.map_run(run.payload(holds=[HELD]))
    assert out["held"] == [HELD]
    text = json.dumps({k: v for k, v in out.items() if k != "held"})
    assert HELD not in text
    assert PRIVATE_TITLE not in text
    assert out["waiting"] == []


def test_what_isn_t_usable_is_set_aside_with_a_token_never_a_title(mapper: ModuleType) -> None:
    run = (
        Run()
        .listing(
            {"id": PRIVATE_TITLE, "name": PRIVATE_TITLE}, "not an item", meta(SHORT, "Quick sync"), meta(CALL, "Call")
        )
        .add("get_note", structured({"notes": []}), file_id="../escape")
        .add("get_note", "no json here", file_id=SHORT)
        .add("get_note", structured({"nothing": "here"}), file_id=SHORT)
        .add("get_file", structured([1, 2]), file_id=SHORT)
        .add("list_files", structured({"total": 0}))
        .add("record_audio", structured({}))
        .add(None, ["not", "a", "backfill"], file="x.json")
        .listing(meta(UNREADY, "Standup", start_at="not a time"))
        .transcript(SHORT, [segment(5000, 1000, "Ben Sample", "backwards")])
        .transcript(CALL, ["not a segment"])
    )
    out = mapper.map_run(run.payload())
    reasons = [(e["id"], e["reason"]) for e in out["set_aside"]]
    assert reasons == [
        (None, "bad-id"),
        (None, "bad-id"),
        (None, "bad-id"),
        (SHORT, "no-json"),
        (SHORT, "no-tabs"),
        (SHORT, "not-an-object"),
        (None, "no-listing"),
        (None, "unknown-tool"),
        (None, "not-a-backfill-file"),
        # Then each recording, in id order.
        (SHORT, "bad-segment"),
        (CALL, "bad-segment"),
        (UNREADY, "no-meta"),
    ]
    assert PRIVATE_TITLE not in json.dumps(out)


def test_the_tab_rule_matches_the_backfill_script(mapper: ModuleType) -> None:
    tabs = [
        {"data_type": "a", "data_title": "", "data_tab_name": "", "data_content": "x"},
        {"data_type": "b", "data_title": None, "data_tab_name": "Named", "data_content": "y"},
        {"data_type": "c", "data_title": "Titled", "data_tab_name": "Named", "data_content": "z"},
        {"data_type": "d", "data_content": ""},
        "not a tab",
    ]
    assert mapper._note_tabs(tabs) == [("a", "Summary", "x"), ("b", "Named", "y"), ("c", "Titled", "z")]
    assert mapper._backfill_tabs([{"type": "a", "title": "", "content": "x"}, {"content": " "}, 3]) == [
        ("a", "Summary", "x")
    ]
    assert mapper._backfill_tabs(None) == []


def test_a_backfill_file_is_final_and_takes_its_start_from_a_listing(mapper: ModuleType) -> None:
    run = (
        Run().listing(meta(CALL, "Supplier call"), meta(SHORT, "Quick sync")).backfill(CALL).backfill(SHORT, block=None)
    )
    run.backfill(UNREADY)
    out = mapper.map_run(run.payload())
    kinds = sorted((r["rid"].removeprefix("plaud:"), r["kind"], r["via"]) for r in out["records"])
    assert kinds == [
        (SHORT, "recording", "plaud-mcp"),
        (f"{SHORT}/summary", "vendor-summary", "plaud-file"),
        (CALL, "recording", "plaud-mcp"),
        (f"{CALL}/summary", "vendor-summary", "plaud-file"),
        (f"{CALL}/transaction_polish", "transcript", "plaud-file"),
    ]
    assert out["set_aside"] == [{"id": UNREADY, "reason": "no-meta", "tool": None, "key": "start_at"}]
    assert out["waiting"] == []


def test_times_with_a_zone_and_in_milliseconds(mapper: ModuleType) -> None:
    assert mapper.parse_time("2026-10-14T11:30:00+02:00") == "2026-10-14T09:30:00Z"
    assert mapper.parse_time("2026-10-14T09:30:00Z") == "2026-10-14T09:30:00Z"
    assert mapper.parse_time(1_791_970_200_000) == "2026-10-14T09:30:00Z"
    assert mapper.parse_time(True) is None
    assert mapper.parse_time(None) is None
    assert mapper.first_json("[unclosed {also") is None


def test_a_page_of_100_utterances_stays_under_claude_codes_file_threshold() -> None:
    # A long utterance, in Plaud's segment shape: under 50,000 characters a page, Claude Code hands the hook the result
    # itself rather than a note about a file it saved it to.
    utterance = "We agreed the supplier ships the invented parts on Friday, and Ben Sample checks the invoice. " * 3
    page = {
        "file_id": CALL,
        "block": "transaction_polish",
        "segments": [segment(n * 4000, n * 4000 + 3900, "Ada Example", utterance) for n in range(100)],
        "next_cursor": "c-next-page-0001",
    }
    assert len(json.dumps(page)) < 50_000
