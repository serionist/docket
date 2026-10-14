from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import index_template, make_record
from starlette.testclient import TestClient

from docket import index
from docket.errors import DocketError
from docket.index.build import db_path
from docket.service.app import LOCK_SECONDS, Service, Settings, create_app
from docket.service.writer import IndexUpdater, Writer
from docket.vault.gate import Gate
from docket.vault.vault import Vault

TOKEN = "t0ken-for-tests-only"  # noqa: S105 - an invented token


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


def copied(template: Path, tmp_path: Path) -> Path:
    shutil.copytree(template, tmp_path / "vault")
    return tmp_path / "vault"


# -- the writer ------------------------------------------------------------------------------------------------------


def test_two_writes_sent_together_run_one_after_the_other() -> None:
    writer = Writer()
    events: list[tuple[str, str]] = []

    def write(name: str) -> str:
        events.append(("start", name))
        time.sleep(0.05)
        events.append(("end", name))
        return name

    async def both() -> list[str]:
        return list(await asyncio.gather(writer.run(lambda: write("a")), writer.run(lambda: write("b"))))

    try:
        assert asyncio.run(both()) == ["a", "b"]
    finally:
        writer.close()
    assert events == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]


def test_a_failing_write_reports_and_the_next_runs() -> None:
    writer = Writer()

    def broken() -> None:
        raise DocketError("refused")

    async def both() -> str:
        with pytest.raises(DocketError, match="refused"):
            await writer.run(broken)
        return await writer.run(lambda: "next")

    try:
        assert asyncio.run(both()) == "next"
    finally:
        writer.close()


def test_a_search_never_waits_behind_a_slow_write() -> None:
    writer = Writer()
    held = threading.Event()
    updater = IndexUpdater(lambda: None)

    async def search_during_write() -> None:
        slow = asyncio.ensure_future(writer.run(held.wait))
        await asyncio.wait_for(updater.fresh(), 5)
        assert not slow.done()
        held.set()
        await slow

    try:
        asyncio.run(search_during_write())
    finally:
        held.set()
        writer.close()


# -- the index updater -----------------------------------------------------------------------------------------------


def test_searches_together_share_an_update_and_a_late_one_waits_for_the_next() -> None:
    releases = [threading.Event(), threading.Event()]
    runs: list[int] = []

    def update() -> None:
        runs.append(len(runs))
        releases[len(runs) - 1].wait(5)

    async def three_searches() -> None:
        updater = IndexUpdater(update)
        first = asyncio.ensure_future(updater.fresh())
        await asyncio.sleep(0.05)
        late = [asyncio.ensure_future(updater.fresh()), asyncio.ensure_future(updater.fresh())]
        await asyncio.sleep(0.05)
        releases[0].set()
        await first
        await asyncio.sleep(0.05)
        # The update the first search waited for started before these two asked: they wait for the next.
        assert not any(task.done() for task in late)
        releases[1].set()
        await asyncio.gather(*late)
        await updater.idle()
        assert updater.runs == 2

    asyncio.run(three_searches())
    assert runs == [0, 1]


def test_a_search_past_its_wait_hears_the_index_is_being_built() -> None:
    release = threading.Event()

    async def impatient() -> None:
        updater = IndexUpdater(lambda: release.wait(5), wait_seconds=0.1)
        with pytest.raises(DocketError, match="the index is being built"):
            await updater.fresh()
        release.set()
        await updater.idle()

    try:
        asyncio.run(impatient())
    finally:
        release.set()


def test_a_failed_update_is_logged_by_its_type_and_the_search_goes_on(caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> None:
        raise ValueError("Kovács Ödön said the door code")

    async def search() -> None:
        updater = IndexUpdater(broken)
        await updater.fresh()

    with caplog.at_level(logging.WARNING, logger="docket.service.writer"):
        asyncio.run(search())
    assert "ValueError" in caplog.text
    assert "Kovács" not in caplog.text


# -- the service's vault and index -----------------------------------------------------------------------------------


def test_the_service_waits_fifteen_seconds_for_the_vault_lock(template: Path, tmp_path: Path) -> None:
    service = Service(Settings(port=8741, vault=copied(template, tmp_path), token=lambda: TOKEN))
    assert service.vault().lock._lock.timeout == LOCK_SECONDS == 15  # pyright: ignore[reportPrivateUsage]
    service.writer.close()


def test_a_record_the_cli_promoted_is_found_by_the_next_search(template: Path, tmp_path: Path) -> None:
    root = copied(template, tmp_path)
    service = Service(Settings(port=8741, vault=root, token=lambda: TOKEN))

    async def search(words: str) -> int:
        await service.updater.fresh()
        return len(index.search(service.vault(), words, now=datetime(2026, 10, 14, 8, 0, tzinfo=UTC)).groups)

    assert asyncio.run(search("zebrafinch")) == 0
    # The command line writes beside the running service, under the vault lock, as `docket staging promote` does.
    gate = Gate(Vault.open(root))
    gate.stage([make_record(77, rid="chat:C0OPS/1791499999.000100", container="chat:C0OPS", text="a zebrafinch")])
    assert gate.promote().runs
    assert asyncio.run(search("zebrafinch")) == 1
    service.writer.close()


def test_the_index_is_built_at_start_when_there_is_none(template: Path, tmp_path: Path) -> None:
    root = copied(template, tmp_path)
    vault = Vault.open(root)
    db_path(vault).unlink(missing_ok=True)
    app = create_app(Settings(port=8741, vault=root, token=lambda: TOKEN))
    with TestClient(app, base_url="http://127.0.0.1:8741"):
        deadline = time.monotonic() + 60
        while not db_path(vault).exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert db_path(vault).exists()
