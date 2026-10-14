from __future__ import annotations

from typing import Any

import pytest
from docket_testkit import make_record

from docket.vault.config import NeverStore
from docket.vault.neverstore import fold, match, matches_text, strings
from docket.vault.records import Attachment


def test_nothing_listed_matches_nothing() -> None:
    assert match(make_record(), NeverStore()) is None
    assert match(make_record(), NeverStore(people=[""], containers=[" "], keywords=["  "])) is None


@pytest.mark.parametrize(
    ("entry", "fields"),
    [
        ("U0KID", {"author": "U0KID"}),
        ("kid@example.invalid", {"to": ["someone@example.invalid", "KID@Example.Invalid"]}),
        ("Kid Example <kid@example.invalid>", {"author": "kid@example.invalid"}),
        ("kid@example.invalid", {"author": "Kid Example <kid@example.invalid>"}),
        ("kid@example.invalid", {"native": {"cc": [{"address": "Kid <kid@example.invalid>"}]}}),
        ("Kid Example", {"native": {"from": {"name": "Kid Example"}}}),
    ],
)
def test_people_match_ids_and_addresses_anywhere_the_record_names_someone(entry: str, fields: dict[str, Any]) -> None:
    assert match(make_record(**fields), NeverStore(people=[entry])) == "people"


def test_a_person_named_only_in_the_text_is_a_keyword_not_a_person() -> None:
    record = make_record(text="Kid Example said hello")
    assert match(record, NeverStore(people=["Kid Example"])) is None
    assert match(record, NeverStore(keywords=["kid example"])) == "keywords"


@pytest.mark.parametrize("entry", ["chat:C0FAMILY", "C0FAMILY", "c0family"])
def test_a_container_matches_with_or_without_its_prefix(entry: str) -> None:
    assert match(make_record(container="chat:C0FAMILY"), NeverStore(containers=[entry])) == "containers"
    assert match(make_record(thread="chat:C0FAMILY"), NeverStore(containers=[entry])) == "containers"


def test_another_container_doesn_t_match() -> None:
    assert match(make_record(container="chat:C0WORK"), NeverStore(containers=["C0FAMILY"])) is None


@pytest.mark.parametrize(
    "fields",
    [
        {"text": "the Kovács birthday party"},
        {"title": "KOVACS BIRTHDAY"},
        {"text": "the kovacs\nbirthday is on friday"},
        {"attachments": [Attachment(name="Kovacs Birthday.pdf")]},
        {"native": {"segments": [{"start": 0, "text": "about the kovács birthday"}]}},
    ],
)
def test_keywords_match_whole_words_folded_anywhere_the_record_says_something(fields: dict[str, Any]) -> None:
    assert match(make_record(**fields), NeverStore(keywords=["Kovacs birthday"])) == "keywords"


def test_a_keyword_inside_a_longer_word_doesn_t_match() -> None:
    assert match(make_record(text="we start the art review"), NeverStore(keywords=["star"])) is None
    assert match(make_record(text="we start the art review"), NeverStore(keywords=["art"])) == "keywords"


def test_folding_and_the_native_walk() -> None:
    assert fold("Kovács ÁRVÍZTŰRŐ") == "kovacs arvizturo"
    assert list(strings({"a": ["b", {"c": 1}], "d": None})) == ["a", "b", "c", "d"]


CURLY = chr(0x2019)
NON_BREAKING_HYPHEN = chr(0x2011)


@pytest.mark.parametrize("apostrophe", [chr(0x2018), CURLY, chr(0x02BC)])
def test_a_curly_apostrophe_is_a_straight_one(apostrophe: str) -> None:
    assert fold(f"O{apostrophe}Brien") == "o'brien"
    assert match(make_record(text=f"met O{apostrophe}Brien today"), NeverStore(keywords=["O'Brien"])) == "keywords"
    assert match(make_record(author=f"O{apostrophe}Brien"), NeverStore(people=["o'brien"])) == "people"


@pytest.mark.parametrize("dash", [chr(c) for c in range(0x2010, 0x2016)])
def test_every_typographic_dash_is_a_hyphen(dash: str) -> None:
    assert fold(f"Jean{dash}Luc") == "jean-luc"
    assert match(make_record(text=f"Jean{dash}Luc said"), NeverStore(keywords=["Jean-Luc"])) == "keywords"


@pytest.mark.parametrize("written", ["dora-example", "dora_example", "call_with_dora", "DORA_TEAM"])
def test_a_one_word_keyword_matches_a_name_joined_by_punctuation(written: str) -> None:
    assert match(make_record(text=f"see {written} now"), NeverStore(keywords=["dora"])) == "keywords"


@pytest.mark.parametrize(
    ("keyword", "text"),
    [
        ("dora", "DoraExample"),
        ("Max", "set maxRetries to 5"),
        ("Mark", "call markAsRead() after the fetch"),
        ("Jo", "JoAnn from finance"),
        ("Kim", "KimTech invoice"),
        ("Hub", "push to GitHub"),
    ],
)
def test_a_one_word_keyword_never_splits_a_word_at_its_case_change(keyword: str, text: str) -> None:
    assert match(make_record(text=text), NeverStore(keywords=[keyword])) is None


@pytest.mark.parametrize(
    ("keyword", "text"),
    [("check-up", "can you check up on the build"), ("check up", "the yearly check-up"), ("Jean-Luc", "jean luc")],
)
def test_a_hyphen_in_a_keyword_also_matches_a_space(keyword: str, text: str) -> None:
    assert match(make_record(text=text), NeverStore(keywords=[keyword])) == "keywords"


@pytest.mark.parametrize(
    "written", ["dora-example", "dora_example", "DoraExample", "doraexample", f"Dora{NON_BREAKING_HYPHEN}Example"]
)
def test_a_two_word_keyword_matches_the_name_joined_any_way(written: str) -> None:
    assert match(make_record(text=f"see {written} now"), NeverStore(keywords=["dora example"])) == "keywords"
    assert match(make_record(text=f"see {written} now"), NeverStore(keywords=["DoraExample"])) == "keywords"


def test_a_two_word_keyword_is_found_inside_a_longer_camel_case_name() -> None:
    assert match(make_record(text="the DoraExampleTeam board"), NeverStore(keywords=["dora example"])) == "keywords"
    assert match(make_record(text="see McDonaldFoods"), NeverStore(keywords=["McDonald"])) == "keywords"


def test_a_two_word_keyword_needs_both_words_and_a_whole_token() -> None:
    lists = NeverStore(keywords=["dora example"])
    for text in ("call_with_dora", "dorado example", "doraexamples", "adoraexample"):
        assert match(make_record(text=text), lists) is None, text


@pytest.mark.parametrize(
    "text",
    ["notes/2026/dora-example/minutes.md", "dora_example_2026-10-14", "chat:C0TEST/DoraExample", "doraexample.md"],
)
def test_matches_text_screens_paths_slugs_and_ids(text: str) -> None:
    assert matches_text(text, NeverStore(keywords=["dora example"]))
    # A one-word keyword finds a word set apart by punctuation, never one inside a token or a case change.
    assert matches_text(text, NeverStore(keywords=["dora"])) == (text.startswith(("notes/", "dora_")))


def test_matches_text_holds_plain_strings_to_the_same_rule() -> None:
    assert not matches_text("start-the-review", NeverStore(keywords=["art"]))
    assert matches_text("art-review", NeverStore(keywords=["art"]))
    assert not matches_text("", NeverStore(keywords=["art"]))
    assert not matches_text("art", NeverStore())
    assert not matches_text("art", NeverStore(keywords=[" "]))


def test_a_word_written_with_inner_capitals_still_matches_whole() -> None:
    assert match(make_record(text="my new iPhone"), NeverStore(keywords=["iphone"])) == "keywords"
    assert match(make_record(text="lunch at McDonald"), NeverStore(keywords=["McDonald"])) == "keywords"


def test_html_entities_between_words_read_as_spaces() -> None:
    record = make_record(native={"body": "<p>Project&nbsp;Phoenix</p>"})
    assert match(record, NeverStore(keywords=["project phoenix"])) == "keywords"


def test_a_keyword_of_punctuation_only_still_matches_literally() -> None:
    assert match(make_record(text="we use ++ here"), NeverStore(keywords=["++"])) == "keywords"
    assert match(make_record(text="nothing here"), NeverStore(keywords=["++"])) is None
