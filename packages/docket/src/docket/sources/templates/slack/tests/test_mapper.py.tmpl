"""The Slack mapper on its own: plain JSON in, plain JSON out, no Docket needed. Every payload is invented, in the
shape the Slack plugin's read tools return."""

from __future__ import annotations

import json
from dataclasses import replace
from types import ModuleType
from typing import Any

from slack_payloads import (
    BEN,
    CHANNEL,
    DM,
    FILE_POST,
    HELD_CHANNEL,
    PARENT,
    POST,
    REPLY_1,
    REPLY_2,
    RITA,
    Msg,
    Run,
    channel_block,
    hooked,
    local,
    page,
    thread_page,
)

FILES_LINE_ONE = "Files: clip.mov (ID: F0TESTFILE7, video/mp4, 12.4 MB)"


def by_rid(output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["rid"]: r for r in output["records"]}


def rid(m: Msg | str, channel: str = CHANNEL) -> str:
    return f"slack:{channel}/{m if isinstance(m, str) else m.ts}"


def test_results_are_read_from_structured_content_or_the_first_json_in_their_text(mapper: ModuleType) -> None:
    payload = {"messages": "Channel: #team-test (C0TEST)\n\n"}
    assert mapper.unwrap({"structuredContent": payload}) == payload
    assert mapper.unwrap({"content": hooked(payload)}) == payload
    assert mapper.unwrap(hooked(payload)) == payload
    assert mapper.unwrap(json.dumps(payload)) == payload
    assert mapper.unwrap("no json here { at all") is None
    assert mapper.unwrap(payload) == payload
    assert mapper.unwrap(None) is None


def test_a_channel_page_gives_one_record_per_message_in_the_core_s_shape(mapper: ModuleType) -> None:
    out = mapper.map_run(Run().channel(CHANNEL, FILE_POST, PARENT, POST).payload())
    records = by_rid(out)
    assert sorted(records) == sorted(rid(m) for m in (POST, PARENT, FILE_POST))
    post = records[rid(POST)]
    assert post["source"] == "slack"
    assert post["kind"] == "message"
    assert post["container"] == f"slack:{CHANNEL}"
    # A top-level message is its own thread's parent, before and after anyone replies.
    assert post["thread"] == rid(POST)
    assert post["author"] == "Ben Sample <U0BEN>"
    assert post["ts"] == "2026-10-15T10:00:00Z"
    assert post["text"] == "Rita, can you send the test plan by Friday?"
    assert post["attachments"] == []
    assert post["native"] == {
        "channel": CHANNEL,
        "ts": POST.ts,
        "posted": "2026-10-15 12:00:00 CEST",
        "tool": "slack_read_channel",
        "user": "U0BEN",
        "email": "ben@example.invalid",
        "meta": ["Reactions: +1 (2), eyes (1)"],
    }
    assert (post["fetched"], post["via"], post["fidelity"]) == ("2026-10-15T16:00:00Z", "slack-mcp", "verbatim")
    parent = records[rid(PARENT)]
    assert parent["thread"] == rid(PARENT)
    assert parent["native"]["meta"] == [PARENT.thread_line()]
    assert (parent["native"]["reply_count"], parent["native"]["latest_reply"]) == (2, local(REPLY_2.ts))
    assert (out["set_aside"], out["waiting"], out["held"]) == ([], [], [])


def test_a_thread_read_ties_each_reply_to_its_parent(mapper: ModuleType) -> None:
    records = by_rid(mapper.map_run(Run().thread(CHANNEL, PARENT, REPLY_1, REPLY_2).payload()))
    assert {r["rid"]: r["thread"] for r in records.values()} == {
        rid(PARENT): rid(PARENT),
        rid(REPLY_1): rid(PARENT),
        rid(REPLY_2): rid(PARENT),
    }
    assert {r["container"] for r in records.values()} == {f"slack:{CHANNEL}"}
    reply = records[rid(REPLY_2)]
    assert reply["author"] == "Rita Sample <U0RITA>"
    assert reply["text"] == "Thanks. Section 3 is the one to check."
    assert reply["native"]["meta"] == ["Reactions: +1 (1)"]
    assert (reply["native"]["posted"], reply["native"]["tool"]) == (local(REPLY_2.ts), "slack_read_thread")
    assert records[rid(REPLY_1)]["text"] == "Megnézem holnap, és visszaírok."


def test_a_parent_reads_the_same_by_its_thread_as_by_its_channel(mapper: ModuleType) -> None:
    # The thread layout puts a blank line before a message's lines and no Thread line under the parent.
    parent = replace(PARENT, lines=("Reactions: +1 (2)", FILES_LINE_ONE))
    by_thread = by_rid(mapper.map_run(Run().thread(CHANNEL, parent, REPLY_1).payload()))[rid(PARENT)]
    by_channel = by_rid(mapper.map_run(Run().channel(CHANNEL, parent).payload()))[rid(PARENT)]
    same = ("container", "thread", "ts", "author", "text", "attachments")
    assert {k: by_thread[k] for k in same} == {k: by_channel[k] for k in same}
    assert "reply_count" in by_channel["native"]
    assert "reply_count" not in by_thread["native"]


def test_a_later_thread_page_without_its_parent_block_hangs_from_the_ts_asked_for(mapper: ModuleType) -> None:
    run = Run().thread(CHANNEL, None, REPLY_2, ts=PARENT.ts, first=51, total=51)
    assert by_rid(mapper.map_run(run.payload()))[rid(REPLY_2)]["thread"] == rid(PARENT)


def test_a_message_read_twice_is_emitted_once_from_the_latest_capture(mapper: ModuleType) -> None:
    edited = replace(PARENT, text="Draft test plan is up (v2).\nComments welcome by Thursday.")
    run = Run().channel(CHANNEL, PARENT).thread(CHANNEL, edited, REPLY_1, REPLY_2)
    # The cursor of a long thread once handed the first page again.
    run.thread(CHANNEL, edited, REPLY_1, REPLY_2)
    out = mapper.map_run(run.payload())
    rids = [r["rid"] for r in out["records"]]
    assert len(rids) == len(set(rids)) == 3
    parent = by_rid(out)[rid(PARENT)]
    assert parent["text"].startswith("Draft test plan is up (v2).")
    assert parent["native"]["tool"] == "slack_read_thread"
    later = Run().thread(CHANNEL, edited).channel(CHANNEL, PARENT)
    assert by_rid(mapper.map_run(later.payload()))[rid(PARENT)]["native"]["tool"] == "slack_read_channel"


def test_reactions_and_reply_counts_go_in_native_never_in_the_text(mapper: ModuleType) -> None:
    text = "Rita, can you send the test plan by Friday?\nReactions: +1 (2)\nThat line stays: it is mid-text."
    quiet = by_rid(mapper.map_run(Run().channel(CHANNEL, Msg(BEN, POST.ts, text)).payload()))
    busy = Msg(BEN, POST.ts, text, ("Reactions: +1 (5), white_check_mark (1)",), replies=3, latest=REPLY_2.ts)
    loud = by_rid(mapper.map_run(Run().channel(CHANNEL, busy).payload()))
    a, b = quiet[rid(POST)], loud[rid(POST)]
    assert a["text"] == b["text"] == text
    assert {k: v for k, v in a.items() if k != "native"} == {k: v for k, v in b.items() if k != "native"}
    assert b["native"]["meta"] == [busy.thread_line(), "Reactions: +1 (5), white_check_mark (1)"]
    assert b["native"]["reply_count"] == 3


def test_prose_that_only_looks_like_those_lines_stays_in_the_text(mapper: ModuleType) -> None:
    for last in ("Reactions: mostly positive", "Files: on the shared drive", "Thread: see the one above"):
        record = by_rid(mapper.map_run(Run().channel(CHANNEL, Msg(BEN, POST.ts, f"Notes.\n{last}")).payload()))
        assert record[rid(POST)]["text"] == f"Notes.\n{last}"
        assert "meta" not in record[rid(POST)]["native"]


def test_files_become_attachments_by_name_and_file_id(mapper: ModuleType) -> None:
    many = (
        "Files: notes (1).txt (ID: F0TESTFILE3, text/plain, 2.0 KB), budget, final.xlsx (ID: F0TESTFILE4, "
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, 15.3 KB), "
        "Notes (ID: F0TESTFILE6, post, unknown size)"
    )
    run = (
        Run()
        .channel(CHANNEL, FILE_POST)
        .channel(DM, Msg(RITA, POST.ts, "All three attached.", ("Reactions: +1 (1)", many)))
        .thread(CHANNEL, PARENT, replace(REPLY_2, lines=(FILES_LINE_ONE,)))
    )
    records = by_rid(mapper.map_run(run.payload()))
    file_post = records[rid(FILE_POST)]
    assert file_post["text"] == ""
    assert file_post["attachments"] == [
        {"name": "test-plan.pdf", "kind": "file", "ref": "F0TESTFILE1"},
        {"name": "board photo.png", "kind": "file", "ref": "F0TESTFILE2"},
    ]
    dm = records[rid(POST, DM)]
    assert [(a["name"], a["ref"]) for a in dm["attachments"]] == [
        ("notes (1).txt", "F0TESTFILE3"),
        ("budget, final.xlsx", "F0TESTFILE4"),
        ("Notes", "F0TESTFILE6"),
    ]
    assert dm["text"] == "All three attached."
    assert records[rid(REPLY_2)]["attachments"] == [{"name": "clip.mov", "kind": "file", "ref": "F0TESTFILE7"}]


def test_a_dm_keeps_its_d_id_and_a_read_by_user_id_is_set_aside(mapper: ModuleType) -> None:
    message = Msg(RITA, POST.ts, "Can we move our 1:1?")
    dm = by_rid(mapper.map_run(Run().channel(DM, message).payload()))
    assert dm[rid(POST, DM)]["container"] == f"slack:{DM}"
    # A channel read by user id is taken only when the result itself names the DM; a thread read never names it.
    named = mapper.map_run(Run().channel("U0RITA", message, shown=DM).payload())
    assert [r["rid"] for r in named["records"]] == [rid(POST, DM)]
    by_user = mapper.map_run(Run().channel("U0RITA", message).thread("U0RITA", message).payload())
    assert by_user["records"] == []
    assert [(e["id"], e["reason"], e["tool"]) for e in by_user["set_aside"]] == [
        (None, "user-id-channel", "slack_read_channel"),
        (None, "user-id-channel", "slack_read_thread"),
    ]


def test_a_held_conversation_thread_or_message_is_never_parsed_or_named(mapper: ModuleType) -> None:
    private = "the lake house on Saturday"
    run = (
        Run()
        .add("slack_read_channel", "not json at all, " + private, channel_id=HELD_CHANNEL)
        .channel(CHANNEL, Msg(BEN, POST.ts, private), Msg(RITA, PARENT.ts, "Fine to keep."))
        .thread(CHANNEL, Msg(RITA, REPLY_2.ts, private), Msg(BEN, REPLY_1.ts, private))
    )
    holds = [HELD_CHANNEL, f"{CHANNEL}/{POST.ts}", f"{CHANNEL}/{REPLY_2.ts}"]
    out = mapper.map_run(run.payload(holds=holds))
    assert out["held"] == sorted(holds)
    assert [r["rid"] for r in out["records"]] == [rid(PARENT)]
    assert private not in json.dumps(out)
    assert out["set_aside"] == []


def test_what_isn_t_usable_is_set_aside_with_a_token_never_text(mapper: ModuleType) -> None:
    private = "Rita's door code"
    bad_header = page(CHANNEL, f"=== Message from nobody, at no time ===\nMessage TS: {POST.ts}\n{private}")
    no_ts = page(CHANNEL, channel_block(Msg(BEN, POST.ts, private)).replace(f"Message TS: {POST.ts}", "Message TS: x"))
    bad_reply = thread_page(PARENT, [REPLY_1])
    bad_reply["messages"] = bad_reply["messages"].replace("--- Reply 1 of 1 ---", f"--- Reply one ---\n{private}")
    no_author = thread_page(PARENT, [Msg(BEN, REPLY_1.ts, private)])
    no_author["messages"] = no_author["messages"].replace("From: Ben Sample <ben@example.invalid> (U0BEN)\n", "")
    thread = {"channel_id": CHANNEL, "message_ts": PARENT.ts}
    run = (
        Run()
        .add("slack_search_public", hooked({"results": private}))
        .add(None, {"messages": private}, file="dump.json")
        .add("slack_read_channel", f"Slack is down: {private}", channel_id=CHANNEL)
        .add("slack_read_channel", hooked({"error": private}), channel_id=CHANNEL)
        .add("slack_read_channel", hooked(page("not an id", channel_block(Msg(BEN, POST.ts, private)))))
        .add("slack_read_thread", hooked(page(CHANNEL, channel_block(Msg(BEN, POST.ts, private)))), channel_id=CHANNEL)
        .add("slack_read_channel", hooked(bad_header), channel_id=CHANNEL)
        .add("slack_read_channel", hooked(no_ts), channel_id=CHANNEL)
        .add("slack_read_thread", hooked(bad_reply), **thread)
        .add("slack_read_thread", hooked(no_author), **thread)
    )
    out = mapper.map_run(run.payload())
    assert [r["rid"] for r in out["records"]] == [rid(PARENT)]
    assert [(e["id"], e["reason"], e["key"]) for e in out["set_aside"]] == [
        (None, "unknown-tool", None),
        (None, "not-a-read", "file"),
        (CHANNEL, "no-json", None),
        (CHANNEL, "no-messages", "messages"),
        (None, "bad-channel", "channel_id"),
        (CHANNEL, "bad-thread-ts", "message_ts"),
        (CHANNEL, "bad-header", None),
        (CHANNEL, "no-ts", "Message TS"),
        (CHANNEL, "bad-header", None),
        (f"{CHANNEL}/{REPLY_1.ts}", "no-author", "From"),
    ]
    assert private not in json.dumps(out)


def test_an_empty_page_gives_nothing(mapper: ModuleType) -> None:
    for result in (hooked(page(CHANNEL)), hooked({"messages": "", "pagination_info": "There are no more messages.\n"})):
        out = mapper.map_run(Run().add("slack_read_channel", result, channel_id=CHANNEL).payload())
        assert (out["records"], out["set_aside"]) == ([], [])
    alone = mapper.map_run(Run().thread(CHANNEL, PARENT).payload())
    assert [r["rid"] for r in alone["records"]] == [rid(PARENT)]


def test_times_are_utc_to_the_second(mapper: ModuleType) -> None:
    assert mapper.slack_time("1792058400.000100") == "2026-10-15T10:00:00Z"
    assert mapper.slack_time("1792058400.999999") == "2026-10-15T10:00:00Z"


def test_a_page_of_50_messages_stays_under_claude_code_s_file_threshold() -> None:
    # The skill reads 50 messages a page: at about 500 characters each, Hungarian included, the result the hook gets
    # stays under the 50,000 characters past which Claude Code hands it a note about a saved file instead.
    text = (
        "Megnéztem a tesztelési tervet, a harmadik rész még hiányos. "
        "Rita, could you add the acceptance criteria for the invented robot arm before Friday's review? "
        "<https://example.invalid/plan|test plan> :eyes: "
    ) * 2
    messages = [Msg(BEN if n % 2 else RITA, f"17920{n:05d}.000100", text, ("Reactions: +1 (2)",)) for n in range(50)]
    channel = page(CHANNEL, *(channel_block(m) for m in messages), cursor="bmV4dF90czoxNzkyMDAwMDAw")
    assert len(json.dumps(channel)) < 50_000
    assert len(json.dumps(thread_page(messages[0], messages[1:]))) < 50_000
