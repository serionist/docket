from __future__ import annotations

from typing import Any

import pytest
from docket_testkit import make_record

from docket.vault.config import NeverStore
from docket.vault.neverstore import fold, match, strings
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
