from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from curate_kit import ANN, NORTHWIND, curate_vault, entity, more_recordings, rid
from docket_testkit import note

from docket import index, recall
from docket.curate import propose
from docket.curate.propose import Card, Mined, card_id, mine, slug
from docket.curate.spellings import DOCS, HEARD, LABEL, Seen
from docket.index.build import db_path
from docket.index.languages import language
from docket.vault.config import NeverStore
from docket.vault.curatelog import AliasFields, Change, EntityFields, Line, fold, folded
from docket.vault.vault import Vault

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return curate_vault(tmp_path)


def mined(vault: Vault, lines: Iterable[Line] = ()) -> Mined:
    conn = sqlite3.connect(db_path(vault))
    try:
        return mine(conn, vault, fold(lines))
    finally:
        conn.close()


def card(found: Mined, name: str) -> Card:
    return next(c for c in found.cards if c.name == name)


def names(found: Mined) -> list[str]:
    return [c.name for c in found.cards]


def spellings(c: Card) -> list[tuple[str, str, str]]:
    return [(s.text, s.trust, s.add) for s in c.spellings]


def line(op: str, n: int, key: str, minutes: int = 0, **fields: Any) -> Line:
    return Line.model_validate(
        {
            "schema_version": 1,
            "id": f"{T0 + timedelta(minutes=minutes):%Y%m%dT%H%M%S}-test-box-{n}",
            "op": op,
            "at": T0 + timedelta(minutes=minutes),
            "machine": "test-box",
            "by": "user",
            "via": "cli",
            "key": key,
            **fields,
        }
    )


def test_a_misheard_name_ranks_first_and_joins_its_written_spelling(vault: Vault) -> None:
    found = mined(vault)
    assert names(found) == ["Northwind", "Dóra", "Harbour Fair", "Cleo Test", "Anna", "Rita Sample"]
    northwind = found.cards[0]
    assert (northwind.kind, northwind.type, northwind.entity, northwind.n) == ("new", "term", "northwind", 1)
    assert spellings(northwind) == [("Northwind", "docs", "name"), ("Nortwin", "heard", "asr")]
    assert (northwind.missed_chunks, northwind.missed_containers) == (3, 3)
    assert northwind.id == card_id("new:-:northwind") and northwind.key == "new:-:northwind"
    assert [text for _, text in northwind.evidence[:3]] == ["Nortwin"] * 3
    assert northwind.evidence[0][0].startswith(f"r:{rid(2)}/transaction@")
    assert northwind.newest.startswith("2026-10-1")


def test_a_label_heads_a_person_with_its_first_word_and_the_hungarian_order(vault: Vault) -> None:
    cleo = card(mined(vault), "Cleo Test")
    assert (cleo.type, cleo.entity, cleo.lang) == ("person", "cleo-test", "en")
    assert spellings(cleo) == [
        ("Cleo Test", "label", "name"),
        ("Cleo", "docs", "plain"),
        ("Test Cleo", "unseen", "plain"),
    ]
    assert cleo.spellings[2].chunks == 0
    assert (cleo.missed_chunks, cleo.containers) == (0, 3)


def test_a_case_form_joins_its_word_and_adds_nothing(vault: Vault) -> None:
    dora = card(mined(vault), "Dóra")
    assert spellings(dora) == [("Dóra", "docs", "name"), ("Dórával", "heard", "no"), ("Dora", "typed", "no")]
    assert [s.alone for s in dora.spellings] == [True, False, True]
    assert (dora.adds(), dora.type) == ([], "term")


def test_tier_3_counts_what_it_leaves_out(vault: Vault) -> None:
    left = mined(vault).left_out
    assert left["one-off words"] == 1
    assert left["common words"] >= 3
    assert left["short"] >= 1
    assert left["rare phrases"] >= 1
    vault.config = vault.config.model_copy(update={"never_store": NeverStore(keywords=["Anna"])})
    found = mined(vault)
    assert "Anna" not in names(found)
    assert found.left_out["never-store"] == 1


def test_a_case_form_that_is_a_name_of_its_own_stays_apart(vault: Vault) -> None:
    # Hungarian's case forms of Ann include Anná, which folds to Anna: a label says Anna is someone.
    entity(vault, ANN, "id: ann-sample\ntype: person\nname: {en: Ann Sample}\naliases:\n  - {text: Ann}\n")
    found = mined(vault)
    assert card(found, "Anna").kind == "new"
    assert all(c.entity != "ann-sample" for c in found.cards)


def test_an_entity_gets_an_alias_card_for_its_misspelling_where_it_is_mentioned(vault: Vault) -> None:
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    found = mined(vault)
    alias = next(c for c in found.cards if c.kind == "alias")
    assert (alias.entity, alias.type, alias.name, alias.key) == (
        "northwind",
        "customer",
        "Northwind",
        "alias:northwind:nortwin",
    )
    assert spellings(alias) == [("Nortwin", "heard", "asr")]
    assert (alias.missed_chunks, alias.missed_containers) == (3, 3)
    assert found.left_out["known"] == 1
    assert "Northwind" not in [c.name for c in found.cards if c.kind == "new"]


def test_a_label_whose_first_word_is_one_person_s_reaches_them(vault: Vault) -> None:
    entity(vault, "entities/person/anna-sample.yaml", "id: anna-sample\ntype: person\nname: {en: Anna Sample}\n")
    anna = next(c for c in mined(vault).cards if c.entity == "anna-sample")
    assert (anna.kind, anna.name, [s.text for s in anna.adds()]) == ("alias", "Anna Sample", ["Anna"])


def test_a_word_that_only_starts_a_known_name_where_it_is_mentioned_adds_nothing(vault: Vault) -> None:
    entity(vault, "entities/person/cleo-test.yaml", "id: cleo-test\ntype: person\nname: {en: Cleo Test}\n")
    found = mined(vault)
    assert all(c.entity != "cleo-test" and c.name not in ("Cleo", "Cleo Test") for c in found.cards)


def test_an_alias_card_that_adds_no_mention_isn_t_made(vault: Vault) -> None:
    text = "id: northwind\ntype: customer\nname: {en: Northwind}\naliases:\n  - {text: Nortwin, kind: asr}\n"
    entity(vault, NORTHWIND, text)
    assert all(c.entity != "northwind" for c in mined(vault).cards)


def test_a_stoplisted_spelling_never_shows_in_the_role_its_key_names(vault: Vault) -> None:
    rejected = [line("reject", 1, "new:-:northwind"), line("reject", 2, "new:-:nortwin")]
    found = mined(vault, rejected)
    assert "Northwind" not in names(found)
    assert found.left_out["rejected"] == 2
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    assert any(c.kind == "alias" for c in mined(vault, rejected).cards)
    stopped = [line("reject", 1, "alias:northwind:nortwin")]
    assert all(c.kind != "alias" for c in mined(vault, stopped).cards)


def test_a_rejected_alias_stays_a_candidate_as_a_new_name(vault: Vault) -> None:
    entity(vault, "entities/person/anna-sample.yaml", "id: anna-sample\ntype: person\nname: {en: Anna Sample}\n")
    found = mined(vault, [line("reject", 1, "alias:anna-sample:anna")])
    assert all(c.entity != "anna-sample" for c in found.cards)
    anna = card(found, "Anna")
    assert (anna.kind, spellings(anna)) == ("new", [("Anna", "label", "name")])


def test_a_case_form_matching_judges_common_for_an_entity_doesn_t_reach_it(vault: Vault) -> None:
    # Jane's accusative is Janet. Where Janet is everywhere, matching doesn't count it as Jane (#93): Janet is a name.
    agreed = "Yes, Janet sent the drawings over. Ok, Jane agreed."
    more_recordings(vault, [agreed] * 3 + ["Yes, Janet sent the drawings over."] * 57)
    text = "id: jane-sample\ntype: person\nname: {en: Jane Sample}\naliases:\n  - {text: Jane}\n"
    entity(vault, "entities/person/jane-sample.yaml", text)
    found = mined(vault)
    assert all(c.entity != "jane-sample" for c in found.cards)
    janet = card(found, "Janet")
    assert (janet.kind, spellings(janet)) == ("new", [("Janet", "heard", "name")])


def test_an_entity_s_case_form_adds_nothing_to_its_card(vault: Vault) -> None:
    heard = ["We sent Otilia the proofs.", "We called Otilia back.", "Tegnap is Ottiliával.", "Holnap is Ottiliával."]
    more_recordings(vault, heard, noted=["Ottilia sent the proofs."] * 4)
    text = "id: ottilia-sample\ntype: person\nname: {en: Ottilia Sample}\naliases:\n  - {text: Ottilia}\n"
    entity(vault, "entities/person/ottilia-sample.yaml", text)
    ottilia = [c for c in mined(vault).cards if c.entity == "ottilia-sample"]
    assert [spellings(c) for c in ottilia] == [[("Otilia", "heard", "asr"), ("Ottiliával", "heard", "no")]]
    # With Otilia rejected as her alias, the case form alone adds nothing, and is no new name either.
    found = mined(vault, [line("reject", 1, "alias:ottilia-sample:otilia")])
    assert all(c.entity != "ottilia-sample" for c in found.cards)
    assert card(found, "Otilia").kind == "new" and "Ottiliával" not in names(found)


def test_a_written_name_near_an_entity_s_is_a_name_of_its_own(vault: Vault) -> None:
    for n in (0, 1):
        covers, lines = [f"{rid(n)}/transaction"], ["- The pallets go to Northwood, our other customer. @00:00"]
        recall.doc_put(vault, note(f"minutes:2026-10-0{5 + n}-extra-{n}", covers=covers, lines=lines))
    index.update(vault)
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    found = mined(vault)
    assert spellings(card(found, "Northwood")) == [("Northwood", "docs", "name")]
    assert [(c.entity, c.key) for c in found.cards if c.kind == "alias"] == [("northwind", "alias:northwind:nortwin")]


def test_an_alias_card_leaves_out_a_spelling_rejected_for_it_and_offers_the_rest(vault: Vault) -> None:
    for n in (0, 1):
        covers, lines = [f"{rid(n)}/transaction"], ["- Cleo Sample checked the proofs. @00:00"]
        recall.doc_put(vault, note(f"minutes:2026-10-0{5 + n}-cleo-{n}", covers=covers, lines=lines))
    entity(vault, "entities/person/cleo-sample.yaml", "id: cleo-sample\ntype: person\nname: {en: Cleo Sample}\n")
    whole = next(c for c in mined(vault).cards if c.entity == "cleo-sample")
    assert [s.text for s in whole.adds()] == ["Cleo Test", "Cleo"]
    found = mined(vault, [line("reject", 1, "alias:cleo-sample:cleo test")])
    rest = next(c for c in found.cards if c.entity == "cleo-sample")
    assert ([s.text for s in rest.adds()], rest.key) == (["Cleo"], "alias:cleo-sample:cleo")


def test_two_misspellings_of_one_entity_make_one_card(vault: Vault) -> None:
    more_recordings(vault, ["We sent Norwind the pallets."] * 2, noted=["Northwind waits."] * 2)
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    northwind = [c for c in mined(vault).cards if c.entity == "northwind"]
    assert [spellings(c) for c in northwind] == [[("Nortwin", "heard", "asr"), ("Norwind", "heard", "asr")]]


def test_spellings_that_reach_an_entity_go_on_besides_the_window(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    monkeypatch.setattr(propose, "TOP", 0)
    found = mined(vault)
    assert [c.key for c in found.cards if c.kind == "alias"] == ["alias:northwind:nortwin"]
    assert sorted(c.name for c in found.cards if c.kind == "new") == ["Anna", "Cleo Test", "Rita Sample"]


def test_a_rejected_name_s_label_stays_a_candidate(vault: Vault) -> None:
    found = mined(vault, [line("reject", 1, "new:-:cleo test")])
    cleo = card(found, "Cleo")
    assert spellings(cleo) == [("Cleo", "docs", "name")]


def created() -> Line:
    fields = EntityFields(
        id="northwind", type="customer", name={"en": "Northwind"}, aliases=(AliasFields(text="Nortwin", kind="asr"),)
    )
    return line("create", 1, "new:-:northwind", entity="northwind", change=Change(entity=fields))


def test_an_undone_create_stoplists_its_name_and_its_joined_spellings_unless_lifted(vault: Vault) -> None:
    create = created()
    undo = line("undo", 2, create.key, minutes=1, entity="northwind", of=create.id)
    found = mined(vault, [create, undo])
    assert "Northwind" not in names(found)
    lifted = line("lift", 3, "new:-:northwind", minutes=2)
    again = mined(vault, [create, undo, lifted])
    assert spellings(card(again, "Northwind")) == [("Northwind", "docs", "name")]
    in_round = line("undo", 2, create.key, minutes=1, entity="northwind", of=create.id, round="r1")
    reopened = mined(vault, [create.model_copy(update={"round": "r1"}), in_round])
    assert "Northwind" in names(reopened)


def test_ids_and_slugs() -> None:
    assert slug("Dóra Minta-Nagy!") == "dora-minta-nagy"
    assert slug("x" * 100 + " y") == "x" * 80
    assert len(card_id("new:-:x")) == 6


def test_a_taken_id_gets_a_number(vault: Vault) -> None:
    entity(vault, "entities/term/northwind.yaml", "id: northwind\ntype: term\nname: {en: North Wind Farm}\n")
    found = mined(vault)
    assert card(found, "Northwind").entity == "northwind-2"


def test_at_most_fifteen_cards(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(propose, "MAX_CARDS", 2)
    assert names(mined(vault)) == ["Northwind", "Dóra"]


def test_a_person_s_other_order_written_once_is_their_alias(vault: Vault) -> None:
    rita = card(mined(vault), "Rita Sample")
    assert spellings(rita) == [("Rita Sample", "label", "name"), ("Sample Rita", "docs", "plain")]
    assert (rita.type, rita.spellings[1].chunks) == ("person", 1)


def test_the_other_order_goes_on_only_where_nothing_else_holds_it(vault: Vault) -> None:
    stopped = card(mined(vault, [line("reject", 1, "new:-:test cleo")]), "Cleo Test")
    assert [s.text for s in stopped.spellings] == ["Cleo Test", "Cleo"]
    text = "id: other-orders\ntype: term\nname: {en: Test Cleo}\naliases:\n  - {text: Sample Rita}\n"
    entity(vault, "entities/term/other-orders.yaml", text)
    found = mined(vault)
    assert [s.text for s in card(found, "Cleo Test").spellings] == ["Cleo Test", "Cleo"]
    rita = card(found, "Rita Sample")
    assert ([s.text for s in rita.spellings], rita.type) == (["Rita Sample"], "person")


def test_no_hungarian_no_unwritten_order(vault: Vault) -> None:
    english = vault.config.owner.model_copy(update={"languages": ["en"]})
    vault.config = vault.config.model_copy(update={"owner": english})
    cleo = card(mined(vault), "Cleo Test")
    assert [s.text for s in cleo.spellings] == ["Cleo Test", "Cleo"]


class _Places:
    """An index that holds nothing more than each spelling's own chunks, in the places given."""

    def __init__(self, places: dict[int, set[str]]) -> None:
        self.places = places

    def holding(self, words: object) -> set[int]:
        return set()

    def where(self, chunks: Iterable[int]) -> set[str]:
        return {p for c in chunks for p in self.places[c]}


def grouped(*seen: Seen) -> list[list[str]]:
    index = _Places({c: {f"rec-{c}"} for s in seen for c in s.chunks})
    g = propose._Groups(list(seen), cast(propose._Index, index))
    propose._group(g, [language("hu")])
    return sorted(sorted(seen[i].text for i in members) for members in g.groups())


def seen(text: str, chunks: set[int], trust: int = HEARD, *, label: bool = False) -> Seen:
    return Seen(text, tuple(folded(text).split()), mid=1, trust=trust, label=label, chunks=chunks)


def test_spellings_join_where_they_meet_and_only_then() -> None:
    assert grouped(seen("Dóra", {1, 2}, DOCS), seen("Dora", {3}), seen("Dórával", {1, 2})) == [
        ["Dora", "Dóra", "Dórával"]
    ]
    assert grouped(seen("Dóra", {1, 2}, DOCS), seen("Dórával", {1, 5})) == [["Dóra"], ["Dórával"]]
    assert grouped(seen("Dóra Minta", {1}, DOCS), seen("Minta Dóra", {2})) == [["Dóra Minta", "Minta Dóra"]]
    assert grouped(seen("Northwind", {1, 2}, DOCS), seen("Nortwin", {1, 2})) == [["Northwind", "Nortwin"]]
    assert grouped(seen("Northwind", {1, 2}), seen("Nortwin", {1, 2})) == [["Northwind"], ["Nortwin"]]


def test_a_label_or_a_written_run_s_head_is_never_a_case_form() -> None:
    assert grouped(seen("Ann", {1, 2}, DOCS), seen("Anna", {1, 2}, LABEL, label=True)) == [["Ann"], ["Anna"]]
    run = seen("Anna Sample", {1, 2}, DOCS)
    assert grouped(seen("Ann", {1, 2}, DOCS), seen("Anna", {1, 2}), run) == [["Ann"], ["Anna", "Anna Sample"]]


def test_a_word_joins_the_one_run_it_starts() -> None:
    assert grouped(seen("Cleo", {1, 2, 3}, DOCS), seen("Cleo Test", {1, 2}, LABEL, label=True)) == [
        ["Cleo", "Cleo Test"]
    ]
    two = grouped(seen("Ada", {1, 2}, DOCS), seen("Ada Example", {1, 2}, DOCS), seen("Ada Sample", {1, 2}, DOCS))
    assert two == [["Ada"], ["Ada Example"], ["Ada Sample"]]
    assert grouped(seen("Ada", {1, 2}, DOCS), seen("Ada Example", {3, 4}, DOCS)) == [["Ada"], ["Ada Example"]]
