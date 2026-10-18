"""The Outlook mail mapper on its own: plain JSON in, plain JSON out, no Docket needed. Every payload is invented, in
the shape scripts/mail-read.js and scripts/mail-rows.js return."""

from __future__ import annotations

import json
from dataclasses import replace
from types import ModuleType
from typing import Any

from outlook_payloads import (
    ALL,
    ANSWER,
    ASK,
    BEN,
    DORA,
    FOLDERS,
    FORWARD,
    JUNK,
    LONG,
    RITA,
    SETTINGS,
    SHOWN,
    THREAD,
    VERSION,
    Run,
    conversation_id,
    esc,
    hooked,
    item_id,
    live_run,
    read,
    rid,
    rows,
    unread_ids,
)


def by_rid(output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["rid"]: r for r in output["records"]}


def mapped(mapper: ModuleType, *reads: list[str], holds: list[str] | None = None) -> dict[str, Any]:
    run = Run()
    for strings in reads:
        run.script(strings)
    return mapper.map_run(run.payload(holds))


# -- the result -------------------------------------------------------------------------------------------------------


def test_the_script_s_strings_are_found_in_every_shape_a_result_can_take(mapper: ModuleType) -> None:
    strings = read(ASK)
    assert mapper.strings(hooked(strings)) == strings
    assert mapper.strings({"content": hooked(strings)}) == strings
    assert mapper.strings({"structuredContent": strings}) == strings
    assert mapper.strings(strings) == strings
    assert mapper.strings(json.dumps(strings) + "\n\nTab Context: [101]") == strings
    assert mapper.strings(json.dumps(json.dumps(strings))) == strings
    assert mapper.strings([{"type": "text", "text": "Result: " + json.dumps(strings, ensure_ascii=True)}]) == strings


def test_rows_snippets_and_results_without_a_known_version_line_are_passed_over_without_a_word(
    mapper: ModuleType,
) -> None:
    run = Run().snippet(("searched", "scope all folders", "rows 2")).script(rows(*ALL)).snippet()
    run.snippet(["docket:mail-read/2", "conv\t..."]).add("javascript_tool", hooked(42))
    out = mapper.map_run(run.add("javascript_tool", "undefined").payload())
    assert out == {"contract": 1, "records": [], "set_aside": [], "waiting": [], "held": []}


def test_a_file_or_another_tool_is_set_aside(mapper: ModuleType) -> None:
    out = mapper.map_run(Run().add(None, "x", file="mail.json").add("get_page_text", "text").payload())
    assert [(s["reason"], s["key"]) for s in out["set_aside"]] == [("not-a-read", "file"), ("unknown-tool", None)]


# -- one record per message ------------------------------------------------------------------------------------------


def test_a_run_gives_one_record_per_message_in_the_core_s_shape(mapper: ModuleType) -> None:
    out = mapper.map_run(live_run(Run()).payload())
    records = by_rid(out)
    assert sorted(records) == sorted(rid(m) for m in ALL)
    assert out["set_aside"] == [] and out["waiting"] == [] and out["held"] == []
    ask = item_id(1).replace("+", "-").replace("/", "_")
    thread = conversation_id(1).replace("+", "-").replace("/", "_")
    assert records[rid(ASK)] == {
        "rid": f"outlook:{ask}",
        "source": "outlook",
        "kind": "mail",
        "thread": f"outlook:{thread}",
        "ts": "2026-10-19T07:58:12Z",
        "author": "Rita Sample <rita.sample@example.com>",
        "to": ["Ben Sample <ben.sample@example.com>", "Dóra Minta <dora.minta@example.com>"],
        "title": "Torque rig: slot on Tuesday?",
        "text": "Hi Ben,\n\nCan we have the rig on Tuesday from 10?\n\nThanks,\nRita",
        "attachments": [],
        "native": {
            "id": ask,
            "conversation": thread,
            "message_id": "<msg1@example.com>",
            "sent": "2026-10-19T07:58:12Z",
            "received": "2026-10-19T07:58:12Z",
            "folder": "Inbox",
            "folder_type": "inbox",
            "flags": ["body:own"],
            "read_as": "conversation",
        },
        # Read by the search and again from Deleted Items: one record, from the later read.
        "fetched": "2026-10-19T16:18:00Z",
        "via": "owa-mail",
        "fidelity": "verbatim",
    }


def test_ids_are_url_safe_and_the_conversation_is_the_thread(mapper: ModuleType) -> None:
    records = by_rid(mapped(mapper, read(*THREAD)))
    assert "+" in item_id(1) and "/" in item_id(1) and conversation_id(1).endswith("=")
    for m in THREAD:
        r = records[rid(m)]
        assert "+" not in r["rid"] and "/" not in r["rid"]
        assert r["thread"] == f"outlook:{m.thread}" and r["thread"].endswith("=")
    assert records[rid(ASK)]["thread"] == records[rid(ANSWER)]["thread"]


def test_the_author_and_to_hold_names_with_addresses_bcc_and_on_behalf_go_in_native(mapper: ModuleType) -> None:
    answer = by_rid(mapped(mapper, read(*THREAD)))[rid(ANSWER)]
    assert answer["author"] == "Ben Sample <ben.sample@example.com>"
    assert answer["to"] == ["Rita Sample <rita.sample@example.com>", "Dóra Minta <dora.minta@example.com>"]
    assert answer["native"]["folder_type"] == "sentitems"
    assert answer["native"]["flags"] == ["body:own", "quoted:thread", "copies:1"]
    settings = by_rid(mapped(mapper, read(SETTINGS)))[rid(SETTINGS)]
    assert settings["to"] == ["Ben Sample <ben.sample@example.com>"]
    assert settings["native"]["bcc"] == ["Dóra Minta <dora.minta@example.com>"]
    assert settings["native"]["sender"] == "Lab Team <lab@example.com>"


def test_attachments_go_in_by_name_only_with_their_type_and_size_in_native(mapper: ModuleType) -> None:
    forward = by_rid(mapped(mapper, read(FORWARD)))[rid(FORWARD)]
    assert forward["attachments"] == [
        {"name": "Quote 2026-114.pdf", "kind": "file", "ref": None},
        {"name": "Sensor spec", "kind": "item", "ref": None},
    ]
    assert forward["native"]["files"] == [
        {"name": "Quote 2026-114.pdf", "type": "application/pdf", "size": 182044},
        {"name": "Sensor spec", "type": None, "size": 52311},
    ]
    # A forward keeps its original: the quoted text Exchange holds apart for it is part of the body.
    assert "Please find the quote attached." in forward["text"]
    assert "inline:2" in forward["native"]["flags"]


def test_text_comes_back_as_typed_through_the_script_s_escapes(mapper: ModuleType) -> None:
    settings = by_rid(mapped(mapper, read(SETTINGS)))[rid(SETTINGS)]
    assert settings["text"] == SETTINGS.text
    assert "https://forms.example.com/r?id=42&lang=en" in settings["text"]
    # No = reaches Chrome, and a tab in the text never splits a field.
    assert "=" not in "".join(SETTINGS.pieces()) and SETTINGS.fields().count("\t") == 16
    assert mapper.unescape("a ¤e b ¤¤e c ¤t d ¤¤¤e") == "a = b ¤e c \t d ¤="
    assert mapper.split_items("Ben¤:ben@example.com¤;Ri¤¤:ta¤:rita@example.com") == [
        ["Ben", "ben@example.com"],
        ["Ri¤:ta", "rita@example.com"],
    ]


def test_a_message_opened_alone_keeps_its_whole_body(mapper: ModuleType) -> None:
    junk = by_rid(mapped(mapper, read(JUNK, mode="message")))[rid(JUNK)]
    assert junk["text"] == JUNK.text
    assert junk["native"]["read_as"] == "message" and junk["native"]["folder_type"] == "junkemail"
    assert junk["thread"] == f"outlook:{JUNK.thread}"


def test_the_latest_read_wins_and_each_rid_comes_once(mapper: ModuleType) -> None:
    before = replace(ASK, text="Hi Ben, can we have the rig?")
    out = mapped(mapper, read(before), read(*THREAD), read(ASK))
    assert [r["rid"] for r in out["records"]] == sorted([rid(ASK), rid(ANSWER)])
    ask = by_rid(out)[rid(ASK)]
    assert ask["text"] == ASK.text and ask["fetched"] == "2026-10-19T16:02:00Z"


# -- what Chrome does to a result, and a long read's parts ------------------------------------------------------------


def test_a_long_message_goes_on_in_plus_strings_and_joins(mapper: ModuleType) -> None:
    strings = read(LONG)
    assert [s.split("\t", 1)[0] for s in strings[2:]] == ["m", "+", "+", "+"]
    assert all(len(s) <= 910 for s in strings)
    assert by_rid(mapped(mapper, strings))[rid(LONG)]["text"] == LONG.text


def test_a_read_in_parts_joins_across_calls_but_never_with_another_read(mapper: ModuleType) -> None:
    first = read(ASK, LONG, of="", stop=3)
    second = read(ASK, LONG, of="", start=3)
    assert first[-1] == "more\t3" and second[2].startswith("+\t")
    records = by_rid(mapped(mapper, first, read(SETTINGS), second))
    assert records[rid(LONG)]["text"] == LONG.text and rid(ASK) in records
    # A part of a read of other content (a new message arrived between the calls) doesn't join.
    other = read(ASK, replace(LONG, text=LONG.text + " and one more line"), of="", start=3)
    out = mapped(mapper, first, other)
    assert [r["rid"] for r in out["records"]] == [rid(ASK)]
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(LONG.id, "cut-short"), (None, "cut-short")]


def test_strings_chrome_blocked_or_cut_set_their_message_aside(mapper: ModuleType) -> None:
    blocked = read(LONG, ASK)
    blocked[3] = "[BLOCKED: Cookie/query string data]"
    out = mapped(mapper, blocked)
    assert [r["rid"] for r in out["records"]] == [rid(ASK)]
    assert out["set_aside"] == [{"id": LONG.id, "reason": "blocked", "tool": "javascript_tool", "key": "line"}]
    whole = read(ASK, ANSWER)
    whole[2] = "[BLOCKED: Base64 encoded data]"
    out = mapped(mapper, whole)
    assert [r["rid"] for r in out["records"]] == [rid(ANSWER)]
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(None, "blocked")]
    cut = read(LONG)
    cut[4] = cut[4][:500] + " [TRUNCATED]"
    out = mapped(mapper, cut)
    assert out["records"] == []
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(LONG.id, "cut-short")]
    dropped = [*read(ASK, LONG)[:4], "[TRUNCATED: 3 more items]"]
    out = mapped(mapper, dropped)
    assert [r["rid"] for r in out["records"]] == [rid(ASK)]
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(LONG.id, "cut-short")]


# -- holds, and a page without ids -----------------------------------------------------------------------------------


def test_a_held_conversation_or_a_held_message_is_skipped(mapper: ModuleType) -> None:
    held_thread = mapped(mapper, read(*THREAD), holds=[ASK.thread])
    assert held_thread["records"] == [] and held_thread["held"] == [ASK.thread]
    held_one = mapped(mapper, read(*THREAD), holds=[ASK.id])
    assert [r["rid"] for r in held_one["records"]] == [rid(ANSWER)]
    assert held_one["held"] == [ASK.id]


def test_without_an_id_the_rid_is_derived_and_stable(mapper: ModuleType) -> None:
    first = by_rid(mapped(mapper, read(SHOWN, mode="pane")))
    (key,) = first
    record = first[key]
    assert key.startswith("outlook:d-") and len(key) == len("outlook:d-") + 24
    assert record["fidelity"] == "derived" and record["native"]["flags"] == ["shown"]
    assert record["author"] == "Rita Sample" and record["to"] == ["Ben Sample"]
    assert record["thread"] == f"outlook:{SHOWN.thread}"
    assert set(by_rid(mapped(mapper, read(SHOWN, mode="pane")))) == {key}
    edited = by_rid(mapped(mapper, read(replace(SHOWN, text="Call me tomorrow"), mode="pane")))
    assert set(edited) != {key}


# -- what doesn't read -----------------------------------------------------------------------------------------------


def test_a_header_or_line_that_doesn_t_read_is_set_aside(mapper: ModuleType) -> None:
    out = mapped(mapper, [VERSION, "conv\tbroken"])
    assert [(s["reason"], s["key"]) for s in out["set_aside"]] == [("bad-header", "conv")]
    slashed = read(ASK)
    slashed[1] = slashed[1].replace(esc(ASK.thread), "AAQk/slash", 1)
    assert [s["reason"] for s in mapped(mapper, slashed)["set_aside"]] == ["bad-header"]
    short = read(ASK)
    short[2] = "m\t1\t" + "\t".join(["x"] * 3)
    out = mapped(mapper, short)
    assert [(s["reason"], s["key"]) for s in out["set_aside"]] == [("bad-line", "line")]


def test_nothing_loaded_reads_nothing(mapper: ModuleType) -> None:
    empty = [
        VERSION,
        "\t".join(["conv", "", "AAkAnotloaded", "", "0", "0", "0", "0", "", "0", "0", "811c9dc5", "0", "0"]),
    ]
    assert mapped(mapper, empty) == {"contract": 1, "records": [], "set_aside": [], "waiting": [], "held": []}


def test_a_message_without_a_body_a_time_an_id_or_readable_attachments_is_set_aside(mapper: ModuleType) -> None:
    out = mapped(mapper, read(replace(ASK, flags=("body:none",), text="")))
    assert out["set_aside"] == [{"id": ASK.id, "reason": "no-body", "tool": "javascript_tool", "key": "text"}]
    out = mapped(mapper, read(replace(ASK, sent="")))
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(ASK.id, "no-time")]
    bad = read(ASK)
    bad[2] = bad[2].replace(ASK.id, "not an id", 1)
    out = mapped(mapper, bad)
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(None, "bad-id")]
    odd = replace(FORWARD, attachments=(("link", "x", "y", 1),))
    out = mapped(mapper, read(odd))
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(FORWARD.id, "bad-attachments")]


def test_every_invented_message_maps(mapper: ModuleType) -> None:
    people = {BEN[1], RITA[1], DORA[1]}
    for m in ALL:
        record = by_rid(mapped(mapper, read(m, mode="message" if m is JUNK else "conversation")))[rid(m)]
        assert record["title"] == m.subject and record["ts"] == m.sent
        assert any(p in " ".join(record["to"]) for p in people)


def test_rows_of_a_folder_give_nothing(mapper: ModuleType) -> None:
    assert mapped(mapper, rows(JUNK, folder="Junk Email", messages=True))["records"] == []
    assert mapped(mapper, rows(ANSWER, folder="Deleted Items"))["records"] == []
    assert mapped(mapper, rows())["records"] == []


# -- the folders apart and the unread restore --------------------------------------------------------------------------


def test_the_folders_list_the_archive_and_the_restore_map_to_nothing(mapper: ModuleType) -> None:
    run = Run().snippet(FOLDERS).snippet(("opened folder", "AQMkDeleted¤e", "deleteditems", "rows 25 of 25"))
    run.snippet(("archive mailbox selected: search now",)).snippet(("searched", "scope archive mailbox", "rows 0"))
    run.snippet(("restored 1", "still unread 1", "still read 0", "no row 0")).snippet(("still unread 2",))
    out = mapper.map_run(run.payload())
    assert out == {"contract": 1, "records": [], "set_aside": [], "waiting": [], "held": []}


def test_the_restore_list_is_the_rows_listed_unread_and_nothing_read_before(mapper: ModuleType) -> None:
    # The search: ASK was unread when listed, so its conversation is on the list; the others were read.
    assert unread_ids(rows(*ALL[:5])) == [ASK.thread.replace("=", "¤e")]
    # Junk Email lists messages: the unread one by its message id.
    assert unread_ids(rows(JUNK, folder="Junk Email", messages=True)) == [JUNK.id]
    # Deleted Items holding only read mail: nothing to restore.
    assert unread_ids(rows(ANSWER, folder="Deleted Items")) == []
    # Reading changes nothing in the records: read state is in native only.
    run = Run().script(read(replace(ASK, flags=("unread", "body:own")))).script(read(ASK))
    assert [r["text"] for r in mapper.map_run(run.payload())["records"]] == [ASK.text]
