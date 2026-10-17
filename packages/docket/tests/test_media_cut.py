"""Cutting audio before it is encrypted (DR-0559, DR-0560): the child decodes, silences and encodes; its parent keeps
it within its limits and its plain files within one folder per process, which a sweep clears once the process is gone.
All audio here is a tone the test makes."""

from __future__ import annotations

import contextlib
import io
import itertools
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from media_kit import dbfs, decoded, media_vault, tone

from docket import childproc
from docket.errors import DocketError
from docket.media import child, cutter
from docket.media import staged as media_staged
from docket.media.ranges import Needed
from docket.vault.home import Home
from docket.vault.vault import Vault

FAKE = Path(__file__).with_name("media_fake_child.py")


def fake(mode: str) -> list[str]:
    return [sys.executable, str(FAKE), "--mode", mode]


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return media_vault(tmp_path)


@pytest.fixture
def home(vault: Vault) -> Home:
    return vault.home


def video() -> bytes:
    """A WebM with one frame of video and no audio at all."""
    import av

    out = io.BytesIO()
    with av.open(out, "w", format="webm") as written:
        stream = written.add_stream("libvpx", rate=1)
        stream.width, stream.height, stream.pix_fmt = 16, 16, "yuv420p"
        for packet in [*stream.encode(av.VideoFrame(16, 16, "yuv420p")), *stream.encode(None)]:
            written.mux(packet)
    return out.getvalue()


def request(source: Path, output: Path, fmt: str, cuts: Sequence[tuple[int, int]], **over: Any) -> dict[str, Any]:
    return {
        "input": str(source),
        "format": fmt,
        "output": str(output),
        "cuts": [list(c) for c in cuts],
        "max_ms": cutter.MAX_MS,
        "max_bytes": 64 * 1024 * 1024,
        **over,
    }


@pytest.mark.parametrize("fmt", ["mp3", "m4a", "wav", "flac", "opus"])
def test_a_tone_comes_back_silent_in_its_cut_and_unchanged_elsewhere(tmp_path: Path, fmt: str) -> None:
    source = tmp_path / f"in.{fmt}"
    source.write_bytes(tone(10.0, fmt=fmt))
    output = tmp_path / "out.webm"
    answer = child.cut(request(source, output, fmt, cutter.padded([(3000, 5000)])))
    done = answer["done"]
    # AAC adds a frame of padding at the end; nothing moves at the start.
    assert done["duration_ms"] in range(9990, 10_100)
    assert done["onset_ms"] == 0
    # Ogg Opus always decodes at 48 kHz, whatever it was made from.
    assert done["rate"] in (16_000, 48_000) and done["channels"] == 1
    assert output.read_bytes()[:4] == b"\x1aE\xdf\xa3"
    before, after = decoded(source.read_bytes()), decoded(output.read_bytes())
    assert dbfs(after, 2780, 5220) < -60
    for start, end in ((300, 2700), (5300, 9700)):
        assert abs(dbfs(after, start, end) - dbfs(before, start, end)) < 1.5
    # Still the tone, not noise: about 440 crossings up through zero a second.
    stretch = after[6000 * 48 : 7000 * 48]
    rises = sum(1 for a, b in itertools.pairwise(stretch) if a < 0 <= b)
    assert 430 <= rises <= 450


def test_the_onset_is_the_first_sound_and_nothing_is_cut_without_ranges(tmp_path: Path) -> None:
    source = tmp_path / "in.mp3"
    source.write_bytes(tone(6.0, start=3.0))
    output = tmp_path / "out.webm"
    done = child.cut(request(source, output, "mp3", []))["done"]
    assert done["onset_ms"] in range(2950, 3051)
    silence = tmp_path / "silence.wav"
    silence.write_bytes(tone(2.0, fmt="wav", start=5.0))
    assert child.cut(request(silence, tmp_path / "s.webm", "wav", []))["done"]["onset_ms"] is None


@pytest.mark.parametrize(
    ("fmt", "data", "over", "reason"),
    [
        ("bin", b"x" * 100, {}, "type"),
        ("flac", "empty", {}, "no-audio"),
        ("webm", "video", {}, "no-audio"),
        ("m4a", b"\x00\x00\x00\x1cftypM4A " + bytes(range(256)) * 8, {}, "damaged"),
        ("mp3", b"ID3\x04" + bytes(4000), {}, "damaged"),
        ("mp3", "tone", {"max_ms": 1000}, "long"),
        ("mp3", "tone", {"max_bytes": 10}, "size"),
    ],
    ids=["type", "no-audio", "video", "damaged", "zeros", "long", "size"],
)
def test_what_the_child_won_t_cut_it_names(
    tmp_path: Path, fmt: str, data: bytes | str, over: dict[str, Any], reason: str
) -> None:
    source = tmp_path / "in"
    made = {"tone": lambda: tone(3.0), "empty": lambda: tone(0.0, fmt="flac"), "video": video}
    source.write_bytes(made[data]() if isinstance(data, str) else data)
    answer = child.cut(request(source, tmp_path / "out.webm", fmt, [], **over))
    assert answer["not_read"]["reason"] == reason


def test_a_playlist_named_as_audio_reaches_nothing(tmp_path: Path) -> None:
    """The demuxer comes from the sniffed type and only the file protocol opens: an HLS playlist is just bad audio."""
    playlist = tmp_path / "in.mp3"
    playlist.write_text("#EXTM3U\n#EXTINF:10,\nhttps://media.example/a.ts\nfile:///etc/passwd\n", encoding="utf-8")
    answer = child.cut(request(playlist, tmp_path / "out.webm", "mp3", []))
    assert "not_read" in answer


def test_the_child_s_own_process_says_ready_then_answers_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Never the test's own process under the child's cap: the real child sets it on itself, as the other tests show.
    monkeypatch.setattr(child, "limit_memory", lambda cap: None)
    source = tmp_path / "in.wav"
    source.write_bytes(tone(2.0, fmt="wav"))
    line = json.dumps(request(source, tmp_path / "out.webm", "wav", [(0, 500)])).encode("utf-8") + b"\n"
    out = io.BytesIO()
    assert child.main(["--memory", str(cutter.MEMORY)], io.BytesIO(line), out, io.StringIO()) == 0
    hello, answer = (json.loads(x) for x in out.getvalue().splitlines())
    assert hello["ready"] is True and hello["pid"] == os.getpid()
    assert answer["done"]["duration_ms"] == 2000
    modules: dict[str, Any] = {}
    child.keep_out(modules)
    assert modules == {"numpy": None}
    with pytest.raises(RuntimeError, match="opens no connection"):
        child.no_sockets("socket.connect", ())
    child.no_sockets("open", ())


def test_the_real_child_cuts_within_its_limits_and_leaves_only_the_cut(home: Home) -> None:
    source = cutter.plain_file(home, ".in")
    source.write_bytes(tone(4.0))
    done = cutter.cut(home, source, "mp3", [(1000, 2000)], max_bytes=64 * 1024 * 1024)
    assert done.path.parent == source.parent == cutter.folder(home)
    assert done.duration_ms == 4000 and done.onset_ms == 0
    assert done.source.startswith("from mp3float, 16000 Hz, 1 channel(s), ")
    assert dbfs(decoded(done.path.read_bytes()), 780, 2220) < -60
    cutter.done_with(source, done.path)
    assert not cutter.folder(home).exists() or not any(cutter.folder(home).iterdir())
    # What the child won't cut comes back in its words, and nothing is left.
    bad = cutter.plain_file(home, ".in")
    bad.write_bytes(b"ID3\x04" + bytes(4000))
    with pytest.raises(DocketError, match="the audio wasn't stored: it doesn't decode as mp3"):
        cutter.cut(home, bad, "mp3", [(0, 1000)], max_bytes=1024 * 1024)
    assert [p.name for p in cutter.folder(home).iterdir()] == [bad.name]


@pytest.mark.parametrize(
    ("mode", "said"),
    [
        ("hang", "took longer than 0 minutes"),
        ("exit", "stopped (it crashed or ran out of memory)"),
        ("junk", "stopped (it crashed or ran out of memory)"),
        ("silent", "didn't start"),
        ("other", "didn't start"),
        ("baddone", "out of its protocol"),
    ],
)
def test_a_child_that_misbehaves_is_stopped_and_leaves_nothing(home: Home, mode: str, said: str) -> None:
    source = cutter.plain_file(home, ".in")
    source.write_bytes(b"ID3")
    with pytest.raises(DocketError, match=said.replace("(", r"\(").replace(")", r"\)")):
        cutter.cut(home, source, "mp3", [], max_bytes=1024, command=fake(mode), time_limit=2.0, hello_seconds=3.0)
    assert [p.name for p in cutter.folder(home).iterdir()] == [source.name]


def test_a_child_without_its_cap_is_stopped_where_the_system_caps(home: Home) -> None:
    source = cutter.plain_file(home, ".in")
    source.write_bytes(b"ID3")
    if childproc.CAPPED:
        with pytest.raises(DocketError, match="without its memory cap"):
            cutter.cut(home, source, "mp3", [], max_bytes=1024, command=fake("uncapped"))
    else:  # pragma: no cover - macOS caps nothing, so the fake's answer stands
        assert cutter.cut(home, source, "mp3", [], max_bytes=1024, command=fake("uncapped")).duration_ms == 1000


def test_a_child_that_won_t_take_the_request_is_named(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    source = cutter.plain_file(home, ".in")
    source.write_bytes(b"ID3")
    real = subprocess.Popen

    def popen(*args: Any, **kwargs: Any) -> Any:
        proc = real(*args, **kwargs)
        assert proc.stdin is not None
        proc.stdin.close()
        return proc

    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(DocketError, match="before it was asked anything"):
        cutter.cut(home, source, "mp3", [], max_bytes=1024, command=fake("normal"))


def test_a_job_the_system_refuses_stops_the_child(home: Home, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(pid: int, memory: int, who: str) -> int | None:
        raise DocketError(f"Windows refused {who} a job object (5)")

    monkeypatch.setattr(childproc, "job", refuse)
    with pytest.raises(DocketError, match="refused the audio cutter a job object"):
        cutter.cut(home, cutter.plain_file(home, ".in"), "mp3", [], max_bytes=1024, command=fake("normal"))


def test_ranges_are_padded_and_merged() -> None:
    assert cutter.padded([(3000, 5000)]) == [(2750, 5250)]
    assert cutter.padded([(100, 200), (600, 700), (5000, 6000)]) == [(0, 950), (4750, 6250)]
    assert cutter.padded([]) == []


def _done(duration: int, onset: int | None) -> cutter.Cut:
    return cutter.Cut(Path("x.webm"), duration, onset, "mp3", 16_000, 1, None)


def test_the_timeline_checks_fail_closed_and_name_the_offset() -> None:
    needed = Needed(((0, 1000),), first_ms=0, last_ms=19_000)
    assert cutter.timeline_problem("plaud:a", needed, _done(20_000, 200)) is None
    assert cutter.timeline_problem("plaud:a", needed, _done(18_500, 0)) is None
    late = cutter.timeline_problem("plaud:a", needed, _done(20_000, 3000))
    assert late is not None and "first sounds at 3.0 s and its transcript starts at 0.0 s, 3.0 s apart" in late
    short = cutter.timeline_problem("plaud:a", needed, _done(15_000, 0))
    assert short is not None and "transcript ends at 19.0 s, after its audio does (15.0 s)" in short
    silent = cutter.timeline_problem("plaud:a", needed, _done(20_000, None))
    assert silent is not None and "silent throughout" in silent
    assert cutter.timeline_problem("plaud:a", Needed(((0, 1000),)), _done(500, None)) is None
    assert cutter.onset_note(_done(1, 1500)) == ", first sound at 1.5 s"
    assert cutter.onset_note(_done(1, None)) == ""
    assert _done(1, 0).source == "from mp3, 16000 Hz, 1 channel(s), bit rate unknown"


def test_a_process_killed_mid_cut_leaves_a_folder_the_next_sweep_clears(vault: Vault, tmp_path: Path) -> None:
    """A separate process cuts with a child that hangs, and the test ends it: its folder outlives it, plain audio and
    all, until a sweep sees the pid is gone. A live process's folder, and this one's own, are kept."""
    script = tmp_path / "parent.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from docket.media import cutter\n"
        "from docket.vault.vault import Vault\n"
        "home = Vault.open(Path(sys.argv[1])).home\n"
        "source = cutter.plain_file(home, '.in')\n"
        "source.write_bytes(b'ID3 uncut')\n"
        "print(source.parent, flush=True)\n"
        f"fake = [sys.executable, {str(FAKE)!r}, '--mode', 'hang']\n"
        "cutter.cut(home, source, 'mp3', [], max_bytes=1024, command=fake)\n",
        encoding="utf-8",
    )
    home = vault.home
    parent = subprocess.Popen([sys.executable, str(script), str(vault.root)], stdout=subprocess.PIPE, text=True)
    assert parent.stdout is not None
    folder = Path(parent.stdout.readline().strip())
    deadline = time.monotonic() + 30
    while not any(p.suffix == ".webm" and p.stat().st_size for p in folder.iterdir()):
        assert time.monotonic() < deadline
        time.sleep(0.1)
    [half] = [p for p in folder.iterdir() if p.suffix == ".webm"]
    stuck = int(half.read_text(encoding="utf-8").split()[1])
    mine = cutter.folder(home)
    (mine / "mine.in").write_bytes(b"x")
    assert media_staged.clear_temps(home) == 0
    assert folder.is_dir(), "kept while its process runs"
    # The folder is named by the interpreter's own pid; on Windows a venv's launcher started it, and is what Popen
    # knows. Each is ended, and the child too where no job takes it with its parent.
    inner = int(folder.name.removeprefix(cutter.FOLDER))
    for pid in (inner, stuck):
        with contextlib.suppress(OSError):
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    parent.kill()
    parent.wait()
    parent.stdout.close()
    deadline = time.monotonic() + 15
    while folder.exists():
        assert time.monotonic() < deadline, "the dead process's folder outlived the sweeps"
        media_staged.clear_temps(home)
        time.sleep(0.2)
    assert (mine / "mine.in").exists()
    (home.tmp_dir / f"{cutter.FOLDER}notapid").mkdir()
    (home.tmp_dir / f"{cutter.FOLDER}12").write_bytes(b"a file, not a folder")
    assert cutter.sweep(home) == 0


def test_whether_a_process_runs(home: Home) -> None:
    assert childproc.alive(os.getpid())
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    assert not childproc.alive(ended.pid)
    if home.tmp_dir.is_dir():
        for path in home.tmp_dir.iterdir():
            path.unlink() if path.is_file() else path.rmdir()
        home.tmp_dir.rmdir()
    assert cutter.sweep(home) == 0
