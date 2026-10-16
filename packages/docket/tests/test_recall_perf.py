"""A read on a big source: by its place in the index it doesn't grow with the source's raw (#76, DR-0446).

The vault holds about 50 MB of invented Plaud raw in one source, 5 MB under coverage, where nothing is timed. Each
read is timed by place and with the source loaded whole, the route a read takes when the index isn't current for it,
and both give the same result. Wall times are asserted only when no tracer runs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import BIG_NOTE, BIG_OLD, big_raw, traced

from docket import index, recall
from docket.index.lookup import ReadIndex, open_read
from docket.vault.spans import RawLookup
from docket.vault.vault import Vault

pytestmark = pytest.mark.perf

MEGABYTES = 50
TRACED_MEGABYTES = 5
# What a read by place needs alone, about ten times what it takes on a desktop; loading 50 MB takes about 800 ms.
SECONDS = 0.1
TIMES = 5


@pytest.fixture(scope="module")
def big(tmp_path_factory: pytest.TempPathFactory) -> tuple[Vault, str]:
    started = time.monotonic()
    vault, recording = big_raw(tmp_path_factory.mktemp("big"), TRACED_MEGABYTES if traced() else MEGABYTES)
    written = time.monotonic() - started
    index.update(vault)
    print(f"\nwrote the raw in {written:.1f} s, indexed it in {time.monotonic() - started - written:.1f} s")
    return vault, recording


def best(read: Callable[[], object]) -> tuple[float, object]:
    out: list[float] = []
    result: object = None
    for _ in range(TIMES):
        started = time.perf_counter()
        result = read()
        out.append(time.perf_counter() - started)
    return min(out), result


def rid_check(vault: Vault, rid: str, place: bool) -> bool:
    found = open_read(vault) if place else None
    try:
        return RawLookup(vault.root, lambda: found).has(rid)
    finally:
        if found is not None:
            found.close()


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
        by_place, placed = best(lambda read=read: read(True))
        with monkeypatch.context() as m:
            m.setattr(ReadIndex, "raw_current", lambda self, source: False)
            loading, whole = best(lambda read=read: read(False))
        assert placed == whole, name
        print(f"{name:22} by place {by_place * 1000:6.1f} ms, loaded {loading * 1000:7.1f} ms")
        if not traced():
            assert by_place < SECONDS, name


def test_the_fixture_is_the_size_asked_for(big: tuple[Vault, str]) -> None:
    vault, _ = big
    runs = list(Path(vault.root, "raw", "plaud").rglob("*.jsonl"))
    size = sum(p.stat().st_size for p in runs) / 1e6
    want = TRACED_MEGABYTES if traced() else MEGABYTES
    assert want <= size < want * 1.1
