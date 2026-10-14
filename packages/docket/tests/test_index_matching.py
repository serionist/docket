from __future__ import annotations

import random
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import copy_vault, index_config, make_record, template_with

from docket.index import build, matching, rebuild
from docket.index.languages import resolve
from docket.index.schema import create
from docket.vault.vault import Vault

ENTITIES = {
    "customer/northwind.yaml": "id: northwind\ntype: customer\nname: {en: Northwind}\naliases:\n"
    "  - {text: Northwind Traders}\n  - {text: Nortwin, kind: asr}\n  - {text: Northwind Logistix, kind: asr}\n"
    "  - {text: Noro, kind: asr}\n  - {text: Norton, kind: asr, needs: [ben-sample, harbour-site]}\n"
    "ids: {email_domain: northwind.example}\n",
    "site/harbour-site.yaml": "id: harbour-site\ntype: site\nname: {en: Harbour site}\n"
    "aliases:\n  - {text: harbourside, needs: [northwind]}\n",
    "person/ben-sample.yaml": "id: ben-sample\ntype: person\nname: {en: Ben Sample}\n"
    "ids: {chat: U0BEN, email: [ben@northwind.example]}\n",
    "person/dora-minta.yaml": "id: dora-minta\ntype: person\nname: {en: Dóra Minta}\naliases: [{text: Dóra}]\n",
    "person/mark.yaml": "id: mark\ntype: person\nname: {en: Mark}\n",
    "person/rob.yaml": "id: rob\ntype: person\nname: {en: Rob}\n",
    "person/kata.yaml": "id: kata\ntype: person\nname: {en: Kata}\n",
    "person/marco.yaml": "id: marco\ntype: person\nname: {en: Marco}\n",
    "person/balint.yaml": "id: balint\ntype: person\nname: {en: Bálint}\n",
    "place/szelvar.yaml": "id: szelvar\ntype: place\nname: {hu: Szélvár}\n",
}
MESSAGES = {
    1: ("Megbeszéltük a Northwind Tradersnél a részleteket.", "U0X"),
    2: ("Marcóval beszéltem tegnap.", "U0X"),
    3: ("Bálintnak elküldtem.", "U0X"),
    4: ("Dórának szóltam, és Katának is.", "U0X"),
    5: ("Szélvárnál találkozunk.", "U0X"),
    6: ("We are marking the boxes and I do the labels.", "U0X"),
    7: ("Nortvinnál voltunk.", "U0X"),
    8: ("Talked to Nortvin today.", "U0X"),
    9: ("Nortvan said hi.", "U0X"),
    10: ("Northwind Logistiks shipped it.", "U0X"),
    11: ("Norton fixed the printer.", "U0BEN"),
    12: ("Norton was here.", "U0X"),
    13: ("Norton at the harbourside.", "U0X"),
    14: ("Noru and Nora came by.", "U0X"),
}
FILLER = 60


def make_matching_vault(root: Path) -> Vault:
    vault = Vault.init(root / "vault", index_config(), "test-box")
    records = [make_record(n, author=who, text=text) for n, (text, who) in MESSAGES.items()]
    records += [make_record(100 + n, text=f"Már kész a robot {n}.") for n in range(FILLER)]
    records.append(
        make_record(
            rid="calls:rec-1",
            source="calls",
            kind="recording",
            container=None,
            native={
                "segments": [
                    {"start_ms": 0, "end_ms": 1000, "speaker": "Ben Sample", "text": "Hello."},
                    {"start_ms": 1000, "end_ms": 2000, "speaker": "Speaker 2", "text": "Hi."},
                ]
            },
        )
    )
    records.append(
        make_record(
            rid="mail:<m1@x.example>",
            source="mail",
            kind="mail",
            author="Ben Sample <ben@northwind.example>",
            text="Offer attached.",
        )
    )
    for source in ("chat", "calls", "mail"):
        vault.write_run(source, [r for r in records if r.source == source], datetime(2026, 10, 14, 7, tzinfo=UTC))
    for rel, text in ENTITIES.items():
        path = vault.root / "entities" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    legacy = vault.root / "processed" / "legacy-note" / "kata-notes.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "---\nid: legacy-note:kata-notes\ntype: legacy-note\nby: import\nentities: [kata, ghost]\n---\n\n"
        "# Odds and ends\n\nNotes.\n",
        encoding="utf-8",
    )
    return vault


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_with(tmp_path_factory, make_matching_vault)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    v = copy_vault(template, tmp_path)
    rebuild(v)
    return v


def query(vault: Vault, sql: str, *args: object) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(build.db_path(vault))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def mentioned(vault: Vault, n: int) -> dict[str, str]:
    return mentioned_rid(vault, make_record(n).rid)


def mentioned_rid(vault: Vault, rid: str) -> dict[str, str]:
    return dict(
        query(vault, "SELECT m.entity, m.how FROM mentions m JOIN chunks c ON c.id = m.chunk WHERE c.rid = ?", rid)
    )


def test_hungarian_inflection_finds_names_through_the_dative_form(vault: Vault) -> None:
    assert mentioned(vault, 1) == {"northwind": "text"}
    assert mentioned(vault, 2) == {"marco": "text"}
    assert mentioned(vault, 3) == {"balint": "text"}
    assert mentioned(vault, 4) == {"dora-minta": "text", "kata": "text"}
    assert mentioned(vault, 5) == {"szelvar": "text"}


def test_short_and_flooded_stem_forms_are_dropped_and_listed(vault: Vault) -> None:
    assert mentioned(vault, 6) == {}
    assert not any("mark" in mentioned(vault, 100 + n) or "rob" in mentioned(vault, 100 + n) for n in range(3))
    dropped = query(vault, "SELECT entity, term, how FROM alias_terms WHERE how IN ('short', 'common') ORDER BY 1, 2")
    assert ("dora-minta", "do", "short") in dropped
    assert ("mark", "mar", "common") in dropped
    assert ("rob", "rob", "common") in dropped


def test_asr_aliases_match_within_one_or_two_edits_from_five_letters(vault: Vault) -> None:
    assert mentioned(vault, 7) == {"northwind": "asr"}
    assert mentioned(vault, 8) == {"northwind": "asr"}
    assert mentioned(vault, 9) == {}
    # The plain name is in it too, and a text mention outranks a misspelling.
    assert mentioned(vault, 10) == {"northwind": "text"}
    assert mentioned(vault, 14) == {}
    terms = query(vault, "SELECT col, word, term, distance FROM alias_terms WHERE how = 'asr' ORDER BY 1, 2, 3")
    assert ("folded", 0, "nortvin", 1) in terms
    assert ("stem_hu", 0, "nortvin", 1) in terms
    assert ("folded", 1, "logistiks", 2) in terms


def test_needs_counts_only_beside_an_unconditional_mention(vault: Vault) -> None:
    assert mentioned(vault, 11) == {"northwind": "needs", "ben-sample": "author"}
    assert mentioned(vault, 12) == {}
    assert mentioned(vault, 13) == {}


def test_speakers_authors_and_tags_are_mentions(vault: Vault) -> None:
    speakers = query(vault, "SELECT speaker, person FROM segments WHERE rid = 'calls:rec-1' ORDER BY ordinal")
    assert speakers == [("Ben Sample", "ben-sample"), ("Speaker 2", None)]
    by_chunk = query(
        vault, "SELECT c.rid, m.entity, m.how FROM mentions m JOIN chunks c ON c.id = m.chunk WHERE m.how != 'text' "
        "AND c.rid IN ('calls:rec-1', 'mail:<m1@x.example>') ORDER BY 1, 2",
    )  # fmt: skip
    # The speaker's label is in the transcript's text as well, so the name is found there first.
    assert by_chunk == [("mail:<m1@x.example>", "ben-sample", "author"), ("mail:<m1@x.example>", "northwind", "author")]
    assert mentioned_rid(vault, "calls:rec-1") == {"ben-sample": "text"}
    tagged = query(
        vault, "SELECT m.entity, m.how FROM mentions m JOIN chunks c ON c.id = m.chunk WHERE c.doc IS NOT NULL"
    )
    assert tagged == [("kata", "tagged")]
    problems = query(vault, "SELECT place, message FROM problems WHERE kind = 'tagged'")
    assert problems == [("processed/legacy-note/kata-notes.md", "entities: no entity has the id 'ghost'")]


def test_ids_are_kept_as_written_and_matched_ignoring_case(tmp_path: Path) -> None:
    v = Vault.init(tmp_path / "vault", index_config(), "test-box")
    at = datetime(2026, 10, 14, 7, tzinfo=UTC)
    v.write_run("chat", [make_record(1, author="U0CLEO", text="Hello from chat.")], at)
    mail = make_record(rid="mail:<a@x.example>", source="mail", kind="mail", author="cleo@Tests.Example", text="Hi.")
    v.write_run("mail", [mail], at)
    person = v.root / "entities" / "person" / "cleo-test.yaml"
    person.parent.mkdir(parents=True)
    person.write_text(
        "id: cleo-test\ntype: person\nname: {en: Cleo Test}\nids: {chat: U0Cleo, email: Cleo@tests.example}\n",
        encoding="utf-8",
    )
    rebuild(v)
    assert query(v, "SELECT key, value FROM external_ids ORDER BY key") == [
        ("chat", "U0Cleo"),
        ("email", "Cleo@tests.example"),
    ]
    assert mentioned_rid(v, make_record(1).rid) == {"cleo-test": "author"}
    assert mentioned_rid(v, mail.rid) == {"cleo-test": "author"}


def brute(word: str, limit: int, vocab: list[str]) -> list[tuple[str, int]]:
    def distance(a: str, b: str) -> int:
        row = list(range(len(b) + 1))
        for i, ca in enumerate(a, start=1):
            prev, row[0] = row[0], i
            for j, cb in enumerate(b, start=1):
                prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
        return row[-1]

    return [(t, d) for t in vocab if 0 < (d := distance(word, t)) <= limit]


def test_the_fuzzy_walk_finds_what_a_brute_force_distance_finds() -> None:
    rng = random.Random(7)  # noqa: S311 - seeded test words, not secrets
    vocab = sorted({"".join(rng.choice("abcdeno") for _ in range(rng.randint(3, 9))) for _ in range(5000)})
    for word in ("nordic", "abba", "cabbage", "eon", "deadbeef"):
        for limit in (1, 2):
            work = [0]
            assert matching.within(word, limit, vocab, work) == brute(word, limit, vocab)
            assert 0 < work[0] < sum(len(t) for t in vocab)
    assert matching.within("same", 1, ["same"]) == []


def test_a_multi_word_asr_alias_makes_at_most_twenty_phrases() -> None:
    conn = sqlite3.connect(":memory:")
    create(conn, resolve(["en"]))
    conn.execute(
        "INSERT INTO aliases (entity, ordinal, text, kind, lang, needs, folded) "
        "VALUES ('e', 0, 'alpha beta', 'asr', NULL, '[]', 'alpha beta')"
    )
    rows = [("e", 0, "folded", -1, "alpha beta", "written", 0)]
    rows += [("e", 0, "folded", w, f"{'ab'[w]}variant{n}", "asr", 1) for w in (0, 1) for n in range(8)]
    conn.executemany("INSERT INTO alias_terms VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    expr = matching.expressions(conn)[("e", 0)]
    assert expr.count(" OR ") + 1 == 1 + matching.MAX_PHRASES
    assert 'folded : "alpha beta"' in expr
    assert matching.expressions(conn, ["other"]) == {}


def test_an_entity_with_only_needs_aliases_has_no_query_expression() -> None:
    conn = sqlite3.connect(":memory:")
    create(conn, resolve(["en"]))
    conn.execute("INSERT INTO aliases VALUES ('e', 0, 'cond', 'plain', NULL, '[\"x\"]', 'cond')")
    conn.execute("INSERT INTO alias_terms VALUES ('e', 0, 'folded', -1, 'cond', 'written', 0)")
    assert matching.expressions(conn, ["e"], needs=False) == {}
    assert matching.expressions(conn, ["e"]) == {("e", 0): 'folded : "cond"'}
