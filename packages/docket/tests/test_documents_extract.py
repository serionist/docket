"""The extraction child (DR-0485): the real one end to end on built documents, its protocol in-process, and a stand-in
child for every way one can fail: a hang, a crash, lines out of protocol, a memory blow-up past the cap."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from documents_kit import docx, pdf, wp

from docket import childproc
from docket.documents import child, extract, formats
from docket.documents.extract import Done, Extractor, Failed, NotRead
from docket.documents.formats import Part
from docket.documents.limits import LIMITS, MIB, Limits
from docket.errors import DocketError

FAKE = Path(__file__).with_name("documents_fake_child.py")


def fake(mode: str = "normal") -> list[str]:
    return [sys.executable, str(FAKE), "--mode", mode]


@pytest.fixture(scope="module")
def real() -> Iterator[Extractor]:
    with Extractor() as extractor:
        yield extractor


def touch(folder: Path, name: str, data: bytes = b"") -> Path:
    path = folder / name
    path.write_bytes(data)
    return path


# -- the real child ---------------------------------------------------------------------------------------------------


def test_the_child_reads_a_file_once_and_its_hash_is_of_those_bytes(real: Extractor, tmp_path: Path) -> None:
    data = pdf([["Northwind invoice"], ["Page two"]])
    got = real.extract(touch(tmp_path, "invoice.pdf", data))
    assert isinstance(got, Done)
    assert (got.sha256, got.size) == (hashlib.sha256(data).hexdigest(), len(data))
    assert got.extracted.parts == [
        Part("page", 1, "page 1", "Northwind invoice"),
        Part("page", 2, "page 2", "Page two"),
    ]
    text = real.extract(touch(tmp_path, "notes.docx", docx(wp("Plan for Q3"), creator="Cleo Test")))
    assert isinstance(text, Done)
    assert (text.extracted.text, text.extracted.creator, text.extracted.format) == ("Plan for Q3", "Cleo Test", "docx")


def test_a_file_the_child_can_t_read_whole_is_not_read_with_its_reason(real: Extractor, tmp_path: Path) -> None:
    damaged = real.extract(touch(tmp_path, "broken.docx", b"not a zip"))
    assert damaged == NotRead("damaged", "damaged (BadZipFile)", hashlib.sha256(b"not a zip").hexdigest(), 9)
    assert isinstance(damaged, NotRead)
    assert damaged.line == "Not read: damaged (BadZipFile)."
    big = tmp_path / "big.txt"
    with big.open("wb") as f:
        f.truncate(65 * MIB)
    # Over the cap by its size alone: never opened, so no hash.
    assert real.extract(big) == NotRead("size", "65 MB, over the cap of 64 MB", None, 65 * MIB)


def test_a_file_the_system_won_t_hand_over_is_unreadable(real: Extractor, tmp_path: Path) -> None:
    assert real.extract(tmp_path / "gone.txt") == Failed("unreadable")
    (tmp_path / "folder.pdf").mkdir()
    assert real.extract(tmp_path / "folder.pdf") == Failed("unreadable")


def test_a_format_docket_doesn_t_read_is_refused_before_any_child_starts(tmp_path: Path) -> None:
    with Extractor(fake()) as extractor, pytest.raises(DocketError, match=r"\.doc isn't a document format"):
        extractor.extract(touch(tmp_path, "old.doc"))
    with Extractor(fake()) as extractor, pytest.raises(DocketError, match="without an extension"):
        extractor.extract(touch(tmp_path, "README"))


def test_a_fresh_child_every_so_many_files(tmp_path: Path) -> None:
    with Extractor(fake(), files_per_child=2) as extractor:
        outcomes = [extractor.extract(touch(tmp_path, f"f{n}.txt")) for n in range(5)]
        assert all(isinstance(o, Done) for o in outcomes)
        assert extractor.started == 3


# -- a child that fails -----------------------------------------------------------------------------------------------


def test_a_hang_is_stopped_at_the_time_limit_and_the_next_file_gets_a_new_child(tmp_path: Path) -> None:
    with Extractor(fake(), time_limit=1.0) as extractor:
        assert extractor.extract(touch(tmp_path, "hang.pdf")) == Failed("stopped")
        assert isinstance(extractor.extract(touch(tmp_path, "next.pdf")), Done)
        assert extractor.started == 2


@pytest.mark.parametrize(
    "stem", ["exit", "junk", "list", "two", "weird", "badpart", "baddone", "nonewline"], ids=lambda s: s
)
def test_a_child_that_ends_or_leaves_the_protocol_is_a_crash(tmp_path: Path, stem: str) -> None:
    with Extractor(fake()) as extractor:
        assert extractor.extract(touch(tmp_path, f"{stem}.txt")) == Failed("crashed")
        assert isinstance(extractor.extract(touch(tmp_path, "after.txt")), Done)


def test_lines_past_their_caps_are_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "LINE_CAP", 100)
    with Extractor(fake()) as extractor:
        assert extractor.extract(touch(tmp_path, "long.txt")) == Failed("crashed")
    monkeypatch.setattr(extract, "LINE_CAP", 8 * MIB)
    monkeypatch.setattr(extract, "FILE_LINES_CAP", 200)
    with Extractor(fake()) as extractor:
        assert extractor.extract(touch(tmp_path, "long.txt")) == Failed("crashed")


class _BrokenPipe(io.RawIOBase):
    """A child's input that went away: every write fails as a closed pipe does."""

    def writable(self) -> bool:
        return True

    def write(self, b: object) -> int:
        raise BrokenPipeError


def test_a_child_whose_input_is_gone_is_a_crash(tmp_path: Path) -> None:
    with Extractor(fake()) as extractor:
        assert isinstance(extractor.extract(touch(tmp_path, "first.txt")), Done)
        running = extractor._child  # pyright: ignore[reportPrivateUsage]
        assert running is not None and running.proc.stdin is not None
        # The real pipe stays open until the end, so the child lives on and is the one asked.
        original = running.proc.stdin
        running.proc.stdin = io.BufferedWriter(_BrokenPipe())
        try:
            assert extractor.extract(touch(tmp_path, "then.txt")) == Failed("crashed")
        finally:
            original.close()


def test_a_child_that_won_t_end_is_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "STOP_SECONDS", 0.2)
    with Extractor(fake("stubborn"), files_per_child=1) as extractor:
        assert isinstance(extractor.extract(touch(tmp_path, "one.txt")), Done)
        assert extractor.started == 1


@pytest.mark.parametrize("mode", ["silent", "other"])
def test_a_child_that_doesn_t_say_ready_stops_the_extraction(tmp_path: Path, mode: str) -> None:
    with (
        Extractor(fake(mode), hello_seconds=1.0) as extractor,
        pytest.raises(DocketError, match="the documents' reader didn't start"),
    ):
        extractor.extract(touch(tmp_path, "a.txt"))


def test_a_cap_the_system_refuses_stops_the_child_it_was_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[int] = []

    def refuse(pid: int, memory: int) -> int | None:
        started.append(pid)
        raise DocketError("no job object here")

    monkeypatch.setattr(extract, "_job", refuse)
    with Extractor(fake()) as extractor, pytest.raises(DocketError, match="no job object"):
        extractor.extract(touch(tmp_path, "a.txt"))
    assert started


def test_the_child_s_stderr_is_kept_and_never_in_an_outcome(tmp_path: Path) -> None:
    with Extractor(fake()) as extractor:
        got = extractor.extract(touch(tmp_path, "noisy.txt"))
        assert "secret" not in repr(got)
        extractor.close()
        assert "the text of a secret clause" in extractor.stderr()


def test_the_child_gets_only_what_python_needs_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKET_TOKEN_FOR_TESTS", "not for children")
    env = childproc.child_env()
    assert "DOCKET_TOKEN_FOR_TESTS" not in env
    assert set(env) <= set(childproc.KEEP_ENV)


# -- the memory cap ---------------------------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS enforces no memory cap; the time limit holds there")
def test_a_child_past_its_memory_cap_is_a_crash_while_the_parent_is_busy(tmp_path: Path) -> None:
    # Busy threads in the parent, as a service has: they delayed a job the parent assigned past the moment a venv's
    # launcher started the interpreter, which then ran uncapped. The child now caps itself, before its hello.
    stopping = threading.Event()

    def spin() -> None:
        while not stopping.is_set():
            pass

    busy = [threading.Thread(target=spin, daemon=True) for _ in range(3)]
    for thread in busy:
        thread.start()
    try:
        for _ in range(3):
            with Extractor(fake(), memory=512 * MIB, files_per_child=1) as extractor:
                assert extractor.extract(touch(tmp_path, "alloc.txt")) == Failed("crashed")
                assert "MemoryError" in extractor.stderr()
    finally:
        stopping.set()
        for thread in busy:
            thread.join()


def test_a_child_without_its_memory_cap_is_stopped_where_the_system_enforces_one(tmp_path: Path) -> None:
    with Extractor(fake("uncapped")) as extractor:
        if extract.CAPPED:
            with pytest.raises(DocketError, match="started without its memory cap"):
                extractor.extract(touch(tmp_path, "a.txt"))
        else:
            assert isinstance(extractor.extract(touch(tmp_path, "a.txt")), Done)


def test_on_windows_the_interpreter_itself_is_in_a_capped_job(tmp_path: Path) -> None:
    # A check pyright reads too, so the Windows-only calls below aren't type-checked on other systems.
    if sys.platform != "win32":
        pytest.skip("a job object is Windows' own")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    for command in (fake(), None):
        with Extractor(command) as extractor:
            assert isinstance(extractor.extract(touch(tmp_path, "a.txt")), Done)
            running = extractor._child  # pyright: ignore[reportPrivateUsage]
            assert running is not None
            # The pid the interpreter gave in its hello, not the launcher's; the start already checked the cap it
            # reported from its own innermost job.
            assert running.pid is not None
            handle = kernel32.OpenProcess(0x1000, False, running.pid)
            inside = wintypes.BOOL()
            assert kernel32.IsProcessInJob(handle, None, ctypes.byref(inside))
            kernel32.CloseHandle(handle)
            assert inside.value


# -- the child's own side, in-process ---------------------------------------------------------------------------------


def run_child(lines: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    # In this process the cap would cap the test run itself.
    monkeypatch.setattr(child, "limit_memory", lambda cap: None)
    stdin = io.BytesIO(b"".join(json.dumps(line).encode() + b"\n" for line in lines))
    stdout = io.BytesIO()
    assert child.main(["--memory", "1"], stdin, stdout, io.StringIO()) == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_the_child_answers_a_line_per_part_then_the_file_s_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = pdf([["one"], ["two"]])
    path = touch(tmp_path, "a.pdf", data)
    answers = run_child([{"path": str(path), "format": "pdf"}], monkeypatch)
    assert answers[0] == {"ready": True, "pid": os.getpid(), "memory": None}
    assert [a["part"]["text"] for a in answers[1:3]] == ["one", "two"]  # type: ignore[index]
    done = answers[3]["done"]
    assert isinstance(done, dict)
    assert (done["sha256"], done["size"], done["format"]) == (hashlib.sha256(data).hexdigest(), len(data), "pdf")
    assert "parts" not in done


def test_the_child_says_why_a_file_wasn_t_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = touch(tmp_path, "b.docx", b"PK broken")
    answers = run_child(
        [
            {"path": str(path), "format": "docx"},
            {"path": str(tmp_path / "missing.txt"), "format": "txt"},
            {"path": str(tmp_path), "format": "txt"},
        ],
        monkeypatch,
    )
    assert answers[1] == {
        "not_read": {
            "reason": "damaged",
            "detail": "damaged (BadZipFile)",
            "sha256": hashlib.sha256(b"PK broken").hexdigest(),
            "size": 9,
        }
    }
    assert answers[2] == {"unreadable": "FileNotFoundError"}
    assert answers[3] == {"unreadable": "not a regular file"}


def test_the_child_checks_the_size_before_it_reads(tmp_path: Path) -> None:
    path = touch(tmp_path, "c.txt", b"x" * 3000)
    answers = list(child.handle({"path": str(path), "format": "txt"}, Limits(file_bytes=2000)))
    assert answers == [
        {"not_read": {"reason": "size", "detail": "1 MB, over the cap of 1 MB", "sha256": None, "size": 3000}}
    ]


def test_a_library_s_print_goes_to_stderr_not_into_the_protocol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_extract = formats.extract

    def chatty(data: bytes, fmt: formats.Format, limits: Limits = LIMITS) -> formats.Extracted:
        print("a library talking")
        return real_extract(data, fmt, limits)

    monkeypatch.setattr(formats, "extract", chatty)
    answers = run_child([{"path": str(touch(tmp_path, "d.txt", b"hi")), "format": "txt"}], monkeypatch)
    assert [next(iter(a)) for a in answers] == ["ready", "done"]


def test_the_child_keeps_numpy_out_of_its_own_process() -> None:
    # openpyxl brings numpy when it can, and numpy's BLAS reserved more than a 512 MiB cap as it loaded.
    modules: dict[str, object] = {}
    child.keep_numpy_out(modules)
    assert modules == {"numpy": None}


def test_running_out_of_memory_ends_the_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def hungry(data: bytes, fmt: formats.Format, limits: Limits = LIMITS) -> formats.Extracted:
        raise MemoryError

    monkeypatch.setattr(formats, "extract", hungry)
    with pytest.raises(MemoryError):
        list(child.handle({"path": str(touch(tmp_path, "e.txt", b"hi")), "format": "txt"}))


def test_stop_leaves_a_child_that_already_ended_alone() -> None:
    import subprocess

    proc = subprocess.Popen([sys.executable, "-c", "pass"], env=childproc.child_env())
    proc.wait()
    childproc.stop(proc)
    assert proc.returncode == 0
