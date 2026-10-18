"""The Sync page over HTTP, signed in, on a consuming project with an invented `memo` source (DR-0300 to DR-0306):
what is due, the runs open and ended, the failures and the cursors, the URL's states, and nothing of a capture."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from docket_webkit import a11y_problems, html_problems, parse
from starlette.testclient import TestClient
from sync_kit import BEARER, OURS, PORT, TITLE, TOKEN, Session, app_of, memo, service_client, sync_template, sync_vault

from docket.service.app import create_app
from docket.service.sessions import COOKIE, Sessions
from docket.service.state import Settings
from docket.vault import cursors
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.vault import Vault
from docket.web import sync as sync_routes
from docket.web.templating import PAGE_CSP

SESSION = "session-4e7a"
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


def page(client: TestClient, path: str = "/sync", status: int = 200) -> str:
    answer = client.get(path)
    assert answer.status_code == status, answer.text
    assert answer.headers["Content-Security-Policy"] == PAGE_CSP
    assert answer.headers["Cache-Control"] == "no-store"
    assert not html_problems(answer.text), path
    assert not a11y_problems(answer.text, whole=not path.startswith("/sync/runs")), path
    return answer.text


def text_of(html: str, cls: str) -> list[str]:
    return [el.text() for el in parse(html).find_all(class_=cls)]


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
    html = page(client)
    tree = parse(html)
    assert tree.find("h1").text() == "Sync"
    assert tree.find("a", aria_current="page").attrs["href"] == "/sync"
    assert text_of(html, "lead") == ["1 source due a sync; 0 runs open; no whole run here yet"]
    assert text_of(html, "need-line") == ["due: never synced"]
    assert text_of(html, "paste-text") == ['Sync memo with Docket: run_open("memo") and follow its steps.']
    empty = text_of(html, "empty-line")
    assert "Nothing failed since each source's last whole run." in empty
    assert "No run yet on this machine. Runs show here when Claude syncs a source." in empty
    assert "No machine has synced memo yet." in empty
    # Its keys are its own: the dialog and the footer name only the keys dialog's key.
    dialog = tree.find("dialog", id="keys-dialog")
    assert [k.attrs["data-key"] for k in dialog.find_all("kbd") if "data-key" in k.attrs] == ["?"]
    assert tree.find("span", class_="hints").text().split() == ["?", "keys"]


def test_a_source_run_open_would_refuse_needs_you_in_its_words(client: TestClient, vault: Vault) -> None:
    (vault.root.parent / ".claude" / "skills" / "sources" / "memo" / "SKILL.md").write_text("# old\n", encoding="utf-8")
    refused = client.post("/api/runs", json={"source": "memo"}, headers=BEARER).json()["error"]
    html = page(client)
    assert text_of(html, "need-line") == [refused]
    assert text_of(html, "paste-text") == []
    assert text_of(html, "lead") == ["1 needs you first; 0 runs open; no whole run here yet"]


def test_a_source_off_or_without_tools_needs_nothing(client: TestClient, vault: Vault) -> None:
    def change(config: VaultConfig) -> None:
        config.sources["memo"].enabled = False
        config.sources["notes"] = SourceConfig(sensitivity="work")

    set_config(vault, change)
    html = page(client)
    assert text_of(html, "need-line") == []
    assert "Nothing needs a session now." in text_of(html, "empty-line")
    set_config(vault, lambda c: setattr(c.sources["memo"], "tools", []))
    empty = " ".join(text_of(page(client), "empty-line"))
    assert "No source syncs live yet" in empty
    assert "in your own terminal, then list its tools in vault.yaml and commit it" in empty


def test_a_private_source_with_tools_needs_nothing(client: TestClient, vault: Vault) -> None:
    set_config(vault, lambda c: setattr(c.sources["memo"], "sensitivity", "private"))
    html = page(client)
    assert text_of(html, "need-line") == []
    assert text_of(html, "lead") == ["nothing needs a session; 0 runs open; no whole run here yet"]


def test_a_vault_yaml_that_doesnt_decode_doesnt_stop_the_service(vault: Vault) -> None:
    text = (vault.root / "vault.yaml").read_text(encoding="utf-8")
    (vault.root / "vault.yaml").write_bytes((text + "# j\u00f3 \u0151sz\n").encode("cp1250"))
    app = create_app(Settings(port=PORT, vault=vault.root, token=lambda: TOKEN))
    assert app.service.history.problem == "UnicodeDecodeError"
    (vault.root / "vault.yaml").write_text(text, encoding="utf-8")
    assert app.service.history.recent() == []
    assert app.service.history.problem is None


# -- the runs ----------------------------------------------------------------------------------------------------------


def test_an_open_run_shows_and_refreshes_until_it_closes(client: TestClient) -> None:
    run = Session(client, session=SESSION)
    run.open()
    run.listing(memo("o1"))
    run.fetch(memo("o1"))
    html = page(client)
    [card] = parse(html).find_all("li", class_="state-open")
    assert card.attrs["id"] == run.run
    assert "2 captures" in card.text()
    poll = parse(html).find("div", class_="poll")
    assert (poll.attrs["hx-get"], poll.attrs["hx-trigger"], poll.attrs["hx-target"]) == (
        "/sync/runs",
        "load delay:5s",
        "#runs",
    )
    assert text_of(html, "lead") == ["nothing needs a session; 1 run open; no whole run here yet"]
    fragment = client.get("/sync/runs", headers={"HX-Current-URL": f"{OURS}/sync"})
    assert fragment.headers["HX-Replace-Url"] == "/sync"
    assert "<html" not in fragment.text
    assert parse(fragment.text).find_all("div", class_="poll")
    assert run.close().status_code == 200
    after = page(client, "/sync/runs")
    assert parse(after).find_all("div", class_="poll") == []
    assert "No run is open now. Reload the page for the cursors and what is due." in after
    [closed] = parse(after).find_all("li", class_="state-closed")
    assert closed.attrs["id"] == run.run


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
    html = page(client)
    words = {
        li.attrs["id"]: li.find("span", class_="outcome").text() for li in parse(html).find_all("li", class_="run")
    }
    assert words == {whole.run: "closed", counted.run: "count", crashed.run: "mapper"}
    failures = {f.find("span", class_="badge").text(): f.text() for f in parse(html).find_all("li", class_="failure")}
    assert set(failures) == {"count", "mapper", "rate-limited"}
    assert "run the mapper's tests" in failures["mapper"]
    assert "the manifest counts 4 capture(s) and 1 arrived" in failures["count"]
    assert "1 id in retry since" in failures["rate-limited"]
    # A whole close later clears the drops; the rate-limited id stays while the cursor file holds it.
    whole_run(client, memo("e4"))
    later = {f.find("span", class_="badge").text() for f in parse(page(client)).find_all("li", class_="failure")}
    assert later == {"rate-limited"}
    with signed_in(vault) as again:
        ids = [li.attrs["id"] for li in parse(page(again)).find_all("li", class_="run")]
        assert ids[-3:] == [crashed.run, counted.run, whole.run]


def test_a_run_in_the_url_opens_at_the_top_and_more_runs_come_by_k(client: TestClient) -> None:
    runs = [whole_run(client, memo(f"k{n}")) for n in range(3)]
    html = page(client, f"/sync?run={runs[0].run}")
    [focus] = parse(html).find("ol", class_="focus").find_all("li", class_="run")
    assert focus.attrs["id"] == runs[0].run
    assert "focused" in focus.attrs["class"]
    short = parse(page(client, "/sync?k=2"))
    assert len(short.find("ol", class_="runs").find_all("li", class_="run")) == 2
    assert short.find("p", class_="more").find("a").attrs["href"] == "/sync?k=22"
    unknown = "run-20261015T000000-ffffffffffffffff"
    note = parse(page(client, f"/sync?run={unknown}")).text()
    assert f"Run {unknown} isn't open, nor among the 500 the history keeps." in note


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
def test_a_value_the_url_cant_hold_is_the_pages_own_error(client: TestClient, query: str, error: str) -> None:
    assert error in parse(page(client, f"/sync?{query}", status=400)).text()
    assert error in parse(page(client, f"/sync/runs?{query}", status=400)).text()


def test_one_source_narrows_the_page_and_an_unknown_one_says_so(client: TestClient, vault: Vault) -> None:
    whole_run(client, memo("n1"))
    write_cursor(vault, "desktop-2", cursors.Cursor(mark=datetime.now(UTC)))
    other = cursors.file_path(vault.root, "desktop-2")
    other.write_text(other.read_text(encoding="utf-8").replace("memo:", "chat:"), encoding="utf-8")
    html = page(client, "/sync?source=chat")
    assert [h.text() for h in parse(html).find_all("h3")] == ["chat"]
    assert parse(html).find_all("ol", class_="runs") == []
    chips = {a.text(): a.attrs.get("aria-current") for a in parse(html).find_all("a", class_="source-chip")}
    assert chips == {"All": None, "chat": "true", "memo": None}
    assert "No source nothing on this page" in parse(page(client, "/sync?source=nothing")).text()


# -- failures and cursors ----------------------------------------------------------------------------------------------


def test_this_machines_cursor_file_that_doesnt_read_is_named_by_its_line(client: TestClient, vault: Vault) -> None:
    path = cursors.file_path(vault.root, "test-box")
    path.parent.mkdir(exist_ok=True)
    path.write_text("schema_version: 1\n<<<<<<< ours\nsources: {}\n", encoding="utf-8")
    html = page(client)
    [failure] = parse(html).find_all("li", class_="failure")
    assert "line 2 is a merge conflict's marker" in failure.text()
    assert "keep one side, the one with the older mark is safe" in failure.text()
    assert "line 2" in text_of(html, "need-line")[0]


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
    html = page(client)
    machines = parse(html).find_all("li", class_="machine")
    assert [m.find("dd").text().split()[0] for m in machines] == ["test-box", "desktop-2", "old-box"]
    assert "its sets are handed over" in machines[1].text()
    assert "its sets aren't handed over" in machines[2].text()
    entries = machines[0].find("details", class_="entries").text()
    leaves = (TODAY - timedelta(days=3) + cursors.EXPIRES).isoformat()
    assert f"leaves {leaves}" in entries
    assert "waits for summary" in entries
    assert "due a final run" in entries
    assert any("1 id due a final run" in line for line in text_of(html, "need-line"))
    failures = text_of(html, "failure")
    assert any("cursors-broken-box.yaml doesn't read" in f for f in failures)
    [limited] = [f for f in failures if f.startswith("rate-limited")]
    assert "1 id in retry since" in limited
    assert "on test-box;" in limited
    assert "r8" not in limited
    assert "due a final run" not in machines[2].text()


# -- what never reaches the page ---------------------------------------------------------------------------------------


def test_nothing_of_a_capture_and_no_session_reaches_the_page(client: TestClient, vault: Vault) -> None:
    whole_run(client, memo("p1"), memo("p2"))
    run = Session(client, session=SESSION)
    run.open()
    run.listing(memo("p3"))
    for path in ("/sync", f"/sync?run={run.run}", "/sync/runs"):
        html = client.get(path).text
        for never in (TITLE, "Ada Example", SESSION):
            assert never not in html, (path, never)


def test_the_page_writes_nothing(client: TestClient) -> None:
    for path in ("/sync", "/sync/runs"):
        answer = client.post(path, headers={"Origin": OURS})
        assert answer.status_code == 405, path


def test_without_a_vault_or_on_a_failure_the_page_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = Sessions()
    app = create_app(Settings(port=PORT, vault=None, token=lambda: TOKEN), sessions=sessions)
    with TestClient(app, base_url=OURS) as client:
        started = sessions.redeem(sessions.new_code())
        assert started is not None
        client.cookies.set(COOKIE, started)
        assert "the service has no vault" in page(client, status=400)
        assert "the service has no vault" in page(client, "/sync/runs", status=400)

        def broken(_service: object) -> None:
            raise RuntimeError(TITLE)

        monkeypatch.setattr(sync_routes, "gather", broken)
        monkeypatch.setattr(sync_routes, "runs_only", broken)
        failed = client.get("/sync")
        assert failed.status_code == 500
        assert "Something failed in Docket" in failed.text
        assert TITLE not in failed.text
        fragment = client.get("/sync/runs")
        assert fragment.status_code == 500
        assert "<html" not in fragment.text
