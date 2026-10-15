"""The gate applies a release on every route: a released recording's transcript goes in cut, at intake and again at
promotion; a record without segments only when the release keeps it at that version; anything else is left out and
only counted; a hold beats a release."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, make_record

from docket.errors import DocketError
from docket.vault import holds, releaselog
from docket.vault.config import NeverStore, SourceConfig, VaultConfig
from docket.vault.cuts import CUT_MARKER
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.releaselog import Kept, LoggedCut, Release
from docket.vault.vault import Vault

TALK: list[dict[str, Any]] = [
    {"start_ms": 0, "end_ms": 10_000, "speaker": "Ben Sample", "text": "Morning, Ada."},
    {"start_ms": 10_000, "end_ms": 20_000, "speaker": "Ada Example", "text": "First, my weekend plans."},
    {"start_ms": 20_000, "end_ms": 30_000, "speaker": "Ben Sample", "text": "Now the offer: ten million."},
]
CUT = (10_000, 20_000)


@pytest.fixture
def vault(tmp_path: Path, config: VaultConfig) -> Vault:
    config.sources["talks"] = SourceConfig(sensitivity="work")
    config.sources["hush"] = SourceConfig(sensitivity="private")
    config.sources["paused"] = SourceConfig(enabled=False)
    config.never_store = NeverStore(keywords=["zebrafinch"])
    return empty_vault(tmp_path, config)


def rec(native: str = "rec-1", source: str = "calls", **fields: Any) -> RawRecord:
    base: dict[str, Any] = {
        "rid": f"{source}:{native}",
        "source": source,
        "kind": "recording",
        "container": f"{source}:{native}",
        "title": "Supplier sync",
        "text": "",
    }
    return make_record(1, **{**base, **fields})


def transcript(native: str = "rec-1", source: str = "calls", segments: list[dict[str, Any]] = TALK) -> RawRecord:
    return rec(native, source, rid=f"{source}:{native}/transcript", kind="transcript", segments=segments, title=None)


def summary(native: str = "rec-1", text: str = "Plans for the weekend, then the offer.") -> RawRecord:
    return rec(native, rid=f"calls:{native}/summary", kind="vendor-summary", text=text, title=None)


def release(vault: Vault, native: str = "rec-1", source: str = "calls", keep: list[RawRecord] | None = None) -> None:
    gate = Gate(vault)
    kept: list[Kept] = []
    for record in keep or []:
        view = gate.intake_view(record)
        assert isinstance(view, RawRecord)
        kept.append(Kept(rid=view.rid, v=view.v))
    line = Release(
        id=native,
        cuts=[LoggedCut(start=CUT[0], end=CUT[1])],
        keep=kept,
        at=datetime(2026, 10, 15, 9, 12, tzinfo=UTC),
        machine="test-box",
    )
    releaselog.add(vault, source, line)


def staged(vault: Vault) -> dict[str, RawRecord]:
    return {e.staged.record.rid: e.staged.record for e in Gate(vault).list().entries}


def raw(vault: Vault) -> dict[str, RawRecord]:
    return {r.rid: r for _, _, r in vault.rawlog.iter_records()}


def inside_cut(record: RawRecord) -> list[str]:
    return [s.text for s in record.segments or [] if s.text != CUT_MARKER and s.start_ms < CUT[1] and s.end_ms > CUT[0]]


def test_a_released_transcript_is_staged_cut_and_the_summary_left_out(vault: Vault) -> None:
    release(vault, keep=[rec()])
    result = Gate(vault).stage([rec(), transcript(), summary()])
    assert (result.pending, result.ready, result.dropped["left-out"]) == (2, 0, 1)
    got = staged(vault)
    assert set(got) == {"calls:rec-1", "calls:rec-1/transcript"}
    cut = got["calls:rec-1/transcript"]
    assert inside_cut(cut) == []
    assert "weekend" not in cut.model_dump_json()
    assert [s.text for s in cut.segments or []] == ["Morning, Ada.", CUT_MARKER, "Now the offer: ten million."]
    assert (cut.fidelity, cut.native) == ("cut", {})


def test_a_kept_record_goes_only_at_the_version_the_owner_saw(vault: Vault) -> None:
    release(vault, keep=[rec()])
    result = Gate(vault).stage([rec(title="Supplier sync, and the weekend")])
    assert result.dropped["left-out"] == 1
    assert staged(vault) == {}


def test_a_version_the_release_s_times_don_t_fit_is_left_out_and_the_batch_goes_on(vault: Vault) -> None:
    release(vault, keep=[rec()])
    short = transcript(segments=[{"start_ms": 0, "end_ms": 5_000, "text": "Morning."}])
    result = Gate(vault).stage([short, rec()])
    assert result.dropped["left-out"] == 1
    assert set(staged(vault)) == {"calls:rec-1"}


def test_one_staged_before_its_release_that_the_times_don_t_fit_is_left_out_at_promotion(vault: Vault) -> None:
    gate = Gate(vault)
    short = transcript(segments=[{"start_ms": 0, "end_ms": 5_000, "text": "Morning."}])
    gate.stage([short, summary()])
    gate.classify("calls:rec-1/transcript", "work")
    release(vault)
    result = gate.promote()
    assert result.dropped["left-out"] == 2
    assert result.runs == []
    assert raw(vault) == {}


def test_a_hold_beats_a_release(vault: Vault) -> None:
    release(vault)
    # A merge brought the hold back: an add would leave a released id out, so the list is written as the merge left it.
    (vault.root / holds.HOLDS).mkdir(parents=True)
    (vault.root / holds.HOLDS / "calls.txt").write_text("rec-1\n", encoding="utf-8")
    result = Gate(vault).stage([transcript()])
    assert (result.on_hold, result.pending) == (1, 0)


def test_one_staged_uncut_under_its_hold_promotes_cut_once_released(vault: Vault) -> None:
    gate = Gate(vault)
    gate.stage([transcript()])
    holds.add(vault, "calls", ["rec-1"])
    assert gate.promote().on_hold == 0
    release(vault)
    holds.remove(vault, "calls", ["rec-1"])
    gate.classify("calls:rec-1/transcript", "work")
    gate.promote()
    stored = raw(vault)["calls:rec-1/transcript"]
    assert inside_cut(stored) == []
    assert CUT_MARKER in stored.text


def test_classify_work_without_cuts_still_cuts_by_the_release_and_private_still_drops(vault: Vault) -> None:
    gate = Gate(vault)
    gate.stage([transcript(), transcript("rec-2")])
    release(vault)
    release(vault, "rec-2")
    gate.classify("calls:rec-1/transcript", "work")
    assert gate.classify("calls:rec-2/transcript", "private") is None
    gate.promote()
    assert set(raw(vault)) == {"calls:rec-1/transcript"}
    assert inside_cut(raw(vault)["calls:rec-1/transcript"]) == []


def test_the_sweep_deletes_a_staged_record_the_release_leaves_out(vault: Vault) -> None:
    gate = Gate(vault)
    gate.stage([summary(), rec()])
    release(vault, keep=[rec()])
    assert set(staged(vault)) == {"calls:rec-1"}


def test_a_released_record_from_a_work_source_waits_as_pending(vault: Vault) -> None:
    release(vault, "talk-1", "talks")
    result = Gate(vault).stage([transcript("talk-1", "talks")])
    assert (result.pending, result.ready) == (1, 0)


def test_a_second_identical_pull_after_promotion_is_skipped(vault: Vault) -> None:
    release(vault)
    gate = Gate(vault)
    gate.stage([transcript()])
    gate.classify("calls:rec-1/transcript", "work")
    gate.promote()
    again = gate.stage([transcript()])
    assert (again.skipped, again.pending) == (1, 0)


def test_intake_view_says_what_intake_drops_and_redacts_the_rest(vault: Vault) -> None:
    gate = Gate(vault)
    assert gate.intake_view(rec(text="about the zebrafinch")) == "never-store"
    assert gate.intake_view(rec(source="hush", rid="hush:rec-1", container=None)) == "private source"
    view = gate.intake_view(rec(text="password: hunter2-long-enough"))
    assert isinstance(view, RawRecord)
    assert "hunter2" not in view.text
    assert view.v.startswith("sha256:")
    with pytest.raises(DocketError, match=r"isn't in vault\.yaml"):
        gate.intake_view(rec(source="other", rid="other:rec-1", container=None))
    # What stage refuses, intake_view refuses: a release stops before its question, not after its commits.
    with pytest.raises(DocketError, match=r"'paused' is disabled in vault\.yaml"):
        gate.intake_view(rec(source="paused", rid="paused:rec-1", container=None))
    with pytest.raises(DocketError, match=r"'paused' is disabled in vault\.yaml"):
        gate.stage([rec(source="paused", rid="paused:rec-1", container=None)])
