from __future__ import annotations

import pytest

from docket.vault.anchors import (
    UNREAD,
    Anchor,
    AnchorError,
    decode_rid,
    encode_rid,
    format_anchor,
    format_ref,
    format_time,
    is_unread,
    paragraphs,
    parse_anchor,
    parse_locator,
    parse_ref,
    parse_time,
    split_lines,
    split_path_locator,
    trailing_anchors,
)

DOC_TYPES = ("recording-note", "brief")


@pytest.mark.parametrize(
    ("token", "rid", "kind", "start", "end"),
    [
        ("@0:05", None, "time", 5, None),
        ("@00:05", None, "time", 5, None),
        ("@75:12", None, "time", 75 * 60 + 12, None),
        ("@1:02:03", None, "time", 3723, None),
        ("@14:30-14:52", None, "time", 870, 892),
        ("@59:59-1:00:00", None, "time", 3599, 3600),
        ("[r:calls:rec-0042@12:08]", "calls:rec-0042", "time", 728, None),
        ("[r:mail:<x1@example.invalid>#2]", "mail:<x1@example.invalid>", "part", 2, None),
        ("[r:mail:<x1@example.invalid>#2-4]", "mail:<x1@example.invalid>", "part", 2, 4),
        ("[r:chat:C0TEST/1791000001.000100]", "chat:C0TEST/1791000001.000100", "record", 0, None),
        # #0 isn't a position (paragraphs count from 1), so it is part of the rid.
        ("[r:calls:rec#0]", "calls:rec#0", "record", 0, None),
    ],
)
def test_valid_anchors(token: str, rid: str | None, kind: str, start: int, end: int | None) -> None:
    anchor = parse_anchor(token)
    assert (anchor.rid, anchor.kind, anchor.start, anchor.end, anchor.token) == (rid, kind, start, end, token)


@pytest.mark.parametrize(
    ("token", "says"),
    [
        ("@1:60", "isn't a time"),
        ("@1:2", "isn't a time"),
        ("@1:60:00", "isn't a time"),
        ("@123:00", "isn't a time"),
        ("@14:52-14:30", "ends before it starts"),
        ("[r:calls:rec#4-2]", "ends before it starts"),
        ("[r:calls:rec@12:08", "isn't an anchor"),
        ("[r:]", "isn't an anchor"),
        ("[r:a]b]", "%5D"),
        ("[r:no-colon@12:08]", "isn't a rid"),
        ("12:08", "isn't an anchor"),
    ],
)
def test_invalid_anchors(token: str, says: str) -> None:
    with pytest.raises(AnchorError, match=says):
        parse_anchor(token)


def test_times() -> None:
    assert parse_time("9:07") == 547
    assert [format_time(s) for s in (0, 59, 3599, 3600, 4512)] == ["00:00", "00:59", "59:59", "1:00:00", "1:15:12"]
    with pytest.raises(AnchorError, match="isn't a time"):
        parse_time("1:60")


AWKWARD_RIDS = [
    "mail:<x1@example.invalid>",
    "mail:<50%off@example.invalid>",
    "mail:<a]b@[10.0.0.1]>",
    "mail:<a@[10.0.0.1]>",
    "calls:rec@1:00",
    "calls:rec@1:00-2:00",
    "calls:rec#3",
    "calls:rec#3-4",
    "calls:rec@",
    "calls:#",
    "calls:a%40b%23c%5D",
    "chat:[weird]",
    "chat:Árvíztűrő@12:08",
    "calls:rec@75:12@1:00",
]
POSITIONS: list[tuple[str, int, int | None]] = [
    ("record", 0, None),
    ("time", 728, None),
    ("time", 870, 892),
    ("time", 3723, None),
    ("part", 2, None),
    ("part", 2, 4),
]


@pytest.mark.parametrize("rid", AWKWARD_RIDS)
@pytest.mark.parametrize(("kind", "start", "end"), POSITIONS)
def test_format_then_parse_gives_back_the_rid_and_position(rid: str, kind: str, start: int, end: int | None) -> None:
    written = format_anchor(rid, kind, start, end)  # type: ignore[arg-type]
    anchor = parse_anchor(written)
    assert (anchor.rid, anchor.kind, anchor.start, anchor.end) == (rid, kind, start, end)
    assert " " not in written


def test_a_mail_rid_stays_readable_and_only_the_ambiguous_tail_is_encoded() -> None:
    assert encode_rid("mail:<x1@example.invalid>") == "mail:<x1@example.invalid>"
    assert encode_rid("calls:rec@1:00") == "calls:rec%401:00"
    assert encode_rid("calls:rec#3") == "calls:rec%233"
    assert encode_rid("mail:<50%off]>") == "mail:<50%25off%5D>"
    assert decode_rid("a%2540b%5d%40%23%41") == "a%40b]@#%41"


def test_short_anchors_and_the_written_form() -> None:
    assert format_anchor(None, "time", 728) == "@12:08"
    assert format_anchor(None, "time", 870, 892) == "@14:30-14:52"
    assert Anchor("calls:rec-0042", "part", 2, 4).text == "[r:calls:rec-0042#2-4]"
    assert Anchor(None, "time", 5).last == 5
    with pytest.raises(ValueError, match="short anchor is a time"):
        format_anchor(None, "part", 2)


@pytest.mark.parametrize(
    ("line", "text", "tokens", "errors"),
    [
        ("- Ben put it at 10 million. @12:08", "- Ben put it at 10 million.", ["@12:08"], 0),
        ("- two anchors [r:calls:a@1:00]\t@2:00  ", "- two anchors", ["[r:calls:a@1:00]", "@2:00"], 0),
        ("meet at @10:00 tomorrow", "meet at @10:00 tomorrow", [], 0),
        ("ask word@12:08", "ask word@12:08", [], 0),
        ("ask @ben", "ask @ben", [], 0),
        ("| Ben | 10 million @12:08 |", "| Ben | 10 million", ["@12:08"], 0),
        ("|", "|", [], 0),
        ("@12:08", "", ["@12:08"], 0),
        ("- a full stop after it @12:08.", "- a full stop after it", [], 1),
        ("- reserved [d:digest-1] @12:08", "- reserved", ["@12:08"], 1),
        ("- a bad one, then a good one @1:60 @2:00", "- a bad one, then a good one", ["@2:00"], 1),
        ("- mid-line @1:60 isn't an anchor @2:00", "- mid-line @1:60 isn't an anchor", ["@2:00"], 0),
        # A redaction marker is text, at a line's end too; any other [<letters>:...] there is still refused.
        ("- text [redacted:phone] @13:40", "- text [redacted:phone]", ["@13:40"], 0),
        ("- the key [redacted:api-key]", "- the key [redacted:api-key]", [], 0),
        ("[redacted:private-key] @00:20", "[redacted:private-key]", ["@00:20"], 0),
        ("| Cleo | [redacted:phone] @13:40 |", "| Cleo | [redacted:phone]", ["@13:40"], 0),
        ("- not a marker [redacted:Phone]", "- not a marker", [], 1),
        ("- another kind [x:foo] @13:40", "- another kind", ["@13:40"], 1),
    ],
)
def test_the_anchors_at_the_end_of_a_line(line: str, text: str, tokens: list[str], errors: int) -> None:
    got = trailing_anchors(line)
    assert got.text == text
    assert [a.token for a in got.anchors] == tokens
    assert len(got.errors) == errors


def test_counting_lines_and_paragraphs() -> None:
    assert split_lines("a\r\nb\rc\u2028d\x0ce") == ["a", "b", "c\u2028d\x0ce"]
    crlf_mail = "\r\n\r\nHello Ben,\r\n\r\n\r\nThe offer stands.\r\nUntil Friday.\r\n \t\u00a0\r\nCleo\r\n\r\n"
    assert paragraphs(crlf_mail) == ["Hello Ben,", "The offer stands.\nUntil Friday.", "Cleo"]
    # A form feed is text, not a blank line; U+2028 doesn't end a line.
    assert paragraphs("a\n\x0c\nb\u2028c") == ["a\n\x0c\nb\u2028c"]
    assert paragraphs("") == []


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("recording-note:2026-10-14-supplier-call", "doc", "recording-note:2026-10-14-supplier-call"),
        ("processed/brief/ben-sample.md", "path", "processed/brief/ben-sample.md"),
        ("C:\\vault\\processed\\brief\\ben-sample.md", "path", "C:\\vault\\processed\\brief\\ben-sample.md"),
        ("/home/ada/vault/processed/brief/ben-sample.md", "path", "/home/ada/vault/processed/brief/ben-sample.md"),
        ("docs:notes/plan.md", "record", "docs:notes/plan.md"),
        ("calls:rec-0042", "record", "calls:rec-0042"),
        (" mail:<a]b%25@x> ", "record", "mail:<a]b%25@x>"),
        ("r:calls:rec-0042@12:08", "record", "calls:rec-0042"),
        ("[r:calls:rec-0042#2]", "record", "calls:rec-0042"),
    ],
)
def test_locators(text: str, kind: str, value: str) -> None:
    loc = parse_locator(text, DOC_TYPES)
    assert (loc.kind, loc.value) == (kind, value)


def test_a_locator_keeps_its_position_and_a_bare_rid_is_taken_as_written() -> None:
    timed = parse_locator("r:calls:rec-0042@12:08-12:40", DOC_TYPES).anchor
    assert timed is not None
    assert (timed.kind, timed.start, timed.end) == ("time", 728, 760)
    bare = parse_locator("calls:rec@12:08", DOC_TYPES).anchor
    assert bare is not None
    assert (bare.rid, bare.kind) == ("calls:rec@12:08", "record")


@pytest.mark.parametrize("text", ["nope", "[x]", "[@12:08]", "r:nocolon"])
def test_a_locator_that_is_nothing_is_refused(text: str) -> None:
    with pytest.raises(AnchorError):
        parse_locator(text, DOC_TYPES)


@pytest.mark.parametrize(
    ("text", "path", "token", "start", "end"),
    [
        ("notes/calls/x.md", "notes/calls/x.md", None, 0, None),
        ("notes/calls/x.md@12:08", "notes/calls/x.md", "@12:08", 728, None),
        ("notes/calls/x.MD@1:02:15", "notes/calls/x.MD", "@1:02:15", 3735, None),
        ("x.markdown@12:08-14:30", "x.markdown", "@12:08-14:30", 728, 870),
        ("notes/b@k/x.md@12:08", "notes/b@k/x.md", "@12:08", 728, None),
        ("calls:notes/x.md@27:17", "calls:notes/x.md", "@27:17", 1637, None),
        (" D:\\work\\x.md ", "D:\\work\\x.md", None, 0, None),
    ],
)
def test_a_path_locator_splits_its_time(text: str, path: str, token: str | None, start: int, end: int | None) -> None:
    found = split_path_locator(text)
    assert found is not None
    assert found[0] == path
    anchor = found[1]
    if token is None:
        assert anchor is None
    else:
        assert anchor is not None
        assert (anchor.rid, anchor.kind, anchor.token, anchor.start, anchor.end) == (None, "time", token, start, end)


@pytest.mark.parametrize(
    "text", ["x.md@12:08:", "x.md@1:60", "notes/x.txt", "x.md#heading", "calls:rec-0042", "@12:08"]
)
def test_what_isnt_a_path_locator(text: str) -> None:
    assert split_path_locator(text) is None


def test_a_path_locator_whose_stretch_runs_backwards_is_refused() -> None:
    with pytest.raises(AnchorError, match="ends before it starts"):
        split_path_locator("x.md@14:52-14:30")


# -- doc-line refs (#160, DR-0540) -------------------------------------------------------------------------------------

NOTE_ID = "recording-note:2026-10-14-supplier-call"


@pytest.mark.parametrize(
    ("token", "rev", "line", "hash_"),
    [
        (f"[d:{NOTE_ID}@2#7]", 2, 7, None),
        (f"[d:{NOTE_ID}#7]", None, 7, None),
        (f"[d:{NOTE_ID}@12#140=0a1b2c3d]", 12, 140, "0a1b2c3d"),
    ],
)
def test_a_ref_parses_and_writes_back_the_same(token: str, rev: int | None, line: int, hash_: str | None) -> None:
    ref = parse_ref(token)
    assert (ref.doc, ref.rev, ref.line, ref.hash) == (NOTE_ID, rev, line, hash_)
    assert ref.text == token == format_ref(NOTE_ID, rev, line, hash_)


@pytest.mark.parametrize(
    "token",
    [
        f"[d:{NOTE_ID}]",
        f"[d:{NOTE_ID}@2]",
        f"[d:{NOTE_ID}@0#7]",
        f"[d:{NOTE_ID}@2#0]",
        f"[d:{NOTE_ID}@2#7=0A1B2C3D]",
        f"[d:{NOTE_ID}#7=0a1b2c3d]",
        f"[d:{NOTE_ID}@2#7=0a1b2c]",
        "[d:Recording-Note:x@2#7]",
        "[d:no-colon@2#7]",
    ],
)
def test_what_isnt_a_ref_is_an_error_at_a_line_s_end(token: str) -> None:
    with pytest.raises(AnchorError):
        parse_ref(token)
    found = trailing_anchors(f"- a line @12:08 {token}")
    assert (found.refs, found.failed, [a.token for a in found.anchors]) == ([], [token], ["@12:08"])


def test_refs_and_anchors_sit_at_a_line_s_end_in_any_order() -> None:
    found = trailing_anchors(f"| Ben | 10 million [d:{NOTE_ID}@2#7] @12:08 [d:{NOTE_ID}@2#9=0a1b2c3d] |")
    assert found.text == "| Ben | 10 million"
    assert [a.token for a in found.anchors] == ["@12:08"]
    assert [(r.line, r.hash) for r in found.refs] == [(7, None), (9, "0a1b2c3d")]
    # A redaction marker stays text, a ref after it is still a ref.
    marked = trailing_anchors(f"- the code [redacted:password] [d:{NOTE_ID}@2#7]")
    assert (marked.text, len(marked.refs)) == ("- the code [redacted:password]", 1)


def test_a_form_this_docket_doesnt_read_is_named_as_one() -> None:
    found = trailing_anchors("- later [zz:soon] @12:08")
    assert found.failed == ["[zz:soon]"]
    assert found.errors == [f"[zz:soon]: {UNREAD}"]
    assert is_unread("[zz:soon]")
    assert not is_unread("[redacted:phone]")
    assert not is_unread(f"[d:{NOTE_ID}@2]")
