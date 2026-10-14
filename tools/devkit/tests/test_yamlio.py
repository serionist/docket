from __future__ import annotations

from typing import Any

import pytest

from devkit.yamlio import Comments, Shape, emit_file, load_yaml, plain_ok, scan_comments

TRICKY = [
    "plain words",
    "2026-10-14",
    "yes",
    "No",
    "on",
    "null",
    "~",
    "0x1F",
    "1e3",
    "12:30",
    "007",
    ".5",
    "- leading dash",
    "key: value",
    "ends with colon:",
    "has # hash",
    "#starts",
    "@at",
    "`tick`",
    "quote's inside",
    '"quoted"',
    "comma, inside",
    "[bracket]",
    "{brace}",
    "Árvíztűrő tükörfúrógép",
    "tab\there",
    " leading space",
    "trailing space ",
    "",
    "line one\nline two",
    "para one\n\npara two",
    "ends with newline\n",
    "unicode … ellipsis",
]


@pytest.mark.parametrize("value", TRICKY)
def test_every_value_reads_back_as_written(value: str) -> None:
    shape = Shape(order=("id", "text", "items"), list_fields=frozenset({"items"}))
    entry: dict[str, Any] = {"id": "X-1", "text": value, "items": [value.replace("\n", " ") or "x", "plain"]}
    text = emit_file([("things", [entry], shape)], Comments(), where="test")
    assert load_yaml(text) == {"things": [entry]}


@pytest.mark.parametrize("value", [v for v in TRICKY if "\n" not in v])
def test_every_value_reads_back_in_a_flow_entry(value: str) -> None:
    shape = Shape(order=("path", "note"), flow=True)
    entry = {"path": "a.md", "note": value}
    text = emit_file([("reading", [entry], shape)], Comments(), where="test")
    assert load_yaml(text) == {"reading": [entry]}
    assert text.count("\n") == 2


def test_plain_is_used_only_where_it_is_safe() -> None:
    assert plain_ok("plain words", flow=False)
    assert not plain_ok("2026-10-14", flow=False)
    assert not plain_ok("yes", flow=False)
    assert not plain_ok("a, b", flow=True)
    assert plain_ok("a, b", flow=False)


def test_long_lists_go_one_item_a_line() -> None:
    shape = Shape(order=("id", "items"), list_fields=frozenset({"items"}))
    items = [f"item number {n} with enough words to be long" for n in range(4)]
    text = emit_file([("things", [{"id": "X-1", "items": items}], shape)], Comments(), where="test")
    assert "      - item number 0" in text
    assert load_yaml(text) == {"things": [{"id": "X-1", "items": items}]}


def test_header_and_comments_above_keys_are_kept() -> None:
    text = "# header\n# more\n\n# about things\nthings:\n  - id: X-1\n"
    c = scan_comments(text)
    assert c.header == ["# header", "# more"]
    assert c.above_key == {"things": ["# about things"]}
    assert c.stray == []


def test_comments_inside_entries_are_stray() -> None:
    text = "things:\n  # inside\n  - id: X-1 # trailing\n"
    assert scan_comments(text).stray == [2, 3]


def test_hashes_inside_quotes_and_block_text_are_not_comments() -> None:
    text = 'things:\n  - id: "X # 1"\n    text: |-\n      # not a comment\n      line\n    other: a#b\n'
    assert scan_comments(text).stray == []


def test_duplicate_keys_are_refused() -> None:
    with pytest.raises(Exception, match="duplicate key"):
        load_yaml("a: 1\na: 2\n")
