"""Derived text: a doc redacted with its lines and anchors kept, and screened with the never-store keywords as it would
be committed. Secret-shaped strings are assembled from parts, as in test_redact.py; people and numbers are invented."""

from __future__ import annotations

from collections import Counter

import pytest

from docket.vault import derived
from docket.vault.anchors import trailing_anchors
from docket.vault.config import NeverStore, Redaction, RedactionPattern
from docket.vault.processed import DocRefusedError, ProcessedDoc, parse_doc
from docket.vault.redact import Redactor

PHONE = "+1 555 010 0188"
HIDDEN = "hunter2-" + "Example"
KEY = "PRIVATE " + "KEY-----"
BEGIN = "-----BEGIN OPENSSH " + KEY
END = "-----END OPENSSH " + KEY
ON = Redactor.from_config(Redaction(phone=True))
DEFAULTS = Redactor.from_config(Redaction())
DORA = NeverStore(keywords=["dora example"])


def body(*lines: str, doc_type: str = "recording-note", redactor: Redactor = ON) -> derived.Redacted:
    return derived.redact(doc_type, "\n".join(lines), redactor)


def doc(*lines: str, **front: str) -> tuple[ProcessedDoc, list[str]]:
    fields = {
        "id": "recording-note:2026-10-14-supplier-call",
        "type": "recording-note",
        "covers": "[calls:rec-0042]",
        "by": "claude, recall 2026-10-15",
        "entities": "[]",
        **front,
    }
    text = "---\n" + "".join(f"{k}: {v}\n" for k, v in fields.items()) + "---\n" + "\n".join(lines) + "\n"
    parsed = parse_doc(text)
    return parsed, text.split("\n")[1 : parsed.head_lines - 1]


def refused(*lines: str, lists: NeverStore = DORA, redactor: Redactor = ON, **front: str) -> list[str]:
    parsed, head = doc(*lines, **front)
    with pytest.raises(DocRefusedError) as e:
        derived.check(parsed, head, redactor, lists)
    for problem in e.value.problems:
        assert problem.excerpt == ""
        assert "dora" not in problem.message.lower()
    return [str(p) for p in e.value.problems]


def passes(*lines: str, lists: NeverStore = DORA, redactor: Redactor = ON, **front: str) -> ProcessedDoc:
    parsed, head = doc(*lines, **front)
    return derived.check(parsed, head, redactor, lists)[0]


# -- redaction ----------------------------------------------------------------------------------------------------


def test_a_marker_changes_a_line_s_length_only() -> None:
    out = body(f"- Cleo's direct line is {PHONE} @13:40", "- the call itself [r:calls:+15550100188@00:12]")
    assert out.body.split("\n") == [
        "- Cleo's direct line is [redacted:phone] @13:40",
        "- the call itself [r:calls:+15550100188@00:12]",
    ]
    assert (out.counts, out.lines) == (Counter({"phone": 1}), [0])
    assert [a.token for a in trailing_anchors(out.body.split("\n")[0]).anchors] == ["@13:40"]


def test_a_label_at_a_line_s_end_takes_its_value_on_the_next() -> None:
    out = body("the wifi password: @00:20", f"{HIDDEN} @00:30")
    assert out.body.split("\n") == ["the wifi password: @00:20", "[redacted:password] @00:30"]
    assert out.lines == [1]


def test_a_key_block_over_lines_makes_each_line_its_marker_and_keeps_the_anchors() -> None:
    out = body(f"{BEGIN} @00:10", "@00:20", "b3BlbnNzaC1rZXktdjEAAAAA @00:30", f"{END} @00:40", "- after it @00:50")
    lines = out.body.split("\n")
    assert lines[:4] == [f"[redacted:private-key] @00:{s}" for s in ("10", "20", "30", "40")]
    assert lines[4] == "- after it @00:50"
    assert [a.token for line in lines for a in trailing_anchors(line).anchors] == [
        "@00:10", "@00:20", "@00:30", "@00:40", "@00:50"
    ]  # fmt: skip


def test_an_unclosed_key_block_runs_to_the_end_with_every_anchor_kept() -> None:
    out = body("- before @00:05", f"{BEGIN} @00:10", "MIIEvQ and the rest was lost @00:20", "- later @00:30")
    assert out.body.split("\n") == ["- before @00:05", *(f"[redacted:private-key] @00:{s}" for s in ("10", "20", "30"))]


def test_a_table_row_fenced_code_and_a_heading_are_redacted_in_place() -> None:
    out = body(
        "## Cleo on " + PHONE,
        "| Who | Number |",
        "|---|---|",
        f"| Cleo | {PHONE} @13:40 |",
        "```",
        f"password: {HIDDEN}",
        "```",
    )
    assert out.body.split("\n") == [
        "## Cleo on [redacted:phone]",
        "| Who | Number |",
        "|---|---|",
        "| Cleo | [redacted:phone] @13:40 |",
        "```",
        "password: [redacted:password]",
        "```",
    ]


def test_a_legacy_note_and_a_line_with_a_bad_anchor_are_text_whole() -> None:
    legacy = body("rang [r:calls:+15550100188]", doc_type="legacy-note")
    assert legacy.body == "rang [r:calls:[redacted:phone]]"
    bad = body("- rang [r:calls:+15550100188")
    assert bad.body == "- rang [r:calls:[redacted:phone]"


def test_redacting_twice_is_once() -> None:
    once = body(f"- Cleo on {PHONE} @13:40", f"password: {HIDDEN} @13:50").body
    twice = derived.redact("recording-note", once, ON)
    assert (twice.body, twice.counts, twice.lines) == (once, Counter(), [])


def test_the_defaults_keep_a_phone_number_and_names_and_skip_a_disabled_pattern() -> None:
    settings = Redaction(patterns=[RedactionPattern(name="door", pattern=r"door code (?P<secret>\d+)", enabled=False)])
    out = body(
        f"- Ben Sample gave Cleo Test {PHONE} and the door code 4711 @13:40",
        f"password: {HIDDEN} @13:50",
        redactor=Redactor.from_config(settings),
    )
    assert out.body.split("\n") == [
        f"- Ben Sample gave Cleo Test {PHONE} and the door code 4711 @13:40",
        "password: [redacted:password] @13:50",
    ]


# -- the never-store screen ---------------------------------------------------------------------------------------


def test_a_keyword_in_a_body_line_refuses_the_doc_naming_the_line_only() -> None:
    assert refused("# Supplier call", "", "- Dora Example will call back @12:08") == [
        "line 10: says something on the never-store keywords list; leave it out"
    ]


@pytest.mark.parametrize(
    ("front", "key"),
    [
        ({"id": "recording-note:2026-10-14-dora-example-call"}, "id"),
        ({"entities": "[dora-example]"}, "entities"),
        ({"supersedes": "[minutes:2026-10-01-dora-example]"}, "supersedes"),
    ],
)
def test_a_keyword_in_the_slug_entities_or_supersedes_refuses_the_doc(front: dict[str, str], key: str) -> None:
    parsed, head = doc("- fine @12:08", **front)
    with pytest.raises(DocRefusedError) as e:
        derived.check(parsed, head, ON, DORA)
    [problem] = [str(p) for p in e.value.problems]
    assert problem.endswith(f"{key}: says something on the never-store keywords list; leave it out")


def test_a_keyword_in_a_legacy_note_s_old_path_refuses_it() -> None:
    [problem] = refused(
        "anything",
        id="legacy-note:x",
        type="legacy-note",
        covers="[]",
        old_paths="[notes/dora-example/x.md]",
        import_hash="sha256:" + "0" * 64,
    )
    assert "old_paths: says something" in problem


def test_a_keyword_only_in_an_anchor_s_rid_in_by_or_in_a_redacted_secret_passes() -> None:
    passes("- the call [r:calls:dora-example-call@00:12]", covers="[calls:dora-example-call]")
    passes("- fine @12:08", by="claude, the dora example run")
    stored = passes(f"- the password: dora-example-{HIDDEN} @12:08")
    assert "[redacted:password]" in stored.body
    assert "dora" not in stored.body.lower()


def test_a_built_in_marker_is_left_out_and_any_other_marker_is_screened() -> None:
    passes("- call [redacted:phone] back @12:08", lists=NeverStore(keywords=["phone"]))
    assert refused("- see [redacted:dora-example] @12:08")


def test_a_pattern_named_after_a_keyword_refuses_the_docs_it_fires_on_without_naming_it() -> None:
    settings = Redaction(patterns=[RedactionPattern(name="Dora Example account", pattern=r"acct-\d+")])
    [problem] = refused("- paid from acct-1234 @12:08", redactor=Redactor.from_config(settings))
    assert problem == f"line 8: {derived.PATTERN_SAYS}"
    passes("- nothing for the pattern here @12:08", redactor=Redactor.from_config(settings))


def test_a_keyword_over_a_line_break_names_both_lines() -> None:
    assert refused("- we met Dora @00:10", "Example at noon @00:20") == [
        "line 8: says something on the never-store keywords list; leave it out",
        "line 9: says something on the never-store keywords list; leave it out",
    ]


def test_a_keyword_over_three_lines_names_every_line_with_text() -> None:
    lists = NeverStore(keywords=["dora example ltd"])
    assert len(refused("- we met Dora @00:10", "Example @00:20", "Ltd today @00:30", lists=lists)) == 3


def test_no_keywords_means_nothing_is_screened() -> None:
    stored = passes("- Dora Example @12:08", lists=NeverStore())
    assert stored.body.startswith("- Dora Example")


def test_a_put_s_by_holding_what_redaction_hides_is_refused_and_a_keyword_in_it_passes() -> None:
    [problem] = refused("- fine @12:08", by=f"claude, call {PHONE}")
    assert problem == "line 5: by: holds what the phone rule would redact; say who wrote it, nothing more"
    assert PHONE not in problem
    passes("- fine @12:08", by="claude, sync 2026-10-15", lists=NeverStore(keywords=["sync"]))


def test_a_blank_line_in_a_hand_edit_s_frontmatter_is_read_past() -> None:
    text = "---\nid: brief:x\ntype: brief\n\nby: claude\n---\n- fine @00:01\n"
    assert derived.hand_edit("brief", text, text, ON, DORA).stops == []
