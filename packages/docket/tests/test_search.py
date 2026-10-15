from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from docket_testkit import (
    CALL,
    FLEET,
    INDEX_FIXTURE,
    INDEX_NOW,
    OFFER,
    PARENT,
    copy_vault,
    index_config,
    index_template,
    make_record,
)

from docket.errors import DocketError
from docket.index import labels, rebuild, search, summary_for
from docket.index import query as search_module
from docket.index.build import languages_of, open_index
from docket.index.query import SearchResult
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

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


def test_common_forms_leave_the_query_only_past_the_floor(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    assert find(vault, "the pallet").query.left_out == []
    monkeypatch.setattr(search_module, "COMMON_MIN", 0)
    reading = find(vault, "the pallet").query
    assert reading.left_out == ["the"] and reading.words == ["pallet"]
    alone = find(vault, "the").query
    assert alone.left_out == [] and alone.words == ["the"]


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
