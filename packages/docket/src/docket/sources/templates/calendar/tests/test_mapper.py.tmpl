"""The calendar mapper on its own: plain JSON in, plain JSON out, no Docket needed. Every payload is invented, in the
shape scripts/calendar-week.js returns."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from types import ModuleType
from typing import Any

from calendar_payloads import (
    BEN,
    DORA,
    EVENTS,
    NEXT_WEEK,
    OFFSITE,
    PLANNING,
    REVIEW,
    REVIEW_MOVED,
    STANDUP,
    STANDUP_MON,
    STANDUP_TUE,
    SYNC_FRI,
    SYNC_FRI_CANCELLED,
    TEAMS,
    VERSION,
    WEEK,
    Event,
    Run,
    hooked,
    live_week,
    occurrence_id,
    rest,
    single_id,
    week,
)


def by_rid(output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["rid"]: r for r in output["records"]}


def rid(e: Event) -> str:
    return f"calendar:{rest(e.id)}"


def one(mapper: ModuleType, *events: Event) -> dict[str, dict[str, Any]]:
    return by_rid(mapper.map_run(Run().script(week(*events)).payload()))


# -- the result -------------------------------------------------------------------------------------------------------


def test_the_script_s_strings_are_found_in_every_shape_a_result_can_take(mapper: ModuleType) -> None:
    strings = week(STANDUP_MON)
    assert mapper.strings(hooked(strings)) == strings
    assert mapper.strings({"content": hooked(strings)}) == strings
    assert mapper.strings({"structuredContent": strings}) == strings
    assert mapper.strings(strings) == strings
    assert mapper.strings(json.dumps(strings) + "\n\nTab Context: [101]") == strings
    assert mapper.strings(json.dumps(json.dumps(strings))) == strings
    assert mapper.strings([{"type": "text", "text": "Result: " + json.dumps(strings, ensure_ascii=True)}]) == strings


def test_a_javascript_result_without_the_version_line_is_passed_over_without_a_word(mapper: ModuleType) -> None:
    run = Run().click().click(["docket:calendar-week/2", "week\t..."]).add("javascript_tool", hooked(42))
    out = mapper.map_run(run.add("javascript_tool", "undefined").payload())
    assert out == {"contract": 1, "records": [], "set_aside": [], "waiting": [], "held": []}


def test_a_file_or_another_tool_is_set_aside(mapper: ModuleType) -> None:
    out = mapper.map_run(Run().add(None, "x", file="week.json").add("get_page_text", "text").payload())
    assert [(s["reason"], s["key"]) for s in out["set_aside"]] == [("not-a-read", "file"), ("unknown-tool", None)]


# -- one record per occurrence ---------------------------------------------------------------------------------------


def test_a_week_gives_one_record_per_event_in_the_core_s_shape(mapper: ModuleType) -> None:
    out = mapper.map_run(live_week(Run()).payload())
    records = by_rid(out)
    assert sorted(records) == sorted(rid(e) for e in EVENTS)
    assert out["set_aside"] == [] and out["waiting"] == [] and out["held"] == []
    standup = records[rid(STANDUP_MON)]
    assert standup == {
        "rid": rid(STANDUP_MON),
        "source": "calendar",
        "kind": "event",
        "thread": f"calendar:{rest(single_id(STANDUP))}",
        "ts": "2026-10-19T07:00:00Z",
        "author": "Rita Sample <rita@example.invalid>",
        "title": "Test bench standup",
        "text": f"9:00 to 9:30, Monday, October 19, 2026\nWhere: {TEAMS}\nResponse: none",
        "native": {
            "id": STANDUP_MON.id,
            "item": "occurrence",
            "slot": "2026-10-19",
            "show_as": "Tentative",
            "all_day": False,
            "end": "2026-10-19T07:30:00Z",
            "response_type": "NoResponseReceived",
            "label": STANDUP_MON.label(),
            "week": WEEK,
            "zone": "Europe/Budapest",
        },
        "fetched": "2026-10-18T16:00:00Z",
        "via": "owa-week-view",
        "fidelity": "verbatim",
    }


def test_rids_hold_the_id_in_graph_s_rest_form(mapper: ModuleType) -> None:
    records = one(mapper, *EVENTS)
    assert any(set(e.id) & {"+", "/"} for e in EVENTS)
    for r in records.values():
        assert "/" not in r["rid"] and "+" not in r["rid"]
    assert mapper.rest_id("AAkA+b/c==") == "AAkA-b_c=="
    assert mapper.rest_id("not an id") is None


def test_each_occurrence_of_a_series_has_its_own_rid_and_the_series_as_thread(mapper: ModuleType) -> None:
    records = one(mapper, STANDUP_MON, STANDUP_TUE)
    assert len(records) == 2
    assert {r["thread"] for r in records.values()} == {f"calendar:{rest(single_id(STANDUP))}"}
    assert [r["native"]["slot"] for r in sorted(records.values(), key=lambda r: r["ts"])] == [
        "2026-10-19",
        "2026-10-20",
    ]


def test_a_moved_occurrence_keeps_the_rid_of_its_slot(mapper: ModuleType) -> None:
    before = replace(
        REVIEW_MOVED,
        when="14:00 to 15:00, Tuesday, October 20, 2026",
        start="2026-10-20T12:00:00Z",
        end="2026-10-20T13:00:00Z",
        kind="Recurring event",
    )
    first, moved = one(mapper, before)[rid(before)], one(mapper, REVIEW_MOVED)[rid(REVIEW_MOVED)]
    assert first["rid"] == moved["rid"]
    assert (first["ts"], moved["ts"]) == ("2026-10-20T12:00:00Z", "2026-10-21T12:00:00Z")
    assert moved["native"]["slot"] == "2026-10-20" and moved["native"]["item"] == "exception"
    assert moved["thread"] == f"calendar:{rest(single_id(REVIEW))}"


def test_a_single_event_has_no_thread_and_no_slot(mapper: ModuleType) -> None:
    planning = one(mapper, PLANNING)[rid(PLANNING)]
    assert planning["thread"] is None
    assert "slot" not in planning["native"]
    assert mapper.occurrence(single_id("x")) == (None, None)
    assert mapper.occurrence("not base64!") == (None, None)


def test_the_series_id_is_rebuilt_from_an_occurrence_s_own_id(mapper: ModuleType) -> None:
    assert mapper.occurrence(occurrence_id(REVIEW, date(2026, 10, 20))) == (rest(single_id(REVIEW)), "2026-10-20")


# -- the label ---------------------------------------------------------------------------------------------------------


def test_an_organiser_with_a_comma_an_own_event_an_icon_and_two_places(mapper: ModuleType) -> None:
    records = one(mapper, REVIEW_MOVED, PLANNING)
    review = records[rid(REVIEW_MOVED)]
    assert review["author"] == f"{DORA[0]} <{DORA[1]}>"
    assert review["text"].endswith("Response: accepted")
    planning = records[rid(PLANNING)]
    assert planning["title"] == "Q4 planning - draft, v2"
    assert planning["author"] == f"{BEN[0]} <{BEN[1]}>"
    assert (
        planning["text"]
        == f"10:00 to 12:00, Thursday, October 22, 2026\nWhere: Room 4.12; {TEAMS}\nResponse: organiser"
    )
    assert planning["native"]["item"] == "single" and planning["native"]["show_as"] == "Busy"


def test_an_all_day_event_over_two_days(mapper: ModuleType) -> None:
    offsite = one(mapper, OFFSITE)[rid(OFFSITE)]
    assert offsite["ts"] == "2026-10-21T22:00:00Z"
    assert offsite["text"] == "all day event, Thursday, October 22, 2026 to Friday, October 23, 2026\nResponse: none"
    assert offsite["native"]["all_day"] is True and offsite["native"]["end"] == "2026-10-23T22:00:00Z"


def test_the_label_s_parts(mapper: ModuleType) -> None:
    parse = mapper.parse_label
    label = parse(REVIEW_MOVED.label())
    assert (label.subject, label.place, label.organiser, label.show_as, label.kind) == (
        "Sensor rig review", TEAMS, DORA[0], "Busy", "exception"
    )  # fmt: skip
    own = parse("Focus time, 11:00 to 12:00, Tuesday, October 13, 2026, Busy")
    assert (own.subject, own.place, own.organiser, own.show_as, own.kind) == (
        "Focus time",
        None,
        None,
        "Busy",
        "single",
    )
    twelve = parse("Call, 9:00 AM to 9:30 AM, Monday, October 19, 2026, By Rita Sample, Tentative")
    assert twelve is not None and twelve.when == "9:00 AM to 9:30 AM, Monday, October 19, 2026"
    assert parse("Nothing like a calendar label") is None
    assert parse("Odd, 9:00 to 9:30, Monday, October 19, 2026and more") is None


def test_a_label_of_another_shape_is_set_aside_by_its_id(mapper: ModuleType) -> None:
    odd = replace(STANDUP_MON, when="Monday 9:00 for half an hour")
    out = mapper.map_run(Run().script(week(odd)).payload())
    assert out["records"] == []
    assert out["set_aside"] == [{"id": rest(odd.id), "reason": "bad-label", "tool": "javascript_tool", "key": "label"}]


# -- what makes a new version, and what doesn't ------------------------------------------------------------------------


def test_a_cancellation_is_the_same_rid_with_its_title_and_text_changed(mapper: ModuleType) -> None:
    before, after = one(mapper, SYNC_FRI)[rid(SYNC_FRI)], one(mapper, SYNC_FRI_CANCELLED)[rid(SYNC_FRI)]
    assert after["title"] == "Canceled: Weekly test sync" != before["title"]
    assert after["text"] == before["text"] + "\nCancelled"


def test_a_new_response_changes_the_text_and_show_as_alone_doesn_t(mapper: ModuleType) -> None:
    base = one(mapper, STANDUP_MON)[rid(STANDUP_MON)]
    accepted = one(mapper, replace(STANDUP_MON, response="Accept", show_as="Busy"))[rid(STANDUP_MON)]
    assert accepted["text"].endswith("Response: accepted") and accepted["text"] != base["text"]
    shown_busy = one(mapper, replace(STANDUP_MON, show_as="Busy"))[rid(STANDUP_MON)]
    assert (shown_busy["text"], shown_busy["title"]) == (base["text"], base["title"])
    assert shown_busy["native"]["show_as"] == "Busy"


def test_a_past_day_s_label_without_its_place_reads_the_same_from_the_event_s_data(mapper: ModuleType) -> None:
    future = one(mapper, STANDUP_MON)[rid(STANDUP_MON)]
    past = one(mapper, replace(STANDUP_MON, past=True))[rid(STANDUP_MON)]
    assert TEAMS not in past["native"]["label"]
    assert (past["text"], past["author"], past["title"], past["ts"]) == (
        future["text"], future["author"], future["title"], future["ts"]
    )  # fmt: skip


def test_without_the_event_s_data_the_label_alone_gives_a_record(mapper: ModuleType) -> None:
    bare = one(mapper, replace(STANDUP_MON, data=False))[rid(STANDUP_MON)]
    assert bare["author"] == "Rita Sample"
    assert bare["text"] == f"9:00 to 9:30, Monday, October 19, 2026\nWhere: {TEAMS}"
    assert bare["ts"] == "2026-10-19T07:00:00Z"


def test_never_a_join_link(mapper: ModuleType) -> None:
    linked = replace(STANDUP_MON, place=f"{TEAMS} https://teams.example.invalid/l/meetup-join/abc")
    record = one(mapper, linked)[rid(STANDUP_MON)]
    assert "http" not in record["text"]


# -- no ids on the page ------------------------------------------------------------------------------------------------


def test_without_an_id_the_rid_is_derived_and_survives_a_cancellation_but_not_a_move(mapper: ModuleType) -> None:
    bare = replace(SYNC_FRI, id="")
    first = one(mapper, bare)
    (key,) = first
    assert key.startswith("calendar:d-") and len(key) == len("calendar:d-") + 24
    assert first[key]["fidelity"] == "derived" and first[key]["thread"] is None and "id" not in first[key]["native"]
    assert set(one(mapper, replace(SYNC_FRI_CANCELLED, id=""))) == {key}
    moved = replace(bare, start="2026-10-23T12:30:00Z", end="2026-10-23T13:15:00Z")
    assert set(one(mapper, moved)) != {key}


# -- a run -------------------------------------------------------------------------------------------------------------


def test_an_event_read_twice_in_a_run_is_emitted_once_from_the_latest_read(mapper: ModuleType) -> None:
    run = Run().script(week(SYNC_FRI)).click().script(week(SYNC_FRI_CANCELLED, range_=NEXT_WEEK))
    out = mapper.map_run(run.payload())
    assert [r["title"] for r in out["records"]] == ["Canceled: Weekly test sync"]
    assert out["records"][0]["fetched"] == "2026-10-18T16:02:00Z"


def test_a_held_occurrence_or_a_held_series_is_skipped_unread(mapper: ModuleType) -> None:
    held_one = mapper.map_run(Run().script(week(STANDUP_MON, STANDUP_TUE)).payload([rest(STANDUP_MON.id)]))
    assert [r["rid"] for r in held_one["records"]] == [rid(STANDUP_TUE)]
    assert held_one["held"] == [rest(STANDUP_MON.id)]
    series = rest(single_id(STANDUP))
    held_series = mapper.map_run(Run().script(week(STANDUP_MON, STANDUP_TUE, PLANNING)).payload([series]))
    assert [r["rid"] for r in held_series["records"]] == [rid(PLANNING)]
    assert held_series["held"] == [series]


def test_a_long_line_goes_on_in_plus_strings_and_joins(mapper: ModuleType) -> None:
    long = replace(PLANNING, subject=("Q4 planning " + "and a very long agenda " * 40).strip())
    strings = week(long)
    assert [s.split("\t", 1)[0] for s in strings[2:]] == ["ev", "+"]
    assert all(len(s) <= 910 for s in strings)
    record = by_rid(mapper.map_run(Run().script(strings).payload()))[rid(long)]
    assert record["title"] == long.subject


def test_strings_chrome_blocked_or_cut_set_their_event_aside(mapper: ModuleType) -> None:
    long = replace(PLANNING, subject=("Q4 planning " + "and a very long agenda " * 40).strip())
    blocked_piece = week(long, STANDUP_MON)
    blocked_piece[3] = "[BLOCKED: Cookie/query string data]"
    out = mapper.map_run(Run().script(blocked_piece).payload())
    assert [r["rid"] for r in out["records"]] == [rid(STANDUP_MON)]
    assert out["set_aside"] == [{"id": rest(long.id), "reason": "blocked", "tool": "javascript_tool", "key": "line"}]
    whole = week(STANDUP_MON, STANDUP_TUE)
    whole[2] = "[BLOCKED: Base64 encoded data]"
    whole.append("[TRUNCATED: 3 more items]")
    out = mapper.map_run(Run().script(whole).payload())
    assert [r["rid"] for r in out["records"]] == [rid(STANDUP_TUE)]
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(None, "blocked"), (None, "cut-short")]
    cut = week(long)
    cut[3] = cut[3][:500] + " [TRUNCATED]"
    out = mapper.map_run(Run().script(cut).payload())
    assert out["records"] == [] and [(s["id"], s["reason"]) for s in out["set_aside"]] == [(rest(long.id), "cut-short")]


def test_a_later_part_and_its_more_line(mapper: ModuleType) -> None:
    run = Run().script(week(STANDUP_MON, more=1)).script(week(STANDUP_TUE, start=1))
    assert sorted(by_rid(mapper.map_run(run.payload()))) == sorted([rid(STANDUP_MON), rid(STANDUP_TUE)])


def test_a_header_or_line_that_doesn_t_read_is_set_aside(mapper: ModuleType) -> None:
    out = mapper.map_run(Run().script([VERSION, "week\tbroken"]).payload())
    assert [(s["reason"], s["key"]) for s in out["set_aside"]] == [("bad-header", "week")]
    short = week(STANDUP_MON)
    short[2] = "ev\t1\t" + "\t".join(["x"] * 3)
    out = mapper.map_run(Run().script([*short, "something else"]).payload())
    assert [(s["reason"], s["key"]) for s in out["set_aside"]] == [("bad-line", "line"), ("bad-line", "line")]
    no_start = replace(STANDUP_MON, start="", when="sometime")
    out = mapper.map_run(Run().script(week(no_start)).payload())
    assert [s["reason"] for s in out["set_aside"]] == ["bad-label"]
    no_start = replace(STANDUP_MON, start="")
    out = mapper.map_run(Run().script(week(no_start)).payload())
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(rest(STANDUP_MON.id), "no-start")]
