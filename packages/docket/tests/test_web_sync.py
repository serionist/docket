"""The Sync page's JSON reads, signed in, on a consuming project with an invented `memo` source (DR-0304, DR-0305,
DR-0788): what is due, the runs open and ended, the failures and the cursors, the URL's states, the runs part the page
asks while a run is open, and nothing of a capture."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import commit_config
from starlette.testclient import TestClient
from sync_kit import BEARER, OURS, PORT, TITLE, TOKEN, Session, app_of, memo, service_client, sync_template, sync_vault

from docket.service.app import create_app
from docket.service.sessions import COOKIE, Sessions
from docket.service.state import Settings
from docket.vault import cursors
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.vault import Vault
from docket.web import sync as sync_routes
from docket.web.display import FAILED

SESSION = "session-4e7a"
TODAY = datetime.now(UTC).date()
PAGE = "/api/web/sync"
RUNS = "/api/web/sync/runs"


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


@contextmanager
def signed_in(vault: Vault) -> Iterator[TestClient]:
    """A client holding a browser session, which also opens runs with the token, as Claude's session does."""
    with service_client(vault) as client:
        sessions = app_of(client).service.sessions
        started = sessions.redeem(sessions.new_code())
        assert started is not None
        client.cookies.set(COOKIE, started)
        yield client


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


def read(client: TestClient, query: str = "", status: int = 200, path: str = PAGE) -> dict[str, Any]:
    answer = client.get(f"{path}?{query}" if query else path)
    assert answer.status_code == status, answer.text
    found: dict[str, Any] = answer.json()
    return found


def runs_of(found: dict[str, Any]) -> list[dict[str, Any]]:
    runs = found["runs"]
    return [*runs["open"], *runs["recent"]]


def write_cursor(vault: Vault, machine: str, cursor: cursors.Cursor) -> None:
    path = cursors.file_path(vault.root, machine)
    path.parent.mkdir(exist_ok=True)
    path.write_text(cursors.dump({"memo": cursor}), encoding="utf-8")


def set_config(vault: Vault, change: Any) -> None:
    config = VaultConfig.load(vault.root / "vault.yaml")
    change(config)
    (vault.root / "vault.yaml").write_text(config.dump(), encoding="utf-8")


def whole_run(client: TestClient, *items: dict[str, Any], **close: Any) -> Session:
    run = Session(client, session=SESSION)
    run.open()
    run.listing(*items)
    for item in items:
        run.fetch(item)
    assert run.close(**close).status_code == 200
    return run


# -- what needs a session ----------------------------------------------------------------------------------------------


def test_a_source_never_synced_is_due_with_the_words_to_paste(client: TestClient) -> None:
    found = read(client)
    assert found["canonical"] == "/sync"
    assert found["lead"] == "1 source due a sync; 0 runs open; no whole run here yet"
    assert [(n["kind"], n["word"], n["line"]) for n in found["needs"]] == [("due", "due a sync", "due: never synced")]
    assert found["needs"][0]["paste"] == 'Sync memo with Docket: run_open("memo") and follow its steps.'
    assert (found["failures"], runs_of(found), found["live_sources"]) == ([], [], 1)
    [cursor] = found["cursors"]
    assert (cursor["source"], cursor["rows"], cursor["problem"]) == ("memo", [], None)
    assert found["machine"] == "test-box"
    assert [(c["label"], c["url"], c["on"]) for c in found["chips"]] == [
        ("All", "/sync", True),
        ("memo", "/sync?source=memo", False),
    ]


def test_a_source_run_open_would_refuse_needs_you_in_its_words(client: TestClient, vault: Vault) -> None:
    (vault.root.parent / ".claude" / "skills" / "sources" / "memo" / "SKILL.md").write_text("# old\n", encoding="utf-8")
    refused = client.post("/api/runs", json={"source": "memo"}, headers=BEARER).json()["error"]
    found = read(client)
    assert [(n["kind"], n["line"], n["paste"]) for n in found["needs"]] == [("you", refused, None)]
    assert found["lead"] == "1 needs you first; 0 runs open; no whole run here yet"


def test_a_source_off_or_without_tools_needs_nothing(client: TestClient, vault: Vault) -> None:
    def change(config: VaultConfig) -> None:
        config.sources["memo"].enabled = False
        config.sources["notes"] = SourceConfig(sensitivity="work")

    set_config(vault, change)
    found = read(client)
    assert (found["needs"], found["live_sources"]) == ([], 1)
    set_config(vault, lambda c: setattr(c.sources["memo"], "tools", []))
    assert read(client)["live_sources"] == 0


def test_a_private_source_with_tools_needs_nothing(client: TestClient, vault: Vault) -> None:
    set_config(vault, lambda c: setattr(c.sources["memo"], "sensitivity", "private"))
    found = read(client)
    assert found["needs"] == []
    assert found["lead"] == "nothing needs a session; 0 runs open; no whole run here yet"


def test_a_vault_yaml_that_doesnt_decode_doesnt_stop_the_service(vault: Vault) -> None:
    text = (vault.root / "vault.yaml").read_text(encoding="utf-8")
    (vault.root / "vault.yaml").write_bytes((text + "# jó ősz\n").encode("cp1250"))
    app = create_app(Settings(port=PORT, vault=vault.root, token=lambda: TOKEN))
    assert app.service.history.problem == "UnicodeDecodeError"
    (vault.root / "vault.yaml").write_text(text, encoding="utf-8")
    assert app.service.history.recent() == []
    assert app.service.history.problem is None


# -- the runs ----------------------------------------------------------------------------------------------------------


def test_an_open_run_shows_and_its_part_reads_alone_until_it_closes(client: TestClient) -> None:
    run = Session(client, session=SESSION)
    run.open()
    run.listing(memo("o1"))
    run.fetch(memo("o1"))
    found = read(client)
    [card] = found["runs"]["open"]
    assert (card["id"], card["state"], card["word"], card["url"]) == (run.run, "open", "open", f"/sync?run={run.run}")
    assert card["facts"][0].startswith("2 captures, ")
    assert card["took"].endswith(" so far")
    assert found["runs"]["live"] is True
    assert found["lead"] == "nothing needs a session; 1 run open; no whole run here yet"
    # The runs part on its own, as the page asks it every 5 seconds: the service's memory, the page's canonical URL.
    part = read(client, "k=5", path=RUNS)
    assert (part["canonical"], part["runs"]["live"], part["runs"]["open"][0]["id"]) == ("/sync?k=5", True, run.run)
    assert run.close().status_code == 200
    after = read(client, path=RUNS)["runs"]
    assert (after["live"], after["open"]) == (False, [])
    [closed] = after["recent"]
    assert (closed["id"], closed["state"], closed["word"]) == (run.run, "closed", "closed")
    assert not closed["took"].endswith("so far")


def test_ended_runs_show_their_kinds_and_advice_and_outlive_the_service(client: TestClient, vault: Vault) -> None:
    whole = whole_run(client, memo("e1"), skipped=[{"id": "e9", "reason": "rate-limited"}])
    counted = Session(client, session=SESSION)
    counted.open()
    counted.listing(memo("e2"))
    assert counted.close(4).status_code == 400
    crashed = Session(client, session=SESSION)
    crashed.open()
    crashed.fetch(memo("e3", crash=TITLE))
    assert crashed.close().status_code == 400
    found = read(client)
    words = {r["id"]: r["word"] for r in runs_of(found)}
    assert words == {whole.run: "closed", counted.run: "count", crashed.run: "mapper"}
    failures = {f["label"]: f for f in found["failures"]}
    assert set(failures) == {"count", "mapper", "rate-limited"}
    assert "run the mapper's tests" in failures["mapper"]["todo"]
    assert "the manifest counts 4 capture(s) and 1 arrived" in failures["count"]["what"]
    assert failures["count"]["run_url"] == f"/sync?run={counted.run}"
    assert failures["count"]["when"]
    assert "1 id in retry since" in failures["rate-limited"]["what"]
    # A whole close later clears the drops; the rate-limited id stays while the cursor file holds it.
    whole_run(client, memo("e4"))
    assert {f["label"] for f in read(client)["failures"]} == {"rate-limited"}
    with signed_in(vault) as again:
        ids = [r["id"] for r in runs_of(read(again))]
        assert ids[-3:] == [crashed.run, counted.run, whole.run]


def test_a_run_in_the_url_is_the_focus_and_more_runs_come_by_k(client: TestClient) -> None:
    runs = [whole_run(client, memo(f"k{n}")) for n in range(3)]
    found = read(client, f"run={runs[0].run}")
    assert found["canonical"] == f"/sync?run={runs[0].run}"
    assert found["runs"]["focus"]["id"] == runs[0].run
    assert runs[0].run not in [r["id"] for r in found["runs"]["recent"]]
    short = read(client, "k=2")
    assert len(short["runs"]["recent"]) == 2
    assert short["runs"]["more"] == "/sync?k=22"
    unknown = "run-20261015T000000-ffffffffffffffff"
    missing = read(client, f"run={unknown}")
    assert missing["runs"]["focus"] is None
    assert f"Run {unknown} isn't open, nor among the 500 the history keeps." in missing["notes"]


# -- the URL -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "error"),
    [
        ("source=Not%20a%20source", "source is a source's name"),
        ("run=run-1", "run is a run's id"),
        ("k=0", "k is a"),
        ("k=%C2%B2", "k is a"),
    ],
)
def test_a_value_the_url_cant_hold_is_refused_with_its_words(client: TestClient, query: str, error: str) -> None:
    assert error in read(client, query, status=400)["error"]
    assert error in read(client, query, status=400, path=RUNS)["error"]


def test_the_url_comes_back_in_one_canonical_form(client: TestClient) -> None:
    assert read(client, "k=40&source=memo&other=kept+out")["canonical"] == "/sync?source=memo&k=40"
    assert read(client, "k=20")["canonical"] == "/sync"


def test_one_source_narrows_the_page_and_an_unknown_one_says_so(client: TestClient, vault: Vault) -> None:
    whole_run(client, memo("n1"))
    write_cursor(vault, "desktop-2", cursors.Cursor(mark=datetime.now(UTC)))
    other = cursors.file_path(vault.root, "desktop-2")
    other.write_text(other.read_text(encoding="utf-8").replace("memo:", "chat:"), encoding="utf-8")
    found = read(client, "source=chat")
    assert [b["source"] for b in found["cursors"]] == ["chat"]
    assert runs_of(found) == []
    assert {c["label"]: c["on"] for c in found["chips"]} == {"All": False, "chat": True, "memo": False}
    assert "No source nothing on this page" in " ".join(read(client, "source=nothing")["notes"])


# -- failures and cursors ----------------------------------------------------------------------------------------------


def test_this_machines_cursor_file_that_doesnt_read_is_named_by_its_line(client: TestClient, vault: Vault) -> None:
    path = cursors.file_path(vault.root, "test-box")
    path.parent.mkdir(exist_ok=True)
    path.write_text("schema_version: 1\n<<<<<<< ours\nsources: {}\n", encoding="utf-8")
    found = read(client)
    [failure] = found["failures"]
    assert "line 2 is a merge conflict's marker" in failure["what"]
    assert "keep one side, the one with the older mark is safe" in failure["todo"]
    assert failure["severity"] == "error"
    assert "line 2" in found["needs"][0]["line"]
    assert found["cursors"][0]["problem"] is not None


def test_each_machines_mark_entries_and_hand_over(client: TestClient, vault: Vault) -> None:
    now = datetime.now(UTC)
    write_cursor(
        vault,
        "test-box",
        cursors.Cursor(
            mark=now - timedelta(hours=2),
            retry={"r1": cursors.Retry("r1", "rate-limited", TODAY - timedelta(days=3))},
            waiting={"w1": cursors.Waiting("w1", "summary", TODAY - timedelta(days=8))},
        ),
    )
    write_cursor(vault, "desktop-2", cursors.Cursor(mark=now - timedelta(days=29)))
    # A retired machine's file: its mark is over 30 days old, so run_open hands its entries to no run.
    write_cursor(
        vault,
        "old-box",
        cursors.Cursor(
            mark=now - timedelta(days=31),
            retry={
                "r8": cursors.Retry("r8", "rate-limited", TODAY - timedelta(days=2)),
                "r9": cursors.Retry("r9", "gone", TODAY - timedelta(days=10)),
            },
        ),
    )
    (vault.root / "state" / "cursors-broken-box.yaml").write_text("nonsense: [\n", encoding="utf-8")
    found = read(client)
    [block] = found["cursors"]
    rows = block["rows"]
    assert [(r["machine"], r["this"]) for r in rows] == [("test-box", True), ("desktop-2", False), ("old-box", False)]
    assert rows[0]["mark_age"] == "2 h ago"
    assert rows[0]["mark"]
    assert rows[1]["handed"] == "its sets are handed over"
    assert rows[2]["handed"] == "its sets aren't handed over: its mark is 30 days old or none"
    [retry] = rows[0]["retry"]
    leaves = (TODAY - timedelta(days=3) + cursors.EXPIRES).isoformat()
    assert (retry["kind"], retry["why"], retry["leaves"]) == ("retry", "rate-limited", leaves)
    [waiting] = rows[0]["waiting"]
    since = (TODAY - timedelta(days=8)).isoformat()
    assert (waiting["kind"], waiting["why"], waiting["since"], waiting["final_due"]) == (
        "waiting",
        "summary",
        since,
        True,
    )
    assert any("1 id due a final run" in n["line"] for n in found["needs"])
    failures = [f["what"] for f in found["failures"]]
    assert any("cursors-broken-box.yaml doesn't read" in f for f in failures)
    [limited] = [f for f in found["failures"] if f["label"] == "rate-limited"]
    assert "1 id in retry since" in limited["what"]
    assert "on test-box;" in limited["what"]
    assert "r8" not in limited["ids"]
    assert not any(e["final_due"] for e in rows[2]["retry"])


def test_each_row_shows_the_lookback_the_window_takes(client: TestClient, vault: Vault) -> None:
    write_cursor(vault, "test-box", cursors.Cursor(mark=datetime.now(UTC) - timedelta(hours=2)))
    [row] = read(client)["cursors"][0]["rows"]
    assert row["lookback"] == "7 days"
    set_config(vault, lambda c: setattr(c.sources["memo"], "lookback_days", 1))
    commit_config(vault.root)
    [row] = read(client)["cursors"][0]["rows"]
    assert row["lookback"] == "1 day"
    assert read(client)["cursors"][0]["window_since"]


# -- what never reaches the page ---------------------------------------------------------------------------------------


def test_nothing_of_a_capture_and_no_session_reaches_the_reads(client: TestClient) -> None:
    whole_run(client, memo("p1"), memo("p2"))
    run = Session(client, session=SESSION)
    run.open()
    run.listing(memo("p3"))
    for path in (PAGE, f"{PAGE}?run={run.run}", RUNS, f"{RUNS}?run={run.run}"):
        said = client.get(path).text
        for never in (TITLE, "Ada Example", SESSION):
            assert never not in said, (path, never)


def test_the_page_writes_nothing_and_takes_the_session_only(client: TestClient) -> None:
    for path in (PAGE, RUNS, "/sync", "/sync/runs"):
        answer = client.post(path, json={}, headers={"Origin": OURS})
        assert answer.status_code in (404, 405), path
    assert client.get(PAGE, headers=BEARER).status_code == 403
    client.cookies.clear()
    assert client.get(PAGE).status_code == 401


def test_the_page_path_answers_the_apps_document(client: TestClient) -> None:
    for path in ("/sync", "/sync?source=memo&run=run-20261015T000000-ffffffffffffffff"):
        page = client.get(path)
        assert page.status_code == 200
        assert '<div id="root"></div>' in page.text


def test_without_a_vault_or_on_a_failure_the_reads_say_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = Sessions()
    app = create_app(Settings(port=PORT, vault=None, token=lambda: TOKEN), sessions=sessions)
    with TestClient(app, base_url=OURS) as client:
        started = sessions.redeem(sessions.new_code())
        assert started is not None
        client.cookies.set(COOKIE, started)
        assert "the service has no vault" in read(client, status=400)["error"]
        assert "the service has no vault" in read(client, status=400, path=RUNS)["error"]

        def broken(_service: object) -> None:
            raise RuntimeError(TITLE)

        monkeypatch.setattr(sync_routes, "gather", broken)
        monkeypatch.setattr(sync_routes, "runs_only", broken)
        for path in (PAGE, RUNS):
            failed = client.get(path)
            assert (failed.status_code, failed.json()) == (500, {"error": FAILED})
            assert TITLE not in failed.text
