"""A recording's doubtful spots (DR-0583): their kinds, rank and cap, what is left out, settled places, a new version,
the screens, the machine check, the fixes doc's id and the listing. The call, its people and its entities are invented
(`reconcile_kit.py`); the fixes doc's form comes with #183's second PR, so fix lines here are given as `Fix`es."""

from __future__ import annotations

import hashlib
import time
import unicodedata
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import copy_vault, traced
from reconcile_kit import CALL, REC, TRANSCRIPT, WHEN, WHISPER, cut, heard, pair, reconcile_template, recording, segment
from reconcile_kit import transcript as recorder

from docket import index, recall
from docket.errors import DocketError
from docket.media import staged as media_staged
from docket.reconcile import align, spots
from docket.reconcile.spots import Fix, Names
from docket.vault import brief_form, cursors
from docket.vault.config import NeverStore, VaultConfig
from docket.vault.entities import Alias, Entity
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

NORTHWIND = Entity("northwind", "customer", "x", {"en": "Northwind"}, (Alias("Nortwin", "asr"),))
BEN = Entity("ben-sample", "person", "y", {"en": "Ben Sample"})
DORA = Entity("dora-minta", "person", "z", {"en": "Dóra Minta"}, (Alias("Dóri"),))
CRANE = Entity("crane-works", "project", "w", {"en": "Crane Works"})
NAMES = Names.of([NORTHWIND, BEN, DORA, CRANE])
NOBODY = NeverStore()


def find(
    transcript: RawRecord, whisper: RawRecord, *, fixes: list[Fix] | None = None, lists: NeverStore = NOBODY
) -> spots.Found:
    shapes = brief_form.phrases([NORTHWIND, BEN, DORA, CRANE], lists)
    return spots.find(transcript, whisper, NAMES, lists=lists, shapes=shapes, fixes=fixes or [])


def call(lines: list[tuple[str, str]], step: int = 6_000) -> tuple[RawRecord, RawRecord]:
    """A call of (recorder, Whisper) lines, one every `step` milliseconds."""
    return pair([("Ben Sample", i * step, i * step + step - 1000, a, b) for i, (a, b) in enumerate(lines)])


def said(found: spots.Found) -> list[tuple[str, str, str]]:
    return [(s.kind, s.left, s.right) for s in found.spots]


def seconds(anchor: str) -> tuple[int, int]:
    """A time anchor's whole seconds, `[r:<rid>@mm:ss-mm:ss]`."""
    start, end = (
        sum(int(p) * 60**i for i, p in enumerate(reversed(t.split(":"))))
        for t in anchor[anchor.index("@") + 1 : -1].split("-")
    )
    return start, end


def fix(spot: spots.Spot, state: spots.State, chosen: str, n: int = 1) -> Fix:
    """A fix line on a spot, its anchors as the spot gives them."""
    return Fix(n, spot.left, spot.right, chosen, state, seconds(spot.recorder_anchor), seconds(spot.whisper_anchor))


def test_a_name_heard_two_ways_is_one_name_spot_near_its_entity_ranked_first() -> None:
    found = find(*pair())
    assert said(found) == [
        ("name", "Nortwin", "Northwood"),
        ("number", "thousand", "million"),
        ("word", "the offer", "the new offer"),
    ]
    first = found.spots[0]
    assert first.near == ["northwind"]
    assert (first.recorder_anchor, first.whisper_anchor) == (
        f"[r:{TRANSCRIPT}@00:00-00:05]",
        f"[r:{WHISPER}@00:00-00:05]",
    )
    assert first.speaker == "Ben Sample"
    # Whisper's words for the line, from its first to its last.
    assert first.line == CALL[0][3] and first.heard == CALL[0][4].rstrip(".")
    assert first.id == spots.spot_id(TRANSCRIPT, 0, "Nortwin", "Northwood")
    assert first.id == find(*pair()).spots[0].id
    assert found.undecided == {"name": 1, "number": 1, "word": 1}


def test_a_number_one_gets_wrong_is_a_spot_and_one_written_two_ways_none() -> None:
    found = find(*call([("the batch is ten thousand", "the batch is ten million"), ("Forty two units", "42 units")]))
    assert said(found) == [("number", "thousand", "million")]
    transcript, whisper = call([("a tízmillió forint", "a tíz millió forint")])
    transcript, whisper = (r.model_copy(update={"lang": "hu"}) for r in (transcript, whisper))
    assert find(transcript, whisper).spots == []
    # Egy alone is a word: Hungarian's article as often as a number.
    transcript, whisper = (r.model_copy(update={"lang": "hu"}) for r in call([("ez egy ajánlat", "ez az ajánlat")]))
    assert said(find(transcript, whisper)) == [("word", "egy", "az")]


def test_a_spot_names_one_place_only() -> None:
    found = find(
        *call([("the offer is ten and the deadline is Friday", "the new offer is ten and the deadline is Friday")])
    )
    assert said(found) == [("word", "the offer", "the new offer")]
    found = find(*call([("ten units, ten each", "ten units, two each")]))
    assert said(found) == [("number", "units, ten each", "units, two each")]


def test_texts_come_back_as_written() -> None:
    found = find(*call([("We met Dr. Kovacs-Toth today", "We met Dr. Kovács-Tóth today")]))
    assert said(found) == [("name", "Kovacs-Toth", "Kovács-Tóth")]
    # A line stored decomposed still holds the spot's text, written composed.
    decomposed = unicodedata.normalize("NFD", "Dóri brought the Nortwin offer")
    found = find(*call([(decomposed, "Dóri brought the Northwind offer")]))
    left = found.spots[0].left
    assert left == "Nortwin" and found.spots[0].line == unicodedata.normalize("NFC", decomposed)
    assert align.place(left, decomposed) is not None


def test_a_capitalised_word_mid_sentence_is_a_name_and_at_a_sentence_s_start_a_word() -> None:
    found = find(*call([("we asked Halden for the crates", "we asked Holden for the crates")]))
    assert said(found) == [("name", "Halden", "Holden")]
    assert found.spots[0].near == []
    found = find(*call([("Done. Later we go", "Done. Then we go")]))
    assert said(found) == [("word", "Later", "Then")]


def test_a_name_both_spell_alike_is_a_spot_when_it_is_an_asr_alias_or_near_a_name_and_none_of_its_forms() -> None:
    found = find(*call([("the offer from Nortwin came", "the offer from Nortwin came")]))
    assert said(found) == [("name", "Nortwin", "Nortwin")]
    assert found.spots[0].near == ["northwind"]
    found = find(*call([("we talked to Ben Sammple", "we talked to Ben Sammple")]))
    assert said(found) == [("name", "Sammple", "Sammple")]
    assert found.spots[0].near == ["ben-sample"]
    # Its own name, or a word in lower case, is no spot.
    assert find(*call([("we talked to Ben Sample", "we talked to Ben Sample")])).spots == []
    assert find(*call([("the nortwin crates", "the nortwin crates")])).spots == []


def test_a_cut_stretch_is_never_listed() -> None:
    transcript, whisper = pair()
    # A cut made after Whisper ran: the recorder's next version holds it, Whisper's transcript doesn't.
    found = find(cut(transcript, 0, 5_500), whisper)
    assert [s.left for s in found.spots] == ["thousand", "the offer"]
    found = find(transcript, cut(whisper, 5_500, 11_500))
    assert [s.left for s in found.spots] == ["Nortwin", "the offer"]


def test_the_cap_lists_forty_names_and_numbers_first_and_counts_the_rest_by_kind() -> None:
    lines = [("we bought it from Nortwin", "we bought it from Northwood")] * 30
    lines += [("we need ten crates now", "we need two crates now")] * 15
    lines += [("we need red crates now", "we need blue crates now")] * 10
    found = find(*call(lines[::-1]))
    assert [s.kind for s in found.spots] == ["name"] * 30 + ["number"] * 10
    assert found.left_out.cap == {"name": 0, "number": 5, "word": 10}
    assert found.undecided == {"name": 30, "number": 15, "word": 10}
    # By time within a kind.
    starts = [seconds(s.recorder_anchor)[0] for s in found.spots[:30]]
    assert starts == sorted(starts)


def test_a_spot_naming_a_listed_person_or_touching_a_redaction_marker_is_left_out_and_counted() -> None:
    lines = [
        ("Ask Dóri about the Nortwin offer", "Ask Dóri about the Northwood offer"),
        ("Call me on [redacted:phone] tonight", "Call me on 06 30 tonight"),
        ("the batch is ten thousand", "the batch is ten million"),
    ]
    lists = NeverStore(people=["Dóra Minta"])
    found = find(*call(lines), lists=lists)
    assert said(found) == [("number", "thousand", "million")]
    assert found.left_out.never_store == 2


def test_heard_differently_is_counted() -> None:
    found = find(*call([("one two three four five six seven", "a b c d e f g")]))
    assert found.spots == [] and found.left_out.heard_differently == 1


def test_a_spot_a_fix_decides_is_not_listed_again_and_an_unsure_one_waits() -> None:
    transcript, whisper = pair()
    first = find(transcript, whisper).spots[0]
    found = find(transcript, whisper, fixes=[fix(first, "unsure", "Northwind")])
    assert [s.left for s in found.spots] == ["thousand", "the offer"]
    assert found.waiting == 1
    assert [(f.n, f.state, f.why, f.chosen) for f in found.fixes] == [(1, "unsure", "waiting", "Northwind")]
    assert found.undecided == {"name": 0, "number": 1, "word": 1}
    found = find(transcript, whisper, fixes=[fix(first, "sure", "Northwind")])
    assert found.waiting == 0 and [f.why for f in found.fixes] == [None]
    # Whisper's anchor may span more segments than its text touches.
    wide = Fix(1, "Nortwin", "Northwood", "Northwind", "sure", (0, 5), (0, 11))
    assert [s.left for s in find(transcript, whisper, fixes=[wide]).spots] == ["thousand", "the offer"]


def test_a_settled_place_comes_after_every_undecided_spot_with_the_picks_turned_down() -> None:
    transcript, whisper = pair()
    first = find(transcript, whisper).spots[0]
    fixes = [fix(first, "rejected", "Northwind"), fix(first, "rejected", "Northwood", 2)]
    found = find(transcript, whisper, fixes=fixes)
    assert [(s.left, s.rejected) for s in found.spots] == [
        ("thousand", []),
        ("the offer", []),
        ("Nortwin", ["Northwind", "Northwood"]),
    ]
    assert found.waiting == 0
    assert found.undecided == {"name": 0, "number": 1, "word": 1}
    assert [f.why for f in found.fixes] == ["rejected", "rejected"]
    # A place where one line is rejected and another stands is decided.
    found = find(transcript, whisper, fixes=[*fixes, fix(first, "unsure", "North Wind", 3)])
    assert [s.left for s in found.spots] == ["thousand", "the offer"]


def test_a_new_version_where_the_word_reads_right_leaves_the_fix_no_longer_matching() -> None:
    transcript, whisper = call([("The offer from Nortwin came", "The offer from Northwind came")])
    first = find(transcript, whisper).spots[0]
    fixed = fix(first, "sure", "Northwind")
    again = recorder([segment(0, 5000, "The offer from Northwind came", "Ben Sample")])
    found = find(again, whisper, fixes=[fixed])
    assert found.spots == []
    assert [(f.why, f.left) for f in found.fixes] == [("no longer matching", "Nortwin")]
    # Its text twice in the new version: no place, so the spot comes back.
    twice = recorder([segment(0, 5000, "Nortwin? The offer from Nortwin came", "Ben Sample")])
    found = find(twice, whisper, fixes=[fixed])
    assert [f.why for f in found.fixes] == ["no longer matching"]
    assert [s.left for s in found.spots] == ["Nortwin? The", "from Nortwin came"]


def test_a_fix_in_a_cut_or_naming_a_listed_person_is_shown_without_its_texts() -> None:
    transcript, whisper = pair()
    first = find(transcript, whisper).spots[0]
    fixed = fix(first, "rejected", "Northwind")
    found = find(cut(transcript, 0, 5_500), whisper, fixes=[fixed])
    assert [(f.why, f.left, f.right, f.chosen) for f in found.fixes] == [("cut", None, None, None)]
    found = find(transcript, cut(whisper, 0, 5_500), fixes=[fixed])
    assert [f.why for f in found.fixes] == ["cut"]
    # Northwind held by a keyword: its alias in the spot names it too, so the spot is left out and its fix hidden.
    found = find(transcript, whisper, fixes=[fixed], lists=NeverStore(keywords=["Northwind"]))
    assert [(f.why, f.chosen) for f in found.fixes] == [("hidden", None)]
    assert [s.left for s in found.spots] == ["thousand", "the offer"]
    assert found.left_out.never_store == 1
    # A Whisper text its segments no longer hold, and a line no longer there.
    elsewhere = Fix(1, "Nortwin", "Northwood", "Northwind", "sure", (0, 5), (40, 45))
    gone = Fix(2, "Nortwin", "Northwood", "Northwind", "sure", (100, 105), (100, 105))
    found = find(transcript, whisper, fixes=[elsewhere, gone])
    assert [f.why for f in found.fixes] == ["no longer matching", "no longer matching"]


def test_the_doc_s_id_is_the_recording_s_date_in_the_owner_s_zone_and_its_rid_s_hash() -> None:
    record = recording(ts=WHEN)
    assert spots.doc_id(record, ZoneInfo("Europe/Budapest")) == f"transcript-fixes:2026-10-15-{_digest(REC)}"
    assert spots.doc_id(record, ZoneInfo("UTC")) == f"transcript-fixes:2026-10-14-{_digest(REC)}"


def _digest(rid: str) -> str:
    return hashlib.sha256(rid.encode()).hexdigest()[:12]


# -- the vault ------------------------------------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Vault:
    return copy_vault(reconcile_template(tmp_path_factory), tmp_path)


def settings(vault: Vault, change: dict[str, object]) -> Vault:
    """vault.yaml changed and committed, as the owner would, and the vault opened again."""
    path = vault.root / "vault.yaml"
    config = VaultConfig.load(path)
    for key, value in change.items():
        target, _, name = key.rpartition(".")
        setattr(getattr(config, target) if target else config, name, value)
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    vault.commit(["vault.yaml"], "Settings")
    return Vault.open(vault.root)


def test_report_gives_the_pair_the_doc_the_people_and_the_spots(vault: Vault) -> None:
    for rid in (REC, TRANSCRIPT, WHISPER):
        found = spots.report(vault, rid)
        assert (found.rid, found.transcript, found.whisper, found.why) == (REC, TRANSCRIPT, WHISPER, None)
    assert found.doc == spots.DocRef(id=f"transcript-fixes:2026-10-15-{_digest(REC)}", rev=None)
    assert [s.left for s in found.spots] == ["Nortwin", "thousand", "the offer"]
    # The meeting's people (its speakers, and Northwind its records name), then the glossary terms near a spot.
    assert [(p.id, p.name) for p in found.people] == [
        ("ben-sample", "Ben Sample"),
        ("dora-minta", "Dóra Minta"),
        ("rita-sample", "Rita Sample"),
        ("northwind", "Northwind"),
    ]
    northwind = found.people[3]
    assert [(a.text, a.asr) for a in northwind.aliases] == [("Nortwin", True), ("Northwind Traders", False)]


def test_a_glossary_term_near_a_spot_joins_the_people_and_a_held_one_never(vault: Vault) -> None:
    lines = [("Ben Sample", 0, 5_000, "the Crane Wurks offer", "the Crane Works offer")]
    vault.write_run("calls", list(pair(lines)))
    found = spots.report(vault, REC)
    assert found.spots[0].near == ["crane-works"]
    assert "crane-works" in [p.id for p in found.people]
    # Held by an alias no record says: out of the people, and the spot that names it left out.
    held = settings(vault, {"never_store.keywords": ["CW Ltd"]})
    found = spots.report(held, REC)
    assert found.why is None
    assert "crane-works" not in [p.id for p in found.people]
    assert (found.spots, found.left_out.never_store) == ([], 1)


def test_another_machine_gets_no_spots_and_why_says_where_to_reconcile(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKET_MACHINE", "other-box")
    found = spots.report(Vault.open(vault.root), REC)
    assert found.spots == [] and found.people == []
    assert found.why == "this machine doesn't ingest calls; reconcile its recordings on the machine that runs it"


def test_a_withheld_family_gets_no_spots_and_why_names_no_value(vault: Vault) -> None:
    media_staged.withhold(vault.root, REC)
    found = spots.report(vault, REC)
    assert found.spots == []
    assert found.why is not None and found.why.startswith("its family is kept out: ")
    listed = settings(Vault.open(vault.root), {"never_store.keywords": ["Nortwin"]})
    media_staged.withheld_path(listed.root).unlink()
    found = spots.report(listed, REC)
    assert found.why == f"its family is kept out: a record of {REC} matches a never-store value"


def test_an_existing_doc_keeps_its_id_when_the_time_zone_moves_its_date(vault: Vault) -> None:
    doc_id = f"transcript-fixes:2026-10-15-{_digest(REC)}"
    path = vault.root / "processed" / "transcript-fixes" / "2026" / "10" / f"2026-10-15-{_digest(REC)}.md"
    path.parent.mkdir(parents=True)
    text = (
        f"---\nid: {doc_id}\ntype: transcript-fixes\nrev: 2\ncovers:\n  - {TRANSCRIPT}\nby: claude-test\n---\n\n# x\n"
    )
    path.write_text(text, encoding="utf-8", newline="\n")
    assert spots.report(vault, REC).doc == spots.DocRef(id=doc_id, rev=None)
    # A file whose name only ends in the hash isn't the doc.
    (path.parent / f"1-{_digest(REC)}.md").write_text(text, encoding="utf-8", newline="\n")
    assert spots.report(vault, REC).doc == spots.DocRef(id=doc_id, rev=None)
    vault.commit([path.relative_to(vault.root).as_posix()], "A fixes doc")
    moved = settings(vault, {"owner.time_zone": "UTC"})
    assert spots.doc_id(recording(), ZoneInfo("UTC")) != doc_id
    assert spots.report(moved, REC).doc == spots.DocRef(id=doc_id, rev=2)


def test_the_recorder_s_transcript_is_the_one_the_summary_covers(vault: Vault) -> None:
    other = recorder([segment(0, 5_000, "Morning, Rita. The offer from Northwind came on Monday.")], rid=f"{REC}/a")
    vault.write_run("calls", [other])
    assert spots.report(vault, REC).transcript == f"{REC}/a"
    note = (
        f"---\nid: recording-note:2026-10-15-supplier-call\ntype: recording-note\ncovers:\n  - {TRANSCRIPT}\n"
        f"by: claude-test, sync 2026-10-15T12:00\n---\n\n# Supplier call\n\n- The offer came. [r:{TRANSCRIPT}@00:00]\n"
    )
    recall.doc_put(vault, note)
    index.rebuild(vault)
    assert spots.report(vault, REC).transcript == TRANSCRIPT


def test_a_rid_that_names_no_pair_is_refused(vault: Vault) -> None:
    with pytest.raises(DocketError, match="isn't a rid"):
        spots.report(vault, "not a rid")
    with pytest.raises(DocketError, match="no record calls:rec-9999 is in raw"):
        spots.report(vault, "calls:rec-9999")
    vault.write_run("calls", [recording("calls:rec-0102"), recorder([segment(0, 900, "Hi")], rid="calls:rec-0102/t")])
    with pytest.raises(DocketError, match="has no Whisper transcript in raw"):
        spots.report(vault, "calls:rec-0102")
    vault.write_run(
        "calls", [recording("calls:rec-0103"), heard([segment(0, 900, "Hi")], rid="calls:rec-0103/whisper")]
    )
    with pytest.raises(DocketError, match="has no transcript of its own source's in raw"):
        spots.report(vault, "calls:rec-0103")


def test_the_listing_holds_recordings_with_spots_no_fix_decides(vault: Vault) -> None:
    quiet = [("Ben Sample", 0, 5_000, "All agreed.", "All agreed.")]
    later = WHEN + timedelta(days=1)
    vault.write_run(
        "calls",
        [
            recording("calls:rec-0102", "Quiet call", later),
            *pair(quiet, "calls:rec-0102"),
            recording("calls:rec-0103", "Held call", later),
            *pair(rec="calls:rec-0103"),
        ],
    )
    media_staged.withhold(vault.root, "calls:rec-0103")
    found = spots.listing(vault)
    assert [(r.rid, r.title, r.spots, r.waiting) for r in found.recordings] == [
        (REC, "Supplier call", {"name": 1, "number": 1, "word": 1}, 0)
    ]
    assert found.hidden == 1
    # Fixes waiting keep it listed; a settled place alone doesn't.
    shown = spots.report(vault, REC).spots
    unsure = [fix(s, "unsure", "x", n) for n, s in enumerate(shown, start=1)]
    found = spots.listing(vault, {REC: unsure})
    assert [(r.spots, r.waiting) for r in found.recordings] == [({"name": 0, "number": 0, "word": 0}, 3)]
    rejected = [fix(s, "rejected", "x", n) for n, s in enumerate(shown, start=1)]
    assert spots.listing(vault, {REC: rejected}).recordings == []


def test_the_listing_holds_at_most_ten_and_passes_over_a_recording_with_no_transcript_of_its_own(vault: Vault) -> None:
    records: list[RawRecord] = []
    for n in range(12):
        rid = f"calls:rec-02{n:02d}"
        records += [recording(rid, f"Call {n}", WHEN + timedelta(hours=n + 1)), *pair(rec=rid)]
    lonely = "calls:rec-0300"
    records += [
        recording(lonely, "Whisper only", WHEN + timedelta(days=2)),
        heard([segment(0, 900, "Hi")], f"{lonely}/whisper"),
    ]
    vault.write_run("calls", records)
    found = spots.listing(vault)
    assert [r.rid for r in found.recordings] == [f"calls:rec-02{n:02d}" for n in range(11, 1, -1)]
    assert found.hidden == 0


def test_the_listing_reads_only_the_sources_this_machine_ingests_and_names_no_listed_person(vault: Vault) -> None:
    listed = settings(vault, {"never_store.people": ["Supplier Call"]})
    assert [r.title for r in spots.listing(listed).recordings] == [None]
    cursors.file_path(listed.root, listed.machine).unlink()
    assert spots.listing(listed) == spots.Listing()


@pytest.mark.perf
def test_an_hour_s_call_takes_well_under_a_second() -> None:
    lines = [
        (
            f"Line {i}: the offer from Nortwin is ten thousand, said Ben",
            f"Line {i}: the offer from Northwood is ten million, said Ben",
        )
        for i in range(720)
    ]
    transcript, whisper = call(lines, step=5_000)
    began = time.perf_counter()
    found = find(transcript, whisper)
    elapsed = time.perf_counter() - began
    print(f"spots: {elapsed:.2f} s for an hour's call of {len(lines)} lines")
    assert len(found.spots) == spots.CAP
    if not traced():
        assert elapsed < 1.0, f"an hour's call took {elapsed:.2f} s"
