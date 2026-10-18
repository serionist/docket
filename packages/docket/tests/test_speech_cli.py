"""`docket transcribe`, `docket models install --speech`, `docket models remove [--speech]` and the speech line of
`docket models status` at the command line, with the job and the install standing in."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from media_kit import CALL, media_vault

from docket import userconfig
from docket.cli import main
from docket.errors import DocketError
from docket.models import install
from docket.speech import job, setup
from docket.vault.vault import Vault


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_install_with_speech_installs_whisper_alone(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[str, str]] = []

    def fake_install(name: str, wanted: str, *, say: Any) -> None:
        asked.append((name, wanted))
        say("ready: whisper tiny on cpu")

    monkeypatch.setattr(setup, "install", fake_install)
    monkeypatch.setattr(install, "install", lambda *a, **k: pytest.fail("search's models aren't installed"))
    code, out = run(capsys, "models", "install", "--speech", "--speech-model", "tiny", "--device", "cpu")
    assert code == 0 and asked == [("tiny", "cpu")] and out == "ready: whisper tiny on cpu\n"
    code, out = run(capsys, "models", "install", "--speech")
    assert asked[-1] == ("large-v3", "auto")


def test_remove_takes_speech_with_search_s_models_or_alone(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    removed: list[str] = []
    monkeypatch.setattr(install, "remove", lambda say: removed.append("search"))
    monkeypatch.setattr(setup, "remove", lambda say: removed.append("speech"))
    assert run(capsys, "models", "remove", "--speech")[0] == 0 and removed == ["speech"]
    assert run(capsys, "models", "remove")[0] == 0 and removed == ["speech", "speech", "search"]


def test_status_names_speech_once_it_was_installed(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "models", "status")
    assert code == 0 and "speech" not in out
    userconfig.set_section("speech", {"model": "large-v3", "device": "cpu"})
    code, out = run(capsys, "models", "status")
    assert "speech     off: speech isn't installed for cpu on this machine" in out


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return media_vault(tmp_path)


def test_transcribe_runs_the_job_and_says_what_it_staged(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, vault: Vault
) -> None:
    calls: list[tuple[str, bool]] = []

    def fake(found: Vault, rid: str, *, wait: bool, say: Any) -> job.Outcome:
        calls.append((rid, wait))
        say("transcribing 0:20 of audio with whisper tiny on cpu")
        return job.Outcome(f"{rid}/whisper", rid == CALL, "staged it" if rid == CALL else "nothing staged")

    monkeypatch.setattr(job, "transcribe", fake)
    code, out = run(capsys, "transcribe", CALL, "--vault", str(vault.root))
    assert code == 0 and out == "transcribing 0:20 of audio with whisper tiny on cpu\nstaged it\n"
    code, out = run(capsys, "transcribe", "plaud:rec-b", "--wait", "--vault", str(vault.root))
    assert code == 1 and out.endswith("nothing staged\n") and calls[-1] == ("plaud:rec-b", True)


def test_transcribe_says_why_it_can_t(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, vault: Vault
) -> None:
    def busy(*_: Any, **__: Any) -> job.Outcome:
        raise DocketError("a transcription is running on this machine (pid 42); let it end")

    monkeypatch.setattr(job, "transcribe", busy)
    code, out = run(capsys, "transcribe", CALL, "--vault", str(vault.root))
    assert code != 0 and "a transcription is running on this machine (pid 42)" in out
