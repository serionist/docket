"""A Whisper transcript follows its family's cuts (DR-0590, DR-0591): the promotion that brings a cut to a recording's
family writes the family's Whisper transcript's next version, cut by text, in the same run and commit. The vaults
here hold no audio, no media key and no speech install, so every test also shows the cut needs none. The records are
invented."""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import make_config, make_record, sh

from docket.index import rebuild, search
from docket.index.build import db_path
from docket.media import staged as media_staged
from docket.speech import follow
from docket.vault import cursors, holds, rawlog, releaselog
from docket.vault.config import CONFIG_FILE, SourceConfig, VaultConfig
from docket.vault.cuts import CUT_MARKER, Cut
from docket.vault.gate import Gate, PromoteResult
from docket.vault.records import TRANSCRIBED_VIA, RawRecord
from docket.vault.releaselog import Kept, LoggedCut, Release
from docket.vault.segments import normalize, render
from docket.vault.spans import RawLookup
from docket.vault.staging import Staging, rid_hash
from docket.vault.vault import Vault

CALL = "plaud:rec-a"
POLISHED = f"{CALL}/transaction_polish"
SUMMARY = f"{CALL}/summary"
WHISPER = f"{CALL}/whisper"
KEY = "ab" * 32
ENGINE = "whisper-fake@0123456 faster-whisper-1.2.1"
AT = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
# The issue's stretch: 31:05 to 31:40.
START, END = 1_865_000, 1_900_000
CUT = Cut(start=START, end=END, unit="ms")
UNDER: dict[str, Any] = {"source": "plaud", "container": CALL, "thread": CALL, "author": None}
SAID = [
    {"start_ms": 0, "end_ms": 9000, "speaker": "Ada Example", "text": "The crane passed its load test."},
    {"start_ms": 1_850_000, "end_ms": 1_864_000, "speaker": "Ada Example", "text": "Before the break."},
    {"start_ms": 1_870_000, "end_ms": 1_895_000, "speaker": "Ben Sample", "text": "My dentist moved to Friday."},
    {"start_ms": 1_910_000, "end_ms": 1_920_000, "speaker": "Ben Sample", "text": "Back to the brake pads."},
]
HEARD = [
    (500, 8800, "The crane passed its load test."),
    (1_850_000, 1_864_900, "Before the break, said twice."),
    (1_866_000, 1_898_000, "My orthodontist moved it to Friday."),
    (1_900_100, 1_904_000, "So, as I said."),
    (1_900_300, 1_908_000, "Back to the brake pads."),
]


class Stopped(BaseException):
    """The process ended here, as a kill would end it."""


def segments(spans: list[tuple[int, int, str]]) -> list[dict[str, Any]]:
    return [{"start_ms": start, "end_ms": end, "text": text} for start, end, text in spans]


def recorder(said: list[dict[str, Any]] = SAID, rid: str = POLISHED) -> RawRecord:
    lines = "\n".join(f"{s['speaker']}: {s['text']}" for s in said)
    return make_record(rid=rid, kind="transcript", segments=said, text=lines, **UNDER)


def whisper(heard: list[tuple[int, int, str]] = HEARD, key: str = KEY) -> RawRecord:
    """A Whisper transcript as `docket transcribe` stages one, without the audio it was heard from."""
    record = make_record(
        rid=WHISPER,
        kind="transcript",
        segments=segments(heard),
        to=["Ada Example", "Ben Sample"],
        via=f"{TRANSCRIBED_VIA} {key}",
        fidelity="derived",
        engine=ENGINE,
        lang="en",
        **UNDER,
    )
    segs = normalize(record.segments or [])
    return record.model_copy(update={"segments": segs, "text": render(segs)})


def summary(text: str = "## Summary\n\nThe crane passed.") -> RawRecord:
    return make_record(rid=SUMMARY, kind="vendor-summary", text=text, **UNDER)


def plaud_vault(tmp_path: Path, *, runs: bool = True, name: str = "vault") -> Vault:
    """A vault with the call, its transcript and its Whisper transcript in raw; plaud a source this machine runs."""
    config = make_config()
    config.sources = {"plaud": SourceConfig()}
    vault = Vault.init(tmp_path / name, config, "test-box")
    if runs:
        cursors.save(vault, "plaud", cursors.Cursor())
    Gate(vault).stage([make_record(rid=CALL, kind="recording", title="Crane check", text="", **UNDER)])
    Gate(vault).stage([recorder(), summary(), whisper()])
    Gate(vault).promote()
    return vault


def later(vault: Vault, *cuts: Cut, said: list[dict[str, Any]] | None = None) -> None:
    """A later version of the transcript, staged and cut by the owner."""
    newer = said or [*SAID, {"start_ms": 1_930_000, "end_ms": 1_935_000, "speaker": "Ada Example", "text": "Bye."}]
    Gate(vault).stage([recorder(newer)])
    if cuts:
        Gate(vault).classify(POLISHED, "work", cuts=cuts)


def latest(vault: Vault, rid: str = WHISPER) -> RawRecord:
    found = RawLookup(vault.root).latest(rid)
    assert found is not None
    return found


def marks(record: RawRecord) -> list[tuple[int, int]]:
    return [(s.start_ms, s.end_ms) for s in record.segments or [] if s.text == CUT_MARKER]


def words(record: RawRecord) -> list[str]:
    return [s.text for s in record.segments or [] if s.text != CUT_MARKER]


def found_in(vault: Vault, query: str) -> set[str]:
    rebuild(vault)
    return {hit.rid or "" for group in search(vault, query).groups for hit in group.hits}


def change_config(vault: Vault, change: Callable[[VaultConfig], None]) -> None:
    """A change of vault.yaml by hand that only makes it stricter: in force at once."""
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path)
    change(config)
    path.write_text(config.dump(), encoding="utf-8", newline="\n")


def unfollowed(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> PromoteResult:
    """The cut promoted as an older Docket promotes it: without the follow."""
    later(vault, CUT)
    with monkeypatch.context() as patched:
        patched.setattr(follow, "due", lambda *args, **kwargs: [])
        result = Gate(vault).promote()
    assert result.followed == 0 and marks(latest(vault)) == []
    return result


def test_a_later_cut_reaches_the_whisper_transcript_in_the_same_run_and_commit(tmp_path: Path) -> None:
    vault = plaud_vault(tmp_path)
    assert POLISHED in found_in(vault, "dentist") and WHISPER in found_in(vault, "orthodontist")
    later(vault, CUT)
    result = Gate(vault).promote()
    assert result.followed == 1
    [run] = result.runs
    assert len(run.files) == 1 and run.written == 2
    lines = [RawRecord.model_validate_json(line) for line in run.files[0].read_text(encoding="utf-8").splitlines()]
    assert [r.rid for r in lines] == [POLISHED, WHISPER]
    made = latest(vault)
    assert made == lines[1]
    assert marks(made) == [(START, END)]
    # The stretch goes, with what Whisper heard within 250 ms of it: the audio it heard still held the stretch.
    assert words(made) == ["The crane passed its load test.", "Back to the brake pads."]
    assert (made.via, made.engine, made.to, made.fidelity) == (
        f"{TRANSCRIBED_VIA} {KEY}",
        ENGINE,
        whisper().to,
        "derived",
    )
    assert sh(vault.root, "git", "status", "--porcelain", "--", "raw") == ""
    assert run.files[0].relative_to(vault.root).as_posix() in sh(vault.root, "git", "show", "--name-only", "HEAD")
    assert POLISHED not in found_in(vault, "dentist") and found_in(vault, "orthodontist") == set()


def test_a_later_version_with_no_new_cut_writes_no_whisper_version(tmp_path: Path) -> None:
    vault = plaud_vault(tmp_path)
    later(vault, said=[*SAID, {"start_ms": 1_930_000, "end_ms": 1_935_000, "speaker": "Ada Example", "text": "Hi."}])
    assert Gate(vault).promote().followed == 0
    later(vault, CUT)
    assert Gate(vault).promote().followed == 1
    # A cut inside the marks the Whisper transcript holds already.
    inside = [*SAID, {"start_ms": 1_940_000, "end_ms": 1_945_000, "speaker": "Ada Example", "text": "Later."}]
    later(vault, Cut(start=START + 1000, end=END - 1000, unit="ms"), said=inside)
    assert Gate(vault).promote().followed == 0
    assert len(RawLookup(vault.root).versions(WHISPER)) == 2


def test_a_fresh_transcript_is_never_cut_further_and_a_grown_range_is_padded_on_its_new_side(tmp_path: Path) -> None:
    vault = plaud_vault(tmp_path)
    later(vault, CUT)
    Gate(vault).promote()
    # As `docket transcribe` makes one from audio silent over the range: what sits beside the range is kept.
    heard = [
        HEARD[0],
        HEARD[1],
        (START, END, CUT_MARKER),
        (1_900_100, 1_901_500, "So, as I said."),
        (1_902_100, 1_903_000, "Just past."),
        (1_903_100, 1_905_000, "Clear past."),
    ]
    Gate(vault).stage([whisper(heard, key="cd" * 32)])
    Gate(vault).classify(WHISPER, "work")
    assert Gate(vault).promote().followed == 0
    assert "So, as I said." in words(latest(vault))
    assert Gate(vault).promote().followed == 0
    # The range grows to 31:42: its held start isn't padded, so 30:50 to 31:04.9 stays; its new end is.
    later(
        vault,
        Cut(start=START, end=1_902_000, unit="ms"),
        said=[*SAID, {**SAID[0], "start_ms": 1_960_000, "end_ms": 1_965_000}],
    )
    assert Gate(vault).promote().followed == 1
    grown = latest(vault)
    assert marks(grown) == [(START, 1_902_000)]
    assert words(grown) == ["The crane passed its load test.", "Before the break, said twice.", "Clear past."]


def test_a_summary_classified_private_beside_the_cut_still_gets_the_whisper_version(tmp_path: Path) -> None:
    vault = plaud_vault(tmp_path)
    Gate(vault).stage([summary("## Summary\n\nThe dentist, then the crane.")])
    Gate(vault).classify(SUMMARY, "private")
    later(vault, CUT)
    assert rid_hash(CALL) in media_staged.read_withheld(vault.root)
    assert Staging(vault.root, vault.home).decided().private(rid_hash(SUMMARY))
    assert Gate(vault).promote().followed == 1
    assert marks(latest(vault)) == [(START, END)]


def test_a_machine_that_doesn_t_run_the_source_writes_nothing(tmp_path: Path) -> None:
    vault = plaud_vault(tmp_path, runs=False)
    later(vault, CUT)
    result = Gate(vault).promote()
    assert result.followed == 0 and result.runs[0].written == 1
    assert marks(latest(vault)) == []


def test_a_staged_whisper_transcript_is_cut_as_it_is_promoted_and_is_the_family_s_only_one(tmp_path: Path) -> None:
    vault = plaud_vault(tmp_path)
    again = whisper([*HEARD, (1_950_000, 1_955_000, "Heard again.")], key="cd" * 32)
    Gate(vault).stage([again])
    later(vault, CUT)
    result = Gate(vault).promote()
    assert result.followed == 1
    lines = [RawRecord.model_validate_json(line) for line in result.runs[0].files[0].read_text("utf-8").splitlines()]
    assert sorted(r.rid for r in lines) == [POLISHED, WHISPER]
    made = latest(vault)
    assert made.via == again.via and marks(made) == [(START, END)] and "Heard again." in words(made)


def test_a_waiting_whisper_transcript_stays_and_is_cut_when_it_is_promoted(tmp_path: Path) -> None:
    vault = plaud_vault(tmp_path)
    later(vault, CUT)
    Gate(vault).promote()
    # Made again after the follow, it waits for the owner: a version of a cut record (DR-0592).
    again = whisper(
        [*HEARD[:2], (START, END, CUT_MARKER), HEARD[4], (1_950_000, 1_955_000, "Heard again.")], key="cd" * 32
    )
    Gate(vault).stage([again])
    [waiting] = [e for e in Staging(vault.root, vault.home).scan().entries if e.staged.record.rid == WHISPER]
    assert waiting.staged.state == "pending"
    second = Cut(start=1_850_000, end=1_864_500, unit="ms")
    later(vault, CUT, second, said=[*SAID[:3], {**SAID[3], "text": "Back to the brakes."}])
    assert Gate(vault).promote().followed == 1
    assert marks(latest(vault)) == [(1_850_000, 1_864_500), (START, END)]
    assert latest(vault).via == whisper().via, "the version in raw was followed; the waiting one stays"
    Gate(vault).classify(WHISPER, "work")
    assert Gate(vault).promote().followed == 1
    assert latest(vault).via == again.via and marks(latest(vault)) == [(1_850_000, 1_864_500), (START, END)]


@pytest.mark.parametrize("stop", ["private", "held", "never-store"])
def test_what_stops_the_follow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: str) -> None:
    vault = plaud_vault(tmp_path)
    unfollowed(vault, monkeypatch)
    if stop == "private":
        change_config(vault, lambda c: setattr(c.sources["plaud"], "sensitivity", "private"))
    if stop == "held":
        holds.add(vault, "plaud", ["rec-a"])
    if stop == "never-store":
        change_config(vault, lambda c: c.never_store.people.append("Ben Sample"))
    assert Gate(vault).promote().followed == 0
    assert marks(latest(vault)) == []


def test_a_source_only_turned_off_is_still_followed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = plaud_vault(tmp_path)
    unfollowed(vault, monkeypatch)
    change_config(vault, lambda c: setattr(c.sources["plaud"], "enabled", False))
    assert Gate(vault).promote().followed == 1


def test_a_cut_from_an_older_docket_is_followed_at_the_next_promotion_and_then_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = plaud_vault(tmp_path)
    unfollowed(vault, monkeypatch)
    result = Gate(vault).promote()
    assert result.followed == 1
    [run] = result.runs
    assert run.written == 1 and marks(latest(vault)) == [(START, END)]
    reads: list[list[str]] = []
    real = Gate._latest  # pyright: ignore[reportPrivateUsage]

    def counted(self: Gate, rids: list[str]) -> dict[str, RawRecord | None]:
        reads.append(rids)
        return real(self, rids)

    monkeypatch.setattr(Gate, "_latest", counted)
    again = Gate(vault).promote()
    assert (again.runs, again.followed) == ([], 0)
    assert reads == [[]], "the family's marks hold every range, so raw isn't read"


def test_a_character_cut_or_one_that_doesn_t_fit_adds_no_range(tmp_path: Path) -> None:
    vault = plaud_vault(tmp_path)
    staging = Staging(vault.root, vault.home)
    other = f"{CALL}/other_block"
    Gate(vault).stage([summary("## Summary\n\nThe dentist, then the crane."), recorder(rid=other)])
    for entry in staging.scan().entries:
        if entry.staged.record.rid == SUMMARY:
            bad = Cut(start=0, end=9999, unit="char")
        else:
            bad = Cut(start=9_000_000, end=9_100_000, unit="ms")
        staging.write(entry.staged.model_copy(update={"state": "ready", "classified_by": "user", "cuts": [bad]}))
    later(vault, CUT)
    result = Gate(vault).promote()
    assert len(result.held) == 2, "neither of those cuts can be made, so both wait"
    assert result.followed == 1 and marks(latest(vault)) == [(START, END)]


def test_a_crash_after_the_run_file_leaves_one_whisper_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = plaud_vault(tmp_path)
    later(vault, CUT)
    real = Vault.commit

    def crash(self: Vault, paths: list[str], message: str) -> str | None:
        if message.startswith("Raw: plaud,"):
            raise Stopped
        return real(self, paths, message)

    with monkeypatch.context() as patched:
        patched.setattr(Vault, "commit", crash)
        with pytest.raises(Stopped):
            Gate(vault).promote()
    assert Gate(vault).promote().followed == 0
    assert [marks(r) for r in RawLookup(vault.root).versions(WHISPER)] == [[], [(START, END)]]
    assert sh(vault.root, "git", "status", "--porcelain", "--", "raw") == ""


def test_a_crash_between_the_recorder_s_line_and_the_whisper_s(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = plaud_vault(tmp_path)
    later(vault, CUT)
    monkeypatch.setattr(rawlog, "RUN_FILE_BYTES", 1)
    real = rawlog.RawLog._write_new  # pyright: ignore[reportPrivateUsage]
    written: list[Path] = []

    def stops(self: rawlog.RawLog, target: Path, text: str) -> None:
        if written:
            raise Stopped
        written.append(target)
        real(self, target, text)

    with monkeypatch.context() as patched:
        patched.setattr(rawlog.RawLog, "_write_new", stops)
        with pytest.raises(Stopped):
            Gate(vault).promote()
    assert marks(latest(vault)) == [] and marks(latest(vault, POLISHED)) == [(START, END)]
    assert Gate(vault).promote().followed == 1
    assert Gate(vault).promote().followed == 0
    assert len(RawLookup(vault.root).versions(WHISPER)) == 2


def test_two_clones_from_the_same_raw_write_the_same_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = plaud_vault(tmp_path)
    shutil.copytree(first.root, tmp_path / "other")
    # A name that sorts after this one: a run in the same minute by one that sorts first lies before (Risks).
    monkeypatch.setenv("DOCKET_MACHINE", "zeta-box")
    second = Vault.open(tmp_path / "other")
    cursors.save(second, "plaud", cursors.Cursor())
    later(second, CUT)
    [theirs] = Gate(second).promote().runs
    monkeypatch.setenv("DOCKET_MACHINE", "test-box")
    later(first, CUT)
    Gate(first).promote()
    assert latest(first).v == latest(second).v
    # What a pull of the other clone's run file brings: the same version twice, read as one.
    copy = first.root / theirs.files[0].relative_to(second.root)
    shutil.copyfile(theirs.files[0], copy)
    assert len(RawLookup(first.root).versions(WHISPER)) == 3 and latest(first).v == latest(second).v
    rebuild(first)
    with sqlite3.connect(db_path(first)) as conn:
        assert conn.execute("SELECT versions FROM records WHERE rid = ?", (WHISPER,)).fetchone() == (2,)


def test_the_follow_keeps_to_the_promoted_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = plaud_vault(tmp_path)
    unfollowed(vault, monkeypatch)
    assert Gate(vault).promote("chat").followed == 0
    assert Gate(vault).promote("plaud").followed == 1


def test_a_release_s_cuts_reach_the_whisper_transcript_with_the_version_they_cut(tmp_path: Path) -> None:
    vault = plaud_vault(tmp_path)
    kept = [Kept(rid=r.rid, v=r.v) for rid in (CALL, SUMMARY) for r in vault.rawlog.read(rid)]
    entry = Release(id="rec-a", cuts=[LoggedCut(start=600_000, end=620_000)], keep=kept, at=AT, machine="test-box")
    releaselog.add(vault, "plaud", entry)
    later(vault)
    Gate(vault).classify(POLISHED, "work")
    assert Gate(vault).promote().followed == 1
    assert marks(latest(vault, POLISHED)) == [(600_000, 620_000)] == marks(latest(vault))
