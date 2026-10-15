from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import INDEX_NOW, copy_vault, index_template, make_record

from docket import render
from docket.errors import DocketError
from docket.index import build, rebuild
from docket.questions import (
    Found,
    Question,
    QuestionFileError,
    QuestionSet,
    answers,
    behind,
    load,
    parse_wanted,
    read_set,
    run,
    score_question,
    summarize,
)
from docket.vault.anchors import parse_anchor
from docket.vault.vault import Vault

TZ = ZoneInfo("Europe/Budapest")
HEAD = 'schema_version: 1\nasked: "2026-10-14T10:00:00+02:00"\nquestions:\n'
ONE = "  - q: pallet labels\n    want: [r:calls:rec-0100]\n    kind: exact-name\n    lang: en\n"


def read(text: str) -> QuestionSet:
    return read_set(text, "q.yaml", TZ)


def refused(text: str) -> str:
    with pytest.raises(QuestionFileError) as e:
        read(text)
    return str(e.value)


# -- the file -----------------------------------------------------------------------------------------------------


def test_a_question_file_loads_with_every_value_as_text(tmp_path: Path) -> None:
    path = tmp_path / "q.yaml"
    path.write_text(
        "﻿"
        + HEAD
        + ONE
        + "  - q: no\n    want: 'thread:chat:C0X/1.000100'\n    kind: time-bound\n    lang: no\n"
        + "    asked: 2026-10-01\n    note: asked in Norwegian\n",
        encoding="utf-8",
    )
    qs = load(path, TZ)
    assert qs.path == str(path) and qs.asked == datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
    first, second = qs.questions
    assert (first.n, first.q, first.kind, first.lang, first.asked, first.note) == (
        1, "pallet labels", "exact-name", "en", qs.asked, None,
    )  # fmt: skip
    # The base loader keeps `no` as text, and a lone locator is a list of one; a date is that day's start.
    assert (second.q, second.lang, second.note) == ("no", "no", "asked in Norwegian")
    assert [w.text for w in second.want] == ["thread:chat:C0X/1.000100"]
    assert second.asked == datetime(2026, 10, 1, tzinfo=TZ)


def test_a_time_without_an_offset_is_in_the_owner_s_zone() -> None:
    qs = read(HEAD.replace('"2026-10-14T10:00:00+02:00"', "2026-10-14T10:00") + ONE)
    assert qs.asked == datetime(2026, 10, 14, 10, 0, tzinfo=TZ)


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("- a\n", "a question file is a mapping"),
        ("a: [b\n", "isn't YAML"),
        ("? [a]\n: b\n", "isn't YAML"),
        (HEAD + ONE + "extra: 1\n", "unknown key 'extra'"),
        (HEAD.replace("schema_version: 1", "schema_version: 2") + ONE, "schema_version is 1"),
        (HEAD.replace("schema_version: 1\n", "") + ONE, "schema_version is 1"),
        (HEAD.replace('asked: "2026-10-14T10:00:00+02:00"\n', "") + ONE, "asked is missing"),
        (HEAD.replace('"2026-10-14T10:00:00+02:00"', "monday") + ONE, "asked is a date or a time"),
        (HEAD + "  []\n", "questions is a list of one or more"),
        (HEAD.replace("questions:\n", "questions: []\n"), "questions is a list of one or more"),
        (HEAD + ONE + ONE.replace("kind:", "wnat: [x]\n    kind:"), "question 2: unknown key 'wnat'"),
        (HEAD + ONE.replace("want: [r:calls:rec-0100]", "want: []"), "question 1: want is one or more"),
        (HEAD + ONE.replace("    want: [r:calls:rec-0100]\n", ""), "question 1: want is one or more"),
        (HEAD + ONE.replace("r:calls:rec-0100", "calls:rec-0100"), "question 1: 'calls:rec-0100' is r:<rid>"),
        (HEAD + ONE.replace("r:calls:rec-0100", "'r:calls:rec 0100'"), "has no spaces"),
        (HEAD + ONE.replace("r:calls:rec-0100", "'thread:nothing'"), "'nothing' isn't a rid"),
        (HEAD + ONE.replace("r:calls:rec-0100", "minutes:a#L9-3"), "the lines run backwards"),
        (HEAD + ONE.replace("exact-name", "name"), "kind is one of exact-name, paraphrase"),
        (HEAD + ONE.replace("lang: en", "lang: English"), "lang is a language code"),
        (HEAD + ONE.replace("    lang: en\n", ""), "question 1: lang is missing"),
        (HEAD + ONE.replace("q: pallet labels", "q: ''"), "question 1: q is empty"),
        (HEAD + ONE.replace("q: pallet labels", "q: [a]"), "question 1: q is text"),
        (HEAD + "  - just words\n", "question 1: is a mapping"),
        (HEAD + ONE + "    kind: typo\n", "line 8 repeats the key 'kind'"),
        (HEAD + ONE.replace("lang: en", "lang: en\n    asked: soon"), "question 1: asked is a date or a time"),
    ],
)
def test_a_file_that_can_t_be_read_is_refused_with_why(text: str, said: str) -> None:
    assert said in refused(text)


def test_a_missing_or_unreadable_file_is_said(tmp_path: Path) -> None:
    with pytest.raises(QuestionFileError, match="no such file"):
        load(tmp_path / "none.yaml", TZ)
    bad = tmp_path / "bad.yaml"
    bad.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(QuestionFileError, match="can't be read"):
        load(bad, TZ)


def test_wanted_locators_read_in_their_four_forms() -> None:
    assert parse_wanted("r:calls:rec-0100") == parse_wanted(" r:calls:rec-0100 ")
    w = parse_wanted("r:calls:rec-0100@12:08-12:40")
    assert (w.what, w.target, w.position, w.start, w.end) == ("record", "calls:rec-0100", "time", 728, 760)
    w = parse_wanted("r:calls:rec-0100@12:08")
    assert (w.position, w.start, w.end) == ("time", 728, 728)
    w = parse_wanted("r:mail:<a%5Db@example.invalid>#2-4")
    assert (w.target, w.position, w.start, w.end) == ("mail:<a]b@example.invalid>", "part", 2, 4)
    w = parse_wanted("minutes:2026-10-07-supplier-call#L7")
    assert (w.what, w.target, w.position, w.start, w.end) == ("doc", "minutes:2026-10-07-supplier-call", "lines", 7, 7)
    w = parse_wanted("minutes:2026-10-07-supplier-call")
    assert (w.what, w.position) == ("doc", "all")
    w = parse_wanted("thread:chat:C0OPS/1791400000.000100")
    assert (w.what, w.target) == ("thread", "chat:C0OPS/1791400000.000100")


# -- what answers -------------------------------------------------------------------------------------------------

CALL = "calls:rec-0100"


def found(locator: str, **fields: object) -> Found:
    fields.setdefault("group", CALL)
    return Found(rank=1, locator=locator, **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("wanted", "hit", "yes"),
    [
        # A record: any window, part or the whole.
        ("r:calls:rec-0100", found("r:calls:rec-0100@02:30-04:00"), True),
        ("r:calls:rec-0100", found("r:calls:rec-0100"), True),
        ("r:mail:<o@x.example>", found("r:mail:<o@x.example>#5-7"), True),
        # Rids compare exactly: a longer rid with the same start is another record.
        ("r:calls:rec-0100", found("r:calls:rec-0100-second@00:30-01:51"), False),
        # A moment or a stretch: windows overlapping it, ends included; the whole record.
        ("r:calls:rec-0100@04:00", found("r:calls:rec-0100@02:30-04:00"), True),
        ("r:calls:rec-0100@04:01", found("r:calls:rec-0100@02:30-04:00"), False),
        ("r:calls:rec-0100@03:50-05:00", found("r:calls:rec-0100@02:30-04:00"), True),
        ("r:calls:rec-0100@01:00-02:30", found("r:calls:rec-0100@02:30-04:00"), True),
        ("r:calls:rec-0100@00:10-02:29", found("r:calls:rec-0100@02:30-04:00"), False),
        ("r:calls:rec-0100@12:08", found("r:calls:rec-0100"), True),
        # Paragraphs: parts overlapping them; a time never answers paragraphs.
        ("r:mail:<o@x.example>#4", found("r:mail:<o@x.example>#1-4"), True),
        ("r:mail:<o@x.example>#5", found("r:mail:<o@x.example>#1-4"), False),
        ("r:mail:<o@x.example>#2", found("r:mail:<o@x.example>@00:01-00:05"), False),
        # A doc and its lines.
        ("minutes:a", found("minutes:a", lines=(14, 20)), True),
        ("minutes:a", found("minutes:ab", lines=(14, 20)), False),
        ("minutes:a#L20-30", found("minutes:a", lines=(14, 20)), True),
        ("minutes:a#L21", found("minutes:a", lines=(14, 20)), False),
        ("minutes:a#L14", found("r:calls:rec-0100"), False),
        # A thread or a recording: anything in its group.
        ("thread:calls:rec-0100", found("minutes:a", lines=(1, 2)), True),
        ("thread:calls:rec-0101", found("r:calls:rec-0100"), False),
        # A thread window holds its messages; a doc's section cites the raw its anchors point into.
        ("r:chat:C/2.0", found("r:chat:C/1.0", holds=frozenset({"chat:C/1.0", "chat:C/2.0"})), True),
        ("r:chat:C/3.0", found("r:chat:C/1.0", holds=frozenset({"chat:C/1.0", "chat:C/2.0"})), False),
        ("r:calls:rec-0100@03:00", found("minutes:a", cites=(parse_anchor("[r:calls:rec-0100@02:30-03:20]"),)), True),
        ("r:calls:rec-0100@04:00", found("minutes:a", cites=(parse_anchor("[r:calls:rec-0100@02:30-03:20]"),)), False),
        ("r:calls:rec-0101", found("minutes:a", cites=(parse_anchor("[r:calls:rec-0100@02:30-03:20]"),)), False),
        # A locator that doesn't parse answers nothing.
        ("r:calls:rec-0100", found("r:calls:rec 0100"), False),
    ],
)
def test_what_answers_a_wanted_locator(wanted: str, hit: Found, yes: bool) -> None:
    assert answers(parse_wanted(wanted), hit) is yes


# -- the scores ---------------------------------------------------------------------------------------------------

ASKED = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)


def question(n: int, kind: str, lang: str, *want: str) -> Question:
    return Question(n, f"q{n}", tuple(parse_wanted(w) for w in want), kind, lang, ASKED)


def test_recall_is_the_share_found_and_mrr_the_first_answer() -> None:
    hits = [
        Found(1, "r:calls:rec-0200", "calls:rec-0200"),
        Found(3, "r:calls:rec-0100@02:30-04:00", CALL),
        Found(3, "r:calls:rec-0100-second@02:30-04:00", CALL),
        Found(12, "r:mail:<o@x.example>", "mail:<o@x.example>"),
    ]
    two = score_question(question(1, "paraphrase", "en", "r:calls:rec-0100@03:00", "r:mail:<o@x.example>"), hits, 10)
    assert (two.ranks, two.recall, two.rr) == ([3, None], 0.5, 1 / 3)
    # A hit past k counts for nothing, and the first of several answering hits gives the rank.
    wide = score_question(question(2, "paraphrase", "en", "r:mail:<o@x.example>", "r:calls:rec-0200"), hits, 20)
    assert (wide.ranks, wide.recall, wide.rr) == ([12, 1], 1.0, 1.0)
    none = score_question(question(3, "typo", "hu", "r:calls:rec-0300"), hits, 10)
    assert (none.ranks, none.recall, none.rr) == ([None], 0.0, 0.0)


def test_kinds_and_languages_take_the_mean_of_their_questions() -> None:
    hits = [Found(1, "r:calls:rec-0100", CALL), Found(2, "r:calls:rec-0200", "calls:rec-0200")]
    results = [
        score_question(question(1, "typo", "hu", "r:calls:rec-0100"), hits, 10),
        score_question(question(2, "exact-name", "en", "r:calls:rec-0200"), hits, 10),
        score_question(question(3, "exact-name", "hu", "r:calls:rec-0300"), hits, 10),
    ]
    report = summarize("q.yaml", 10, results, [], None)
    # Kinds in the file's order of the five; languages sorted.
    assert list(report.kinds) == ["exact-name", "typo"] and list(report.langs) == ["en", "hu"]
    assert report.kinds["exact-name"].model_dump() == {"questions": 2, "recall": 0.5, "mrr": 0.25}
    assert report.kinds["typo"].model_dump() == {"questions": 1, "recall": 1.0, "mrr": 1.0}
    assert report.langs["hu"].model_dump() == {"questions": 2, "recall": 0.5, "mrr": 0.5}
    assert report.all.model_dump() == {"questions": 3, "recall": 2 / 3, "mrr": 0.5}


# -- a run --------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    v = copy_vault(index_template(tmp_path_factory), tmp_path_factory.mktemp("questions"))
    rebuild(v)
    return v


def test_a_run_scores_each_question_through_search(vault: Vault) -> None:
    text = HEAD.replace('"2026-10-14T10:00:00+02:00"', INDEX_NOW.isoformat()) + "\n".join(
        [
            "  - q: deadline with Ben",
            "    want: ['recording-note:2026-10-07-supplier-call#L21', 'r:calls:rec-0100@02:40']",
            "    kind: paraphrase",
            "    lang: en",
            "  - q: pallet labels",
            "    want: ['thread:chat:C0OPS/1791400000.000100', 'r:chat:C0OPS/1791401000.000100']",
            "    kind: exact-name",
            "    lang: en",
            "  - q: '?!'",
            "    want: [r:cal:evt-0001]",
            "    kind: typo",
            "    lang: en",
            "  - q: zebracorn",
            "    want: [r:calls:rec-9999, 'minutes:no-such-doc', 'thread:chat:C0NONE/1.0']",
            "    kind: typo",
            "    lang: en",
        ]
    )
    report = run(vault, read(text), k=10)
    deadline, pallets, nothing, unknown = report.questions
    # The doc's lines come first, and the same section answers the raw stretch its anchors cite.
    assert deadline.ranks == [1, 1] and deadline.rr == 1.0
    # The thread's summary is in its group; the message is held by a thread window collapsed under a message hit.
    assert pallets.ranks[0] == 1 and pallets.ranks[1] is not None
    assert nothing.refused is not None and "nothing to search for" in nothing.refused and nothing.recall == 0
    assert unknown.ranks == [None, None, None]
    assert report.unknown == ["r:calls:rec-9999", "minutes:no-such-doc", "thread:chat:C0NONE/1.0"]
    assert report.behind is None and report.k == 10


def test_k_is_from_1_to_100(vault: Vault) -> None:
    for k in (0, 101):
        with pytest.raises(DocketError, match="k is from 1 to 100"):
            run(vault, read(HEAD + ONE), k=k)


def test_a_set_found_whole_prints_no_misses() -> None:
    hits = [Found(2, "r:calls:rec-0100", CALL)]
    whole = score_question(question(1, "typo", "hu", "r:calls:rec-0100"), hits, 10)
    lines = render.eval_report(summarize("q.yaml", 10, [whole], [], None))
    assert lines[-1].split() == ["all", "1", "1.00", "0.50"] and not any("missed" in ln for ln in lines)


def test_behind_names_what_the_index_lacks(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> None:
    v = copy_vault(index_template(tmp_path_factory), tmp_path)
    assert behind(v) is None
    rebuild(v)
    assert behind(v) is None
    v.write_run("chat", [make_record(400, text="A new message.")], datetime(2026, 10, 14, 9, tzinfo=UTC))
    (v.root / "entities" / "person" / "cleo-test.yaml").write_text(
        "id: cleo-test\ntype: person\nname: {en: Cleo Test}\n", encoding="utf-8"
    )
    doc = next((v.root / "processed").rglob("*.md"))
    doc.write_text(doc.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert behind(v) == (
        "the index is behind the vault: 1 new run file(s), 1 changed doc(s), the entity files; "
        "docket index update for current numbers"
    )
    first = sorted((v.root / "raw" / "cal").rglob("*.jsonl"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert behind(v) == "the index is behind the vault: 1 indexed run file(s) changed; docket index rebuild"
    build.db_path(v).unlink()
    assert behind(v) is None
