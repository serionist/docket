"""Live sync runs over the REST API, on a consuming project with an invented `memo` source: whole runs, what they skip
and owe, drops that keep the mark, the close's limits, the routes' checks, and what never reaches the log."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import commit_config, make_record, sh
from starlette.testclient import TestClient
from sync_kit import (
    BEARER,
    OURS,
    PORT,
    TITLE,
    TOKEN,
    Session,
    app_of,
    as_structured,
    memo,
    service_client,
    sync_template,
    sync_vault,
)

from docket.cli import main
from docket.errors import DocketError
from docket.service import api, logs, state, sync
from docket.service.runs import Run
from docket.service.sessions import COOKIE
from docket.vault import cursors, holds
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.vault import Vault

TODAY = datetime.now(UTC).date()


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Today as each test starts, not as the module loaded: the service reads its clock when a run opens, and a test
    run that crosses midnight UTC would otherwise put a retry a day older than the test means."""
    monkeypatch.setattr(sys.modules[__name__], "TODAY", datetime.now(UTC).date())


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return sync_template(tmp_path_factory)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    return sync_vault(template, tmp_path)


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with service_client(vault) as found:
        yield found


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    root = logging.getLogger()
    levels = {name: logging.getLogger(name).level for name in ("", "docket", *logs.LIBRARIES)}
    path = tmp_path / "log" / "service.log"
    handler = logs.configure(path)
    yield path
    root.removeHandler(handler)
    handler.close()
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


def own_cursor(vault: Vault) -> cursors.Cursor | None:
    return cursors.read(vault.root, "test-box").get("memo")


def staged(vault: Vault) -> set[str]:
    return {e.staged.record.rid for e in Gate(vault).list(source="memo").entries}


def history_file(vault: Vault) -> Path:
    return vault.home.private / "sync" / "runs.jsonl"


def last_kind(vault: Vault) -> str:
    return json.loads(history_file(vault).read_text(encoding="utf-8").splitlines()[-1])["kind"]


def log(vault: Vault) -> list[str]:
    return sh(vault.root, "git", "log", "--format=%s").splitlines()


def set_config(vault: Vault, change: Any) -> None:
    config = VaultConfig.load(vault.root / "vault.yaml")
    change(config)
    (vault.root / "vault.yaml").write_text(config.dump(), encoding="utf-8")
    commit_config(vault.root)


# -- whole runs --------------------------------------------------------------------------------------------------------


def test_a_whole_run_stages_its_records_and_commits_the_moved_mark(client: TestClient, vault: Vault) -> None:
    run = Session(client)
    opened = run.open()
    assert opened["kept"] == []
    assert opened["held"] == []
    assert opened["tools"] == ["get_memo", "list_memos"]
    assert opened["skill"].endswith("SKILL.md")
    assert opened["budget"] == 400 * 1024
    assert "How this run goes" in opened["steps"]
    a, b = memo("a1"), memo("b1")
    run.listing(a, b)
    run.fetch(a)
    run.fetch(b)
    answer = run.close()
    assert answer.status_code == 200, answer.text
    closed = answer.json()
    assert (closed["pending"], closed["moved"], closed["repeated"]) == (2, True, False)
    assert staged(vault) == {"memo:a1", "memo:b1"}
    cursor = own_cursor(vault)
    assert cursor is not None
    assert cursor.mark == datetime.fromisoformat(opened["until"])
    assert log(vault)[0] == "Cursors: memo on test-box"
    # A repeated close answers what the first one did.
    again = run.close()
    assert again.status_code == 200
    assert again.json()["repeated"] is True
    # The next run starts from the mark, less the lookback, and lists what staging holds.
    second = Session(client).open()
    assert cursor.mark is not None
    assert second["since"] == (cursor.mark - timedelta(days=7)).isoformat().replace("+00:00", "Z")
    assert second["kept"] == ["a1", "b1"]


def test_after_two_whole_runs_a_third_owes_nothing(client: TestClient, vault: Vault) -> None:
    a, b = memo("a2"), memo("b2")
    first = Session(client)
    first.open()
    first.listing(a, b)
    first.fetch(a)
    first.fetch(b)
    assert first.close().status_code == 200
    # The second run skips what is kept, so the mapper sees both only listed and reports them waiting to be fetched.
    second = Session(client)
    assert second.open()["kept"] == ["a2", "b2"]
    second.listing(a, b)
    closed = second.close().json()
    assert {w["id"] for w in closed["waiting"]} == {"a2", "b2"}
    third = Session(client).open()
    assert (third["waiting"], third["retry"]) == ([], [])
    assert third["kept"] == ["a2", "b2"]


def test_an_item_nobody_fetched_is_fetched_on_the_next_run(client: TestClient) -> None:
    a, b, old = memo("a3"), memo("b3"), memo("c3", days_ago=30)
    first = Session(client)
    first.open()
    first.listing(a, b, old)
    first.fetch(a)
    assert first.close().status_code == 200
    second = Session(client).open()
    assert [(w["id"], w["why"]) for w in second["waiting"]] == [("b3", "fetch")]


def test_a_skipped_item_stays_in_retry_though_the_mapper_reports_it_waiting(client: TestClient, vault: Vault) -> None:
    a, b = memo("a4"), memo("b4")
    run = Session(client)
    run.open()
    run.listing(a, b)
    run.fetch(a)
    closed = run.close(skipped=[{"id": "b4", "reason": "later"}, {"reason": "rate-limited"}]).json()
    assert closed["retry_count"] == 1
    cursor = own_cursor(vault)
    assert cursor is not None
    assert set(cursor.retry) == {"b4"}
    assert cursor.waiting == {}
    second = Session(client).open()
    assert [(r["id"], r["why"]) for r in second["retry"]] == [("b4", "later")]


def test_a_record_staged_while_its_id_still_waits_stays_waiting(client: TestClient, vault: Vault) -> None:
    item = memo("p5", wait="summary", partial=True)
    for _ in range(2):
        run = Session(client)
        run.open()
        run.fetch(item)
        assert run.close().status_code == 200
        cursor = own_cursor(vault)
        assert cursor is not None
        assert set(cursor.waiting) == {"p5"}
    assert staged(vault) == {"memo:p5/part"}


def test_a_close_with_no_captures_leaves_the_mark(client: TestClient, vault: Vault) -> None:
    run = Session(client)
    run.open()
    closed = run.close(0).json()
    assert closed["moved"] is False
    assert "mark     stays" in sync.closed_text(sync.RunClosed.model_validate(closed))
    cursor = own_cursor(vault)
    assert cursor is not None
    assert cursor.mark is None


# -- drops that keep the mark ------------------------------------------------------------------------------------------


def test_a_count_that_doesnt_match_drops_the_run_and_keeps_the_mark(client: TestClient, vault: Vault) -> None:
    run = Session(client)
    run.open()
    run.listing(memo("a6"))
    answer = run.close(2)
    assert answer.status_code == 400
    assert "counts 2 capture(s) and 1 arrived" in answer.json()["error"]
    again = run.close(1)
    assert "was dropped: the manifest counts 2" in again.json()["error"]
    assert own_cursor(vault) is None
    assert staged(vault) == set()


def test_a_run_short_of_a_capture_is_dropped_at_its_close(client: TestClient) -> None:
    run = Session(client)
    run.open()
    refused = run.capture("delete_memo", as_structured(memo("a7")))
    assert refused.status_code == 400
    assert "isn't one of memo's tools" in refused.json()["error"]
    answer = run.close(0)
    assert answer.status_code == 400
    assert "came up short" in answer.json()["error"]


def test_a_run_the_hook_never_bound_is_dropped_at_its_close(client: TestClient) -> None:
    opened = client.post("/api/runs", json={"source": "memo"}, headers=BEARER).json()
    answer = client.post(f"/api/runs/{opened['run']}/close", json={"captures": 0}, headers=BEARER)
    assert "never tied the run to a session" in answer.json()["error"]


def test_a_mapper_past_its_time_drops_the_run(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync, "MAPPER_SECONDS", 0.3)
    run = Session(client)
    run.open()
    run.fetch(memo("a8", sleep=30))
    answer = run.close()
    assert answer.status_code == 400
    error = answer.json()["error"]
    assert "time limit" in error
    assert "the run is dropped and nothing is staged; the mark stays" in error
    assert own_cursor(vault) is None


def test_a_crashing_mapper_drops_the_run_and_its_errors_go_nowhere(
    client: TestClient, vault: Vault, log_file: Path
) -> None:
    run = Session(client, session="session-9f2e")
    run.open()
    run.fetch(memo("a9", crash=TITLE))
    answer = run.close()
    assert answer.status_code == 400
    assert TITLE not in answer.text
    assert "run its tests" in answer.json()["error"]
    text = log_file.read_text(encoding="utf-8")
    assert TITLE not in text
    assert "session-9f2e" not in text
    assert "Ada Example" not in text


def test_a_held_record_waiting_in_staging_drops_the_run(client: TestClient, vault: Vault) -> None:
    run = Session(client)
    run.open()
    run.fetch(memo("h1"))
    record = make_record(rid="memo:h1", source="memo", container="memo:h1", thread=None)
    Gate(vault).stage([record])
    holds.add(vault, "memo", ["h1"])
    answer = run.close()
    assert answer.status_code == 400
    assert "on hold wait in staging" in answer.json()["error"]
    assert "the run is dropped and the mark stays" in answer.json()["error"]
    assert last_kind(vault) == "refused"


def test_a_second_close_while_one_maps_is_refused_and_its_run_stays_open(client: TestClient) -> None:
    runs = app_of(client).service.runs
    busy = Session(client, session="s-busy")
    busy.open("memo")
    runs.begin_close(busy.run)
    refused = client.post("/api/runs", json={"source": "memo"}, headers=BEARER)
    assert "a run of memo is closing" in refused.json()["error"]
    runs.reopen(busy.run)
    other = Run(
        id="run-other",
        source="chat",
        tools=frozenset({"x"}),
        opened=runs.clock(),
        final=False,
        window=None,
        kept=frozenset(),
        handed=cursors.Handover(),
        held=frozenset(),
        session="s-other",
    )
    runs.open(other)
    runs.begin_close("run-other")
    answer = busy.close(0)
    assert "another run is closing; its run stays open" in answer.json()["error"]
    runs.reopen("run-other")
    assert busy.close(0).status_code == 200


def test_a_vault_lock_another_process_holds_leaves_the_run_open(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(state, "LOCK_SECONDS", 0.2)
    run = Session(client)
    run.open()
    run.fetch(memo("l1"))
    with Vault.open(vault.root).lock:
        answer = run.close()
    assert answer.status_code == 400
    assert "the run stays open" in answer.json()["error"]
    assert run.close().status_code == 200


# -- final runs and what they owe ----------------------------------------------------------------------------------


def write_cursor(vault: Vault, cursor: cursors.Cursor, machine: str = "test-box") -> None:
    path = cursors.file_path(vault.root, machine)
    path.parent.mkdir(exist_ok=True)
    path.write_text(cursors.dump({"memo": cursor}), encoding="utf-8")


def test_a_final_run_takes_what_waited_a_week_fetches_it_by_id_and_never_moves_the_mark(
    client: TestClient, vault: Vault
) -> None:
    mark = datetime.now(UTC) - timedelta(days=1)
    old = TODAY - timedelta(days=8)
    write_cursor(
        vault,
        cursors.Cursor(
            mark=mark,
            waiting={
                "w-old": cursors.Waiting("w-old", "summary", old),
                "w-new": cursors.Waiting("w-new", "summary", TODAY),
            },
            retry={"r-gone": cursors.Retry("r-gone", "gone", TODAY - timedelta(days=9))},
        ),
    )
    run = Session(client)
    opened = run.open(final=True)
    assert opened["since"] is None
    assert [w["id"] for w in opened["waiting"]] == ["w-old"]
    assert [r["id"] for r in opened["retry"]] == ["r-gone"]
    assert "How this final run goes" in opened["steps"]
    run.fetch(memo("w-old", days_ago=9, wait="summary"))
    wrong = run.close(final=False)
    assert "a final run closes with final=true" in wrong.json()["error"]
    answer = run.close(final=True, skipped=[{"id": "r-gone", "reason": "gone"}])
    assert answer.status_code == 200, answer.text
    closed = answer.json()
    assert closed["moved"] is False
    assert closed["expired"] == ["w-old"]
    cursor = own_cursor(vault)
    assert cursor is not None
    assert cursor.mark == mark.replace(microsecond=0)
    assert set(cursor.waiting) == {"w-new"}
    assert cursor.retry["r-gone"].since == TODAY - timedelta(days=9)


def test_a_gone_id_expires_after_a_month(client: TestClient, vault: Vault) -> None:
    write_cursor(vault, cursors.Cursor(retry={"r1": cursors.Retry("r1", "rate-limited", TODAY - timedelta(days=29))}))
    run = Session(client)
    assert [r["id"] for r in run.open()["retry"]] == ["r1"]
    closed = run.close(0, skipped=[{"id": "r1", "reason": "gone"}]).json()
    assert closed["expired"] == []
    write_cursor(vault, cursors.Cursor(retry={"r1": cursors.Retry("r1", "gone", TODAY - timedelta(days=30))}))
    assert Session(client).open()["retry"] == []


def test_a_final_run_with_nothing_due_is_refused(client: TestClient) -> None:
    answer = client.post("/api/runs", json={"source": "memo", "final": True}, headers=BEARER)
    assert "nothing of memo has waited a week" in answer.json()["error"]


def test_held_ids_are_handed_over_by_no_set(client: TestClient, vault: Vault) -> None:
    write_cursor(vault, cursors.Cursor(retry={"held-r": cursors.Retry("held-r", "later", TODAY)}))
    holds.add(vault, "memo", ["held-r"])
    opened = Session(client).open()
    assert opened["held"] == ["held-r"]
    assert opened["retry"] == []


def test_another_machines_ids_come_until_raw_holds_them(client: TestClient, vault: Vault) -> None:
    vault.write_run("memo", [make_record(rid="memo:in-raw", source="memo", container="memo:in-raw", thread=None)])
    write_cursor(
        vault,
        cursors.Cursor(
            mark=datetime.now(UTC) - timedelta(days=2),
            retry={
                "in-raw": cursors.Retry("in-raw", "later", TODAY),
                "missing": cursors.Retry("missing", "later", TODAY),
            },
        ),
        machine="laptop",
    )
    opened = Session(client).open()
    assert [r["id"] for r in opened["retry"]] == ["missing"]


# -- what run_open says and refuses ------------------------------------------------------------------------------------


def test_kept_comes_from_raw_too_and_is_cut_short_past_its_room(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    ts = datetime.now(UTC) - timedelta(days=1)
    vault.write_run(
        "memo", [make_record(n, rid=f"memo:raw-{n}", source="memo", ts=ts, container=None, thread=None) for n in (1, 2)]
    )
    opened = Session(client).open()
    assert opened["kept"] == ["raw-1", "raw-2"]
    assert opened["kept_whole"] is True
    monkeypatch.setattr(sync, "KEPT_CHARS", 8)
    cut = Session(client, session="s2").open()
    assert cut["kept"] == ["raw-1"]
    assert cut["kept_whole"] is False
    assert "kept is incomplete: more ids than the answer holds" in cut["notes"]
    assert ", and more" in sync.opened_text(sync.RunOpened.model_validate(cut))


def test_a_run_opened_with_since_gets_no_kept_list(client: TestClient) -> None:
    since = (datetime.now(UTC) - timedelta(days=40)).date().isoformat()
    opened = Session(client).open(since=since)
    assert opened["kept"] == []
    assert "a run opened with since gets no kept list: it is for a backfill's listing" in opened["notes"]
    assert opened["since"].startswith(since)
    timed = Session(client, session="s2").open(since=(datetime.now(UTC) - timedelta(days=3)).isoformat())
    assert timed["kept"] == []
    naive = Session(client, session="s3").open(since="2026-01-02T09:00")
    assert naive["since"].startswith("2026-01-02T")


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"source": "nope"}, "no source 'nope' in vault.yaml; sources that can run: memo"),
        ({"source": "memo", "since": "2026-01-02", "final": True}, "give since or final, not both"),
        ({"source": "memo", "since": "2999-01-01"}, "since is in the future"),
        ({"source": "memo", "since": "soon"}, "since is a date or a time"),
    ],
)
def test_run_open_refuses_before_any_work(client: TestClient, body: dict[str, Any], error: str) -> None:
    answer = client.post("/api/runs", json=body, headers=BEARER)
    assert answer.status_code == 400
    assert error in answer.json()["error"]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda c: setattr(c.sources["memo"], "enabled", False), "turned off"),
        (lambda c: setattr(c.sources["memo"], "sensitivity", "private"), "private in vault.yaml"),
        (lambda c: setattr(c.sources["memo"], "tools", []), "lists no tools"),
        (lambda c: setattr(c.sources["memo"], "mapper", None), "has no mapper"),
        (
            lambda c: c.sources.__setitem__("memo", SourceConfig(folder="~/Work")),
            "is a folder source: Docket reads its folder itself with docket scan memo",
        ),
    ],
)
def test_run_open_refuses_a_source_that_cant_run(client: TestClient, vault: Vault, change: Any, error: str) -> None:
    set_config(vault, change)
    answer = client.post("/api/runs", json={"source": "memo"}, headers=BEARER)
    assert error in answer.json()["error"]


def test_run_open_refuses_a_skill_missing_or_from_before_live_runs(client: TestClient, vault: Vault) -> None:
    skill = vault.root.parent / ".claude" / "skills" / "sources" / "memo" / "SKILL.md"
    skill.write_text("# memo, an old copy\n", encoding="utf-8")
    old = client.post("/api/runs", json={"source": "memo"}, headers=BEARER).json()["error"]
    assert "is from before live runs: copy the marker line and the run sections from Docket's blank template" in old
    assert sync.BLANK in old
    skill.unlink()
    assert "no SKILL.md in" in client.post("/api/runs", json={"source": "memo"}, headers=BEARER).json()["error"]


def test_a_plaud_skill_from_before_live_runs_names_the_reference(client: TestClient, vault: Vault) -> None:
    folder = vault.root.parent / ".claude" / "skills" / "sources" / "plaud"
    folder.mkdir(parents=True)
    (folder / "mapper.py").write_text("def map_run(run):\n    return {}\n", encoding="utf-8")
    (folder / "SKILL.md").write_text("# plaud, an old copy\n", encoding="utf-8")
    set_config(
        vault,
        lambda c: c.sources.__setitem__(
            "plaud",
            c.sources["memo"].model_copy(update={"mapper": ".claude/skills/sources/plaud/mapper.py", "tools": ["x"]}),
        ),
    )
    error = client.post("/api/runs", json={"source": "plaud"}, headers=BEARER).json()["error"]
    assert f"copy SKILL.md and mapper.py from {sync.REFERENCE} over yours" in error
    assert "or move your folder aside and run docket source new plaud --example" in error


def test_run_open_refuses_while_this_machines_cursor_doesnt_read(client: TestClient, vault: Vault) -> None:
    path = cursors.file_path(vault.root, "test-box")
    path.parent.mkdir(exist_ok=True)
    path.write_text("schema_version: 1\nsurprise: 1\n", encoding="utf-8")
    assert "line 2" in client.post("/api/runs", json={"source": "memo"}, headers=BEARER).json()["error"]


def test_run_open_notes_another_machines_unreadable_cursor_and_a_mark_from_the_future(
    client: TestClient, vault: Vault
) -> None:
    write_cursor(vault, cursors.Cursor(mark=datetime.now(UTC) + timedelta(days=3)), machine="laptop")
    (vault.root / "state" / "cursors-desktop.yaml").write_text("nonsense: [\n", encoding="utf-8")
    notes = Session(client).open()["notes"]
    assert "the cursor file of cursors-desktop.yaml doesn't read, so its marks and sets are left out" in notes
    assert "the mark of laptop is ahead of this machine's clock, so it is left out" in notes


def test_run_open_refuses_while_a_held_record_waits_in_staging(client: TestClient, vault: Vault) -> None:
    Gate(vault).stage([make_record(rid="memo:h2", source="memo", container=None, thread=None)])
    holds.add(vault, "memo", ["h2"])
    assert (
        "on hold wait in staging" in client.post("/api/runs", json={"source": "memo"}, headers=BEARER).json()["error"]
    )


def test_kept_says_so_when_the_index_cant_answer(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docket import index
    from docket.errors import DocketError

    def no_index(*_args: object) -> Any:
        raise DocketError("there is no index yet")

    async def building() -> None:
        raise DocketError("the index is being built")

    monkeypatch.setattr(index, "kept", no_index)
    monkeypatch.setattr(index, "stored_under", no_index)
    monkeypatch.setattr(app_of(client).service.updater, "fresh", building)
    write_cursor(
        vault,
        cursors.Cursor(mark=datetime.now(UTC), retry={"r9": cursors.Retry("r9", "later", TODAY)}),
        machine="laptop",
    )
    opened = Session(client).open()
    assert "kept is incomplete: the index can't answer, so raw's records aren't listed" in opened["notes"]
    assert "the index is still being built, so kept may miss raw's newest records" in opened["notes"]
    assert [r["id"] for r in opened["retry"]] == ["r9"]


# -- folders -----------------------------------------------------------------------------------------------------------


def test_a_runs_folder_must_be_one_docket_ingest_read_here(client: TestClient, vault: Vault, tmp_path: Path) -> None:
    folder = tmp_path / "backfill"
    folder.mkdir()
    (folder / "f1.json").write_text(json.dumps(memo("f1", days_ago=20)), encoding="utf-8")
    run = Session(client)
    run.open(since=(datetime.now(UTC) - timedelta(days=30)).date().isoformat())
    run.listing(memo("f1", days_ago=20), memo("gap", days_ago=15))
    refused = run.close(folder=str(folder))
    assert refused.status_code == 400
    assert "wasn't imported on this machine; run docket ingest memo" in refused.json()["error"]
    assert main(["ingest", "memo", str(folder), "--vault", str(vault.root)]) == 0
    answer = run.close(folder=str(folder))
    assert answer.status_code == 200, answer.text
    assert [w["waits_for"] for w in answer.json()["waiting"]] == ["fetch"]
    assert "memo:f1" in staged(vault)
    # The gap the folder lacked waits to be fetched by id.
    assert [(w["id"], w["why"]) for w in Session(client, session="s2").open()["waiting"]] == [("gap", "fetch")]


def test_the_folder_rule_refuses_a_relative_path_a_link_a_file_and_the_vault(
    client: TestClient, vault: Vault, tmp_path: Path
) -> None:
    from docket.errors import DocketError
    from docket.sources import local

    with pytest.raises(DocketError, match="an absolute path"):
        local.allowed(vault, "memo", "relative/folder")
    with pytest.raises(DocketError, match="isn't a folder"):
        local.allowed(vault, "memo", str(tmp_path / "missing"))
    with pytest.raises(DocketError, match="inside the vault"):
        local.allowed(vault, "memo", str(vault.root / "state"))
    real = tmp_path / "real"
    (real / "under").mkdir(parents=True)
    local.record(vault, "memo", real)
    local.record(vault, "memo", real)
    assert local.imported(vault) == {"memo": [str(real.resolve())]}
    assert local.allowed(vault, "memo", str(real / "under")) == (real / "under").resolve()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        return
    with pytest.raises(DocketError, match="is a link"):
        local.allowed(vault, "memo", str(link))


def test_an_unreadable_list_of_imported_folders_counts_as_none(vault: Vault) -> None:
    from docket.sources import local

    path = vault.home.private / local.FOLDERS
    path.parent.mkdir(parents=True, exist_ok=True)
    for text in ("not json", "[]", '{"folders": []}', '{"folders": {"memo": "x", "chat": ["a", 3]}}'):
        path.write_text(text, encoding="utf-8")
        assert local.imported(vault) in ({}, {"chat": ["a"]})


# -- the routes' checks ------------------------------------------------------------------------------------------------


def test_the_run_routes_take_the_token_never_the_browsers_session(client: TestClient) -> None:
    service = app_of(client).service
    started = service.sessions.redeem(service.sessions.new_code())
    assert started is not None
    client.cookies.set(COOKIE, started)
    for method, path in (("GET", "/api/runs"), ("POST", "/api/runs"), ("POST", "/api/runs/x/captures")):
        answer = client.request(method, path, json={}, headers={"Origin": OURS})
        assert answer.status_code == 401, path
        assert answer.json()["detail"] == "the run routes take the bearer token only"


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Origin": "http://evil.example"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Host": "evil.example"}, 421),
    ],
)
def test_the_run_routes_refuse_other_origins_and_hosts(
    client: TestClient, headers: dict[str, str], status: int
) -> None:
    answer = client.post("/api/runs", json={"source": "memo"}, headers={**BEARER, **headers})
    assert answer.status_code == status
    assert client.options("/api/runs", headers=BEARER).status_code == 405


def test_a_body_past_the_cap_is_refused_before_it_is_read_whole(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "CAPTURE_CAP", 1000)
    monkeypatch.setattr(api, "RUN_BODY", 1000)
    run = Session(client)
    run.open()
    url = f"/api/runs/{run.run}/captures"
    said = client.post(url, content=b"x" * 2000, headers={**BEARER, "Content-Type": "application/json"})
    assert said.status_code == 413
    chunks = (b"y" * 100 for _ in range(50))
    unsaid = client.post(url, content=chunks, headers={**BEARER, "Content-Type": "application/json"})
    assert unsaid.status_code == 413
    assert client.post("/api/runs", content=b"z" * 2000, headers=BEARER).status_code == 413
    assert "came up short: a capture past" in run.close(0).json()["error"]


def test_a_body_that_isnt_one_is_named_by_its_field_never_its_value(client: TestClient) -> None:
    answer = client.post("/api/runs", json={"source": ["Dentist with Cleo"]}, headers=BEARER)
    assert answer.status_code == 400
    assert answer.json()["error"] == "source isn't valid (string_type)"
    garbled = client.post("/api/runs", content=b"{", headers=BEARER)
    assert garbled.json()["error"].endswith("isn't valid (json_invalid)")


def test_a_close_refuses_a_bad_skip_or_count_and_leaves_the_run_open(client: TestClient) -> None:
    run = Session(client)
    run.open()
    bad_id = run.close(0, skipped=[{"id": "two words", "reason": "later"}])
    assert "skipped 1: its id isn't one" in bad_id.json()["error"]
    bad_reason = run.close(0, skipped=[{"id": "a", "reason": TITLE}])
    assert "skipped 1: its reason is a short token" in bad_reason.json()["error"]
    assert TITLE not in bad_reason.text
    assert "captures is 0 or more" in run.close(-1).json()["error"]
    assert run.close(0).status_code == 200


def test_the_listing_route_names_open_runs_without_content(client: TestClient) -> None:
    run = Session(client)
    run.open()
    run.fetch(memo("v1"))
    listed = client.get("/api/runs", headers=BEARER).json()["runs"]
    assert [(r["run"], r["captures"]) for r in listed] == [(run.run, 1)]
    assert TITLE not in json.dumps(listed)


def test_a_capture_without_a_time_zone_is_taken_as_now(client: TestClient) -> None:
    run = Session(client)
    run.open()
    answer = client.post(
        f"/api/runs/{run.run}/captures",
        json={
            "session": "s1",
            "tool_name": "get_memo",
            "tool_use_id": "t1",
            "result": [],
            "captured": "2026-10-14T09:00",
        },
        headers=BEARER,
    )
    assert answer.json()["capture"] == 1
    assert PORT != 8741


def test_a_cursor_that_cant_be_saved_leaves_the_run_closed_and_the_mark_where_it_was(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docket.errors import DocketError

    def refuse(*_args: object) -> None:
        raise DocketError("another docket process holds the lock; try again")

    monkeypatch.setattr(cursors, "save", refuse)
    run = Session(client)
    run.open()
    run.fetch(memo("c1"))
    closed = run.close().json()
    assert closed["cursor_problem"].endswith("the mark didn't move, and the next run fetches the window again")
    assert "memo:c1" in staged(vault)
    assert own_cursor(vault) is None


def test_an_unexpected_failure_drops_the_run(client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    from docket.sources import ingest

    def broken(*_args: object) -> None:
        raise RuntimeError("a bug")

    monkeypatch.setattr(ingest, "stage", broken)
    run = Session(client)
    run.open()
    run.fetch(memo("u1"))
    assert run.close().status_code == 500
    assert "was dropped: the close failed" in run.close().json()["error"]
    assert last_kind(vault) == "failed"


def test_the_close_text_names_ids_and_counts() -> None:
    closed = sync.RunClosed(
        run="run-x",
        source="memo",
        final=False,
        on_hold=2,
        dropped={"never-store": 1},
        set_aside=[
            sync.SetAsideEntry(id="m1", reason="no-ts", tool="get_memo", key="ts"),
            sync.SetAsideEntry(id=None, reason="bad-item", tool=None, key=None),
        ],
        held_in_raw=["memo:h9"],
        cursor_problem="the lock was held",
        expired=["old-1"],
        repeated=True,
    )
    text = sync.closed_text(closed)
    for line in (
        "(repeated: this is the first close's answer)",
        "on hold  2",
        "dropped  never-store 1",
        "set aside  m1  no-ts  get_memo ts",
        "set aside  (no id)  bad-item",
        "in raw   memo:h9 is on hold",
        "cursor   the lock was held",
        "expired  old-1",
    ):
        assert line in text


# -- code review, round 1 ----------------------------------------------------------------------------------------------


def test_a_folder_the_system_cant_read_leaves_the_run_open(client: TestClient) -> None:
    run = Session(client)
    run.open()
    answer = run.close(0, folder="C:/bad\x00folder" if sys.platform == "win32" else "/bad\x00folder")
    assert answer.status_code == 400
    assert "can't be read as a folder" in answer.json()["error"]
    assert run.close(0).status_code == 200


def test_an_unexpected_failure_before_the_mapper_drops_the_run_and_frees_the_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docket.sources import local

    def broken(*_args: object) -> Path:
        raise RuntimeError("a bug")

    monkeypatch.setattr(local, "allowed", broken)
    run = Session(client)
    run.open()
    assert run.close(0, folder="/anywhere").status_code == 500
    assert "was dropped: the close failed" in run.close(0).json()["error"]
    # Neither the source nor the next close is stuck behind a run left closing.
    other = Session(client, session="s2")
    other.open()
    assert other.close(0).status_code == 200


def test_an_id_the_cursor_file_cant_hold_never_reaches_it(client: TestClient, vault: Vault) -> None:
    run = Session(client)
    run.open()
    refused = run.close(0, skipped=[{"id": "of_\ufffe", "reason": "later"}])
    assert "skipped 1: its id isn't one" in refused.json()["error"]
    run.fetch(memo("w\uffff", wait="summary"))
    assert run.close().status_code == 200
    cursor = own_cursor(vault)
    assert cursor is not None
    assert cursor.waiting == {}
    assert Session(client, session="s2").open()["waiting"] == []


def test_a_mark_that_moved_but_wasnt_committed_says_so_and_the_next_close_commits_it(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docket.errors import DocketError

    real = Vault.commit

    def refuse(self: Vault, paths: list[str], message: str) -> str | None:
        if message.startswith("Cursors:"):
            raise DocketError("git couldn't commit")
        return real(self, paths, message)

    monkeypatch.setattr(Vault, "commit", refuse)
    run = Session(client)
    run.open()
    run.fetch(memo("m1"))
    closed = run.close().json()
    assert (
        closed["cursor_problem"] == "git couldn't commit; the mark moved but isn't committed; the next close commits it"
    )
    assert closed["moved"] is True
    cursor = own_cursor(vault)
    assert cursor is not None
    assert cursor.mark is not None
    monkeypatch.setattr(Vault, "commit", real)
    again = Session(client, session="s2")
    again.open()
    assert again.close(0).status_code == 200
    assert log(vault)[0] == "Cursors: memo on test-box"


def test_a_cursor_the_system_couldnt_write_leaves_the_run_closed_and_the_mark_unmoved(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked(*_args: object) -> None:
        raise PermissionError("held open by another program")

    monkeypatch.setattr(cursors, "write_replacing", locked)
    run = Session(client)
    run.open()
    run.fetch(memo("m2"))
    answer = run.close()
    assert answer.status_code == 200
    closed = answer.json()
    assert (
        closed["cursor_problem"] == "PermissionError; the mark didn't move, and the next run fetches the window again"
    )
    assert (closed["moved"], closed["mark_after"]) == (False, None)
    assert "memo:m2" in staged(vault)


def test_a_cursor_left_unreadable_by_a_failed_save_counts_as_unmoved(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def garbled(v: Vault, source: str, cursor: cursors.Cursor) -> None:
        cursors.file_path(v.root, v.machine).write_text("schema_version: [\n", encoding="utf-8")
        raise OSError("the disk filled up")

    monkeypatch.setattr(cursors, "save", garbled)
    run = Session(client)
    run.open()
    closed = run.close(0).json()
    assert closed["cursor_problem"] == "OSError; the mark didn't move, and the next run fetches the window again"


def test_a_cursor_file_broken_during_the_run_leaves_the_mark_unmoved(client: TestClient, vault: Vault) -> None:
    run = Session(client)
    run.open()
    run.fetch(memo("m3"))
    path = cursors.file_path(vault.root, "test-box")
    path.parent.mkdir(exist_ok=True)
    path.write_text("schema_version: 1\nsurprise: 1\n", encoding="utf-8")
    closed = run.close().json()
    assert "line 2" in closed["cursor_problem"]
    assert closed["cursor_problem"].endswith("the mark didn't move, and the next run fetches the window again")


def test_a_close_whose_caller_gives_up_finishes_and_keeps_its_outcome(vault: Vault) -> None:
    from docket.service.state import Service, Settings

    async def scenario() -> tuple[Any, Any]:
        service = Service(Settings(port=8751, vault=vault.root, token=lambda: TOKEN))
        opened, _ = await sync.run_open(service, "memo", session="s1")
        # Long enough that the close is still mapping when its caller gives up, on a slow runner too.
        item = memo("z1", sleep=1.5)
        service.runs.capture(
            opened.run,
            session="s1",
            agent=None,
            tool="get_memo",
            tool_use_id="t1",
            arguments={"id": "z1"},
            result=as_structured(item),
            captured=datetime.now(UTC),
            size=10,
        )
        task = asyncio.ensure_future(sync.ingest(service, opened.run, captures=1))
        await asyncio.sleep(0.4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        outcome = None
        for _ in range(200):
            outcome = service.runs.outcome(opened.run)
            if outcome is not None:
                break
            await asyncio.sleep(0.05)
        again, _ = await sync.ingest(service, opened.run, captures=1)
        service.writer.close()
        return outcome, again

    outcome, again = asyncio.run(scenario())
    assert outcome is not None
    assert outcome.closed is not None
    assert (again.repeated, again.pending, again.moved) == (True, 1, True)
    assert "memo:z1" in staged(vault)


def test_the_final_steps_say_what_to_do_with_an_item_too_large_to_keep() -> None:
    line = '"docket: not kept: <tool> (too large)": put that item in skipped as too-large and go on.'
    assert line in sync.STEPS
    assert line in sync.FINAL_STEPS


def owed_ids(n: int) -> dict[str, cursors.Waiting]:
    old = TODAY - timedelta(days=10)
    ids = [f"of_{k:032x}" for k in range(n)]
    return {i: cursors.Waiting(i, "summary", old) for i in ids}


def test_six_hundred_owed_ids_keep_the_answer_small_and_the_run_keeps_them_all(
    client: TestClient, vault: Vault
) -> None:
    write_cursor(vault, cursors.Cursor(waiting=owed_ids(600)))
    holds.add(vault, "memo", [f"held-{k:04d}" for k in range(300)])
    run = Session(client)
    opened = run.open()
    text = sync.opened_text(sync.RunOpened.model_validate(opened))
    assert len(text) < 20_000
    assert len(json.dumps(opened)) < 50_000
    shown = len(opened["waiting"])
    assert 0 < shown < 600
    assert f"waiting is incomplete: 600 ids, the {shown} oldest shown; the rest come in later runs" in opened["notes"]
    assert any(n.startswith("held is incomplete: 300 ids") for n in opened["notes"])
    assert run.close(0).status_code == 200
    cursor = own_cursor(vault)
    assert cursor is not None
    assert len(cursor.waiting) == 600


def test_a_final_run_expires_only_the_owed_ids_it_showed(client: TestClient, vault: Vault) -> None:
    retry = {f"r{k:03d}": cursors.Retry(f"r{k:03d}", "gone", TODAY - timedelta(days=8)) for k in range(300)}
    write_cursor(vault, cursors.Cursor(waiting=owed_ids(600), retry=retry))
    run = Session(client)
    opened = run.open(final=True)
    shown = len(opened["waiting"])
    assert any(n.startswith("retry is incomplete: 300 ids") for n in opened["notes"])
    closed = run.close(0, final=True).json()
    assert len(closed["expired"]) == shown
    cursor = own_cursor(vault)
    assert cursor is not None
    assert len(cursor.waiting) == 600 - shown


def test_the_window_line_is_in_utc_whatever_zone_since_came_in(client: TestClient) -> None:
    opened = Session(client).open(since="2026-01-02T09:00")
    text = sync.opened_text(sync.RunOpened.model_validate(opened))
    assert "window   2026-01-02T08:00:00Z to " in text


# -- the run history (DR-0301, DR-0302) --------------------------------------------------------------------------------


def test_each_ended_run_leaves_a_line_of_ids_and_counts_that_outlives_the_service(
    client: TestClient, vault: Vault
) -> None:
    whole = Session(client, session="session-5c1d")
    whole.open()
    a, b = memo("h1"), memo("h2")
    whole.listing(a, b)
    whole.fetch(a)
    assert whole.close(skipped=[{"id": "h2", "reason": "rate-limited"}]).status_code == 200
    counted = Session(client, session="session-5c1d")
    counted.open()
    counted.listing(memo("h3"))
    assert counted.close(5).status_code == 400
    crashed = Session(client, session="session-5c1d")
    crashed.open()
    crashed.fetch(memo("h4", crash=TITLE))
    assert crashed.close().status_code == 400
    unbound = client.post("/api/runs", json={"source": "memo"}, headers=BEARER).json()["run"]
    assert client.post(f"/api/runs/{unbound}/close", json={"captures": 0}, headers=BEARER).status_code == 400
    short = Session(client, session="session-5c1d")
    short.open()
    short.capture("delete_memo", as_structured(memo("h5")))
    assert short.close(0).status_code == 400

    lines = [json.loads(t) for t in history_file(vault).read_text(encoding="utf-8").splitlines()]
    assert [(line["run"], line["kind"]) for line in lines] == [
        (whole.run, "closed"),
        (counted.run, "count"),
        (crashed.run, "mapper"),
        (unbound, "unbound"),
        (short.run, "short"),
    ]
    first = lines[0]
    assert (first["outcome"], first["captures"], first["skipped"]) == ("closed", 2, {"rate-limited": 1})
    assert (first["staged"]["pending"], first["mark"]["moved"], first["owed"]) == (1, True, {"retry": 1, "waiting": 0})
    assert "counts 5 capture(s)" in lines[1]["why"]
    assert lines[2]["why"].startswith("the mapper failed")
    text = history_file(vault).read_text(encoding="utf-8")
    for never in (TITLE, "session-5c1d", "Ada Example"):
        assert never not in text
    # A new service on the same vault reads them back, newest first.
    with service_client(vault) as again:
        recent = app_of(again).service.history.recent()
        assert [line.run for line in recent] == [short.run, unbound, crashed.run, counted.run, whole.run]


# -- run_open's checks and its final rule, which the Sync page shares (DR-0304) ---------------------------------------


def _old_skill(vault: Vault) -> None:
    (vault.root.parent / ".claude" / "skills" / "sources" / "memo" / "SKILL.md").write_text("# old\n", encoding="utf-8")


def _bad_cursor(vault: Vault) -> None:
    path = cursors.file_path(vault.root, "test-box")
    path.parent.mkdir(exist_ok=True)
    path.write_text("schema_version: 1\nsurprise: 1\n", encoding="utf-8")


def _held_in_staging(vault: Vault) -> None:
    Gate(vault).stage([make_record(rid="memo:h9", source="memo", container=None, thread=None)])
    holds.add(vault, "memo", ["h9"])


@pytest.mark.parametrize(
    "breaks",
    [
        _old_skill,
        _bad_cursor,
        _held_in_staging,
        lambda v: set_config(v, lambda c: setattr(c.sources["memo"], "enabled", False)),
        lambda v: set_config(v, lambda c: setattr(c.sources["memo"], "mapper", None)),
    ],
)
def test_preflight_refuses_with_run_opens_own_words(client: TestClient, vault: Vault, breaks: Any) -> None:
    breaks(vault)
    with pytest.raises(DocketError) as refused:
        sync.preflight(Vault.open(vault.root), "memo")
    assert client.post("/api/runs", json={"source": "memo"}, headers=BEARER).json()["error"] == str(refused.value)


def test_a_final_run_is_owed_exactly_when_run_open_would_open_one(client: TestClient, vault: Vault) -> None:
    now = datetime.now(UTC)
    write_cursor(vault, cursors.Cursor(mark=now, waiting={"w1": cursors.Waiting("w1", "summary", TODAY)}))
    ready = sync.preflight(vault, "memo")
    assert sync.owed(vault, "memo", ready, now, final=True) == cursors.Handover()
    assert set(sync.owed(vault, "memo", ready, now, final=False).waiting) == {"w1"}
    refused = client.post("/api/runs", json={"source": "memo", "final": True}, headers=BEARER)
    assert "no final run is needed" in refused.json()["error"]
    old = TODAY - timedelta(days=8)
    write_cursor(vault, cursors.Cursor(mark=now, waiting={"w1": cursors.Waiting("w1", "summary", old)}))
    ready = sync.preflight(vault, "memo")
    assert set(sync.owed(vault, "memo", ready, now, final=True).waiting) == {"w1"}
    assert [w["id"] for w in Session(client).open(final=True)["waiting"]] == ["w1"]


def test_another_refusal_before_the_mapper_drops_the_run_as_refused(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuses(*_args: object) -> None:
        raise DocketError("a check of its own refused the close")

    monkeypatch.setattr(sync, "_check_close", refuses)
    run = Session(client)
    run.open()
    answer = run.close(0)
    assert answer.status_code == 400
    assert answer.json()["error"] == "a check of its own refused the close"
    assert last_kind(vault) == "refused"
