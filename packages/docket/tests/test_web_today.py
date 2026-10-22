"""Today's JSON routes, signed in (DR-0669, DR-0709 to DR-0711, DR-0773): / goes to Today and the old ways in still
open; the next meeting with its prep leads, then the items overdue, due today and back from a snooze, then the rest of
today's meetings and the next working day's, cancelled and declined ones left out, then what closed today; each live
source's last sync with the sidebar's due mark; the version that moves with a cursor file or a run; the panel of an
item or a meeting; and the writes and saves through the shared routes. The clock is noon today in the owner's zone.
Invented people only."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import UTC, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import ui_world
from day_kit import SAME, OnTheDay, add, at, day, meeting, meetings_week, read_today, signed_in, today
from docket_testkit import empty_vault, make_config
from starlette.testclient import TestClient

from docket.ledger import day as ledger_day
from docket.ledger import ops as ledger_ops
from docket.ledger import store, views
from docket.service.runs import Runs
from docket.service.sessions import open_command
from docket.vault import cursors
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.vault import Vault
from docket.web import today_view
from docket.web.day_state import DayState

API = "/api/web/today"
WRITE = "/api/web/ledger/write"
SAVE = "/api/web/ledger/save"
CHANGED = "/api/web/ledger/changed"


def live_config() -> VaultConfig:
    """Two live sources, chat and mail, beside the kit's calls, which syncs by no tool."""
    made = make_config()
    made.sources["chat"] = SourceConfig(tools=["read_channel"])
    made.sources["mail"] = SourceConfig(tools=["read_mail"])
    return made


def next_day() -> int:
    """How many days on the next working day is, as Today counts it."""
    return (ledger_day.next_working_day(today()) - today()).days


def the_day() -> list[dict[str, Any]]:
    """Beside meetings_week's: a follow-up later today with a proposed point and an item on it, and the next working
    day's planning with five open points, a cancelled and a declined one beside it; an item dropped today."""
    ahead = next_day()
    planning = [{"op": "point", "item": "$plan", "fields": {"text": f"Plan point {n}"}} for n in range(1, 6)]
    return [
        meeting("follow", "Supplier follow-up", 0, 15, 30, stream="pilot"),
        {"op": "point", "item": "$follow", "fields": {"text": "The written offer", "proposed": True}},
        add("offer", "task", "Read the written offer", "Ben Sample", "pilot", meeting="$follow"),
        meeting("plan", "Spring planning", ahead, 9, 60),
        *planning,
        meeting("gone", "Cancelled planning", ahead, 11, response="cancelled"),
        meeting("skip", "Declined review", ahead, 13, response="declined"),
        add("badges", "task", "Print the old badges", "Ada Example", "ops"),
        {"op": "drop", "item": "$badges", "note": "not needed"},
    ]


def _today(vault: Vault) -> None:
    store.write(vault, meetings_week(), by="claude", via="cli")
    store.write(vault, the_day(), by="claude", via="cli")
    ui_world.woke(vault)
    cursors.save(vault, "chat", cursors.Cursor(mark=at(0, 11, 20).astimezone(UTC)))


TODAY = OnTheDay("today", _today, live_config)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    found = TODAY.copy(tmp_path)
    monkeypatch.setattr(views, "now", lambda: at(0, 12).astimezone(UTC))
    return found


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


@pytest.fixture
def seen(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    """The worker's shared copy of today's Today vault, its index built, for the tests that only read: a write into it
    fails the run (DR-0531), and past midnight it is the next day's."""
    return read_today(tmp_path_factory, TODAY.template(tmp_path_factory))


@pytest.fixture
def reader(seen: Vault, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(views, "now", lambda: at(0, 12).astimezone(UTC))
    with signed_in(seen) as found:
        yield found


def read(client: TestClient, path: str, status: int = 200) -> Any:
    answer = client.get(path)
    assert answer.status_code == status, answer.text
    assert answer.headers["cache-control"] == "no-store"
    return answer.json()


def post(client: TestClient, body: dict[str, Any], path: str = WRITE, status: int = 200) -> Any:
    answer = client.post(path, json={"page": "today", **body}, headers=SAME)
    assert answer.status_code == status, answer.text
    return answer.json()


def ops(vault: Vault) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in sorted((vault.root / ledger_ops.OPS_DIR).glob("*.jsonl")):
        out += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


def refs(group: Any) -> list[str]:
    return [r["ref"] for r in group["rows"]]


# -- the way in -------------------------------------------------------------------------------------------------------


def test_slash_goes_to_today_and_the_old_ways_in_still_open(reader: TestClient) -> None:
    start = reader.get("/")
    assert (start.status_code, start.headers["location"]) == (303, "/today")
    assert reader.get("/today").status_code == 200
    for page in ("/search", "/queue", "/ledger", "/meetings", "/today?open=A-0001"):
        assert reader.get(page).status_code == 200, page
    # docket open with no page lands on Today; a sign-in page for Search names Search.
    assert open_command("/") == open_command("/today") == "docket open"
    assert open_command("/search") == 'docket open "/search"'


# -- the list ---------------------------------------------------------------------------------------------------------


def test_the_meeting_under_way_leads_with_its_prep(reader: TestClient) -> None:
    found = read(reader, API)
    assert found["canonical"] == "/today"
    assert found["list"]["day"] == f"{at(0, 9):%A} {today().day} {at(0, 9):%B}"
    lead = found["list"]["lead"]
    assert lead["label"] == "Now, until 12:30"
    meeting = lead["meeting"]
    assert (meeting["line"]["ref"], meeting["line"]["subject"], meeting["line"]["now"]) == (
        "M-0003",
        "Budget review",
        True,
    )
    assert meeting["where"] == "Room 2"
    assert meeting["people"] == "Ben Sample, Ada Example, ben@northwind.example and Dora Nobody"
    # Its open points whole, each by its id; the meeting's open items with their rows.
    assert [p["text"] for p in meeting["points"]] == ["Ben: the spring numbers"]
    assert all(ledger_ops.ULID.fullmatch(p["id"]) for p in meeting["points"])
    assert (meeting["more_points"], meeting["items"], meeting["more_items"]) == (0, [], 0)


def test_with_no_meeting_left_today_the_next_working_day_s_first_leads(
    reader: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "now", lambda: at(0, 18).astimezone(UTC))
    found = read(reader, API)["list"]
    near = (
        "tomorrow" if next_day() == 1 else f"on {at(next_day(), 9):%a} {at(next_day(), 9).day} {at(next_day(), 9):%b}"
    )
    assert found["lead"]["label"] == f"Next, {near}"
    first = found["lead"]["meeting"]
    # The day's first, its five points whole as the next meeting's prep.
    assert first["line"]["subject"] in ("Pilot sync", "Spring planning")
    assert not any(g["label"] == "Later today" for g in found["meetings"])


def test_once_the_meeting_under_way_ends_the_next_one_today_leads_alone_on_its_day(
    reader: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(views, "now", lambda: at(0, 13).astimezone(UTC))
    found = read(reader, API)["list"]
    assert (found["lead"]["label"], found["lead"]["meeting"]["line"]["subject"]) == ("Next", "Supplier follow-up")
    # It was today's last, so no Later today: the next working day's meetings come next.
    assert [g["key"] for g in found["meetings"]] == [(today() + timedelta(days=next_day())).isoformat()]


def test_the_items_due_go_overdue_then_today_then_back_from_a_snooze(reader: TestClient) -> None:
    due = read(reader, API)["list"]["due"]
    assert [(g["key"], g["label"], g["tone"]) for g in due] == [
        ("overdue", "Overdue", "late"),
        ("today", "Due today", "today"),
        ("woke", "Snooze ended", "today"),
    ]
    assert refs(due[0]) == ["A-0001"]
    assert refs(due[1]) == ["A-0002"]
    assert [r["title"] for r in due[2]["rows"]] == ["Renew the parking permit"]
    # Each a row the act bar and the quick buttons take, as on the Ledger.
    assert all(g["selectable"] and all(r["callable"] and r["quick"] for r in g["rows"]) for g in due)
    assert due[0]["rows"][0]["triage"] == "chase"


def test_the_due_rows_stop_at_the_ledger_s_first_page_and_count_all(
    reader: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(today_view, "DUE_SHOWN", 2)
    due = read(reader, API)["list"]["due"]
    assert [(g["key"], len(g["rows"]), g["total"]) for g in due] == [("overdue", 1, 1), ("today", 1, 1), ("woke", 0, 1)]
    assert not due[2]["selectable"], "no row drawn, nothing to tick"


def test_the_rest_is_later_today_then_the_next_working_day_without_cancelled_or_declined(reader: TestClient) -> None:
    found = read(reader, API)["list"]
    groups = found["meetings"]
    assert groups[0]["label"] == "Later today"
    later = groups[0]["meetings"]
    assert [m["line"]["subject"] for m in later] == ["Supplier follow-up"]
    # A meeting after the next lists its open points and items, a proposed one marked.
    assert [(p["text"], p["proposed"]) for p in later[0]["points"]] == [("The written offer", True)]
    assert [i["title"] for i in later[0]["items"]] == ["Read the written offer"]
    ahead = groups[1]
    assert ahead["key"] == (today() + timedelta(days=next_day())).isoformat()
    subjects = [m["line"]["subject"] for m in ahead["meetings"]]
    assert "Spring planning" in subjects
    assert not {"Cancelled planning", "Declined review", "Cancelled sync", "Venue walk"} & set(subjects)
    planning = next(m for m in ahead["meetings"] if m["line"]["subject"] == "Spring planning")
    # Cut to four points, as `docket ledger day` cuts them; the panel has the rest.
    assert (len(planning["points"]), planning["more_points"]) == (4, 1)
    # The stand-up this morning is over and yesterday's call is another day's: neither is on Today.
    every = [
        found["lead"]["meeting"]["line"]["subject"],
        *(m["line"]["subject"] for g in groups for m in g["meetings"]),
    ]
    assert "Morning stand-up" not in every and "Supplier call" not in every


def test_closed_today_lists_what_was_done_or_dropped_today(reader: TestClient) -> None:
    closed = read(reader, API)["list"]["closed"]
    assert closed["label"] == "Closed today" and not closed["selectable"]
    assert sorted((r["title"], r["state"]) for r in closed["rows"]) == [
        ("Print the old badges", "dropped"),
        ("Send the minutes", "done"),
    ]


def test_the_counts_say_the_day_in_one_line(reader: TestClient) -> None:
    counts = read(reader, API)["list"]["counts"]
    assert counts.startswith("1 overdue, 1 due today; 2 meetings left today, ")
    assert counts.endswith("; 1 snooze ended")


def test_an_item_back_from_a_snooze_counts_as_ended_not_overdue(client: TestClient, vault: Vault) -> None:
    """A snooze that ended is its own group whatever its due, and the counts say what the groups hold."""
    permit = next(g for g in read(client, API)["list"]["due"] if g["key"] == "woke")["rows"][0]["ref"]
    store.write(vault, [{"op": "set", "item": permit, "fields": {"due": day(-3)}}], by="user", via="cli")
    found = read(client, API)["list"]
    assert next(r for g in found["due"] for r in g["rows"] if r["ref"] == permit)["due"]["text"] == "snooze ended"
    groups = {g["key"]: [r["title"] for r in g["rows"]] for g in found["due"]}
    assert groups["woke"] == ["Renew the parking permit"] and "Renew the parking permit" not in groups["overdue"]
    assert found["counts"].startswith("1 overdue, 1 due today;") and found["counts"].endswith("; 1 snooze ended")


# -- the syncs --------------------------------------------------------------------------------------------------------


def test_each_live_source_says_its_last_sync_and_its_due_mark_is_the_sidebar_s(reader: TestClient) -> None:
    syncs = read(reader, API)["list"]["syncs"]
    assert [(s["source"], s["said"], s["due"], s["href"]) for s in syncs] == [
        ("chat", "synced 11:20", False, "/sync?source=chat"),
        ("mail", "never synced", True, "/sync?source=mail"),
    ]
    assert syncs[0]["title"] == f"Synced on test-box, {at(0, 9):%a} {today().day} {at(0, 9):%b} 11:20"
    assert read(reader, "/api/web/shell")["sync_due"] == [s["source"] for s in syncs if s["due"]]


def test_a_source_with_a_run_open_is_syncing_never_due(reader: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Runs, "snapshot", lambda _self: [SimpleNamespace(source="mail")])
    syncs = {s["source"]: s for s in read(reader, API)["list"]["syncs"]}
    assert (syncs["mail"]["said"], syncs["mail"]["due"], syncs["mail"]["running"]) == ("syncing now", False, True)
    assert read(reader, "/api/web/shell")["sync_due"] == []


def test_yesterday_s_and_older_marks_say_their_day(vault: Vault, client: TestClient) -> None:
    cursors.save(vault, "chat", cursors.Cursor(mark=at(-1, 17, 40).astimezone(UTC)))
    cursors.save(vault, "mail", cursors.Cursor(mark=at(-3, 8).astimezone(UTC)))
    syncs = {s["source"]: s for s in read(client, API)["list"]["syncs"]}
    # Yesterday 17:40 is under a day before noon: not due yet. Three days back is.
    assert (syncs["chat"]["said"], syncs["chat"]["due"]) == ("synced yesterday 17:40", False)
    old = at(-3, 8)
    assert (syncs["mail"]["said"], syncs["mail"]["due"]) == (f"synced {old:%a} {old.day} {old:%b}", True)


def test_the_list_says_when_it_goes_out_of_date(client: TestClient, vault: Vault) -> None:
    # The budget review ends at 12:30, before the follow-up starts or chat's sync falls due at 11:20 tomorrow.
    assert read(client, API)["list"]["until"] == at(0, 12, 30).isoformat()
    cursors.save(vault, "chat", cursors.Cursor(mark=at(-1, 12, 10).astimezone(UTC)))
    # Now chat falls due at 12:10, before the review ends.
    assert read(client, API)["list"]["until"] == at(0, 12, 10).astimezone(UTC).isoformat()


# -- the poll ---------------------------------------------------------------------------------------------------------


def test_today_s_version_moves_with_a_cursor_file_and_a_run_where_the_others_don_t(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = read(client, API)["version"]
    ledger = read(client, "/api/web/ledger")["version"]
    assert version != ledger, "Today's hashes the cursor files too"
    assert read(client, f"{CHANGED}?v={version}&page=today") == {"changed": False, "version": version}
    cursors.save(vault, "mail", cursors.Cursor(mark=at(0, 11, 50).astimezone(UTC)))
    moved = read(client, f"{CHANGED}?v={version}&page=today")
    assert moved["changed"] and moved["version"] == read(client, API)["version"]
    # The Ledger's version is the op files' alone, which the sync didn't touch.
    assert read(client, f"{CHANGED}?v={ledger}")["changed"] is False
    monkeypatch.setattr(Runs, "snapshot", lambda _self: [SimpleNamespace(source="chat")])
    assert read(client, f"{CHANGED}?v={moved['version']}&page=today")["changed"] is True


def test_a_vault_with_no_state_folder_reads_and_polls(client: TestClient, vault: Vault) -> None:
    """No machine's cursor file, not even the folder they live in: every live source is due, and the version holds."""
    shutil.rmtree(vault.root / cursors.STATE)
    found = read(client, API)
    assert [(s["source"], s["said"], s["due"]) for s in found["list"]["syncs"]] == [
        ("chat", "never synced", True),
        ("mail", "never synced", True),
    ]
    assert read(client, f"{CHANGED}?v={found['version']}&page=today")["changed"] is False


# -- the URL and the panel --------------------------------------------------------------------------------------------


def test_today_takes_no_filter_and_keeps_its_own_parameters(reader: TestClient) -> None:
    given = "q=room&stream=pilot&kind=task&who=Ada&k=200&state=closed&group=who&archived=1"
    assert read(reader, f"{API}?{given}")["canonical"] == "/today"
    assert read(reader, f"{API}/panel?open=A-0001&drop=1&suggest=1")["canonical"] == "/today?open=A-0001&drop=1"
    assert read(reader, f"{API}/panel?open=M-0003&drop=1&suggest=1")["canonical"] == "/today?open=M-0003&suggest=1"
    assert read(reader, f"{API}?at=A-0002&done=nothing")["canonical"] == "/today?at=A-0002"
    assert DayState(page="today", open="M-0003", text="notes/plan.md").url() == "/today?open=M-0003&text=notes/plan.md"
    assert read(reader, f"{API}?open=nope", 400)["error"].startswith("open is an A- or M- number")


def test_the_panel_is_an_item_s_or_a_meeting_s(reader: TestClient) -> None:
    item = read(reader, f"{API}/panel?open=A-0002")
    assert (item["item"]["ref"], item["meeting"], item["missing"]) == ("A-0002", None, "")
    assert item["item"]["meeting"]["ref"] == "M-0001", "the meeting it hangs on, as on the Ledger"
    meeting = read(reader, f"{API}/panel?open=M-0003&suggest=1")
    assert (meeting["item"], meeting["meeting"]["ref"]) == (None, "M-0003")
    assert meeting["meeting"]["suggestions"] is not None, "suggest=1 reads them, as on Meetings"
    # Opened by its id, as two meetings sharing a number after a merge are, it keeps suggest and reads them too.
    by_id = read(reader, f"{API}/panel?open={meeting['meeting']['id']}&suggest=1")
    assert by_id["canonical"] == f"/today?open={meeting['meeting']['id']}&suggest=1"
    assert (by_id["meeting"]["ref"], by_id["meeting"]["suggestions"]) == ("M-0003", meeting["meeting"]["suggestions"])
    assert [p["text"] for p in meeting["meeting"]["points"]] == ["Ben: the spring numbers"]
    gone = read(reader, f"{API}/panel?open=A-0999")
    assert (gone["item"], gone["meeting"]) == (None, None) and gone["missing"]


# -- writes and saves -------------------------------------------------------------------------------------------------


def test_a_call_from_today_is_one_batch_by_user_via_page_and_undoes(client: TestClient, vault: Vault) -> None:
    wrote = post(client, {"op": "triage", "items": ["A-0002"], "verdict": "me", "at": None})
    batch = [o for o in ops(vault) if o.get("batch") == wrote["done"]]
    assert [(o["op"], o["by"], o["via"], o.get("verdict")) for o in batch] == [("triage", "user", "page", "me")]
    assert wrote["notice"]["button"] == "Undo"
    due = read(client, API)["list"]["due"]
    assert due[1]["rows"][0]["triage"] == "me"
    undone = post(client, {"op": "undo", "batch": wrote["done"], "said": "undo"})
    assert undone["notice"]["text"].startswith("Undone.")


def test_a_bulk_from_today_s_act_bar_skips_what_it_doesn_t_take(client: TestClient) -> None:
    wrote = post(client, {"op": "reopen", "items": ["A-0001", "A-0006"], "bulk": True})
    assert wrote["skipped"] == ["A-0001"], "an open item doesn't reopen; the done one does"


def test_today_saves_an_item_s_field_and_a_meeting_s_outcome(client: TestClient, vault: Vault) -> None:
    saved = post(client, {"op": "set", "items": ["A-0002"], "fields": {"who": "Cleo Example"}}, SAVE)
    assert (saved["row"]["who"], saved["panel"]["who"]) == ("Cleo Example", "Cleo Example")
    outcome = post(client, {"op": "set", "items": ["M-0003"], "fields": {"outcome": "Numbers by Friday"}}, SAVE)
    assert outcome["panel"]["outcome"] == "Numbers by Friday" and outcome["line"]["ref"] == "M-0003"
    # A meeting named by its id saves as a meeting's too.
    by_id = read(client, f"{API}/panel?open=M-0003")["meeting"]["id"]
    notes = post(client, {"op": "set", "items": [by_id], "fields": {"notes": "Room 2 again"}}, SAVE)
    assert notes["panel"]["notes"] == "Room 2 again"
    assert post(client, {"op": "set", "items": ["M-0003"], "fields": {"title": "x"}}, SAVE, 400)["error"]
    written = [o for o in ops(vault) if o.get("via") == "page"]
    assert all(o["by"] == "user" for o in written) and len(written) == 3


def test_today_s_own_writes_answer_its_version_so_its_poll_stays_quiet(client: TestClient) -> None:
    """A save and a write from Today answer the version Today's read and poll take, cursor files and runs in it: a
    save that is the only change since the read refetches nothing, and a write that changes nothing leaves the poll
    quiet (DR-0710, DR-0711)."""
    version = read(client, API)["version"]
    saved = post(client, {"op": "set", "items": ["A-0002"], "fields": {"notes": "Room 2 is free"}}, SAVE)
    assert saved["before"] == version
    assert saved["version"] == read(client, API)["version"]
    meeting = post(client, {"op": "set", "items": ["M-0003"], "fields": {"notes": "Bring the numbers"}}, SAVE)
    assert meeting["before"] == saved["version"]
    # No call yet on A-0002, so taking its call away writes nothing.
    nothing = post(client, {"op": "triage", "items": ["A-0002"], "verdict": None})
    assert nothing["notice"]["text"] == "Nothing changed."
    assert read(client, f"{CHANGED}?v={nothing['version']}&page=today")["changed"] is False
    # The Ledger's own write answers the op files' version, as its read does.
    ledger = client.post(WRITE, json={"page": "ledger", "op": "triage", "items": ["A-0002"], "verdict": None},
                         headers=SAME).json()  # fmt: skip
    assert ledger["version"] == read(client, "/api/web/ledger")["version"]


# -- an empty day -----------------------------------------------------------------------------------------------------


def test_an_empty_day_says_so_and_still_names_its_syncs(tmp_path: Path) -> None:
    vault = empty_vault(tmp_path, live_config())
    with signed_in(vault) as client:
        found = read(client, API)["list"]
    assert (found["lead"], found["due"], found["meetings"], found["closed"]) == (None, [], [], None)
    assert found["empty"] == "Nothing on today"
    assert found["counts"] == "nothing due; no meeting left today"
    assert [(s["source"], s["due"]) for s in found["syncs"]] == [("chat", True), ("mail", True)]
