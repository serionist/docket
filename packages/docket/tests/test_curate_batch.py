from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from curate_kit import NORTHWIND, curate_vault, entity, rid

from docket import userconfig
from docket.curate import batch as curate
from docket.curate import propose
from docket.curate.batch import Batch, Measure
from docket.curate.propose import Card
from docket.errors import DocketError
from docket.index.build import open_index
from docket.vault import curatelog
from docket.vault.config import NeverStore
from docket.vault.curatelog import AliasFields, Change, EntityFields, Line, read_all
from docket.vault.entitystore import EntityStore, Planned
from docket.vault.vault import Vault

T0 = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return curate_vault(tmp_path)


@pytest.fixture
def started(vault: Vault) -> Batch:
    return curate.start(vault)


def by_name(batch: Batch, name: str) -> Card:
    return next(c for c in batch.cards if c.name == name)


def states(vault: Vault, batch: Batch) -> dict[str, str]:
    now = curate.states(vault, batch, read_all(vault.root).lines)
    return {c.name: now[c.id] for c in batch.cards}


def questions_file(tmp_path: Path) -> Path:
    path = tmp_path / "questions.yaml"
    path.write_text(
        'schema_version: 1\nasked: "2026-10-14T10:00:00+02:00"\nquestions:\n'
        f'  - q: Nortwin pallets\n    want: ["r:{rid(0)}/transaction"]\n    kind: typo\n    lang: en\n'
        f'  - q: Cleo proofs\n    want: ["r:{rid(0)}/transaction"]\n    kind: exact-name\n    lang: en\n',
        encoding="utf-8",
    )
    userconfig.set_section("curate", {"questions": str(path)})
    return path


def test_a_round_keeps_its_cards_per_machine_and_reads_back(vault: Vault, started: Batch) -> None:
    assert curate.started(started.round) == started.started
    assert started.round.endswith("-test-box")
    assert [c.name for c in started.cards][:2] == ["Northwind", "Dóra"]
    assert started.left_out["common words"] > 0
    assert started.before is None
    assert curate.load(vault) == started
    assert curate.batch_path(vault).parent.name == "curate"
    assert set(states(vault, started).values()) == {"open"}


def test_a_batch_that_is_gone_or_doesn_t_read_is_none(vault: Vault, started: Batch) -> None:
    curate.batch_path(vault).write_text("{", encoding="utf-8")
    assert curate.load(vault) is None
    curate.batch_path(vault).unlink()
    assert curate.load(vault) is None
    assert curate.started("not a round") is None


def test_cards_are_named_by_id_or_by_round_and_number(vault: Vault, started: Batch) -> None:
    first = started.cards[0]
    assert curate.cards_for(started, [first.id], None)[1] == [first]
    assert curate.cards_for(started, ["1"], started.round)[1] == [first]
    with pytest.raises(DocketError, match="no round on this machine yet"):
        curate.cards_for(None, ["1"], None)
    with pytest.raises(DocketError, match=f"isn't this machine's current one, {started.round}"):
        curate.cards_for(started, ["1"], "2020-01-01T000000-other")
    with pytest.raises(DocketError, match="a card's number needs its round"):
        curate.cards_for(started, ["1"], None)
    with pytest.raises(DocketError, match="no card abcdef in round"):
        curate.cards_for(started, ["abcdef"], None)


def test_accepting_a_new_card_creates_its_entity_and_closes_the_card(vault: Vault, started: Batch) -> None:
    northwind = by_name(started, "Northwind")
    written = curate.accept(vault, started, [northwind], entity_type="customer")
    assert [(ln.op, ln.round, ln.card) for ln in written.lines] == [("create", started.round, northwind.id)]
    text = (vault.root / NORTHWIND).read_text(encoding="utf-8")
    assert "type: customer" in text and "{text: Nortwin, kind: asr}" in text
    assert states(vault, started)["Northwind"] == "accepted"
    with pytest.raises(DocketError, match=f"card {northwind.id} is accepted already"):
        curate.accept(vault, started, [northwind])


def test_type_and_id_change_one_new_card_only(vault: Vault, started: Batch) -> None:
    cleo = by_name(started, "Cleo Test")
    curate.accept(vault, started, [cleo], entity_id="cleo-t")
    assert (vault.root / "entities/person/cleo-t.yaml").exists()
    with pytest.raises(DocketError, match="--type and --id are for one new-entity card"):
        curate.accept(vault, started, started.cards[:2], entity_type="person")
    with pytest.raises(DocketError, match="--type or --id: one of person"):
        curate.accept(vault, started, [by_name(started, "Anna")], entity_type="pet")


def test_accepting_an_alias_card_adds_its_aliases(vault: Vault) -> None:
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    batch = curate.start(vault)
    alias = next(c for c in batch.cards if c.kind == "alias")
    written = curate.accept(vault, batch, [alias])
    assert [(ln.op, ln.key) for ln in written.lines] == [("add", "alias:northwind:nortwin")]
    assert states(vault, batch)["Northwind"] == "accepted"


def test_rejecting_a_card_stoplists_by_the_rule(vault: Vault, started: Batch) -> None:
    dora, northwind = by_name(started, "Dóra"), by_name(started, "Northwind")
    written = curate.reject(vault, started, [dora, northwind])
    assert [ln.key for ln in written.lines] == ["new:-:dora", "new:-:doraval", "new:-:northwind", "new:-:nortwin"]
    assert {ln.round for ln in written.lines} == {started.round}
    assert states(vault, started)["Dóra"] == "rejected"
    stoplist = curatelog.fold(read_all(vault.root).lines).stoplist
    assert "new:-:dora" in stoplist and "new:-:doraval" in stoplist


def test_a_rejection_naming_something_a_list_holds_writes_no_line_for_it(vault: Vault, started: Batch) -> None:
    vault.config = vault.config.model_copy(update={"never_store": NeverStore(keywords=["Nortwin"])})
    written = curate.reject(vault, started, [by_name(started, "Northwind")])
    assert [ln.key for ln in written.lines] == ["new:-:northwind"]


def test_an_alias_card_s_rejection_stoplists_each_alias(vault: Vault) -> None:
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    batch = curate.start(vault)
    alias = next(c for c in batch.cards if c.kind == "alias")
    assert [r.key for r in curate.rejects(alias, batch.round)] == ["alias:northwind:nortwin"]


def test_a_lifted_rejection_opens_its_card_again(vault: Vault, started: Batch) -> None:
    northwind = by_name(started, "Northwind")
    written = curate.reject(vault, started, [northwind])
    EntityStore(vault).lift([ln.key for ln in written.lines])
    assert states(vault, started)["Northwind"] == "open"
    again = curate.start(vault)
    back = by_name(again, "Northwind")
    assert back.id == northwind.id and states(vault, again)["Northwind"] == "open"
    assert [ln.op for ln in curate.accept(vault, again, [back]).lines] == ["create"]
    assert states(vault, again)["Northwind"] == "accepted"


def test_lifting_a_rejected_card_s_name_opens_it_again(vault: Vault, started: Batch) -> None:
    northwind = by_name(started, "Northwind")
    curate.reject(vault, started, [northwind])
    EntityStore(vault).lift(["new:-:northwind"])
    again = curate.start(vault)
    back = by_name(again, "Northwind")
    assert (back.id, [s.text for s in back.spellings]) == (northwind.id, ["Northwind"])
    assert states(vault, again)["Northwind"] == "open"
    assert [ln.op for ln in curate.accept(vault, again, [back]).lines] == ["create"]


def test_later_rounds_reach_past_what_earlier_ones_rejected(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(propose, "TOP", 3)
    shown: list[str] = []
    batch = curate.start(vault)
    while batch.cards and len(shown) < 50:
        shown += [c.name for c in batch.cards]
        curate.reject(vault, batch, batch.cards)
        batch = curate.start(vault)
    assert batch.cards == [] and batch.left_out["rejected"] > 0
    assert {"Northwind", "Dóra", "Harbour Fair", "Cleo Test", "Anna", "Rita Sample"} <= set(shown)


def test_a_hand_edit_that_makes_a_card_s_change_shows_it_done(vault: Vault, started: Batch) -> None:
    entity(vault, "entities/person/anna.yaml", "id: anna\ntype: person\nname: {en: Anna}\n")
    assert states(vault, started)["Anna"] == "done"


def test_an_undo_in_the_open_round_reopens_its_card(vault: Vault, started: Batch) -> None:
    anna = by_name(started, "Anna")
    created = curate.accept(vault, started, [anna]).lines[0]
    EntityStore(vault).undo([created.id], round_id=started.round)
    assert states(vault, started)["Anna"] == "open"
    assert "new:-:anna" not in curatelog.fold(read_all(vault.root).lines).stoplist


def planned(card: Card, aliases: tuple[AliasFields, ...]) -> Planned:
    fields = EntityFields(id=card.entity, type="person", name={"en": card.name}, aliases=aliases)
    return Planned(1, card.entity, Change(entity=fields), card=card.id)


def test_an_edit_naming_a_card_settles_it_and_rejects_what_it_left_out(vault: Vault, started: Batch) -> None:
    cleo = by_name(started, "Cleo Test")
    out, unticked = curate.settle(vault, started, [planned(cleo, (AliasFields(text="Cleo"),))])
    assert [(p.round, p.card) for p in out] == [(started.round, cleo.id)]
    assert [r.key for r in unticked] == ["alias:cleo-test:test cleo"]
    written = EntityStore(vault).apply(out, rejects=unticked)
    assert [ln.op for ln in written.lines] == ["create", "reject"]
    assert states(vault, started)["Cleo Test"] == "accepted"
    plain = Planned(1, "x", Change(alias=AliasFields(text="X")))
    assert curate.settle(vault, started, [plain]) == ([plain], [])
    with pytest.raises(DocketError, match="no card"):
        curate.settle(vault, started, [Planned(1, "x", Change(alias=AliasFields(text="X")), card="abcdef")])


def test_an_edit_that_moves_the_other_order_to_a_name_rejects_nothing(vault: Vault, started: Batch) -> None:
    rita = by_name(started, "Rita Sample")
    fields = EntityFields(id="rita-sample", type="person", name={"en": "Rita Sample", "hu": "Sample Rita"})
    assert curate.settle(vault, started, [Planned(1, "rita-sample", Change(entity=fields), card=rita.id)])[1] == []


def test_a_round_is_measured_before_and_after_its_last_card(vault: Vault, tmp_path: Path) -> None:
    questions_file(tmp_path)
    batch = curate.start(vault)
    assert batch.before is not None
    assert set(batch.before.kinds) == {"typo", "exact-name"}
    assert curate.after(vault, batch) == batch
    curate.reject(vault, batch, batch.cards[1:])
    curate.accept(vault, batch, batch.cards[:1])
    measured = curate.after(vault, batch)
    assert measured.after is not None
    assert curate.load(vault) == measured
    assert curate.after(vault, measured) == measured


def test_a_round_without_a_question_file_measures_nothing(vault: Vault, started: Batch) -> None:
    assert curate.measure(vault) is None
    assert curate.after(vault, started) == started


def line(n: int, minutes: float, round_: str | None, by: str = "user") -> Line:
    at = T0 + timedelta(minutes=minutes)
    return Line.model_validate(
        {
            "schema_version": 1,
            "id": f"{at:%Y%m%dT%H%M%S}-test-box-{n}",
            "op": "reject",
            "at": at,
            "machine": "test-box",
            "by": by,
            "via": "cli",
            "key": f"new:-:x{n}",
            "round": round_,
        }
    )


def test_review_minutes_are_the_gaps_through_a_round_each_capped(vault: Vault) -> None:
    first = curate.round_id(T0, "test-box")
    later = curate.round_id(T0 + timedelta(days=8), "laptop-1x")
    lines = [
        line(1, 2, first),
        line(2, 3, first),
        line(3, 30, first),
        line(4, 8 * 24 * 60 + 1, later),
        line(5, 4, None),
        line(6, 5, "not a round"),
    ]
    assert curate.minutes(lines) == {"2026-W43": 1.0, "2026-W42": 8.0}
    assert curate.last_round(lines, None) == T0 + timedelta(days=8)


def test_status_says_whether_a_round_is_due(vault: Vault, tmp_path: Path) -> None:
    empty = curate.status(vault)
    assert (empty.round, empty.due, empty.last, empty.questions) == (None, True, None, None)
    path = questions_file(tmp_path)
    batch = curate.start(vault)
    curate.reject(vault, batch, batch.cards[:1])
    now = curate.status(vault)
    assert (now.round, now.cards, now.open, now.due) == (batch.round, len(batch.cards), len(batch.cards) - 1, False)
    assert now.questions == str(path) and now.stoplist == 2 and now.after is None
    again = curate.status(vault, again=True)
    assert again.after is not None and again.before is not None


def test_a_round_shows_its_cards_with_snippets_cut_when_shown(vault: Vault, started: Batch) -> None:
    shown = curate.view(vault, started)
    northwind = shown.cards[0]
    assert northwind.state == "open"
    assert len(northwind.snippets) == 3
    first = northwind.snippets[0]
    assert first.mark is not None and first.text[first.mark[0] : first.mark[1]] == "Nortwin"
    assert shown.applied == []
    tier1 = line(9, 0, None, by="tier1").model_copy(update={"at": started.started + timedelta(seconds=1)})
    vault_lines = curatelog.read_all(vault.root).lines
    assert tier1 not in vault_lines


def test_cut_marks_the_spelling_where_it_is_as_written() -> None:
    long = "word " * 40 + "Nortwin said" + " word" * 40
    snippet = curate.cut(long, "Nortwin", "r:x")
    assert snippet.mark is not None and snippet.text[snippet.mark[0] : snippet.mark[1]] == "Nortwin"
    assert len(snippet.text) <= 2 * curate.WIDTH + len("Nortwin")
    assert curate.cut("Dora\nsaid", "Dóra", "r:x") == curate.Snippet(locator="r:x", text="Dora said")


def test_a_chunk_the_index_no_longer_holds_gives_no_snippet(vault: Vault, started: Batch) -> None:
    card = started.cards[0].model_copy(update={"evidence": [("r:gone", "Nortwin"), *started.cards[0].evidence]})
    with closing(open_index(vault)) as conn:
        assert "r:gone" not in [s.locator for s in curate.snippets(conn, card)]


def test_measures_compare(vault: Vault) -> None:
    measure = Measure(at=T0, all=(0.5, 0.25), kinds={"typo": (0.5, 0.25)})
    assert Measure.model_validate_json(measure.model_dump_json()) == measure


@pytest.mark.parametrize("value", [{"questions": 3}, {"questions": ""}, "questions"])
def test_curate_questions_must_be_a_file_s_path(value: Any) -> None:
    userconfig.set_section("curate", value)
    with pytest.raises(DocketError, match=r"curate.questions must be a question file.s path"):
        userconfig.curate_questions()


def test_an_edit_of_an_alias_card_rejects_the_aliases_it_left_out(vault: Vault) -> None:
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    batch = curate.start(vault)
    alias = next(c for c in batch.cards if c.kind == "alias")
    other = Planned(1, "northwind", Change(alias=AliasFields(text="North Wind")), card=alias.id)
    out, unticked = curate.settle(vault, batch, [other])
    assert [r.key for r in unticked] == ["alias:northwind:nortwin"]
    assert out[0].round == batch.round


def test_an_id_of_six_digits_is_an_id_not_a_number(vault: Vault, started: Batch) -> None:
    digits = started.cards[0].model_copy(update={"id": "559371"})
    batch = started.model_copy(update={"cards": [digits, *started.cards[1:]]})
    assert curate.cards_for(batch, ["559371"], None)[1] == [digits]
