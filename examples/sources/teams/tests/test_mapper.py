"""The Teams mapper on its own: plain JSON in, plain JSON out, no Docket needed. Every payload is invented, in the shape
scripts/teams-chat.js and scripts/teams-chats.js return."""

from __future__ import annotations

import json
from dataclasses import replace
from types import ModuleType
from typing import Any

from teams_payloads import (
    ALL,
    BEN,
    DORA,
    EDITED,
    GROUP,
    GROUP_MESSAGES,
    LONG,
    MEETING,
    MEETING_NOTE,
    ONE,
    PLAN,
    RIG_FREE,
    RIG_YES,
    RITA,
    SETTINGS,
    SHOWN,
    VERSION,
    Message,
    Run,
    chat,
    chat_list,
    guid,
    hooked,
    live_run,
    rid,
)


def by_rid(output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["rid"]: r for r in output["records"]}


def one(mapper: ModuleType, *messages: Message, c: Any = GROUP) -> dict[str, dict[str, Any]]:
    return by_rid(mapper.map_run(Run().script(chat(c, *messages)).payload()))


# -- the result -------------------------------------------------------------------------------------------------------


def test_the_script_s_strings_are_found_in_every_shape_a_result_can_take(mapper: ModuleType) -> None:
    strings = chat(GROUP, RIG_FREE)
    assert mapper.strings(hooked(strings)) == strings
    assert mapper.strings({"content": hooked(strings)}) == strings
    assert mapper.strings({"structuredContent": strings}) == strings
    assert mapper.strings(strings) == strings
    assert mapper.strings(json.dumps(strings) + "\n\nTab Context: [101]") == strings
    assert mapper.strings(json.dumps(json.dumps(strings))) == strings
    assert mapper.strings([{"type": "text", "text": "Result: " + json.dumps(strings, ensure_ascii=True)}]) == strings


def test_the_chat_list_and_results_without_a_known_version_line_are_passed_over_without_a_word(
    mapper: ModuleType,
) -> None:
    run = Run().script(chat_list((GROUP, True, "2026-10-19T09:02:00.000Z"))).snippet()
    run.snippet(["docket:teams-chat/2", "chat\t..."]).add("javascript_tool", hooked(42))
    out = mapper.map_run(run.add("javascript_tool", "undefined").payload())
    assert out == {"contract": 1, "records": [], "set_aside": [], "waiting": [], "held": []}


def test_a_file_or_another_tool_is_set_aside(mapper: ModuleType) -> None:
    out = mapper.map_run(Run().add(None, "x", file="chat.json").add("get_page_text", "text").payload())
    assert [(s["reason"], s["key"]) for s in out["set_aside"]] == [("not-a-read", "file"), ("unknown-tool", None)]


# -- one record per message --------------------------------------------------------------------------------------------


def test_a_run_gives_one_record_per_message_in_the_core_s_shape(mapper: ModuleType) -> None:
    out = mapper.map_run(live_run(Run()).payload())
    records = by_rid(out)
    assert sorted(records) == sorted(rid(m) for m in ALL)
    assert out["set_aside"] == [] and out["waiting"] == [] and out["held"] == []
    assert records[rid(RIG_FREE)] == {
        "rid": f"teams:{GROUP.id}/{RIG_FREE.mid}",
        "source": "teams",
        "kind": "message",
        "container": f"teams:{GROUP.id}",
        "thread": f"teams:{GROUP.id}",
        "ts": "2026-10-19T07:58:12.311Z",
        "author": f"Rita Sample <{guid(RITA)}>",
        "text": "Morning! Is the rig free at 10?",
        "attachments": [],
        "native": {
            "chat": GROUP.id,
            "chat_kind": "group",
            "chat_name": "Ben, Rita, Dóra",
            "id": RIG_FREE.mid,
            "posted": "2026-10-19T07:58:12.311Z",
            "user": RITA[1],
        },
        "fetched": "2026-10-19T16:02:00Z",
        "via": "teams-web",
        "fidelity": "verbatim",
    }


def test_each_chat_layout_keeps_its_own_id_as_container_and_thread(mapper: ModuleType) -> None:
    records = by_rid(mapper.map_run(live_run(Run()).payload()))
    for m in (RIG_FREE, SHOWN, MEETING_NOTE):
        r = records[rid(m)]
        assert r["container"] == r["thread"] == r["rid"].rsplit("/", 1)[0]
    assert records[rid(SHOWN)]["native"]["chat_kind"] == "one-on-one"
    assert records[rid(MEETING_NOTE)]["native"]["chat_kind"] == "meeting"
    assert MEETING.id.startswith("19:meeting_") and ONE.id.endswith("@unq.gbl.spaces")


def test_the_author_is_the_name_with_the_entra_id_or_the_name_alone_when_the_page_gave_no_id(
    mapper: ModuleType,
) -> None:
    records = one(mapper, RIG_YES, PLAN)
    assert records[rid(RIG_YES)]["author"] == f"Ben Sample <{guid(BEN)}>"
    assert records[rid(PLAN)]["author"] == f"Dóra Minta <{guid(DORA)}>"
    shown = one(mapper, SHOWN, c=ONE)[rid(SHOWN)]
    assert shown["author"] == "Rita Sample"
    assert shown["native"]["flags"] == ["shown"] and "user" not in shown["native"]
    guest = replace(RIG_FREE, author=("Gus Guest", "8:guest:abc123"))
    assert one(mapper, guest)[rid(RIG_FREE)]["author"] == "Gus Guest <8:guest:abc123>"


def test_a_reply_keeps_its_own_text_and_names_the_message_it_quotes_in_native(mapper: ModuleType) -> None:
    reply = one(mapper, RIG_YES)[rid(RIG_YES)]
    assert reply["text"] == "Yes, until noon 👍"
    assert reply["native"]["reply_to"] == {
        "id": RIG_FREE.mid,
        "author": "Rita Sample",
        "preview": "Morning! Is the rig free at 10?",
    }
    assert reply["native"]["flags"] == ["mine"]
    assert reply["thread"] == f"teams:{GROUP.id}"


def test_files_and_images_are_attachments_by_name_with_their_ids(mapper: ModuleType) -> None:
    plan = one(mapper, PLAN)[rid(PLAN)]
    assert plan["attachments"] == [
        {"name": "Torque test plan v2.xlsx", "kind": "file", "ref": "f1e2d3c4-b5a6-4978-8a9b-0c1d2e3f4a5b"},
        {"name": "image", "kind": "image", "ref": "0-weu-d3-9c0ffee0d15ea5e5ca7f00d5ba5eba11"},
    ]
    assert plan["text"] == "Plan attached, and the wiring photo"
    only_file = replace(PLAN, text="", attachments=(("file", "", "notes ¤ v=2.docx"),))
    assert one(mapper, only_file)[rid(PLAN)]["attachments"] == [
        {"name": "notes ¤ v=2.docx", "kind": "file", "ref": None}
    ]


def test_text_comes_back_as_typed_through_the_script_s_escapes(mapper: ModuleType) -> None:
    settings = one(mapper, SETTINGS)[rid(SETTINGS)]
    assert settings["text"] == SETTINGS.text
    # No = reaches Chrome, and a tab in the text never splits a field.
    assert "=" not in "".join(SETTINGS.pieces()) and SETTINGS.fields().count("\t") == 10
    assert mapper.unescape("a ¤e b ¤¤e c ¤t d ¤¤¤e") == "a = b ¤e c \t d ¤="
    assert mapper.split_items("file¤:id1¤:a¤¤:b.txt¤;image¤:id2¤:image") == [
        ["file", "id1", "a¤:b.txt"],
        ["image", "id2", "image"],
    ]


def test_an_edit_is_kept_in_native_and_the_latest_read_wins(mapper: ModuleType) -> None:
    before = replace(EDITED, text="Moved to 10:00, room 4.12", edited="")
    run = Run().script(chat(GROUP, before)).snippet().script(chat(GROUP, EDITED))
    out = mapper.map_run(run.payload())
    (record,) = out["records"]
    assert record["text"] == "Moved to 10:30, room 4.12"
    assert record["native"]["edited"] == "2026-10-19T08:33:00.000Z"
    assert record["fetched"] == "2026-10-19T16:02:00Z"


# -- what Chrome does to a result ------------------------------------------------------------------------------------


def test_a_long_message_goes_on_in_plus_strings_and_joins(mapper: ModuleType) -> None:
    strings = chat(GROUP, LONG)
    assert [s.split("\t", 1)[0] for s in strings[2:]] == ["m", "+"]
    assert all(len(s) <= 910 for s in strings)
    assert one(mapper, LONG)[rid(LONG)]["text"] == LONG.text


def test_strings_chrome_blocked_or_cut_set_their_message_aside(mapper: ModuleType) -> None:
    blocked_piece = chat(GROUP, LONG, RIG_FREE)
    blocked_piece[3] = "[BLOCKED: Cookie/query string data]"
    out = mapper.map_run(Run().script(blocked_piece).payload())
    assert [r["rid"] for r in out["records"]] == [rid(RIG_FREE)]
    assert out["set_aside"] == [
        {"id": f"{GROUP.id}/{LONG.mid}", "reason": "blocked", "tool": "javascript_tool", "key": "line"}
    ]
    whole = chat(GROUP, RIG_FREE, RIG_YES)
    whole[2] = "[BLOCKED: Base64 encoded data]"
    whole.append("[TRUNCATED: 3 more items]")
    out = mapper.map_run(Run().script(whole).payload())
    assert [r["rid"] for r in out["records"]] == [rid(RIG_YES)]
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(None, "blocked"), (None, "cut-short")]
    cut = chat(GROUP, LONG)
    cut[3] = cut[3][:500] + " [TRUNCATED]"
    out = mapper.map_run(Run().script(cut).payload())
    assert out["records"] == []
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(f"{GROUP.id}/{LONG.mid}", "cut-short")]


def test_a_later_part_and_its_more_line(mapper: ModuleType) -> None:
    run = Run().script(chat(GROUP, RIG_FREE, more=1)).script(chat(GROUP, RIG_YES, start=1))
    assert sorted(by_rid(mapper.map_run(run.payload()))) == sorted([rid(RIG_FREE), rid(RIG_YES)])


# -- holds, and a page without ids -----------------------------------------------------------------------------------


def test_a_held_chat_or_a_held_message_is_skipped_unread(mapper: ModuleType) -> None:
    held_chat = mapper.map_run(Run().script(chat(GROUP, RIG_FREE, RIG_YES)).payload([GROUP.id]))
    assert held_chat["records"] == [] and held_chat["held"] == [GROUP.id]
    native = f"{GROUP.id}/{RIG_FREE.mid}"
    held_one = mapper.map_run(Run().script(chat(GROUP, RIG_FREE, RIG_YES)).payload([native]))
    assert [r["rid"] for r in held_one["records"]] == [rid(RIG_YES)]
    assert held_one["held"] == [native]


def test_without_a_chat_id_the_container_is_derived_from_the_name_and_a_rename_splits_it(mapper: ModuleType) -> None:
    first = by_rid(mapper.map_run(Run().script(chat(GROUP, RIG_FREE, chat_id="")).payload()))
    (key,) = first
    container = first[key]["container"]
    assert container.startswith("teams:d-") and len(container) == len("teams:d-") + 24
    assert key == f"{container}/{RIG_FREE.mid}" and first[key]["fidelity"] == "derived"
    again = by_rid(mapper.map_run(Run().script(chat(GROUP, RIG_FREE, chat_id="")).payload()))
    assert set(again) == {key}
    renamed = replace(GROUP, name="Rig crew")
    other = by_rid(mapper.map_run(Run().script(chat(renamed, RIG_FREE, chat_id="")).payload()))
    assert set(other) != {key}


# -- what doesn't read -------------------------------------------------------------------------------------------------


def test_a_header_or_line_that_doesn_t_read_is_set_aside(mapper: ModuleType) -> None:
    out = mapper.map_run(Run().script([VERSION, "chat\tbroken"]).payload())
    assert [(s["reason"], s["key"]) for s in out["set_aside"]] == [("bad-header", "chat")]
    slashed = mapper.map_run(Run().script(chat(GROUP, RIG_FREE, chat_id="19:a/b@thread.v2")).payload())
    assert [s["reason"] for s in slashed["set_aside"]] == ["bad-header"]
    nameless = mapper.map_run(Run().script(chat(replace(GROUP, name=""), RIG_FREE, chat_id="")).payload())
    assert [s["reason"] for s in nameless["set_aside"]] == ["bad-header"]
    short = chat(GROUP, RIG_FREE)
    short[2] = "m\t1\t" + "\t".join(["x"] * 3)
    out = mapper.map_run(Run().script([*short, "something else"]).payload())
    assert [(s["reason"], s["key"]) for s in out["set_aside"]] == [("bad-line", "line"), ("bad-line", "line")]


def test_a_message_without_a_time_an_id_or_readable_attachments_is_set_aside(mapper: ModuleType) -> None:
    out = mapper.map_run(Run().script(chat(GROUP, replace(RIG_FREE, time="", id="1792000000001"))).payload())
    assert out["set_aside"] == [
        {"id": f"{GROUP.id}/1792000000001", "reason": "no-time", "tool": "javascript_tool", "key": "time"}
    ]
    out = mapper.map_run(Run().script(chat(GROUP, replace(RIG_FREE, id="not an id"))).payload())
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(None, "bad-id")]
    odd = replace(PLAN, attachments=(("link", "x", "y"),))
    out = mapper.map_run(Run().script(chat(GROUP, odd)).payload())
    assert [(s["id"], s["reason"]) for s in out["set_aside"]] == [(f"{GROUP.id}/{PLAN.mid}", "bad-attachments")]


def test_every_message_of_the_group_chat_maps(mapper: ModuleType) -> None:
    assert sorted(one(mapper, *GROUP_MESSAGES)) == sorted(rid(m) for m in GROUP_MESSAGES)
