"""`docket transcribe` (DR-0569, DR-0570): a recording's stored audio, decoded by the real child and transcribed by a
stand-in worker, staged as `<rid>/whisper` with the recording's people, cut where the family is cut. The checks run
before anything decodes and again before staging; the gate's sweep drops a waiting Whisper transcript whose family
nothing may be kept of. The audio is a tone made here, the records invented."""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from filelock import FileLock
from media_kit import CALL, POLISHED, SAID, call, in_raw, media_vault, tone

from docket import userconfig
from docket.errors import DocketError
from docket.media import crypto, layout, reader
from docket.media import put as media
from docket.models.catalogue import ModelFile
from docket.speech import client, job, setup
from docket.speech.catalogue import SpeechModel
from docket.vault import holds, releaselog
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.cuts import Cut
from docket.vault.gate import Gate
from docket.vault.records import TRANSCRIBED_VIA, RawRecord, Segment
from docket.vault.releaselog import Kept, LoggedCut, Release
from docket.vault.staging import Staging
from docket.vault.vault import Vault

FAKE = Path(__file__).with_name("speech_fake_worker.py")
WHISPER = f"{CALL}/whisper"
MODEL = SpeechModel(
    name="fake",
    repo="example/fake-whisper",
    revision="0123456789abcdef0123456789abcdef01234567",
    files=(ModelFile("model.bin", 7, "0" * 64),),
    licence="MIT",
)
SPEECH = setup.Speech(MODEL, "cpu", Path(sys.executable))
WORDS = [[500, 4000, "The crane passed its load test."], [10_500, 18_000, "The brake pad comes on Monday."]]
NEWER = dataclasses.replace(SPEECH, model=dataclasses.replace(MODEL, revision="89abcdef" * 5))
AT = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def worker(tmp_path: Path, **plan: Any) -> list[str]:
    plan.setdefault("segments", WORDS)
    plan.setdefault("spec_out", str(tmp_path / "spec.json"))
    return [sys.executable, str(FAKE), json.dumps(plan)]


def put(vault: Vault) -> None:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(tone(20.0))
    media.put(vault, CALL, path=str(drop / "take"))


def stored_vault(tmp_path: Path, **kwargs: Any) -> Vault:
    vault = media_vault(tmp_path, **kwargs)
    in_raw(vault)
    put(vault)
    in_raw(vault)
    assert reader.state_of(vault, CALL) == "ok"
    return vault


def transcribe(vault: Vault, tmp_path: Path, speech: setup.Speech = SPEECH, **plan: Any) -> job.Outcome:
    return job.transcribe(vault, CALL, speech=speech, worker=worker(tmp_path, **plan), say=lambda _: None)


def staged(vault: Vault) -> list[RawRecord]:
    return [e.staged.record for e in Staging(vault.root, vault.home).scan().entries if e.staged.record.rid == WHISPER]


def plain_left(vault: Vault) -> list[str]:
    folder = vault.home.tmp_dir / f"media-cut-{os.getpid()}"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def change_config(vault: Vault, change: Callable[[VaultConfig], None]) -> None:
    """A change of vault.yaml by hand that only makes it stricter: in force at once."""
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path)
    change(config)
    path.write_text(config.dump(), encoding="utf-8", newline="\n")


def release(vault: Vault, *cuts: tuple[int, int]) -> None:
    kept = {(r.rid, r.v) for r in vault.rawlog.read(CALL)}
    entry = Release(
        id="rec-a",
        cuts=[LoggedCut(start=s, end=e) for s, e in cuts],
        keep=[Kept(rid=rid, v=v) for rid, v in sorted(kept)],
        at=AT,
        machine="test-box",
    )
    releaselog.add(vault, "plaud", entry)


def during(monkeypatch: pytest.MonkeyPatch, act: Callable[[], object]) -> None:
    """Something happens while the worker runs: after it answers and before step 4."""
    real = client.run_job

    def wrapped(*args: Any, **kwargs: Any) -> client.Result:
        found = real(*args, **kwargs)
        act()
        return found

    monkeypatch.setattr(client, "run_job", wrapped)


def test_a_recording_s_stored_audio_becomes_its_whisper_transcript_at_the_gate(tmp_path: Path) -> None:
    vault = stored_vault(tmp_path)
    said: list[str] = []
    done = job.transcribe(vault, CALL, speech=SPEECH, worker=worker(tmp_path, language="en"), say=said.append)
    assert done.staged and done.line == f"staged {WHISPER}: 2 segment(s), en; it goes in at the next promotion"
    assert said == ["transcribing 0:20 of audio with whisper fake on cpu", "  100%"]
    [record] = staged(vault)
    key = crypto.key_id(layout.stored_files(vault.root, CALL)[0])
    assert (record.kind, record.source, record.container, record.thread) == ("transcript", "plaud", CALL, CALL)
    assert record.ts == call()[0].ts and record.author is None
    assert record.to == ["Ada Example", "Ben Sample"], "the recorder's speakers, so a never-store name reaches it"
    assert [(s.start_ms, s.end_ms, s.speaker, s.text) for s in record.segments or []] == [
        (500, 4000, None, "The crane passed its load test."),
        (10_500, 18_000, None, "The brake pad comes on Monday."),
    ]
    assert record.text == "The crane passed its load test.\nThe brake pad comes on Monday."
    assert record.via == f"{TRANSCRIBED_VIA} {key}" and record.fidelity == "derived" and record.lang == "en"
    assert record.engine == "whisper-fake@0123456 faster-whisper-1.2.1"
    spec = json.loads((tmp_path / "spec.json").read_text(encoding="utf-8"))
    # The owner speaks two languages and the recorder named none, so Whisper detects it, with no prompt.
    assert (spec["language"], spec["prompt"], spec["vad"], spec["compute"]) == (None, None, True, "int8")
    assert plain_left(vault) == [], "both plain files go"
    assert reader.state_of(vault, CALL) == "ok"
    # The same file and engine again: nothing, while it waits and once it is in raw.
    again = transcribe(vault, tmp_path)
    assert not again.staged and again.line.endswith("was transcribed by whisper-fake@0123456 faster-whisper-1.2.1 "
                                                    "already; nothing staged")  # fmt: skip
    Gate(vault).promote()
    assert [r.rid for r in vault.rawlog.read(WHISPER)] == [WHISPER]
    assert not transcribe(vault, tmp_path).staged
    assert reader.state_of(vault, CALL) == "ok"
    # A new model revision makes a new version when it hears something else, and nothing when it hears the same.
    assert "the same transcript is staged or in raw already" in transcribe(vault, tmp_path, NEWER).line
    done = transcribe(vault, tmp_path, NEWER, segments=[[500, 4000, "The crane passed its load test, twice."]])
    assert done.staged and staged(vault)[0].engine == "whisper-fake@89abcde faster-whisper-1.2.1"


def test_the_family_s_cuts_hold_no_words_and_the_audio_stays_playable(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    Gate(vault).classify(POLISHED, "work", [Cut(start=10_000, end=12_000, unit="ms")])
    in_raw(vault)
    put(vault)
    in_raw(vault)
    heard = [
        [500, 4000, "The crane passed."],
        [8_500, 10_200, "straddles the cut"],
        [11_000, 12_000, "inside the cut"],
        [19_200, 19_800, "after it"],
    ]
    assert transcribe(vault, tmp_path, segments=heard).staged
    [record] = staged(vault)
    assert [(s.start_ms, s.end_ms, s.text) for s in record.segments or []] == [
        (500, 4000, "The crane passed."),
        (10_000, 19_000, "[cut]"),
        (19_200, 19_800, "after it"),
    ]
    assert record.to == ["Ada Example"], "Ben's utterance was cut with its stretch"
    assert reader.state_of(vault, CALL) == "ok", "its [cut] lies inside what the stored file was cut by"
    Gate(vault).promote()
    assert reader.state_of(vault, CALL) == "ok" and vault.rawlog.read(WHISPER)


def test_a_hungarian_recording_gets_its_people_s_names_as_its_prompt(tmp_path: Path) -> None:
    vault = stored_vault(tmp_path)
    rel = "entities/person/ben-sample.yaml"
    (vault.root / rel).parent.mkdir(parents=True, exist_ok=True)
    (vault.root / rel).write_text(
        "id: ben-sample\ntype: person\nname: {en: Ben Sample}\naliases:\n  - {text: Bén Szempl, kind: asr}\n",
        encoding="utf-8",
        newline="\n",
    )
    vault.commit([rel], "An entity")
    # A second version of the recorder's transcript, which says it is Hungarian.
    [polished] = [r for r in call(text="A daru átment a próbán.") if r.rid == POLISHED]
    Gate(vault).stage([polished.model_copy(update={"lang": "hu"})])
    assert transcribe(vault, tmp_path).staged
    spec = json.loads((tmp_path / "spec.json").read_text(encoding="utf-8"))
    assert spec["language"] == "hu" and spec["prompt"] == "Résztvevők és témák: Ben Sample, Bén Szempl."
    assert staged(vault)[0].lang == "hu"


def never_stored(vault: Vault) -> None:
    change_config(vault, lambda c: c.never_store.keywords.append("crane"))


def private(vault: Vault) -> None:
    change_config(vault, lambda c: setattr(c.sources["plaud"], "sensitivity", "private"))


@pytest.mark.parametrize(
    ("make", "rid", "said"),
    [
        ("unstored", CALL, "nothing transcribed: no audio is stored for it"),
        ("held", CALL, "nothing transcribed: plaud:rec-a is on hold"),
        ("recut", CALL, "nothing transcribed: its stored audio was cut before its family's latest cuts"),
        ("never-store", CALL, "nothing transcribed: a record of plaud:rec-a matches a never-store value"),
        ("private", CALL, "nothing transcribed: source plaud is private"),
        ("stored", "elsewhere:rec-a", "this machine hasn't ingested elsewhere"),
        ("stored", "not a rid", "'not a rid' isn't a rid"),
        ("stored", WHISPER, "plaud:rec-a/whisper is a Whisper transcript; give its recording's rid"),
    ],
)
def test_what_can_t_be_transcribed_is_refused_before_anything_decodes(
    tmp_path: Path, make: str, rid: str, said: str
) -> None:
    vault = media_vault(tmp_path) if make == "unstored" else stored_vault(tmp_path)
    in_raw(vault)
    if make == "held":
        holds.add(vault, "plaud", ["rec-a"])
    if make == "recut":
        release(vault, (2000, 4000))
    if make == "never-store":
        never_stored(vault)
    if make == "private":
        private(vault)
    with pytest.raises(DocketError) as caught:
        job.transcribe(vault, rid, speech=SPEECH, worker=worker(tmp_path), say=lambda _: None)
    assert str(caught.value).startswith(said)
    assert not (tmp_path / "spec.json").exists() and plain_left(vault) == []


def test_without_speech_installed_it_says_how_to_add_it(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    with pytest.raises(DocketError, match="speech isn't installed on this machine; docket models install --speech"):
        job.transcribe(vault, CALL)


def test_a_people_value_listed_mid_job_stages_nothing_and_drops_one_staged_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = stored_vault(tmp_path)
    assert transcribe(vault, tmp_path).staged
    during(monkeypatch, lambda: change_config(vault, lambda c: c.never_store.people.append("Ben Sample")))
    with pytest.raises(DocketError, match="nothing transcribed: a record of plaud:rec-a matches a never-store value"):
        transcribe(vault, tmp_path, NEWER, segments=[[500, 4000, "Something else."]])
    # The one staged before carries Ben in its `to`, so the sweep that ran with the check dropped it.
    assert staged(vault) == [] and plain_left(vault) == []


def test_the_audio_leaving_ok_mid_job_stages_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = stored_vault(tmp_path)
    during(monkeypatch, lambda: release(vault, (2000, 4000)))
    with pytest.raises(DocketError, match="nothing transcribed: its stored audio was cut before"):
        transcribe(vault, tmp_path)
    assert staged(vault) == []


def test_audio_that_stops_reading_before_it_decodes_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = stored_vault(tmp_path)
    monkeypatch.setattr(reader, "read", lambda *a, **k: reader.Media("no-key"))
    with pytest.raises(DocketError, match="nothing transcribed: this machine has no key to open its stored audio"):
        transcribe(vault, tmp_path)
    assert not (tmp_path / "spec.json").exists() and plain_left(vault) == []


def test_a_stored_file_replaced_mid_job_stages_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = stored_vault(tmp_path)

    def recut() -> None:
        release(vault, (2000, 4000))
        put(vault)
        Gate(vault).promote()

    during(monkeypatch, recut)
    done = transcribe(vault, tmp_path)
    assert not done.staged and done.line == f"{CALL}'s stored audio changed while it was transcribed; nothing staged"
    assert reader.state_of(vault, CALL) == "ok" and staged(vault) == []


def test_a_version_from_the_same_file_arriving_mid_job_stages_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = stored_vault(tmp_path)
    key = crypto.key_id(layout.stored_files(vault.root, CALL)[0])
    [recording] = [r for r in call() if r.rid == CALL]
    other = RawRecord(
        rid=WHISPER,
        source="plaud",
        kind="transcript",
        ts=recording.ts,
        segments=[Segment(start_ms=0, end_ms=900, text="Made on another machine.")],
        text="Made on another machine.",
        fetched=AT,
        via=f"{TRANSCRIBED_VIA} {key}",
        fidelity="derived",
        engine=MODEL.engine,
    )
    during(monkeypatch, lambda: Gate(vault).stage([other]))
    done = transcribe(vault, tmp_path)
    assert not done.staged and done.line.endswith("already; nothing staged")
    assert [r.text for r in staged(vault)] == ["Made on another machine."]


def test_what_the_gate_does_with_it_is_said(tmp_path: Path) -> None:
    mixed = media_vault(tmp_path / "mixed", sensitivity="mixed")
    for rid in (CALL, POLISHED):
        Gate(mixed).classify(rid, "work")
    in_raw(mixed)
    put(mixed)
    in_raw(mixed)
    done = transcribe(mixed, tmp_path)
    assert done.staged and done.line.endswith("it waits for you: docket staging list")
    vault = stored_vault(tmp_path / "work")
    done = transcribe(vault, tmp_path, segments=[])
    assert not done.staged and done.line == f"Whisper heard no speech in {CALL}'s stored audio; nothing staged"
    holds.add(vault, "plaud", ["rec-a/whisper"])
    done = transcribe(vault, tmp_path)
    assert not done.staged and done.line == f"{WHISPER}: a hold covers it, so nothing was staged"
    change_config(vault, lambda c: c.never_store.keywords.append("Zephyrine"))
    done = transcribe(vault, tmp_path, segments=[[500, 4000, "Ask Zephyrine."]])
    assert not done.staged and done.line == f"{WHISPER}: the gate dropped it (never-store); nothing staged"


def test_a_never_store_word_whisper_hears_stops_the_stored_audio_playing(tmp_path: Path) -> None:
    vault = stored_vault(tmp_path)
    change_config(vault, lambda c: c.never_store.keywords.append("Zephyrine"))
    done = transcribe(vault, tmp_path, segments=[[500, 4000, "Ask Zephyrine."]])
    assert not done.staged and done.line.endswith("the gate dropped it (never-store); nothing staged")
    # The recorder's transcript missed the word, so only the drop's withheld mark says it: the audio stops too.
    assert reader.state_of(vault, CALL) == "withheld"
    assert reader.read(vault, CALL).data is None
    with pytest.raises(DocketError, match="dropped for a never-store value or as private"):
        transcribe(vault, tmp_path, NEWER)


def test_a_source_only_turned_off_still_plays_and_a_private_one_doesn_t(tmp_path: Path) -> None:
    vault = stored_vault(tmp_path)
    change_config(vault, lambda c: setattr(c.sources["plaud"], "enabled", False))
    assert reader.state_of(Vault.open(vault.root), CALL) == "ok"
    change_config(vault, lambda c: setattr(c.sources["plaud"], "sensitivity", "private"))
    assert reader.state_of(Vault.open(vault.root), CALL) == "withheld"


def test_one_transcription_at_a_time_on_a_machine() -> None:
    state = userconfig.state_dir()
    with job.machine_lock(wait=False):
        assert (state / job.PID).read_text(encoding="utf-8") == str(os.getpid())
        busy = rf"running on this machine \(pid {os.getpid()}\); let it end"
        with pytest.raises(job.SpeechBusyError, match=busy), job.machine_lock(wait=False):
            pass
    assert not (state / job.PID).exists()
    held = FileLock(str(state / job.LOCK))
    held.acquire()
    try:
        with pytest.raises(job.SpeechBusyError, match=r"\(pid unknown\)"), job.machine_lock(wait=False):
            pass
    finally:
        held.release()
    with job.machine_lock(wait=True):
        pass


def test_a_speaker_named_in_a_later_version_drops_the_waiting_whisper_transcript(tmp_path: Path) -> None:
    vault = stored_vault(tmp_path)
    assert transcribe(vault, tmp_path).staged
    change_config(vault, lambda c: c.never_store.people.append("Cleo Test"))
    renamed = [{**s, "speaker": "Cleo Test" if s["speaker"] == "Ben Sample" else s["speaker"]} for s in SAID]
    [polished] = [r for r in call() if r.rid == POLISHED]
    result = Gate(vault).stage([RawRecord.model_validate({**polished.model_dump(), "segments": renamed})])
    assert result.dropped["never-store"] == 1
    assert staged(vault), "its own people don't name Cleo"
    Gate(vault).list()
    assert staged(vault) == [], "the family is withheld now, so the sweep drops it"


def test_a_source_turned_off_keeps_its_waiting_whisper_transcript(tmp_path: Path) -> None:
    vault = stored_vault(tmp_path)
    assert transcribe(vault, tmp_path).staged
    change_config(vault, lambda c: setattr(c.sources["plaud"], "enabled", False))
    Gate(vault).list()
    assert staged(vault)
