"""A read, a verify and a put's anchor checks on a big source: by their places in the index they don't grow with the
source's raw (#76, #111, DR-0446, DR-0522).

The vault holds about 50 MB of invented Plaud raw in one source, 5 MB under coverage, where nothing is timed, and a
summary of its middle recording's transcript. Each call is timed by place, best of five, and once with the source
loaded whole, the route a call takes when the index isn't current for it, and both give the same result. The put is
one refused on its last line: it runs every check and writes nothing into the shared vault. Wall times are asserted
only when no tracer runs.

Only the timed tests carry `perf`. The vault is a shared template the untimed test asks for too, so in a lane
without coverage the parallel run builds it and the timed run, alone, only times (DR-0492).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import BIG_NOTE, BIG_OLD, best_of, big_raw, note, shared, traced

from docket import index, recall
from docket.index.lookup import ReadIndex, open_read
from docket.vault.docstore import DocStore
from docket.vault.processed import DocRefusedError
from docket.vault.spans import RawLookup
from docket.vault.vault import Vault

MEGABYTES = 50
TRACED_MEGABYTES = 5
# What a read by place needs alone, about ten times what it takes on a desktop; loading 50 MB takes about 800 ms.
SECONDS = 0.1
TIMES = 5
BIG_SUMMARY = "recording-note:2026-05-10-big-call"


def summary(recording: str) -> str:
    """Two lines on the recording's transcript, the doc's one record."""
    said = f"{recording}/transaction"
    return note(BIG_SUMMARY, covers=[said], lines=["- Price agreed. @00:10", "- Budget next. @02:00-02:30"])


def refused(recording: str) -> str:
    """A line on the transcript, then a time on the recording, refused naming the transcript under it (DR-0391)."""
    said = f"{recording}/transaction"
    lines = [f"- Price agreed. [r:{said}@00:10]", f"- On the recording. [r:{recording}@00:20]"]
    return note("recording-note:2026-05-10-refused", covers=[recording, said], lines=lines)


def put_problems(vault: Vault, text: str) -> list[str]:
    try:
        recall.doc_put(vault, text)
    except DocRefusedError as e:
        return [str(p) for p in e.problems]
    raise AssertionError("the put was stored, into a shared vault")


def indexed_big_raw(root: Path, megabytes: float) -> Path:
    started = time.monotonic()
    vault, middle = big_raw(root, megabytes)
    DocStore(vault).put(summary(middle))
    written = time.monotonic() - started
    index.update(vault)
    print(f"\nwrote the raw in {written:.1f} s, indexed it in {time.monotonic() - started - written:.1f} s")
    return vault.root


@pytest.fixture(scope="module")
def big(tmp_path_factory: pytest.TempPathFactory) -> tuple[Vault, str]:
    """The indexed vault, read where it was built: a read writes nothing, and the template check says if one did."""
    started = time.monotonic()
    megabytes = TRACED_MEGABYTES if traced() else MEGABYTES
    root = shared(lambda folder: indexed_big_raw(folder, megabytes), f"big-raw-{megabytes}", tmp_path_factory)
    vault = Vault.open(root)
    legacy = DocStore(vault).get(BIG_NOTE)
    assert legacy is not None
    print(f"\n{megabytes} MB of raw, indexed, ready in {time.monotonic() - started:.1f} s")
    return vault, legacy.meta.covers[0]


def both_routes(name: str, call: Callable[[bool], object], monkeypatch: pytest.MonkeyPatch) -> None:
    """A call timed by place, best of five, then once with the source loaded whole: the same result, and by place
    under the limit."""
    by_place = best_of(TIMES, lambda: call(True))
    with monkeypatch.context() as m:
        m.setattr(ReadIndex, "raw_current", lambda self, source: False)
        # Once: it is printed, never asserted, and each costs a whole load of the source.
        loading = best_of(1, lambda: call(False))
    assert by_place.result == loading.result, name
    print(
        f"{name:22} by place {by_place.seconds * 1000:6.1f} ms ({by_place.runs('ms')}), loaded "
        f"{loading.seconds * 1000:7.1f} ms"
    )
    if not traced():
        assert by_place.seconds < SECONDS, name


def rid_check(vault: Vault, rid: str, place: bool) -> bool:
    found = open_read(vault) if place else None
    try:
        return RawLookup(vault.root, lambda: found).has(rid)
    finally:
        if found is not None:
            found.close()


@pytest.mark.perf
def test_a_read_by_place_doesn_t_grow_with_its_source(big: tuple[Vault, str], monkeypatch: pytest.MonkeyPatch) -> None:
    vault, recording = big
    megabytes = sum(p.stat().st_size for p in (vault.root / "raw" / "plaud").rglob("*.jsonl")) / 1e6
    reads: dict[str, Callable[[bool], object]] = {
        "read r:<transcript>": lambda _: recall.read(vault, f"r:{recording}/transaction").model_dump(),
        "read r:<recording>": lambda _: recall.read(vault, f"r:{recording}").model_dump(),
        "read plaud:<old path>": lambda _: recall.read(vault, f"plaud:{BIG_OLD}").model_dump(),
        "the rid check": lambda place: rid_check(vault, f"plaud:{BIG_OLD}", place),
    }
    assert recall.read(vault, f"plaud:{BIG_OLD}").resolved == BIG_NOTE
    print(f"\n{megabytes:.1f} MB of raw in one source")
    for name, read in reads.items():
        both_routes(name, read, monkeypatch)


@pytest.mark.perf
def test_verify_and_a_put_s_checks_by_place_don_t_grow_with_their_source(
    big: tuple[Vault, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, recording = big
    checked = recall.verify(vault, BIG_SUMMARY)
    assert [s.problem for line in checked.lines for s in line.spans] == [None, None]
    problems = put_problems(vault, refused(recording))
    assert len(problems) == 1 and f"the time is on its transcript {recording}/transaction" in problems[0]
    calls: dict[str, Callable[[bool], object]] = {
        "verify <summary>": lambda _: recall.verify(vault, BIG_SUMMARY).model_dump(),
        "doc_put, refused": lambda _: put_problems(vault, refused(recording)),
    }
    for name, call in calls.items():
        both_routes(name, call, monkeypatch)


def test_the_fixture_is_the_size_asked_for(big: tuple[Vault, str]) -> None:
    vault, _ = big
    runs = list(Path(vault.root, "raw", "plaud").rglob("*.jsonl"))
    size = sum(p.stat().st_size for p in runs) / 1e6
    want = TRACED_MEGABYTES if traced() else MEGABYTES
    assert want <= size < want * 1.1
