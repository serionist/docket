from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from docket_testkit import (
    CALL,
    COMPOUND_FILLERS,
    COMPOUND_FOLLOW,
    COMPOUND_KICK,
    CRANE,
    EDITORS,
    FLEET,
    INDEX_FIXTURE,
    INDEX_NOW,
    OFFER,
    PARENT,
    compound_chat,
    compound_template,
    copy_vault,
    fts_and_check,
    index_config,
    index_template,
    make_record,
    name_template,
    query_words,
    stem_every_word,
)

from docket import render
from docket.errors import DocketError
from docket.index import labels, rebuild, search, summary_for
from docket.index import query as search_module
from docket.index.build import languages_of, open_index
from docket.index.languages import resolve
from docket.index.query import Hit, SearchResult
from docket.index.terms import Checker, phrase_of
from docket.index.text import Analyzer
from docket.vault.records import RawRecord
from docket.vault.vault import Vault
from docket.web import templating, views

QUESTIONS: list[dict[str, Any]] = yaml.safe_load((INDEX_FIXTURE / "questions.yaml").read_text(encoding="utf-8"))
LATER = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def vault(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    v = copy_vault(index_template(tmp_path_factory), tmp_path_factory.mktemp("search"))
    rebuild(v)
    return v


def find(vault: Vault, query: str, **kwargs: Any) -> SearchResult:
    return search(vault, query, now=INDEX_NOW, **kwargs)


def locators(result: SearchResult) -> list[str]:
    return [loc for g in result.groups for h in g.hits for loc in (h.locator, *h.also)]


def hits(result: SearchResult) -> list[tuple[str, str]]:
    return [(g.key, h.locator) for g in result.groups for h in g.hits]


def answers(loc: str, expect: str) -> bool:
    """The locator expected, or a part of the record expected: a prefix alone would let `rec-0100` answer for
    `rec-0100-second`."""
    return loc == expect or loc.startswith((f"{expect}@", f"{expect}#"))


def test_an_answer_is_its_locator_or_a_part_of_its_record() -> None:
    assert answers("r:calls:rec-0100@00:30-01:50", "r:calls:rec-0100")
    assert answers("r:mail:<offer-1@northwind.example>#1-4", "r:mail:<offer-1@northwind.example>")
    assert not answers("r:calls:rec-0100-second@00:30-01:51", "r:calls:rec-0100")
    assert not answers("r:calls:rec-0100@02:30-04:00", "r:calls:rec-0100@02:30")


@pytest.mark.parametrize("question", QUESTIONS, ids=[q["q"] for q in QUESTIONS])
def test_each_question_finds_its_answer_in_the_top_five(vault: Vault, question: dict[str, Any]) -> None:
    result = find(vault, question["q"], k=5)
    found = [(g.key, loc) for g in result.groups for h in g.hits for loc in (h.locator, *h.also)]
    assert any(answers(loc, question["expect"]) for _, loc in found), found
    if "group" in question:
        assert any(key == question["group"] and answers(loc, question["expect"]) for key, loc in found)


def test_a_bare_word_finds_its_inflected_form_through_its_dative_stem(vault: Vault) -> None:
    result = find(vault, "forint", sources=["calls"], k=20)
    assert any(loc.startswith(f"r:{CALL}@00:") for loc in locators(result))


def test_an_inflected_word_finds_the_bare_word_through_its_stem_stemmed_again(tmp_path: Path) -> None:
    v = Vault.init(tmp_path / "vault", index_config(), "test-box")
    said = [
        "Az ajánlat még nincs kész, a robot leállt.",
        "A projekt felelőse Dóra.",
        "Thanks for the update.",
        "Please fill in the form.",
    ]
    v.write_run("chat", [make_record(n, container="chat:C0X", text=t) for n, t in enumerate(said, start=1)], LATER)
    rebuild(v)
    for query, expect in [("ajánlatot", 1), ("ajánlatról", 1), ("robotot", 1), ("projektről", 2)]:
        result = find(v, query)
        assert [h.rid for g in result.groups for h in g.hits] == [f"chat:C0TEST/179100000{expect}.000100"], query
    # A bare word's stem is no word the index holds, so its second cut (`forint`, `forin`, `for`) isn't searched.
    assert find(v, "forint").groups == []
    # The second cut of `formed` (`form`, `for`) is a word written as often as `form`, so it isn't searched either.
    thanks = make_record(3).rid
    assert thanks not in [h.rid for g in find(v, "formed").groups for h in g.hits]


def test_all_then_any_then_prefixes(vault: Vault) -> None:
    every = find(vault, "pallet labels")
    assert {h.found_by for g in every.groups for h in g.hits} >= {"all"}
    some = find(vault, "pallet zebracorn")
    assert {h.found_by for g in some.groups for h in g.hits} == {"any"}
    start = find(vault, "consulta")
    assert start.groups and {h.found_by for g in start.groups for h in g.hits} == {"prefix"}
    assert find(vault, "zebracorn qwertyuiop").groups == []


def test_every_word_stays_however_common(vault: Vault) -> None:
    result = find(vault, "the pallet the")
    assert result.query.words == ["the", "pallet"] and result.query.left_out == [] and result.query.absent == []
    assert {h.found_by for g in result.groups for h in g.hits} >= {"all"}


def test_filters_apply_before_the_cut(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_module, "CANDIDATES", 2)
    result = find(vault, "fleet", since=date(2026, 10, 13))
    assert result.groups
    assert all(
        h.ts is not None and h.ts >= datetime(2026, 10, 12, 22, tzinfo=UTC) for g in result.groups for h in g.hits
    )
    mail = find(vault, "consultant", sources=["mail"])
    assert {g.key for g in mail.groups} == {OFFER}


def test_a_boost_reorders_and_never_drops(vault: Vault) -> None:
    plain = find(vault, "labels", k=20)
    boosted = find(vault, "labels", people=["Cleo Test"], k=20)
    assert set(hits(boosted)) >= set(hits(plain))
    top = boosted.groups[0].hits
    assert any("cleo-test" in h.entities for h in top)
    assert boosted.query.boosts == ["cleo-test"]
    assert any(h.found_by == "entity" for g in boosted.groups for h in g.hits) or len(hits(boosted)) == len(hits(plain))


def test_boosts_resolve_by_id_name_or_alias_and_report_the_rest(vault: Vault) -> None:
    reading = find(
        vault, "printer", people=["ben-sample", "Dóra", "Nobody Here", "northwind"], entities=["Harbour site"]
    )
    assert reading.query.boosts == ["ben-sample", "dora-minta", "harbour-site"]
    assert reading.query.unknown == ["Nobody Here", "northwind"]


def test_entities_in_the_query_expand_to_their_aliases(vault: Vault) -> None:
    result = find(vault, "Northwind", k=20)
    assert [e.id for e in result.query.entities] == ["northwind"]
    assert any(loc.startswith(f"r:{CALL}@01:50") for loc in locators(result))
    hungarian = find(vault, "Northwind Tradersnél")
    assert [(e.id, e.words) for e in hungarian.query.entities] == [("northwind", "northwind tradersnél")]


def test_time_words_filter_and_are_reported(vault: Vault) -> None:
    result = find(vault, "múlt héten pallet")
    reading = result.query
    assert reading.time_words == ["múlt héten"] and reading.words == ["pallet"]
    assert reading.since == datetime(2026, 10, 4, 22, tzinfo=UTC) and reading.until == datetime(
        2026, 10, 11, 22, tzinfo=UTC
    )
    narrowed = find(vault, "múlt héten pallet", since=date(2026, 10, 8), until=datetime(2026, 10, 30, tzinfo=UTC))
    assert narrowed.query.since == datetime(2026, 10, 7, 22, tzinfo=UTC)
    assert narrowed.query.until == datetime(2026, 10, 11, 22, tzinfo=UTC)
    naive = find(vault, "pallet", until=datetime(2026, 10, 9, 12, 0))
    assert naive.query.since is None and naive.query.until == datetime(2026, 10, 9, 10, tzinfo=UTC)
    cut = naive.query.until
    assert cut is not None
    assert all(h.ts is not None and h.ts < cut for g in naive.groups for h in g.hits)


def test_a_time_word_alone_returns_the_newest_in_range(vault: Vault) -> None:
    result = find(vault, "this week")
    found = [h for g in result.groups for h in g.hits]
    assert found and {h.found_by for h in found} == {"time"}
    assert all(h.ts is not None and h.ts >= datetime(2026, 10, 11, 22, tzinfo=UTC) for h in found)
    assert find(vault, "", sources=["cal"]).groups[0].key == "cal:evt-0001"


def test_boosts_alone_find_what_mentions_them(vault: Vault) -> None:
    result = find(vault, "", entities=["fleet-management"])
    assert {h.found_by for g in result.groups for h in g.hits} == {"entity"}
    assert all("fleet-management" in h.entities for g in result.groups for h in g.hits)


def test_fts5_syntax_in_a_query_stays_literal(vault: Vault) -> None:
    for hostile in ('pallet" OR "x', "NEAR(pallet labels)", "label*", "-pallet", "folded: pallet", "^pallet", "(a"):
        find(vault, hostile)
    assert find(vault, '"pallet labels"').query.phrases == ["pallet labels"]
    assert find(vault, '"pallet labels" keep').groups


def test_two_transcripts_of_one_stretch_collapse_and_a_record_s_own_windows_do_not(vault: Vault) -> None:
    result = find(vault, "tanácsadó", k=20)
    group = next(g for g in result.groups if g.key == CALL)
    first = group.hits[0]
    others = [loc for loc in first.also if not loc.startswith(first.locator.split("@")[0] + "@")]
    assert others, first
    harbour = find(vault, "harbour", sources=["calls"], k=20)
    windows = [h.locator for g in harbour.groups for h in g.hits if h.rid == CALL]
    assert len(windows) >= 2


def test_two_recordings_in_one_thread_at_other_hours_stay_apart(tmp_path: Path) -> None:
    v = Vault.init(tmp_path / "vault", index_config(), "test-box")

    def part(n: int, hour: int, said: str) -> RawRecord:
        segs = [{"start_ms": 0, "end_ms": 50000, "speaker": "Ben Sample", "text": said}]
        return make_record(
            rid=f"calls:part-{n}", source="calls", kind="transcript", container=None, thread="calls:meeting-7",
            ts=datetime(2026, 10, 12, hour, tzinfo=UTC), segments=segs,
        )  # fmt: skip

    v.write_run("calls", [part(1, 8, "the crane budget is fine"), part(2, 10, "the crane budget was cut")], LATER)
    rebuild(v)
    result = find(v, "crane budget")
    assert sorted(h.rid or "" for g in result.groups for h in g.hits) == ["calls:part-1", "calls:part-2"]
    assert all(h.also == [] for g in result.groups for h in g.hits)


def test_groups_carry_their_kind_title_and_current_summary(vault: Vault) -> None:
    result = find(vault, "fleet dashboard", k=20)
    by_key = {g.key: g for g in result.groups}
    assert by_key[FLEET].kind == "recording" and by_key[FLEET].title == "Fleet dashboard review"
    summary = by_key[FLEET].summary
    assert summary is not None and summary.id == "recording-note:2026-10-13-fleet-review"
    call = {g.key: g for g in find(vault, "offer in writing", k=20).groups}[CALL]
    assert call.summary is not None and call.summary.id == "minutes:2026-10-07-supplier-call"
    thread = {g.key: g for g in find(vault, "cold-rated", k=20).groups}[PARENT]
    assert thread.kind == "thread" and thread.title.startswith("Pallet labels at the harbour")
    assert thread.summary is not None and thread.summary.id == "thread-summary:2026-10-08-pallet-labels"
    offer = {g.key: g for g in find(vault, "day rate", k=20).groups}
    assert offer[OFFER].kind == "record" and offer["digest:2026-10-09-week"].kind == "doc"
    assert offer["digest:2026-10-09-week"].summary is None


def test_hits_carry_their_place(vault: Vault) -> None:
    result = find(vault, "tízmillió", k=20)
    transcript = next(h for g in result.groups for h in g.hits if h.kind == "transcript")
    assert transcript.start is not None and transcript.end is not None and transcript.speakers
    doc = next(h for g in find(vault, "deadline with Ben", k=20).groups for h in g.hits if h.kind == "doc")
    assert doc.lines is not None and doc.anchors and doc.doc == "recording-note:2026-10-07-supplier-call"
    assert all(h.snippet for g in result.groups for h in g.hits)


def test_facets_count_the_candidates(vault: Vault) -> None:
    facets = find(vault, "pallet", k=20).facets
    assert facets["sources"]["chat"] >= 1 and "2026-10" in facets["months"] and facets["entities"]


def test_a_query_with_nothing_to_search_for_or_a_bad_k_is_refused(vault: Vault) -> None:
    with pytest.raises(DocketError, match="nothing to search for"):
        find(vault, "   ")
    with pytest.raises(DocketError, match="k is from 1 to 100"):
        find(vault, "pallet", k=0)


def test_a_snippet_cuts_around_the_first_match(vault: Vault) -> None:
    long = find(vault, "audit", k=5)
    snippet = long.groups[0].hits[0].snippet
    assert "audit" in snippet and snippet.startswith("...")
    conn = open_index(vault)
    try:
        reader = search_module._Search(conn, vault, languages_of(vault))  # pyright: ignore[reportPrivateUsage]
        assert reader.snippet("  ---\n  !!!  ") == "--- !!!"
        assert reader.snippet("no match here at all") == "no match here at all"
    finally:
        conn.close()


def test_labels_name_each_entity_in_the_owner_s_language(vault: Vault) -> None:
    found = labels(vault, ["ben-sample", "northwind", "ben-sample", "nobody-known"])
    assert set(found) == {"ben-sample", "northwind"}
    assert (found["ben-sample"].type, found["ben-sample"].name) == ("person", "Ben Sample")
    assert found["northwind"].type == "customer"
    assert labels(vault, []) == {}


def test_summary_for_gives_what_search_attaches_to_the_record_s_group(vault: Vault) -> None:
    by_search = {g.key: g.summary for g in find(vault, "Nortwin", k=20).groups}
    assert by_search[CALL] is not None
    assert summary_for(vault, CALL) == by_search[CALL]
    assert summary_for(vault, "calls:rec-0100-second") == by_search[CALL]
    assert summary_for(vault, "chat:C0SIDE/1791500000.000100") is None
    assert summary_for(vault, "chat:nowhere/1") is None


# -- the name vault: a name in most chunks, and titles ---------------------------------------------------------------

NOTE = "recording-note:2026-10-12-acme-kick-off"


@pytest.fixture(scope="module")
def names(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    v = copy_vault(name_template(tmp_path_factory), tmp_path_factory.mktemp("names"))
    rebuild(v)
    return v


def ranked(result: SearchResult) -> list[Hit]:
    return sorted((h for g in result.groups for h in g.hits), key=lambda h: h.rank)


def first(result: SearchResult, found: str) -> int:
    """The best rank of a hit in a record or a doc."""
    return min(h.rank for h in ranked(result) if found in (h.rid, h.doc))


def test_the_name_vault_holds_its_names_in_most_chunks(names: Vault) -> None:
    conn = open_index(names)
    try:
        total = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        for name in ("acme", "orbex"):
            n = conn.execute("SELECT doc FROM vocab WHERE col = 'folded' AND term = ?", (name,)).fetchone()[0]
            assert n * 5 > total and n > 100, name
    finally:
        conn.close()


def test_a_name_in_most_chunks_stays_in_the_query(names: Vault) -> None:
    # Orbex's forms are all the word itself, so it was dropped and reported; Acme's stem differs, so its form as
    # written was in no stemmed column and the all-words stage found nothing.
    for query in ("orbex budget", "acme budget"):
        result = find(names, query)
        assert result.query.words == query.split() and result.query.left_out == [] and result.query.absent == []
        top = ranked(result)[0]
        assert top.found_by == "all" and all(w in top.snippet.lower() for w in query.split()), query


def test_a_word_in_no_chunk_is_said(names: Vault) -> None:
    result = find(names, "acme zebracorn")
    assert result.query.absent == ["zebracorn"]
    tz = ZoneInfo("Europe/Budapest")
    assert "in no chunk: zebracorn" in render.search(result, tz)
    assert views.reading_line(result.query, {}, tz) == ["in no chunk: zebracorn"]


def test_a_title_holding_the_words_ranks_first(names: Vault) -> None:
    budget = find(names, "acme budget")
    assert ranked(budget)[0].doc == "legacy-note:acme-budget-review"
    assert first(budget, "legacy-note:acme-budget-review") < first(budget, "legacy-note:notes-from-the-week")
    kick = find(names, "kick-off acme dana")
    assert ranked(kick)[0].doc == NOTE
    assert first(kick, NOTE) < first(kick, "legacy-note:weekly-notes")


def test_a_long_heading_lifts_only_by_its_lead(names: Vault) -> None:
    result = find(names, "acme kick-off dana")
    order = [first(result, d) for d in (NOTE, "legacy-note:acme-kick-off-second-half", "legacy-note:weekly-notes")]
    assert order == sorted(order)
    # The sweep names every word after its lead: it keeps its BM25 place, below a short note saying them in its body.
    assert first(result, "legacy-note:weekly-notes") < first(result, "legacy-note:mail-sweep")


def test_a_recording_s_name_lifts_its_group(names: Vault) -> None:
    result = find(names, "borealis crane audit")
    assert result.groups[0].key == CRANE and ranked(result)[0].rid == CRANE
    assert first(result, CRANE) < first(result, "chat:C0NAMES/1791500004.000100")


def test_a_quoted_phrase_in_a_title_counts(names: Vault) -> None:
    result = find(names, '"budget review" acme')
    order = [
        first(result, f"legacy-note:{slug}")
        for slug in ("acme-budget-review", "notes-from-the-week", "acme-review-of-the-budget")
    ]
    assert order == sorted(order)


def test_a_name_in_a_title_counts_through_its_alias(names: Vault) -> None:
    result = find(names, "rita budget")
    assert [(e.id, e.words) for e in result.query.entities] == [("rita-sample", "rita")]
    assert ranked(result)[0].rid == "mail:<rita-1@acme.example>"


def test_a_hungarian_title_is_held_through_its_stems(names: Vault) -> None:
    result = find(names, "acme határidő")
    assert ranked(result)[0].doc == "legacy-note:acme-dontes"
    assert first(result, "legacy-note:acme-dontes") < first(result, "legacy-note:heti-jegyzet")


def test_a_title_lifts_one_hit_per_group(names: Vault) -> None:
    hits = ranked(find(names, "kick-off acme dana"))
    assert [h.doc for h in hits[:2]] == [NOTE, "legacy-note:acme-kick-off-second-half"]
    assert len([h for h in hits if h.doc == NOTE]) == 3


def test_the_any_word_stage_ranks_more_of_the_words_first(names: Vault) -> None:
    result = find(names, "acme budget approval")
    assert {h.found_by for h in ranked(result)} == {"any"}
    assert first(result, "chat:C0NAMES/1791500002.000100") < first(result, "chat:C0NAMES/1791500001.000100")


def test_a_word_in_half_the_chunks_leaves_the_any_word_match_and_counts_in_its_order(names: Vault) -> None:
    # Acme is in most chunks, so BM25 gives it no weight: the any-word stage doesn't match it, so a weekly mail holding
    # only Acme comes only from the last query that adds it, after every chunk holding another word, while Acme still
    # counts in the order above.
    hits = ranked(find(names, "acme budget approval", k=50))
    weekly = [h.rank for h in hits if (h.rid or "").startswith("mail:<weekly-")]
    others = [h.rank for h in hits if not (h.rid or "").startswith("mail:<weekly-")]
    assert weekly and others and max(others) < min(weekly)
    # When the other words are in no chunk, the least common of the weightless ones joins: Orbex, in fewer chunks.
    fallback = find(names, "acme orbex zebracorn")
    assert fallback.query.absent == ["zebracorn"]
    assert {h.found_by for h in ranked(fallback)} == {"any"}
    assert all("orbex" in h.snippet.lower() for h in ranked(fallback))


def test_a_title_never_lifts_a_hit_into_an_earlier_stage(names: Vault) -> None:
    hits = ranked(find(names, "crane audit approval"))
    every = [h.rank for h in hits if h.found_by == "all"]
    some = [h for h in hits if h.found_by == "any"]
    assert every and some and max(every) < min(h.rank for h in some)
    assert some[0].rid == CRANE


def test_the_word_check_agrees_with_stemming_every_word(vault: Vault, names: Vault, compounds: Vault) -> None:
    # Stems that change a word's early letters (`cries` to `cri`, `dying` to `die`, `skies` to `sky`), and a capital
    # that lowercases to two characters (`İ`): a screen on more than the first letter would miss them. With compounds
    # (#59), joined words and splits, whose halves' first letters the screen must take too.
    texts = [
        "Acménál volt szó a határidőről, és az Acme határidő csúszik.",
        "The dying light under grey skies; the baby cries, the kick-off is dry. ÁRVÍZTŰRŐ tükörfúrógép, İSTANBUL.",
    ]
    for v in (vault, names, compounds):
        conn = open_index(v)
        try:
            texts += [r[0] for r in conn.execute("SELECT text FROM chunks ORDER BY id")]
        finally:
            conn.close()
    for languages, joined in ((["en", "hu"], False), (["en", "de"], False), (["en", "hu"], True), (["en", "de"], True)):
        analyzer = Analyzer(resolve(languages))
        picked = ["acme", "határidő", "die", "sky", "cry", "dry", "istanbul", "dana", "budget", "forint", "árvíztűrő"]
        if joined:
            picked += ["kickoff", "kickoffs", "followup", "email", "emailben", "online", "editor", "visitor", "setup"]
        words = query_words(analyzer, texts, picked, seed=57, compounds=joined)
        phrases = [phrase_of(analyzer, p) for p in ("budget review", "crane audit", "acme kick", "grey skies")]
        checker = Checker(analyzer, words, phrases)
        assert (checker.firsts is not None) == (languages == ["en", "hu"])
        for text in texts:
            want_words, want_phrases, want_split = stem_every_word(analyzer, words, phrases, text)
            held = checker.held(text)
            got = (set(held.words), set(held.phrases), set(held.split_only))
            assert got == (want_words, want_phrases, want_split), text
            assert checker.examine(text) == (len(want_words) + len(want_phrases) + len(want_split), bool(want_split))


@pytest.mark.parametrize(
    ("title", "want"),
    [
        ("RE: Acme budget", "RE: Acme budget"),
        ("Fwd: Granite pump overhaul", "Fwd: Granite pump overhaul"),
        ("Acme: budget call", "Acme: budget call"),
        ("[EXT] Re: Granite pump overhaul", "[EXT] Re: Granite pump overhaul"),
        ("Válasz: Acme határidő", "Válasz: Acme határidő"),
        ("2026-10-12: Acme kick-off", "2026-10-12: Acme kick-off"),
        ("Re: Acme budget review: notes from the call", "Re: Acme budget review"),
        (
            "2026-10-12 - Mail sweep, received 11 Oct 23:44 to 12 Oct 22:50: the Acme kick-off ran, Dana sent notes",
            "2026-10-12 - Mail sweep, received 11 Oct 23:44 to 12 Oct 22:50",
        ),
        (
            "2026-10-12 - Acme kick-off with Dana (second half): no site in the plan yet",
            "2026-10-12 - Acme kick-off with Dana (second half)",
        ),
        (
            "one two three four five six seven eight nine ten eleven twelve thirteen",
            "one two three four five six seven eight nine ten eleven twelve",
        ),
    ],
)
def test_a_title_s_lead_runs_past_a_label_before_its_colon(title: str, want: str) -> None:
    assert search_module.lead(title) == want


def test_a_reply_s_subject_lifts_it(names: Vault) -> None:
    result = find(names, "granite pump overhaul")
    assert ranked(result)[0].rid == "mail:<granite-2@acme.example>"


def test_the_any_word_stage_adds_a_weightless_word_when_the_stages_leave_fewer_than_k(names: Vault) -> None:
    # Crane is in chunks, but in no mail: without Acme the stages find nothing there.
    mail = find(names, "acme crane", sources=["mail"])
    assert len(ranked(mail)) == 20 and all((h.rid or "").startswith("mail:") for h in ranked(mail))
    # Nor in May, when only the weekly mails were written.
    may = find(names, "acme crane", since=date(2026, 5, 1), until=date(2026, 5, 31))
    assert len(ranked(may)) == 20
    # A quoted phrase no chunk holds finds nothing on its own.
    phrase = find(names, '"zebra crossing" acme')
    assert len(ranked(phrase)) == 20 and phrase.query.absent == []
    # Two weightless words and a source where no chunk holds both: the least common, Orbex, finds the chat.
    chat = find(names, "acme orbex", sources=["chat"])
    assert [h.rid for h in ranked(chat)] == ["chat:C0NAMES/1791500001.000100"]


# -- the compound vault: hyphenated, joined and spaced spellings (#59) -----------------------------------------------

MAIL_KICK = "mail:<kick-1@orbex.example>"
MAIL_FOLLOW = "mail:<follow-1@orbex.example>"
MAIL_EDIT = "mail:<edit-1@orbex.example>"
AGREEMENT = [
    "orbex kickoff", "orbex kick-off", "orbex kick off", "crane followup", "crane follow-up", "crane follow up",
    "email", "e-mail", "emailben", "online", "on-line", "well", "setup", "set up", "editor", "visitor", "to day",
    # A stemmed compound, and queries where no other word screens in the split's second half ("off").
    "kickoffs", "kickoff", "crane kickoff",
]  # fmt: skip


@pytest.fixture(scope="module")
def compounds(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    v = copy_vault(compound_template(tmp_path_factory), tmp_path_factory.mktemp("compounds"))
    rebuild(v)
    return v


def rids(result: SearchResult) -> set[str | None]:
    return {h.rid for h in ranked(result)}


def test_the_compound_vault_holds_each_spelling(compounds: Vault) -> None:
    conn = open_index(compounds)
    try:
        total = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]

        def docs(col: str, term: str) -> int:
            row = conn.execute("SELECT doc FROM vocab WHERE col = ? AND term = ?", (col, term)).fetchone()
            return 0 if row is None else row[0]

        # The joined words: kick-off, follow-up, e-mailt, e-mail-címedet, on-line, and kick-offs through its stem.
        for term in ("kickoff", "followup", "emailt", "emailcimedet", "online"):
            assert docs("folded", term), term
        assert docs("stem_hu", "email") and docs("stem_en", "kickoff")
        # The chunk keeps the text as written: only its FTS5 row holds the joined word.
        text = conn.execute("SELECT text FROM chunks WHERE rid = ?", (MAIL_KICK,)).fetchone()[0]
        assert "kick-off" in text and "kickoff" not in text
        assert docs("folded", "set") * 2 >= total and docs("folded", "up") * 2 >= total
        assert docs("folded", "today") * 2 >= total
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("query", "want"),
    [
        *((q, [MAIL_KICK, compound_chat("kickoff"), COMPOUND_KICK]) for q in ("orbex kickoff", "orbex kick-off",
                                                                              "orbex kick off")),
        *((q, [MAIL_FOLLOW, compound_chat("followup"), COMPOUND_FOLLOW]) for q in ("crane followup", "crane follow-up",
                                                                                   "crane follow up")),
    ],
)  # fmt: skip
def test_every_spelling_finds_all_three(compounds: Vault, query: str, want: list[str]) -> None:
    by = {h.rid: h.found_by for h in ranked(find(compounds, query))}
    assert [by.get(rid) for rid in want] == ["all", "all", "all"], query


def test_email_finds_its_hungarian_spellings(compounds: Vault) -> None:
    for query in ("email", "e-mail", "emailben"):
        assert {compound_chat(k) for k in ("emailben", "emailt", "email", "emailcim")} <= rids(find(compounds, query))


def test_online_and_on_line_find_each_other(compounds: Vault) -> None:
    for query in ("online", "on-line"):
        assert {compound_chat("online-hu"), compound_chat("online")} <= rids(find(compounds, query)), query


def test_a_split_s_first_half_has_three_letters(compounds: Vault) -> None:
    found = rids(find(compounds, "well"))
    assert compound_chat("well") in found and compound_chat("well-short") not in found


def test_a_split_needs_a_letter_in_each_half(compounds: Vault) -> None:
    # "round 10" is said, and both halves are words the index holds, but a number never splits.
    result = find(compounds, "round10")
    assert result.query.absent == ["round10"] and not result.groups


def test_a_split_of_two_words_in_most_chunks_is_left_out(compounds: Vault) -> None:
    found = rids(find(compounds, "setup"))
    assert compound_chat("setup") in found and compound_chat("set-up") not in found
    assert {compound_chat("setup"), compound_chat("set-up")} <= rids(find(compounds, "set up", k=100))


def test_a_joined_word_in_most_chunks_is_left_out(compounds: Vault) -> None:
    # "today" is in most chunks, so `to day` matches its own words and not their joined word, which would score every
    # chunk saying it for next to no weight.
    fillers = {compound_chat(f"filler-{n}") for n in range(COMPOUND_FILLERS)}
    result = find(compounds, "to day", k=100)
    by = {h.rid: h.found_by for h in ranked(result)}
    assert by.get(compound_chat("to-day")) == "all" and not by.keys() & fillers and result.query.absent == []


def test_a_split_ranks_after_the_word_itself(compounds: Vault) -> None:
    editor = ranked(find(compounds, "editor"))
    editors = [h.rank for h in editor if h.rid in {compound_chat(f"editor-{n}") for n in range(EDITORS)}]
    edit_or = [h.rank for h in editor if h.rid in {compound_chat("edit-or-0"), compound_chat("edit-or-1"), MAIL_EDIT}]
    # The mail titled "Edit or delete the crane list" holds `editor` only through a split: its lead doesn't lift it.
    assert len(editors) == EDITORS and len(edit_or) == 3 and max(editors) < min(edit_or)
    visitor = find(compounds, "visitor")
    assert first(visitor, compound_chat("visitor")) < first(visitor, compound_chat("visit-or"))


def test_a_split_ranks_by_the_terms_held_in_the_any_word_stage(compounds: Vault) -> None:
    # No chunk holds all three. The transcript holds followup only as "follow up", and crane: two terms, after the
    # chunks holding two through their forms, above those holding crane alone.
    result = ranked(find(compounds, "followup crane permit"))
    rank = {h.rid: h.rank for h in result}
    two = [rank[r] for r in (MAIL_FOLLOW, compound_chat("followup"), compound_chat("online"))]
    one = [rank[r] for r in (compound_chat(f"editor-{n}") for n in range(EDITORS)) if r in rank]
    assert all(h.found_by == "any" for h in result) and one
    assert max(two) < rank[COMPOUND_FOLLOW] < min(one)


def test_a_word_whose_splits_no_chunk_says_is_in_no_chunk(compounds: Vault) -> None:
    # "list" and "crane" are both words of the vault, never in that order; "crane quote" is said.
    assert find(compounds, "listcrane").query.absent == ["listcrane"]
    quote = find(compounds, "cranequote")
    assert quote.query.absent == [] and MAIL_FOLLOW in rids(quote)


def test_a_title_with_a_compound_leads(compounds: Vault) -> None:
    # Its lead holds both words only when the check reads kick-off as kickoff; the checklist says kickoff twice.
    result = find(compounds, "orbex kickoff")
    assert ranked(result)[0].rid == MAIL_KICK
    assert first(result, MAIL_KICK) < first(result, compound_chat("checklist"))


@pytest.mark.parametrize("query", AGREEMENT)
def test_the_word_check_agrees_with_fts5_on_compounds(compounds: Vault, query: str) -> None:
    found, held = fts_and_check(compounds, query)
    assert found and found == held, query


def test_a_compound_s_snippet_shows_it_as_written(compounds: Vault) -> None:
    snippets = {h.rid: h.snippet for h in ranked(find(compounds, "kickoff"))}
    assert "kick off" in snippets[COMPOUND_KICK] and "kick-off" in snippets[MAIL_KICK]
    # "We'll" opens the message, "well" ends it 60 words on: the snippet starts at the word, not at the contraction.
    assert "went well" in {h.rid: h.snippet for h in ranked(find(compounds, "well"))}[compound_chat("well-long")]
    marks = templating.marked("we kick off, the kick-off, a kickoff; we'll see", ["kickoff", "well"])
    assert re.findall(r"<mark>(.*?)</mark>", str(marks)) == ["kick", "off", "kick", "off", "kickoff"]
