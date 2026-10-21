from __future__ import annotations

import random
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import copy_vault, empty_vault, index_config, indexed, make_record, read_only, template_with

from docket.index import build, matching, rebuild, search
from docket.index.languages import hungarian_cases, resolve
from docket.index.schema import create
from docket.index.text import Analyzer
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
            segments=[
                {"start_ms": 0, "end_ms": 1000, "speaker": "Ben Sample", "text": "Hello."},
                {"start_ms": 1000, "end_ms": 2000, "speaker": "Speaker 2", "text": "Hi."},
            ],
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
    """The matching vault with its index built, once per session; tests copy it."""
    return indexed(tmp_path_factory, template_with(tmp_path_factory, make_matching_vault))


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return copy_vault(template, tmp_path)


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


# A vault about one customer: Acme in 270 of 400 messages, 110 of them only inflected. Mark and Sam are named often
# enough that the ratio rule lets their stem forms through, while már and the English same stem like them.
ONE_CUSTOMER = {
    "customer/acme.yaml": "id: acme\ntype: customer\nname: {en: Acme}\n",
    "person/mark.yaml": "id: mark\ntype: person\nname: {en: Mark}\n",
    "person/sam.yaml": "id: sam\ntype: person\nname: {en: Sam}\n",
    "person/tony.yaml": "id: tony\ntype: person\nname: {en: Tony}\n",
}
INFLECTED = [
    "Az Acménél voltunk", "Az Acménak elküldtem", "Az Acmét felhívtam", "Az Acmével egyeztettünk",
    "Az Acméhoz megyünk", "Az Acméról beszéltünk", "Az Acmében dolgoznak", "Az Acmének írtam",
]  # fmt: skip


def one_customer_message(n: int) -> tuple[str, set[str]]:
    """Message n's text and the entities it names."""
    named: set[str] = set()
    if n < 100:
        parts = [f"Az Acme kérte a darulistát, {n}."]
    elif n < 210:
        parts = [f"{INFLECTED[n % len(INFLECTED)]} a darulista miatt, {n}."]
    elif n < 230:
        parts = [f"Az Acme-nak elküldtem a listát, {n}."]
    elif n < 270:
        parts = [f"Acme's crane list is done, {n}."]
    else:
        parts = [f"A raktárban rendben van minden, {n}."]
    if n < 270:
        named.add("acme")
    if n % 8 in (0, 3, 5):
        parts.append("Már kész a terv.")
    if n % 8 == 1 or n % 40 == 2:
        parts.append("Mark szerint jó." if n % 8 == 1 else "Marknak szóltam, Markkal megbeszéltük.")
        named.add("mark")
    if n % 40 in (4, 7, 9, 13, 17, 19, 23):
        parts.append("The same crane again.")
    if n % 10 == 6 or n % 20 == 8:
        parts.append("Sammal beszéltem." if n % 20 == 16 else "Sam is on site.")
        named.add("sam")
    if n % 10 in (0, 3, 9):
        parts.append({0: "Tony hívott.", 3: "Tonyval beszéltem.", 9: "Tonynak írtam."}[n % 10])
        named.add("tony")
    return " ".join(parts), named


def make_one_customer_vault(root: Path) -> Vault:
    vault = Vault.init(root / "vault", index_config(), "test-box")
    records = [make_record(n, text=one_customer_message(n)[0]) for n in range(400)]
    vault.write_run("chat", records, datetime(2026, 10, 14, 7, tzinfo=UTC))
    for rel, text in ONE_CUSTOMER.items():
        path = vault.root / "entities" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return vault


@pytest.fixture(scope="module")
def one_customer(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    """One copy, its index built, for the tests that only read it: a write into it fails the run (DR-0531)."""
    return Vault.open(read_only(indexed(tmp_path_factory, template_with(tmp_path_factory, make_one_customer_vault))))


def mentions_of(vault: Vault, entity: str) -> set[int]:
    rids = {
        r[0]
        for r in query(vault, "SELECT c.rid FROM mentions m JOIN chunks c ON c.id = m.chunk WHERE entity = ?", entity)
    }
    return {n for n in range(400) if make_record(n).rid in rids}


def test_a_name_in_most_chunks_keeps_its_inflected_mentions(one_customer: Vault) -> None:
    assert mentions_of(one_customer, "acme") == set(range(270))
    forms = query(one_customer, "SELECT term, how FROM alias_terms WHERE entity = 'acme' AND col = 'stem_hu'")
    assert ("acm", "stem") in forms
    result = search(one_customer, "acménél", now=datetime(2026, 10, 15, tzinfo=UTC))
    assert [e.id for e in result.query.entities] == ["acme"]


def test_a_common_word_sharing_a_name_s_stem_never_counts_as_the_name(one_customer: Vault) -> None:
    # már and the English same are in over a fifth of the chunks, and each name as written in over a tenth as many as
    # its stem form: the forms stay, and count only beside the name as written or in a case form (DR-0350).
    forms = query(one_customer, "SELECT entity, term, how FROM alias_terms WHERE col = 'stem_hu'")
    assert {("mark", "mar", "stem"), ("sam", "sam", "stem")} <= set(forms)
    for entity in ("mark", "sam"):
        # Marknak, Markkal and Sammal included; no chunk saying only "Már kész" or "the same".
        assert mentions_of(one_customer, entity) == {n for n in range(400) if entity in one_customer_message(n)[1]}


# A team vault where no stem form is in a fifth of the chunks, so only the check per chunk tells Sam from the English
# same, and Mark from marked and market. Bálint is named only inflected; Ryan, Jason, Sam, Andy and Gary take front
# endings by sound, and Nimish's -sh doubles as s. Ben, Len, Pat, Herb, Des and Rob have case forms that fold to
# ordinary words: benné to benne, lenné to lenne, patról to patrol, herbért to Herbert, desért to desert, robért to
# Robert, who is someone else. Zoltán, István, Milán and Csaba are written without accents too, which Snowball cuts
# apart from the accented forms (zoltannak to zoltan, zoltánnak to zolt).
TEAM = {
    "person/mark.yaml": "id: mark\ntype: person\nname: {en: Mark}\n",
    "person/sam.yaml": "id: sam\ntype: person\nname: {en: Sam}\n",
    "person/balint.yaml": "id: balint\ntype: person\nname: {en: Bálint}\n",
    "person/ryan.yaml": "id: ryan\ntype: person\nname: {en: Ryan}\n",
    "person/jason.yaml": "id: jason\ntype: person\nname: {en: Jason}\n",
    "person/nimish.yaml": "id: nimish\ntype: person\nname: {en: Nimish}\n",
    "person/andy.yaml": "id: andy\ntype: person\nname: {en: Andy}\n",
    "person/gary.yaml": "id: gary\ntype: person\nname: {en: Gary}\n",
    "person/ben.yaml": "id: ben\ntype: person\nname: {en: Ben}\n",
    "person/len.yaml": "id: len\ntype: person\nname: {en: Len}\n",
    "person/pat.yaml": "id: pat\ntype: person\nname: {en: Pat}\n",
    "person/herb.yaml": "id: herb\ntype: person\nname: {en: Herb}\n",
    "person/des.yaml": "id: des\ntype: person\nname: {en: Des}\n",
    "person/rob.yaml": "id: rob\ntype: person\nname: {en: Rob}\n",
    "person/robert.yaml": "id: robert\ntype: person\nname: {en: Robert}\n",
    "person/zoltan.yaml": "id: zoltan\ntype: person\nname: {hu: Zoltán}\n",
    "person/istvan.yaml": "id: istvan\ntype: person\nname: {hu: István}\n",
    "person/milan.yaml": "id: milan\ntype: person\nname: {hu: Milán}\n",
    "person/csaba.yaml": "id: csaba\ntype: person\nname: {hu: Csaba}\n",
}
# The messages a line is in (n % mod == rest), the line, and the person it names.
Line = tuple[int, int, str, str | None]
TEAM_LINES: list[Line] = [
    (25, 3, "Sam is on site.", "sam"),
    (25, 11, "Sammal beszéltem.", "sam"),
    (50, 19, "Sam's truck is here.", "sam"),
    (50, 44, "Sammel beszéltem, Samnek elküldtem.", "sam"),
    (13, 5, "The same crane again.", None),
    (25, 7, "Mark szerint jó.", "mark"),
    (50, 21, "Marknak szóltam, Markkal megbeszéltük.", "mark"),
    (100, 33, "Markot hívtam.", "mark"),
    (20, 4, "The boxes are marked.", None),
    (20, 9, "The market is slow.", None),
    (40, 17, "Bálintnak elküldtem.", "balint"),
    (40, 37, "Bálinttal beszéltem.", "balint"),
    (50, 23, "Ryannel beszéltem.", "ryan"),
    (100, 61, "Jasonnek elküldtem.", "jason"),
    (100, 86, "Jasonnel megbeszéltük.", "jason"),
    (100, 41, "Nimishsel beszéltem.", "nimish"),
    (100, 91, "Nimishnek szóltam.", "nimish"),
    (50, 14, "Andyvel beszéltem.", "andy"),
    (50, 47, "Gary hívott.", "gary"),
    (50, 6, "Ben hívott.", "ben"),
    (25, 13, "Nincs benne a számla.", None),
    (50, 31, "Len szerint kész.", "len"),
    (25, 18, "Jó lenne holnap.", None),
    (50, 8, "Pat hívott.", "pat"),
    (25, 16, "The patrol route is ready.", None),
    (50, 27, "Herb is here.", "herb"),
    (25, 22, "Herbert sent the plan.", None),
    (50, 38, "Des hívott.", "des"),
    (25, 24, "The desert road is closed.", None),
    (50, 2, "Rob hívott.", "rob"),
    (25, 1, "Robert szerint kész.", "robert"),
    (50, 3, "Zoltannak irtam.", "zoltan"),
    (100, 9, "Zoltant kerdeztem.", "zoltan"),
    (100, 59, "Zoltánnal beszéltem.", "zoltan"),
    (50, 9, "Istvant kerdeztem.", "istvan"),
    (100, 12, "Istvánnak szóltam.", "istvan"),
    (50, 11, "Milannak szoltam.", "milan"),
    (50, 26, "Csaban mulik.", "csaba"),
]


def lines_message(lines: list[Line], n: int) -> tuple[str, set[str]]:
    """Message n's text and the people it names, from lines given as (mod, rest, line, person)."""
    picked = [(line, who) for mod, rest, line, who in lines if n % mod == rest]
    return " ".join([f"A raktárban minden rendben, {n}.", *(line for line, _ in picked)]), {w for _, w in picked if w}


def team_message(n: int) -> tuple[str, set[str]]:
    """Message n's text and the people it names."""
    return lines_message(TEAM_LINES, n)


def people_vault(root: Path, people: dict[str, str], lines: list[Line]) -> Vault:
    """400 messages made of the lines, and the people's entity files."""
    vault = Vault.init(root / "vault", index_config(), "test-box")
    records = [make_record(n, text=lines_message(lines, n)[0]) for n in range(400)]
    vault.write_run("chat", records, datetime(2026, 10, 14, 7, tzinfo=UTC))
    for rel, text in people.items():
        path = vault.root / "entities" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return vault


def make_team_vault(root: Path) -> Vault:
    return people_vault(root, TEAM, TEAM_LINES)


@pytest.fixture(scope="module")
def team(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    """One copy, its index built, for the tests that only read it: a write into it fails the run (DR-0531)."""
    return Vault.open(read_only(indexed(tmp_path_factory, template_with(tmp_path_factory, make_team_vault))))


def test_an_english_word_stemming_like_a_name_is_no_mention(team: Vault) -> None:
    # The forms are kept: under a fifth of the chunks, and each name as written in over a tenth as many.
    forms = query(team, "SELECT entity, term, how FROM alias_terms WHERE col = 'stem_hu'")
    assert {("sam", "sam", "stem"), ("mark", "mar", "stem")} <= set(forms)
    for entity in {e.split("/")[1].removesuffix(".yaml") for e in TEAM}:
        # Sammal, Sammel, Sam's, Marknak, Markot, Ryannel, Jasonnek, Nimishsel, Andyvel, every Bálint and Zoltannak,
        # Istvant, Milannak and Csaban without accents in; no chunk saying only the same, marked, market, benne, lenne,
        # patrol, Herbert or desert, and Robert only as Robert.
        assert mentions_of(team, entity) == {n for n in range(400) if entity in team_message(n)[1]}, entity


def test_a_query_word_stemming_like_a_name_stays_a_word(team: Vault) -> None:
    now = datetime(2026, 10, 15, tzinfo=UTC)
    same = search(team, "the same crane", now=now)
    assert same.query.entities == []
    assert "same" in same.query.words
    both = search(team, "same sammal", now=now).query
    assert [(e.id, e.words) for e in both.entities] == [("sam", "sammal")]
    assert both.words == ["same"]
    named = [
        ("markot", "mark"), ("sammel", "sam"), ("ryannel", "ryan"), ("jasonnek", "jason"), ("nimishsel", "nimish"),
        ("andyvel", "andy"), ("garyvel", "gary"), ("robert", "robert"), ("zoltannak", "zoltan"),
        ("zoltánnak", "zoltan"), ("istvant", "istvan"), ("milannak", "milan"),
    ]  # fmt: skip
    for word, entity in named:
        assert [e.id for e in search(team, word, now=now).query.entities] == [entity], word
    # A word that folds like a case form and stems elsewhere stays a word.
    for text, word in [("mi van benne", "benne"), ("the patrol route", "patrol"), ("jó lenne", "lenne")]:
        reading = search(team, text, now=now).query
        assert reading.entities == [], text
        assert word in reading.words, text
    # A search for Sam leads with his own chunks. The word sam still meets the same through its Hungarian stem, as any
    # word meets its inflections, but only his chunks mention him too.
    his = {make_record(n).rid for n in range(400) if "sam" in team_message(n)[1]}
    sam = search(team, "Sam", k=len(his), now=now)
    assert {h.rid for g in sam.groups for h in g.hits} == his


# A vault where a common word crowds out the stem form a name's case forms are cut to: már stems to mar, as Markkal,
# Marknak and Markot do, and bele to bel, as Bélával, Bélának and Béláról do, each in over ten times as many chunks as
# the name as written. No word stems to mark or bela, so each name keeps a stem form.
CROWDED = {
    "person/mark.yaml": "id: mark\ntype: person\nname: {en: Mark}\n",
    "person/bela.yaml": "id: bela\ntype: person\nname: {hu: Béla}\n",
}
CROWDED_LINES: list[Line] = [
    (3, 0, "Már kész a terv.", None),
    (3, 1, "Bele kell nézni.", None),
    (200, 2, "Mark szerint jó.", "mark"),
    (100, 5, "Markkal megbeszéltük.", "mark"),
    (100, 17, "Marknak szóltam.", "mark"),
    (100, 29, "Markot hívtam.", "mark"),
    (200, 8, "Béla szerint jó.", "bela"),
    (100, 11, "Bélával beszéltem.", "bela"),
    (100, 23, "Bélának elküldtem.", "bela"),
    (100, 41, "Béláról beszéltünk.", "bela"),
]


def make_crowded_vault(root: Path) -> Vault:
    return people_vault(root, CROWDED, CROWDED_LINES)


@pytest.fixture(scope="module")
def crowded(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    """One copy, its index built, for the tests that only read it: a write into it fails the run (DR-0531)."""
    return Vault.open(read_only(indexed(tmp_path_factory, template_with(tmp_path_factory, make_crowded_vault))))


def test_a_case_form_counts_beside_the_stem_form_it_is_cut_to_while_a_common_word_crowds_that_out(
    crowded: Vault,
) -> None:
    forms = set(query(crowded, "SELECT entity, term, how FROM alias_terms WHERE col = 'stem_hu'"))
    assert {("mark", "mar", "common"), ("mark", "mark", "stem")} <= forms
    assert {("bela", "bel", "common"), ("bela", "bela", "stem")} <= forms
    for entity in ("mark", "bela"):
        # Markkal, Marknak, Markot, Bélával, Bélának and Béláról in; no chunk saying only "Már kész" or "Bele kell".
        assert mentions_of(crowded, entity) == {n for n in range(400) if entity in lines_message(CROWDED_LINES, n)[1]}
    now = datetime(2026, 10, 15, tzinfo=UTC)
    for word, entity in [("markkal", "mark"), ("marknak", "mark"), ("markot", "mark"), ("bélával", "bela")]:
        assert [e.id for e in search(crowded, word, now=now).query.entities] == [entity], word
    for text in ("már kész", "bele kell"):
        assert search(crowded, text, now=now).query.entities == [], text


# A vault where a name's case form is also a common word, in over ten times as many chunks as the name as written:
# annál for Ann's Annál, évet for Eve's Evét, edit for Edi's Edit, belát for Béla's Bélát, the English name Evan for
# Éva's Éván, bent for Ben's Bent and dönt for Don's Dont. The first five are cut to a stem form the short or the ratio
# rule drops, while another is kept; Ben's and Don's only stem form drops too. Ann's, Eve's, Edi's, Éva's, Béla's and
# Bea's other case forms are named.
COMMON_WORDS = {
    "person/ann.yaml": "id: ann\ntype: person\nname: {en: Ann}\n",
    "person/eve.yaml": "id: eve\ntype: person\nname: {en: Eve}\n",
    "person/edi.yaml": "id: edi\ntype: person\nname: {hu: Edi}\n",
    "person/eva.yaml": "id: eva\ntype: person\nname: {hu: Éva}\n",
    "person/bela.yaml": "id: bela\ntype: person\nname: {hu: Béla}\n",
    "person/bea.yaml": "id: bea\ntype: person\nname: {hu: Bea}\n",
    "person/ben.yaml": "id: ben\ntype: person\nname: {en: Ben}\n",
    "person/don.yaml": "id: don\ntype: person\nname: {en: Don}\n",
}
COMMON_WORD_LINES: list[Line] = [
    (7, 0, "Annál jobb, ha holnap jön.", None),
    (7, 1, "Egy évet vártunk rá.", None),
    (7, 2, "Please edit the doc today.", None),
    (7, 3, "Ezt mindenki belát.", None),
    (7, 4, "Evan sent the plan.", None),
    (7, 5, "Bent vagyok az irodában.", None),
    (7, 6, "Holnap dönt a bizottság.", None),
    (200, 10, "Ann szerint jó.", "ann"),
    (100, 11, "Annhoz megyek, Anntól kaptam.", "ann"),
    (200, 12, "Eve szerint jó.", "eve"),
    (100, 13, "Evével beszéltem, Evének írtam.", "eve"),
    (200, 14, "Edi szerint jó.", "edi"),
    (100, 15, "Edivel beszéltem, Edinek írtam.", "edi"),
    (200, 16, "Éva szerint jó.", "eva"),
    (100, 17, "Évával beszéltem, Évának írtam.", "eva"),
    (200, 18, "Béla szerint jó.", "bela"),
    (100, 19, "Bélával beszéltem, Bélának írtam.", "bela"),
    (200, 20, "Bea szerint jó.", "bea"),
    (100, 21, "Beával beszéltem, Beának írtam.", "bea"),
    (200, 22, "Ben hívott.", "ben"),
    (200, 23, "Don hívott.", "don"),
]


def make_common_words_vault(root: Path) -> Vault:
    return people_vault(root, COMMON_WORDS, COMMON_WORD_LINES)


@pytest.fixture(scope="module")
def common_words(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    """One copy, its index built, for the tests that only read it: a write into it fails the run (DR-0531)."""
    return Vault.open(read_only(indexed(tmp_path_factory, template_with(tmp_path_factory, make_common_words_vault))))


def test_a_case_form_far_commoner_than_a_rare_name_counts_as_a_word(common_words: Vault) -> None:
    dropped = set(query(common_words, "SELECT entity, term FROM alias_terms WHERE col = 'folded' AND how = 'common'"))
    assert {("ann", "annal"), ("eve", "evet"), ("edi", "edit"), ("bela", "belat"), ("eva", "evan")} <= dropped
    # Ben's and Don's only stem form goes first, by the ratio rule as on main: bent stems to ben and dönt to don.
    stems = set(query(common_words, "SELECT entity, term, how FROM alias_terms WHERE col = 'stem_hu'"))
    assert {("ben", "ben", "common"), ("don", "don", "common")} <= stems
    for entity in {e.split("/")[1].removesuffix(".yaml") for e in COMMON_WORDS}:
        # Annhoz, Evével, Edivel, Évával, Bélával and Beával in; no chunk saying only annál, évet, edit, belát, Evan,
        # bent or dönt.
        assert mentions_of(common_words, entity) == {
            n for n in range(400) if entity in lines_message(COMMON_WORD_LINES, n)[1]
        }, entity
    now = datetime(2026, 10, 15, tzinfo=UTC)
    for text in ("annál jobb", "egy évet", "edit the doc", "mindenki belát", "evan sent", "bent vagyok", "dönt"):
        assert search(common_words, text, now=now).query.entities == [], text
    named = [
        ("annhoz", "ann"), ("evével", "eve"), ("edivel", "edi"), ("évával", "eva"), ("bélával", "bela"),
        ("beával", "bea"),
    ]  # fmt: skip
    for word, entity in named:
        assert [e.id for e in search(common_words, word, now=now).query.entities] == [entity], word


# A team where people are named often: each name bare in 40 of 400 chunks and inflected in 20 more, and a word that is
# one of its case forms in 100, over twice the name as written yet under ten times. Anna, in 100 chunks, folds like
# Ann's Anná.
COLLEAGUES = {
    "person/ann.yaml": "id: ann\ntype: person\nname: {en: Ann}\n",
    "person/anna.yaml": "id: anna\ntype: person\nname: {hu: Anna}\n",
    "person/eve.yaml": "id: eve\ntype: person\nname: {en: Eve}\n",
    "person/edi.yaml": "id: edi\ntype: person\nname: {hu: Edi}\n",
    "person/ada.yaml": "id: ada\ntype: person\nname: {en: Ada}\n",
    "person/bela.yaml": "id: bela\ntype: person\nname: {hu: Béla}\n",
    "person/eva.yaml": "id: eva\ntype: person\nname: {hu: Éva}\n",
    "person/bea.yaml": "id: bea\ntype: person\nname: {hu: Bea}\n",
    "person/ben.yaml": "id: ben\ntype: person\nname: {en: Ben}\n",
    "person/don.yaml": "id: don\ntype: person\nname: {en: Don}\n",
}
COLLEAGUE_LINES: list[Line] = [
    (4, 0, "Annál jobb, ha holnap jön.", None),
    (4, 0, "Egy évet vártunk rá.", None),
    (4, 1, "Please edit the doc.", None),
    (4, 1, "Even so, it works.", None),
    (4, 1, "Az adat hiányzik.", None),
    (4, 2, "Ezt mindenki belát.", None),
    (4, 2, "Evan sent the plan.", None),
    (4, 2, "Beat the deadline.", None),
    (4, 3, "Bent vagyok az irodában.", None),
    (4, 3, "Holnap dönt a bizottság.", None),
    (4, 3, "Anna küldte a listát.", "anna"),
    (10, 0, "Ann szerint jó.", "ann"),
    (10, 1, "Eve szerint jó.", "eve"),
    (10, 2, "Edi szerint jó.", "edi"),
    (10, 3, "Ada szerint jó.", "ada"),
    (10, 4, "Béla szerint jó.", "bela"),
    (10, 5, "Éva szerint jó.", "eva"),
    (10, 6, "Bea szerint jó.", "bea"),
    (10, 7, "Ben hívott.", "ben"),
    (10, 8, "Don hívott.", "don"),
    (20, 11, "Annhoz megyek.", "ann"),
    (20, 12, "Evével beszéltem.", "eve"),
    (20, 13, "Edivel beszéltem.", "edi"),
    (20, 14, "Adával beszéltem.", "ada"),
    (20, 15, "Bélával beszéltem.", "bela"),
    (20, 16, "Évával beszéltem.", "eva"),
    (20, 17, "Beával beszéltem.", "bea"),
    (20, 18, "Bennel beszéltem.", "ben"),
    (20, 19, "Donnal beszéltem.", "don"),
]


def make_colleagues_vault(root: Path) -> Vault:
    return people_vault(root, COLLEAGUES, COLLEAGUE_LINES)


@pytest.fixture(scope="module")
def colleagues(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    template = template_with(tmp_path_factory, make_colleagues_vault)
    v = copy_vault(template, tmp_path_factory.mktemp("colleagues"))
    rebuild(v)
    return v


def colleague_chunks(entity: str) -> set[int]:
    return {n for n in range(400) if entity in lines_message(COLLEAGUE_LINES, n)[1]}


def chunks_saying(text: str) -> set[int]:
    return {n for n in range(400) if text in lines_message(COLLEAGUE_LINES, n)[0]}


def test_a_case_form_no_kept_stem_form_checks_counts_as_a_word_past_twice_the_name(colleagues: Vault) -> None:
    # Each of these is cut only to a stem form under three letters (an, ev, ed, ad, be), so nothing but its count tells
    # it from the name.
    dropped = set(query(colleagues, "SELECT entity, term FROM alias_terms WHERE col = 'folded' AND how = 'common'"))
    assert {
        ("ann", "annal"), ("eve", "evet"), ("eve", "even"), ("edi", "edit"), ("ada", "adat"), ("eva", "evan"),
        ("bea", "beat"),
    } <= dropped  # fmt: skip
    for entity in ("eve", "edi", "ada", "eva", "bea"):
        # Evével, Edivel, Adával, Évával and Beával in; no chunk saying only évet, even, edit, adat, Evan or beat.
        assert mentions_of(colleagues, entity) == colleague_chunks(entity), entity
    assert not (mentions_of(colleagues, "ann") - colleague_chunks("ann")) & chunks_saying("Annál")
    now = datetime(2026, 10, 15, tzinfo=UTC)
    for word in ("annál", "évet", "edit", "even", "adat", "evan", "beat"):
        reading = search(colleagues, word, now=now).query
        assert reading.entities == [], word
        assert reading.words == [word], word
    named = [
        ("annhoz", "ann"), ("evével", "eve"), ("edivel", "edi"), ("adával", "ada"), ("bélával", "bela"),
        ("évával", "eva"), ("beával", "bea"), ("bennel", "ben"), ("donnal", "don"),
    ]  # fmt: skip
    for word, entity in named:
        assert [e.id for e in search(colleagues, word, now=now).query.entities] == [entity], word


def test_a_case_form_a_kept_stem_form_checks_is_judged_as_main_judges_it(colleagues: Vault) -> None:
    # bent, dönt, belát and Anna (Ann's Anná without its accent) are cut to Ben's ben, Don's don, Béla's bel and Ann's
    # ann, all kept, so they are judged at ten times the name, as a stem form is. Under that, they count as the name,
    # as on main: a word and a name used about as often can't be told apart by counts.
    dropped = set(query(colleagues, "SELECT entity, term FROM alias_terms WHERE col = 'folded' AND how = 'common'"))
    assert not {("ben", "bent"), ("don", "dont"), ("bela", "belat"), ("ann", "anna")} & dropped
    collisions = {"ben": "Bent", "don": "dönt", "bela": "belát", "ann": "Anna"}
    for entity, word in collisions.items():
        assert mentions_of(colleagues, entity) == colleague_chunks(entity) | chunks_saying(word), entity
    assert mentions_of(colleagues, "anna") == colleague_chunks("anna")
    now = datetime(2026, 10, 15, tzinfo=UTC)
    for word, entities in [("bent", ["ben"]), ("dönt", ["don"]), ("belát", ["bela"]), ("anna", ["ann", "anna"])]:
        assert [e.id for e in search(colleagues, word, now=now).query.entities] == entities, word


# Names written mostly inflected or never bare, as to-dos and places are: Laci bare in 5 chunks and Lacinak in 16,
# Tamás bare in 10 and Tamásnak in 25, and Péternek, Bálintnak and the place Győrben, never bare, in 40, 20 and 31. Each
# case form is past twice the name as written, and a kept stem form checks it. Nimish bare in 3 with Nimishsel in 13,
# and Max bare in 10 with Maxszal in 25: forms the stemmer leaves whole, spellings no ordinary word has.
REGULARS = {
    "person/laci.yaml": "id: laci\ntype: person\nname: {hu: Laci}\n",
    "person/tamas.yaml": "id: tamas\ntype: person\nname: {hu: Tamás}\n",
    "person/peter.yaml": "id: peter\ntype: person\nname: {hu: Péter}\n",
    "person/balint.yaml": "id: balint\ntype: person\nname: {hu: Bálint}\n",
    "place/gyor.yaml": "id: gyor\ntype: place\nname: {hu: Győr}\n",
    "person/nimish.yaml": "id: nimish\ntype: person\nname: {en: Nimish}\n",
    "person/max.yaml": "id: max\ntype: person\nname: {en: Max}\n",
}
REGULAR_LINES: list[Line] = [
    (80, 0, "Laci szerint jó.", "laci"),
    (25, 1, "Lacinak szóltam.", "laci"),
    (40, 4, "Tamás szerint jó.", "tamas"),
    (16, 5, "Tamásnak megírni a választ.", "tamas"),
    (27, 6, "Tamással egyeztetni.", "tamas"),
    (10, 2, "Péternek írtam.", "peter"),
    (20, 3, "Bálintnak írtam.", "balint"),
    (13, 7, "Győrben voltunk.", "gyor"),
    (40, 8, "Győrbe megyünk.", "gyor"),
    (150, 0, "Nimish szerint jó.", "nimish"),
    (31, 9, "Nimishsel beszéltem.", "nimish"),
    (40, 11, "Max szerint jó.", "max"),
    (16, 12, "Maxszal beszéltem.", "max"),
]


def make_regulars_vault(root: Path) -> Vault:
    return people_vault(root, REGULARS, REGULAR_LINES)


@pytest.fixture(scope="module")
def regulars(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    template = template_with(tmp_path_factory, make_regulars_vault)
    v = copy_vault(template, tmp_path_factory.mktemp("regulars"))
    rebuild(v)
    return v


def test_a_name_mostly_inflected_or_never_bare_keeps_its_mentions(regulars: Vault) -> None:
    assert not query(regulars, "SELECT entity, term FROM alias_terms WHERE how = 'common'")
    for entity in ("laci", "tamas", "peter", "balint", "gyor", "nimish", "max"):
        expected = {n for n in range(400) if entity in lines_message(REGULAR_LINES, n)[1]}
        assert mentions_of(regulars, entity) == expected, entity
    now = datetime(2026, 10, 15, tzinfo=UTC)
    named = [
        ("lacinak", "laci"), ("tamásnak", "tamas"), ("péternek", "peter"), ("bálintnak", "balint"), ("győrben", "gyor"),
        ("nimishsel", "nimish"), ("maxszal", "max"),
    ]  # fmt: skip
    for word, entity in named:
        assert [e.id for e in search(regulars, word, now=now).query.entities] == [entity], word


def test_a_name_ending_in_y_keeps_its_inflected_mentions(one_customer: Vault) -> None:
    # Tony is in 120 chunks, a third of them Tonyval: a y read as a vowel takes -val, not a doubled consonant.
    assert mentions_of(one_customer, "tony") == {n for n in range(400) if n % 10 in (0, 3, 9)}
    forms = query(one_customer, "SELECT term, how FROM alias_terms WHERE entity = 'tony' AND col = 'stem_hu'")
    assert ("tony", "stem") in forms


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
    v = empty_vault(tmp_path, index_config())
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


def test_an_author_written_name_and_id_is_matched_by_the_id_for_its_source(tmp_path: Path) -> None:
    v = empty_vault(tmp_path, index_config())
    at = datetime(2026, 10, 14, 7, tzinfo=UTC)
    named = make_record(2, author="C. Test (Northwind) <U0CLEO>", text="Hello from chat.")
    other = make_record(3, author="Someone <U0ELSE>", text="Hello again.")
    v.write_run("chat", [make_record(1, author="U0CLEO", text="Hi."), named, other], at)
    elsewhere = make_record(rid="memo:1", source="memo", kind="message", author="Cleo <U0CLEO>", text="A memo.")
    v.write_run("memo", [elsewhere], at)
    person = v.root / "entities" / "person" / "cleo-test.yaml"
    person.parent.mkdir(parents=True)
    person.write_text("id: cleo-test\ntype: person\nname: {en: Cleo Test}\nids: {chat: U0Cleo}\n", encoding="utf-8")
    rebuild(v)
    assert mentioned_rid(v, make_record(1).rid) == {"cleo-test": "author"}
    assert mentioned_rid(v, named.rid) == {"cleo-test": "author"}
    assert mentioned_rid(v, other.rid) == {}
    # The id is the chat's: under another source it names no one.
    assert mentioned_rid(v, elsewhere.rid) == {}


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
    expr = matching.expressions(conn, Analyzer(resolve(["en"])))[("e", 0)]
    assert expr.count(" OR ") + 1 == 1 + matching.MAX_PHRASES
    assert 'folded : "alpha beta"' in expr
    assert matching.expressions(conn, Analyzer(resolve(["en"])), ["other"]) == {}


def test_an_entity_with_only_needs_aliases_has_no_query_expression() -> None:
    conn = sqlite3.connect(":memory:")
    create(conn, resolve(["en"]))
    conn.execute("INSERT INTO aliases VALUES ('e', 0, 'cond', 'plain', NULL, '[\"x\"]', 'cond')")
    conn.execute("INSERT INTO alias_terms VALUES ('e', 0, 'folded', -1, 'cond', 'written', 0)")
    assert matching.expressions(conn, Analyzer(resolve(["en"])), ["e"], needs=False) == {}
    assert matching.expressions(conn, Analyzer(resolve(["en"])), ["e"]) == {("e", 0): 'folded : "cond"'}


def hold(conn: sqlite3.Connection, analyzer: Analyzer, *texts: str) -> None:
    """Chunks holding the texts, in the full-text index only: what the vocabulary and a match read."""
    start = conn.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
    marks = ", ".join("?" * len(analyzer.columns))
    sql = f"INSERT INTO chunks_fts (rowid, {', '.join(analyzer.columns)}) VALUES (?, {marks})"  # noqa: S608
    conn.executemany(sql, [(start + n + 1, *analyzer.index_values(text)) for n, text in enumerate(texts)])


def test_a_case_form_counts_beside_the_stem_form_it_is_cut_to_or_where_the_stemmer_leaves_it_whole() -> None:
    conn = sqlite3.connect(":memory:")
    create(conn, resolve(["en", "hu"]))
    analyzer = Analyzer(resolve(["en", "hu"]))
    # Chunks holding every case form of each name, since a form no chunk holds is left out.
    hold(conn, analyzer, *(" ".join(hungarian_cases(name)) for name in ("sam", "nimish", "rob", "mark", "bálint")))
    conn.executemany(
        "INSERT INTO aliases VALUES (?, 0, ?, 'name', NULL, '[]', ?)",
        [
            ("sam", "Sam", "sam"),
            ("nim", "Nimish", "nimish"),
            ("rel", "Release 2026", "release 2026"),
            ("rob", "Rob", "rob"),
            ("mark", "Mark", "mark"),
            ("bal", "Bálint", "balint"),
        ],
    )
    conn.executemany(
        "INSERT INTO alias_terms VALUES (?, 0, ?, -1, ?, ?, 0)",
        [
            ("sam", "folded", "sam", "written"),
            ("sam", "stem_hu", "sam", "stem"),
            ("nim", "folded", "nimish", "written"),
            ("nim", "stem_hu", "nimish", "stem"),
            ("rel", "folded", "release 2026", "written"),
            ("rel", "stem_hu", "releas 2026", "stem"),
            ("rob", "folded", "rob", "written"),
            ("rob", "stem_hu", "rob", "common"),
            ("mark", "folded", "mark", "written"),
            ("mark", "stem_hu", "mar", "common"),
            ("mark", "stem_hu", "mark", "stem"),
            ("bal", "folded", "balint", "written"),
            ("bal", "stem_hu", "balin", "stem"),
            ("bal", "stem_hu", "balint", "stem"),
        ],
    )
    exprs = matching.expressions(conn, analyzer)
    sam = exprs[("sam", 0)]
    assert sam.startswith('(folded : "sam" OR (stem_hu : "sam" AND (folded : "samnak" OR ')
    assert sam.endswith('folded : "sammel")))')
    # The stemmer cuts Nimishnek to nimish, and leaves Nimishsel whole: that one counts on its own.
    nimish = exprs[("nim", 0)]
    assert '(stem_hu : "nimish" AND (folded : "nimishnek" OR ' in nimish
    assert nimish.endswith(') OR folded : "nimishsel" OR folded : "nimishse")')
    # A number has no case forms; and with every stem form dropped (robot everywhere), the case forms don't count.
    assert exprs[("rel", 0)] == 'folded : "release 2026"'
    assert exprs[("rob", 0)] == 'folded : "rob"'
    # Mark's case forms are all cut to mar, which már crowds out: they count beside mar while his mark is kept.
    mark = exprs[("mark", 0)]
    assert mark.startswith('(folded : "mark" OR (stem_hu : "mar" AND (folded : "marknak" OR ')
    # Markká without its accent, markka, is cut to mark.
    assert mark.endswith(' OR (stem_hu : "mark" AND folded : "markka"))')
    # Bálint's are cut to two stem forms, and each goes beside its own.
    balint = exprs[("bal", 0)]
    assert balint.count(" AND ") == 2
    assert '(stem_hu : "balint" AND (folded : "balintnak" OR ' in balint
    assert '(stem_hu : "balin" AND ' in balint


def test_a_case_form_no_chunk_holds_is_left_out_until_one_does() -> None:
    # Stemming every case form of every name was most of the mention pass; a form no chunk holds matches nothing.
    conn = sqlite3.connect(":memory:")
    create(conn, resolve(["en", "hu"]))
    analyzer = Analyzer(resolve(["en", "hu"]))
    conn.execute("INSERT INTO aliases VALUES ('sam', 0, 'Sam', 'name', NULL, '[]', 'sam')")
    conn.executemany(
        "INSERT INTO alias_terms VALUES ('sam', 0, ?, -1, ?, ?, 0)",
        [("folded", "sam", "written"), ("stem_hu", "sam", "stem")],
    )
    hold(conn, analyzer, "Sam is on site.")
    assert matching.expressions(conn, analyzer) == {("sam", 0): 'folded : "sam"'}
    hold(conn, analyzer, "Sammal beszéltem.")
    assert matching.expressions(conn, analyzer) == {
        ("sam", 0): '(folded : "sam" OR (stem_hu : "sam" AND folded : "sammal"))'
    }


def test_a_case_form_the_tokenizer_may_fold_otherwise_is_kept_though_no_chunk_holds_it() -> None:
    # fold keeps ø, which the full-text tokenizer may fold to o, so the vocabulary can't say whether a chunk holds it.
    conn = sqlite3.connect(":memory:")
    create(conn, resolve(["en", "hu"]))
    analyzer = Analyzer(resolve(["en", "hu"]))
    hu = analyzer.languages[1]
    every = matching.cases_of(analyzer, ["søren"], hu)
    held = matching.held_cases(conn, ["søren"], hu)
    assert every.forms() and all(not form[-1].isascii() for form in every.forms())
    assert matching.cases_of(analyzer, ["søren"], hu, held) == every
    # A plain ASCII form no chunk holds is left out.
    assert matching.held_cases(conn, ["sam"], hu) == set()
