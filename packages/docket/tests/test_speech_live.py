"""Whisper tiny for real (DR-0569), on an invented clip the system's own speech synthesis makes at test time: SAPI on
Windows, `say` on macOS, espeak-ng on Linux. CI's Models jobs install `docket models install --speech --speech-model
tiny --device cpu` and run this with DOCKET_TEST_SPEECH naming that models folder; anywhere else it is skipped. The
kept sentence's words come back, and the stretch the transcript was cut at has none. No real recording is used."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import make_config, make_record

from docket import userconfig
from docket.media import put as media
from docket.media import reader
from docket.media.keystore import KeyStore
from docket.speech import job, setup
from docket.vault import cursors
from docket.vault.config import SourceConfig
from docket.vault.cuts import CUT_MARKER, Cut
from docket.vault.gate import Gate
from docket.vault.staging import Staging
from docket.vault.vault import Vault

MODELS = os.environ.get("DOCKET_TEST_SPEECH")
pytestmark = pytest.mark.skipif(not MODELS, reason="needs Whisper tiny installed: DOCKET_TEST_SPEECH=<models folder>")

CALL = "plaud:rec-live"
POLISHED = f"{CALL}/transaction_polish"
KEPT = "Good morning everyone, thank you for coming to the meeting."
CUT = "The secret number is seven hundred and forty two."
GAP_MS = 2000


def synthesize(text: str, path: Path) -> None:
    """One sentence spoken into a WAV file by the system's own voice."""
    if sys.platform == "win32":
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$voice.SetOutputToWaveFile($env:DOCKET_CLIP); $voice.Speak($env:DOCKET_SAY); $voice.Dispose()"
        )
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
        env = {**os.environ, "DOCKET_CLIP": str(path), "DOCKET_SAY": text}
        subprocess.run(argv, env=env, check=True, timeout=120)
    elif sys.platform == "darwin":
        argv = ["say", "-o", str(path), "--file-format=WAVE", "--data-format=LEI16@16000", text]
        subprocess.run(argv, check=True, timeout=120)
    else:
        subprocess.run(["espeak-ng", "-w", str(path), text], check=True, timeout=120)


def clip(folder: Path) -> tuple[bytes, int, int, int]:
    """The two sentences with a gap after each, as one WAV: its bytes, where the first ends, and where the second
    starts and ends, in ms."""
    kept, cut = folder / "kept.wav", folder / "cut.wav"
    synthesize(KEPT, kept)
    synthesize(CUT, cut)
    with wave.open(str(kept), "rb") as one, wave.open(str(cut), "rb") as two:
        shape = (one.getnchannels(), one.getsampwidth(), one.getframerate())
        assert (two.getnchannels(), two.getsampwidth(), two.getframerate()) == shape
        first, second = one.readframes(one.getnframes()), two.readframes(two.getnframes())
    channels, width, rate = shape
    frame = channels * width
    # Silence is the middle of the range: 0x80 for unsigned 8-bit samples, zero for signed wider ones.
    gap = (b"\x80" if width == 1 else b"\x00") * (GAP_MS * rate // 1000) * frame
    out = io.BytesIO()
    with wave.open(out, "wb") as written:
        written.setnchannels(channels)
        written.setsampwidth(width)
        written.setframerate(rate)
        written.writeframes(first + gap + second + gap)

    def ms(data: bytes) -> int:
        return len(data) // frame * 1000 // rate

    return out.getvalue(), ms(first), ms(first) + GAP_MS, ms(first) + GAP_MS + ms(second)


def live_vault(tmp_path: Path, first_end: int, start: int, end: int) -> Vault:
    """A plaud recording whose transcript says the two sentences, the second cut by hand, and promoted."""
    config = make_config()
    config.sources = {"plaud": SourceConfig()}
    vault = Vault.init(tmp_path / "vault", config, "test-box")
    cursors.save(vault, "plaud", cursors.Cursor())
    KeyStore(vault.home).init("file")
    under = {"source": "plaud", "container": CALL, "thread": CALL, "ts": datetime(2026, 10, 16, 9, 0, tzinfo=UTC)}
    said = [
        {"start_ms": 0, "end_ms": first_end, "speaker": "Ada Example", "text": KEPT},
        {"start_ms": start, "end_ms": end, "speaker": "Ben Sample", "text": CUT},
    ]
    text = "\n".join(f"{s['speaker']}: {s['text']}" for s in said)
    Gate(vault).stage(
        [
            make_record(rid=CALL, kind="recording", title="Morning meeting", text="", **under),
            make_record(rid=POLISHED, kind="transcript", segments=said, text=text, **under),
        ]
    )
    Gate(vault).classify(POLISHED, "work", [Cut(start=start, end=end, unit="ms")])
    Gate(vault).promote()
    return vault


def test_whisper_tiny_hears_the_kept_sentence_and_nothing_where_it_was_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert MODELS is not None
    monkeypatch.setenv("DOCKET_MODELS_DIR", MODELS)
    userconfig.set_section("speech", {"model": "tiny", "device": "cpu"})
    speech = setup.chosen()
    assert isinstance(speech, setup.Speech), speech
    data, first_end, start, end = clip(tmp_path)
    vault = live_vault(tmp_path, first_end, start, end)
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(data)
    media.put(vault, CALL, path=str(drop / "take"))
    Gate(vault).promote()
    assert reader.state_of(vault, CALL) == "ok"
    done = job.transcribe(vault, CALL, speech=speech, say=print)
    assert done.staged, done.line
    [record] = [
        e.staged.record for e in Staging(vault.root, vault.home).scan().entries if e.staged.record.rid == done.rid
    ]
    heard = record.text.lower()
    print(record.text)
    assert any(word in heard for word in ("morning", "thank", "meeting")), heard
    assert not any(word in heard for word in ("secret", "seven", "forty")), heard
    marks = [s for s in record.segments or [] if s.text == CUT_MARKER]
    assert any(s.start_ms <= start and end <= s.end_ms for s in marks)
    assert record.engine == "whisper-tiny@d90ca5f faster-whisper-1.2.1"
