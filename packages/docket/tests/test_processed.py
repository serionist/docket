from __future__ import annotations

import logging

import pytest
import yaml

from docket.vault.processed import DocProblem, DocRefusedError, doc_path, double_quoted, line_roles, parse_doc

NOTE = """\
---
id: recording-note:2026-10-14-supplier-call
type: recording-note
rev: 1
covers:
  - calls:rec-0042
by: claude-opus-5.5, sync 2026-10-14T16:07
supersedes: []
entities: [ben-sample, northwind]
---

# Supplier call

- Ben put the consultant at 10 million forints for the quarter. @12:08
- Cleo asked for the offer in writing by Friday. @14:30-14:52
"""


def doc(**fields: str) -> str:
    """A doc whose frontmatter lines are the given key: value pairs, in order."""
    head = "\n".join(f"{k}: {v}" for k, v in fields.items())
    return f"---\n{head}\n---\n\n- a line @00:01\n"


def refusal(text: str, *, lenient: bool = False) -> list[DocProblem]:
    with pytest.raises(DocRefusedError) as e:
        parse_doc(text, lenient=lenient)
    return e.value.problems


def test_the_canonical_form_round_trips() -> None:
    parsed = parse_doc(NOTE)
    assert parsed.render() == NOTE
    assert parsed.meta.who == "claude-opus-5.5"
    assert parsed.meta.run == "sync 2026-10-14T16:07"
    assert parsed.lines()[3] == (14, "- Ben put the consultant at 10 million forints for the quarter. @12:08")
    assert parse_doc(parsed.render(rev=2)).meta.rev == 2


def test_a_hand_written_frontmatter_comes_out_canonical() -> None:
    text = (
        "\ufeff---\r\nid: brief:ben-sample\r\ntype: brief\r\n"
        "covers: [calls:rec-0042, chat:C0TEST/1791000001.000100]\r\nby: Ada Example\r\n"
        "supersedes: null\r\nentities: [no, on, 2026]\r\n---\r\nBen's brief. @00:01\r\n\r\n\r\n"
    )
    parsed = parse_doc(text)
    assert parsed.meta.entities == ["no", "on", "2026"]
    assert parsed.meta.supersedes == []
    assert parsed.meta.who == "Ada Example"
    assert parsed.meta.run is None
    rendered = parsed.render()
    assert 'entities: ["no", "on", "2026"]' in rendered
    assert rendered.endswith("---\nBen's brief. @00:01\n")
    assert parse_doc(rendered).meta == parsed.meta


@pytest.mark.parametrize(
    "value",
    [
        "mail:<a\x7fb@example.invalid>",
        "chat:x\x9b\x80",
        "chat:y" + chr(0xFFFE) + chr(0xFFFF),
        'chat:q"b\\z',
        chr(0x2028) + chr(0x2029) + chr(0x85) + chr(0x0B) + chr(9),
        chr(0xE9) + chr(0x1F600),
        "chat:\ud800",
    ],
)
def test_a_double_quoted_value_reads_back_the_same_in_every_yaml_reader(value: str) -> None:
    quoted = double_quoted(value)
    assert yaml.safe_load(f"k: {quoted}") == {"k": value}
    assert yaml.load(f"k: {quoted}", Loader=yaml.BaseLoader) == {"k": value}  # noqa: S506 - the base loader builds no objects
    assert quoted.isprintable()


def test_a_rid_with_a_comma_or_a_quote_survives() -> None:
    awkward = ["mail:<a,b@example.invalid>", 'mail:"quoted"@x', "chat:#general", "chat: x"]
    text = "---\nid: digest:2026-10-14\ntype: digest\ncovers:\n" + "".join(
        f"  - {line}\n" for line in ['"mail:<a,b@example.invalid>"', "'mail:\"quoted\"@x'", '"chat:#general"']
    )
    text += "by: '  spaced '\n---\n"
    parsed = parse_doc(text)
    assert parsed.meta.covers == awkward[:3]
    again = parse_doc(parsed.render())
    assert again.meta == parsed.meta
    assert again.meta.by == "  spaced "


def test_a_flow_list_with_a_comma_in_a_rid_is_caught() -> None:
    problems = refusal(doc(id="digest:x", type="digest", covers="[mail:<a,b@example.invalid>]", by="Ada"))
    assert problems[0].line == 4
    assert "isn't a rid" in problems[0].message


def test_unknown_keys_and_types_are_refused_strict_and_kept_lenient(caplog: pytest.LogCaptureFixture) -> None:
    text = doc(id="podcast:2026-10-14-x", type="podcast", covers="[calls:rec-0042]", by="Ada", mood="sunny")
    messages = [str(p) for p in refusal(text)]
    assert any("mood" in m and "line 6" in m for m in messages)
    caplog.set_level(logging.WARNING)
    lenient = parse_doc(text, lenient=True)
    assert lenient.extra == {"mood": "sunny"}
    assert lenient.meta.type == "podcast"
    assert "mood" in caplog.text
    assert "podcast" in caplog.text


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({"id": "brief:ben", "type": "minutes", "covers": "[calls:a]", "by": "Ada"}, "starts with 'brief'"),
        ({"id": "brief:con", "type": "brief", "covers": "[calls:a]", "by": "Ada"}, "Windows reserves"),
        ({"id": "brief:" + "a" * 81, "type": "brief", "covers": "[calls:a]", "by": "Ada"}, "at most 80"),
        ({"id": "brief:Ben_Sample", "type": "brief", "covers": "[calls:a]", "by": "Ada"}, "lowercase"),
        ({"id": "ben-sample", "type": "brief", "covers": "[calls:a]", "by": "Ada"}, "<type>:<slug>"),
        ({"id": "brief:ben", "type": "brief", "by": "Ada"}, "at least one rid"),
        ({"id": "brief:ben", "type": "brief", "covers": "[calls:a, calls:a]", "by": "Ada"}, "listed twice"),
        ({"id": "brief:ben", "type": "brief", "covers": "[calls:a]", "by": "''"}, "who or what wrote it"),
        ({"id": "brief:ben", "type": "brief", "covers": "[calls:a]", "by": "'" + "a" * 201 + "'"}, "at most 200"),
        ({"id": "brief:ben", "type": "brief", "covers": "[calls:a]", "by": '"two\\u2028lines"'}, "one line"),
        ({"id": "brief:ben", "type": "brief", "covers": "[calls:a]", "by": "Ada", "rev": "-1"}, "rev"),
        ({"id": "brief:ben", "type": "brief", "covers": "[calls:a]", "by": "Ada", "rev": "two"}, "rev"),
        ({"id": "brief:ben", "type": "brief", "covers": "[calls:a]", "by": "Ada", "supersedes": "brief:ben"}, "itself"),
        ({"id": "brief:ben", "type": "brief", "covers": "[calls:a]", "by": "Ada", "supersedes": "Bad"}, "isn't"),
        ({"id": "brief:ben", "type": "brief", "covers": "[calls:a]", "by": "Ada", "entities": "[Ben]"}, "entity"),
        (
            {"id": "brief:ben", "type": "brief", "covers": "[calls:a]", "by": "!!python/object/apply:os.system [x]"},
            "by",
        ),
        ({"id": "podcast:x", "type": "podcast", "covers": "[calls:a]", "by": "Ada"}, "isn't one of"),
    ],
)
def test_a_wrong_frontmatter_is_refused(fields: dict[str, str], says: str) -> None:
    assert any(says in str(p) for p in refusal(doc(**fields)))


def test_a_missing_key_is_a_problem_without_a_line() -> None:
    problem = refusal(doc(id="brief:ben", type="brief", covers="[calls:a]"))[0]
    assert (problem.line, problem.message) == (None, "by: Field required")


def test_a_value_yaml_cant_read_plain_is_quoted() -> None:
    parsed = parse_doc(doc(id="brief:ben", type="brief", covers="[calls:a]", by="'[draft, by Ada'"))
    assert 'by: "[draft, by Ada"' in parsed.render()
    assert parse_doc(parsed.render()).meta.by == "[draft, by Ada"


def test_scalars_where_lists_belong_and_nulls() -> None:
    parsed = parse_doc(doc(id="brief:ben", type="brief", covers="calls:a", by="Ada", rev="~", entities=""))
    assert (parsed.meta.covers, parsed.meta.rev, parsed.meta.entities) == (["calls:a"], 0, [])


def test_a_legacy_note_may_cover_nothing() -> None:
    assert parse_doc(doc(id="legacy-note:old-plans", type="legacy-note", by="import")).meta.covers == []


@pytest.mark.parametrize(
    ("text", "line", "says"),
    [
        ("# no frontmatter\n", 1, "starts with a --- line"),
        ("---\nid: brief:ben\n", 1, "no closing"),
        ("---\nid: brief:ben\nby: [unclosed\n---\n", 3, "isn't YAML"),
        ("---\n- a list\n---\n", 2, "keys and values"),
        ("---\nid: brief:ben\x00\n---\n", None, "isn't YAML"),
    ],
)
def test_a_doc_that_isnt_one_is_refused(text: str, line: int | None, says: str) -> None:
    problem = refusal(text)[0]
    assert problem.line == line
    assert says in problem.message


def test_problems_print_with_their_line_and_a_short_excerpt() -> None:
    long_line = "- " + "word " * 20
    assert str(DocProblem(7, "no anchors", long_line)).startswith('line 7 ("- word word')
    assert '..."): no anchors' in str(DocProblem(7, "no anchors", long_line))
    assert str(DocProblem(None, "whole")) == "doc: whole"
    refused = DocRefusedError([DocProblem(1, "a"), DocProblem(2, "b")])
    assert str(refused) == "the doc was refused: line 1: a (and 1 more)"
    assert str(DocRefusedError([])) == "the doc was refused: refused"


def test_paths_follow_from_the_id() -> None:
    assert doc_path("recording-note:2026-10-14-supplier-call").as_posix() == (
        "processed/recording-note/2026/10/2026-10-14-supplier-call.md"
    )
    assert doc_path("digest:2026-10-14").as_posix() == "processed/digest/2026/10/2026-10-14.md"
    assert doc_path("brief:ben-sample").as_posix() == "processed/brief/ben-sample.md"
    assert doc_path("brief:2026-plans").as_posix() == "processed/brief/2026-plans.md"


def test_which_lines_need_anchors() -> None:
    body = [
        "",  # free
        "# Heading",  # free
        "- a fact @00:01",  # content
        " \t\N{NO-BREAK SPACE}",  # free: blank
        "```text",  # skip
        "code @00:01",  # skip
        "```",  # skip
        "~~~~",  # skip
        "~~~",  # skip: too short to close
        "~~~~",  # skip: closes
        "<!-- a note -->",  # skip
        "<!-- a long",  # skip
        "note -->",  # skip
        "---",  # free
        "| who | what |",  # free: header
        "|-----|:----:|",  # free: separator
        "| Ben | 10 million @12:08 |",  # content
        "#hashtag line",  # content: not a heading
        "-",  # content: an empty list item
    ]
    assert line_roles(body) == [
        "free",
        "free",
        "content",
        "free",
        "skip",
        "skip",
        "skip",
        "skip",
        "skip",
        "skip",
        "skip",
        "skip",
        "skip",
        "free",
        "free",
        "free",
        "content",
        "content",
        "content",
    ]
    numbered = parse_doc(NOTE).roles()
    assert [(n, role) for n, _, role in numbered if role == "content"] == [(14, "content"), (15, "content")]
