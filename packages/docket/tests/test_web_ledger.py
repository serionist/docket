"""The Ledger's JSON routes, signed in (DR-0664 to DR-0666, DR-0709 to DR-0711): its groups and their order, the
filters, the item panel, every write as one batch by user via page, a bulk through its dry run, undo and redo, the
refusals with the writer's words, the saves on change, the poll, and the materials' checks with the launcher patched,
so no test starts an app. Invented people only."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from day_kit import CHAT, LINK, PLAN, SAME, ZONE, add, day, day_template, day_vault, signed_in, today
from docket_testkit import AtConsole, commit_config, empty_vault, indexed, make_config, make_record, read_only
from docket_webkit import OURS, PORT, TOKEN
from ledger_kit import ADD_FIELDS, op, ulid, write_ops
from starlette.testclient import TestClient

from docket.forget import now as forget_now
from docket.ledger import ops as ledger_ops
from docket.ledger import store, views
from docket.ledger.store import LedgerError
from docket.service.app import create_app
from docket.service.sessions import COOKIE, Sessions
from docket.service.state import Settings
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.vault import Vault
from docket.web import day as day_routes
from docket.web import day_view, display
from docket.web.day_state import DayState, DayStateError

OPEN = ["A-0001", "A-0002", "A-0003", "A-0004", "A-0005"]
DUE = ["Overdue", "Due today", "Due this week", "Due later", "No due date"]
API = "/api/web/ledger"
# The days the app's date picker shows, as [day, today, text], which src/lib/dates.test.ts holds the app to.
DAYS = Path(__file__).parents[1] / "web" / "src" / "test" / "days.json"
WRITE = f"{API}/write"
SAVE = f"{API}/save"


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return day_vault(tmp_path)


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


@pytest.fixture(scope="module")
def seen(tmp_path_factory: pytest.TempPathFactory) -> Vault:
    """One copy of the day vault, its index built, for the tests that only read: a write into it fails the run
    (DR-0531)."""
    return Vault.open(read_only(indexed(tmp_path_factory, day_template(tmp_path_factory))))


@pytest.fixture
def reader(seen: Vault) -> Iterator[TestClient]:
    with signed_in(seen) as found:
        yield found


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """The launcher, patched: what would have opened, by name, and whether in its folder."""
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(day_routes, "launch", lambda path, *, reveal: seen.append((Path(path).name, reveal)))
    return seen


def read(client: TestClient, path: str = API, status: int = 200) -> Any:
    answer = client.get(path)
    assert answer.status_code == status, answer.text
    assert answer.headers["cache-control"] == "no-store"
    assert answer.headers["content-type"] == "application/json"
    return answer.json()


def listed(client: TestClient, query: str = "", status: int = 200) -> Any:
    return read(client, f"{API}{'?' if query else ''}{query}", status)


def panel(client: TestClient, query: str, status: int = 200) -> Any:
    return read(client, f"{API}/panel?{query}", status)


def rows(found: Any) -> list[str]:
    return [r["ref"] for g in found["list"]["groups"] for r in g["rows"]]


def row(found: Any, ref: str) -> Any:
    return next(r for g in found["list"]["groups"] for r in g["rows"] if r["ref"] == ref)


def groups(found: Any) -> list[str]:
    return [g["label"] for g in found["list"]["groups"]]


def post(client: TestClient, body: dict[str, Any], path: str = WRITE) -> Any:
    return client.post(path, json=body, headers=SAME)


def wrote(answer: Any) -> Any:
    assert answer.status_code == 200, answer.text
    return answer.json()


def refused(answer: Any) -> str:
    assert answer.status_code == 400, answer.text
    return str(answer.json()["error"])


def ops(vault: Vault) -> list[dict[str, Any]]:
    """Every op line in the vault, in its files' order."""
    out: list[dict[str, Any]] = []
    for f in sorted((vault.root / ledger_ops.OPS_DIR).glob("*.jsonl")):
        out += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


def write(vault: Vault, *changes: dict[str, Any]) -> store.Written:
    return store.write(vault, list(changes), by="user", via="cli")


def link_dir(link: Path, target: Path) -> None:
    """A folder link: a junction on Windows, where a symlink needs a privilege, a symlink elsewhere."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


# -- the list --------------------------------------------------------------------------------------------------------


def test_the_ledger_lists_the_open_items_by_due_and_counts_every_state(reader: TestClient, seen: Vault) -> None:
    found = listed(reader)
    assert found["canonical"] == "/ledger"
    assert found["version"] == day_routes.version(seen.root)
    assert found["today"] == today().isoformat()
    assert groups(found) == DUE
    assert [g["tone"] for g in found["list"]["groups"]] == ["late", "today", "", "", ""]
    assert rows(found) == OPEN
    assert found["list"]["counts"] == "5 open, 1 snoozed, 1 closed; 1 overdue"
    chips = [(c["label"], c["count"], c["on"]) for c in found["list"]["chips"]]
    assert chips == [("Open", 5, True), ("Snoozed", 1, False), ("Closed", 1, False), ("All", 7, False)]
    first = row(found, "A-0001")
    assert (first["triage"], first["due"]["text"], first["due"]["tone"]) == ("chase", "2d late", "late")
    assert first["callable"] is True
    # Chase is pressed, so its button clears it; the others set theirs, each with its key.
    quick = [(b["verdict"], b["writes"], b["key"], b["pressed"]) for b in first["quick"]]
    assert quick == [
        ("discuss", "discuss", "1", False), ("claude", "claude", "2", False), ("me", "me", "3", False),
        ("chase", None, "4", True),
    ]  # fmt: skip
    assert first["quick"][3]["title"] == "Chase: nudge the other side (4); again clears it"
    room = row(found, "A-0002")
    assert (room["meeting"], room["due"]["text"]) == ("M-0001", "today")
    # The act bar's verdicts as no row holds them, its snooze choices, and the day its date may take.
    assert [b["writes"] for b in found["verdicts"]] == ["discuss", "claude", "me", "chase"]
    assert [s["until"] for s in found["snooze"]] == [s.until.isoformat() for s in day_view.snooze_choices(today())]
    assert found["tomorrow"] == day(1)
    assert found["kinds"] == list(ledger_ops.KINDS)
    assert found["list"]["streams"] == ["ops", "pilot"]
    assert found["filters"] == {"q": "", "stream": "", "kind": "", "who": "", "k": 100, "state": "open",
                                "group": "due", "archived": False}  # fmt: skip
    assert found["notice"] is None


@pytest.mark.parametrize(
    ("query", "named", "shown"),
    [
        ("state=snoozed", ["Back"], ["A-0007"]),
        ("state=closed", ["Closed today"], ["A-0006"]),
        ("state=all", [*DUE, "Back", "Closed today"], [*OPEN, "A-0007", "A-0006"]),
        ("group=stream", ["ops", "pilot"], ["A-0003", "A-0004", "A-0005", "A-0001", "A-0002"]),
        ("group=kind", ["Replies owed", "Decisions", "Tasks", "Waiting on others"],
         ["A-0001", "A-0003", "A-0002", "A-0004", "A-0005"]),
        ("group=who", ["Ada Example", "Ben Sample"], ["A-0001", "A-0003", "A-0002", "A-0004", "A-0005"]),
        ("group=meeting", ["M-0001", "No meeting"], ["A-0002", "A-0001", "A-0003", "A-0004", "A-0005"]),
        ("state=all&group=who", ["Ada Example", "Ben Sample"],
         ["A-0001", "A-0003", "A-0006", "A-0007", "A-0002", "A-0004", "A-0005"]),
    ],
)  # fmt: skip
def test_each_state_and_grouping_lists_in_its_order(
    reader: TestClient, query: str, named: list[str], shown: list[str]
) -> None:
    found = listed(reader, query)
    assert [g.split()[0] if g.startswith("Back") else g for g in groups(found)] == named
    assert rows(found) == shown


@pytest.mark.parametrize(
    ("query", "shown"),
    [
        ("q=caterer", ["A-0005"]),
        # A log line, a meeting's subject, a material's label, every word in any order.
        ("q=thread&state=closed", ["A-0006"]),
        ("q=sync", ["A-0002"]),
        ("q=plan.md", ["A-0005"]),
        ("q=ROOM+book", ["A-0002"]),
        ("stream=pilot", ["A-0001", "A-0002"]),
        ("kind=task", ["A-0002", "A-0004"]),
        ("who=BEN", ["A-0002", "A-0004", "A-0005"]),
        ("q=zeppelin", []),
        ("q=%21%21", []),
    ],
)
def test_q_and_the_filters_narrow_the_list(reader: TestClient, query: str, shown: list[str]) -> None:
    found = listed(reader, query)
    assert rows(found) == shown
    if not shown:
        assert found["list"]["empty"] == "Nothing here matches."


def test_a_snooze_whose_day_has_come_leads_the_open_items(reader: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    later = datetime.combine(today() + timedelta(days=6), datetime.min.time(), ZONE).replace(hour=12)
    monkeypatch.setattr(views, "now", lambda: later.astimezone(UTC))
    found = listed(reader)
    assert groups(found)[0] == "Snooze ended"
    assert rows(found)[0] == "A-0007"
    assert row(found, "A-0007")["due"] == {"text": "snooze ended", "title": f"back since {day(5)}", "tone": "today"}
    assert found["list"]["counts"] == "6 open (1 with their snooze ended), 0 snoozed, 1 closed; 3 overdue"
    assert listed(reader, "state=snoozed")["list"]["empty"] == "Nothing is snoozed."


def test_an_archived_item_shows_only_when_asked(client: TestClient, vault: Vault) -> None:
    write(vault, {"op": "archive", "item": "A-0006"})
    assert listed(client, "state=closed")["list"]["empty"] == "Nothing is closed."
    found = listed(client, "state=closed&archived=1")
    assert rows(found) == ["A-0006"]
    assert found["canonical"] == "/ledger?state=closed&archived=1"
    closed = row(found, "A-0006")
    assert (closed["archived"], closed["callable"], closed["quick"]) == (True, False, [])
    # Its panel takes no call.
    shown = panel(client, "open=A-0006&archived=1")["panel"]
    assert (shown["archived"], shown["callable"], shown["verdicts"], shown["snooze"]) == (True, False, [], [])
    assert shown["state_line"].endswith(", archived")


def test_a_list_shows_100_rows_and_show_more_goes_past_the_views_500(client: TestClient, vault: Vault) -> None:
    adds = [{"op": "add", "force": True, "fields": {"kind": "task", "title": f"Chore {n}", "who": "Ben Sample",
                                                    "stream": "ops", "source": CHAT}} for n in range(600)]  # fmt: skip
    write(vault, *adds)
    found = listed(client)
    assert len(rows(found)) == 100
    assert (found["list"]["shown"], found["list"]["total"], found["list"]["more"]) == (100, 605, 200)
    assert len(rows(listed(client, "k=550"))) == 550
    wide = listed(client, "k=700")
    assert len(rows(wide)) == 605
    assert wide["list"]["more"] is None


def test_an_empty_ledger_says_how_items_come(tmp_path: Path) -> None:
    with signed_in(empty_vault(tmp_path, make_config())) as client:
        found = listed(client)
        assert found["list"]["groups"] == []
        assert found["list"]["empty"] == (
            "The ledger is empty. Claude adds what you owe and await as it syncs, or add an item here."
        )
        assert listed(client, "stream=pilot")["list"]["empty"] == "Nothing here matches."


@pytest.mark.parametrize(
    ("query", "said"),
    [
        ("k=0", "k is a whole number"),
        ("k=%C2%B2", "k is a whole number"),
        ("state=nope", "state is one of"),
        ("group=nope", "group is one of"),
        ("open=nope", "open is an A- or M- number"),
        ("at=nope", "at is an A- or M- number"),
        ("stream=Not+A+Slug", "stream is a stream's name"),
        ("kind=nope", "kind is one of"),
        ("archived=2", "archived is 1"),
        ("done=nope", "done is what the last change did"),
        ("q=" + "x" * 201, "q is at most 200"),
        ("who=" + "x" * 121, "who is at most 120"),
        ("open=A-0001&text=" + "x" * 1001, "text is a material's ref"),
        ("done=nothing" + "&skipped=A-1" * 501, "skipped names at most 500"),
        ("done=nothing&said=nope", "said is undo or redo"),
    ],
)
def test_a_value_the_url_can_t_hold_is_refused_with_its_words(reader: TestClient, query: str, said: str) -> None:
    assert listed(reader, query, status=400)["error"].startswith(said)
    assert panel(reader, query, status=400)["error"].startswith(said)


def test_the_state_reads_into_one_canonical_url(reader: TestClient) -> None:
    state = DayState.read({"open": "a-12", "at": "A-0001", "k": "100", "q": "  two   words ", "drop": "1"})
    assert state.url() == "/ledger?open=A-0012&q=two+words&drop=1"
    assert DayState.read({"open": "M-7", "drop": "1", "text": "a.md"}).url() == "/ledger?open=M-0007"
    # What a write said never stays in the canonical URL: the notice is the page's own.
    assert DayState.read({"at": "A-0002", "done": "nothing", "said": "undo", "skipped": "A-1"}).url() == (
        "/ledger?at=A-0002"
    )
    assert listed(reader, "open=a-1&q=+Ada++dates+&drop=1&x=y")["canonical"] == "/ledger?open=A-0001&q=Ada+dates&drop=1"
    assert panel(reader, "open=a-1&state=all")["canonical"] == "/ledger?open=A-0001&state=all"
    with pytest.raises(DayStateError, match="page is one of"):
        day_routes.page_of("nope")


def test_a_url_that_still_names_a_write_shows_its_notice(client: TestClient, vault: Vault) -> None:
    batch = write(vault, {"op": "triage", "item": "A-0003", "verdict": "me"}).ops[0].batch
    found = listed(client, f"done={batch}&said=redo")
    assert found["canonical"] == "/ledger"
    assert found["notice"] == {"kind": "info", "text": "Redone.", "lines": ["A-0003: triage: me"], "button": "Undo",
                               "batch": batch, "then": "undo"}  # fmt: skip
    assert listed(client, "done=nothing&skipped=A-6")["notice"]["text"] == (
        "Nothing changed. Skipped A-0006: the change doesn't take them as they stand."
    )


# -- the item panel ----------------------------------------------------------------------------------------------------


def test_an_item_opens_beside_the_list_with_its_call_fields_materials_source_and_history(reader: TestClient) -> None:
    found = panel(reader, "open=A-0005")
    assert found["canonical"] == "/ledger?open=A-0005"
    item = found["panel"]
    assert (item["ref"], item["title"], item["kind"], item["stream"]) == ("A-0005", "Quote from the caterer",
                                                                          "waiting", "ops")  # fmt: skip
    assert item["state_line"] == "open, no call yet"
    assert [b["writes"] for b in item["verdicts"]] == ["discuss", "claude", "me", "chase"]
    assert (item["notes"], item["description"], item["outcome"], item["due"]) == (
        "Asked twice.", "The full quote with the menu", "", ""
    )  # fmt: skip
    assert [(m["kind"], m["label"]) for m in item["materials"]] == [
        ("text", "The plan"), ("path", "notes/costs.xlsx"), ("url", "The offer"),
    ]  # fmt: skip
    assert item["materials"][2]["href"] == LINK
    # Its source is a record of the vault's chat source: Search's panel opens on its raw.
    assert item["source"] == {"ref": CHAT, "label": CHAT, "kind": "record", "href": f"/search?hit=r:{CHAT}"}
    lines = [(h["line"], h["button"], h["by"], h["via"]) for h in item["history"]]
    assert lines == [
        ("material added: The offer", "Undo", "claude", "cli"), ("material added: notes/costs.xlsx", "Undo", "claude",
         "cli"), ("material added: The plan", "Undo", "claude", "cli"), ("added", None, "claude", "cli"),
    ]  # fmt: skip
    # A time as the page shows it, in the owner's zone, and as a time element's.
    first = item["history"][0]
    stamp = datetime.fromisoformat(first["stamp"])
    assert first["when"] == f"{stamp.astimezone(ZONE).day} {stamp.astimezone(ZONE):%b %Y %H:%M}"
    assert item["facts"] == f"opened {today().isoformat()} by claude; open 0 days"
    assert item["tomorrow"] == day(1)
    assert (item["callable"], item["closed"], found["missing"]) == (True, False, "")


def test_an_item_on_a_meeting_shows_the_meeting_with_its_points(client: TestClient, vault: Vault) -> None:
    box = panel(client, "open=A-0002")["panel"]["meeting"]
    assert (box["ref"], box["subject"]) == ("M-0001", "Pilot sync")
    assert box["when"].endswith("10:00-10:30")
    assert [(p["n"], p["state"], p["text"], p["proposed"]) for p in box["points"]] == [
        (1, "open", "The venue's dates", False),
        (2, "open", "Who books the room", True),
    ]
    # The meeting's add undone: the item no longer hangs on a meeting that stands, and the panel shows none.
    meeting = next(o for o in ops(vault) if o["op"] == "meeting")
    write(vault, {"op": "undo", "of": meeting["id"]})
    assert panel(client, "open=A-0002")["panel"]["meeting"] is None


@pytest.mark.parametrize(
    ("ref", "said"),
    [
        ("A-0099", "no item A-0099"),
        ("M-0001", "M-0001 is a meeting; its panel is on the Meetings page."),
    ],
)
def test_a_panel_that_can_t_open_says_why(reader: TestClient, ref: str, said: str) -> None:
    found = panel(reader, f"open={ref}")
    assert (found["panel"], found["missing"]) == (None, said)


def test_a_meeting_s_id_opens_no_item_panel(reader: TestClient, seen: Vault) -> None:
    meeting = next(o for o in ops(seen) if o["op"] == "meeting")["id"]
    found = panel(reader, f"open={meeting}")
    assert found["missing"] == f"{meeting} is a meeting; its panel is on the Meetings page."


def test_a_panel_with_nothing_open_has_nothing_to_say(reader: TestClient) -> None:
    assert read(reader, f"{API}/panel") == {"canonical": "/ledger", "panel": None, "missing": ""}
    # The drop form's flag stays with an open item only.
    assert panel(reader, "open=A-0001&drop=1")["canonical"] == "/ledger?open=A-0001&drop=1"
    assert panel(reader, "drop=1")["canonical"] == "/ledger"


def test_a_closed_item_reopens_and_a_snoozed_one_wakes(reader: TestClient) -> None:
    closed = panel(reader, "open=A-0006&state=closed")["panel"]
    assert (closed["callable"], closed["closed"], closed["verdicts"]) == (False, True, [])
    assert closed["state_line"].startswith("done ")
    assert closed["facts"].endswith("done after 0 days")
    snoozed = panel(reader, "open=A-0007&state=snoozed")["panel"]
    assert (snoozed["callable"], snoozed["state"]) == (True, "snoozed")
    assert snoozed["state_line"].startswith("snoozed to")


# -- the writes: one batch each, by user via page (DR-0710) ------------------------------------------------------------


def test_a_verdict_writes_one_batch_by_user_via_page_and_names_where_the_selection_goes(
    client: TestClient, vault: Vault
) -> None:
    body = {"op": "triage", "items": ["A-0002"], "verdict": "claude", "at": "A-0003"}
    answer = wrote(post(client, body))
    last = ops(vault)[-1]
    assert (last["op"], last["verdict"], last["by"], last["via"]) == ("triage", "claude", "user", "page")
    assert (answer["done"], answer["at"], answer["skipped"], answer["added"]) == (last["batch"], "A-0003", [], None)
    assert answer["version"] == day_routes.version(vault.root)
    assert answer["notice"] == {"kind": "info", "text": "Saved.", "lines": ["A-0002: triage: claude"],
                                "button": "Undo", "batch": last["batch"], "then": "undo"}  # fmt: skip
    # Written twice, it changes nothing the second time and says so, with no Undo.
    again = wrote(post(client, body))
    assert (again["done"], again["notice"]["text"], again["notice"]["button"]) == (None, "Nothing changed.", None)
    assert ops(vault)[-1]["id"] == last["id"]


def test_a_clash_s_rows_are_named_by_their_ids_as_a_batch_takes_them(client: TestClient, vault: Vault) -> None:
    """Another clone's add claims A-0001 too: both rows are named by their ids until the renumber, the refs a batch
    takes (DR-0613), and a call on one writes, the writer renumbering it first."""
    theirs = op("add", 0, number=1, machine="laptop", fields={**ADD_FIELDS, "title": "Their copy of the dates"})
    write_ops(vault.root, [theirs])
    ours = next(o["id"] for o in ops(vault) if o["op"] == "add" and o["fields"]["title"] == "Send Ada the dates")
    shown = rows(listed(client))
    assert {ours, ulid(0)} <= set(shown)
    assert "A-0001" not in shown
    answer = wrote(post(client, {"op": "triage", "items": [ours], "verdict": "me"}))
    assert [o["op"] for o in ops(vault) if o.get("batch") == answer["done"]] == ["renumber", "triage"]
    assert panel(client, f"open={ours}")["panel"]["id"] == ours


def test_a_row_s_call_says_where_the_selection_goes_without_a_panel(client: TestClient, vault: Vault) -> None:
    answer = wrote(post(client, {"op": "done", "items": ["A-0001"], "at": "A-0002"}))
    assert answer["at"] == "A-0002"
    assert ops(vault)[-1]["op"] == "done"
    # A write that isn't a call moves nothing.
    logged = wrote(post(client, {"op": "log", "items": ["A-0003"], "note": "Ada called", "at": "A-0004"}))
    assert logged["at"] is None


def test_a_bulk_gives_each_op_the_rows_it_takes_and_names_the_rest(client: TestClient, vault: Vault) -> None:
    answer = wrote(post(client, {"op": "triage", "verdict": "me", "items": ["A-0001", "A-0003", "A-0006"]}))
    assert answer["skipped"] == ["A-0006"]
    assert [(o["op"], o["verdict"]) for o in ops(vault) if o.get("batch") == answer["done"]] == [
        ("triage", "me"), ("triage", "me"),
    ]  # fmt: skip
    assert answer["notice"]["text"] == "Saved. Skipped A-0006: the change doesn't take them as they stand."
    # Reopen over open and closed rows reopens the closed one; over open rows only, the writer refuses it whole.
    reopened = wrote(post(client, {"op": "reopen", "items": ["A-0001", "A-0006"]}))
    assert reopened["skipped"] == ["A-0001"]
    assert ops(vault)[-1]["op"] == "reopen"
    assert "A-0001 is already open" in refused(post(client, {"op": "reopen", "items": ["A-0001", "A-0003"]}))


def test_one_ticked_row_runs_as_a_bulk_as_many_do(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The act bar answers the same for one ticked row as for many: a dry run first, the rows it takes written, and a
    row it doesn't take refused as a bulk the writer refuses whole is (DR-0710). A row's or the panel's own call is one
    item, with no dry run."""
    real = store.write
    dry: list[bool] = []

    def spied(*args: Any, **keys: Any) -> store.Written:
        dry.append(bool(keys.get("dry_run")))
        return real(*args, **keys)

    monkeypatch.setattr(store, "write", spied)
    one = post(client, {"op": "reopen", "items": ["A-0001"], "bulk": True})
    many = post(client, {"op": "reopen", "items": ["A-0001", "A-0003"], "bulk": True})
    assert refused(one) == refused(many) == "A-0001 is already open"
    assert dry == [True, True]
    assert wrote(post(client, {"op": "triage", "verdict": "me", "items": ["A-0003"], "bulk": True}))["done"]
    assert dry[2:] == [True, False]
    assert wrote(post(client, {"op": "triage", "verdict": "me", "items": ["A-0004"]}))["done"]
    assert dry[4:] == [False]


def test_a_drop_with_no_why_is_refused_with_the_writer_s_words_and_nothing_written(
    client: TestClient, vault: Vault
) -> None:
    before = len(ops(vault))
    answer = post(client, {"op": "drop", "items": ["A-0003"], "note": "", "outcome": "We met in person"})
    assert refused(answer) == "a drop needs a note: why"
    assert answer.json()["alikes"] == []
    assert refused(post(client, {"op": "snooze", "items": ["A-0001"], "until": day(-5), "bulk": True}))
    assert len(ops(vault)) == before


def test_each_panel_write_makes_its_change(client: TestClient, vault: Vault) -> None:
    def sent(**body: Any) -> dict[str, Any]:
        wrote(post(client, {"items": ["A-0004"], **body}))
        return ops(vault)[-1]

    # An item's fields go as fields, so its who never reads as the page's who filter.
    assert sent(op="set", fields={"who": "Cleo Example"})["fields"] == {"who": "Cleo Example"}
    assert sent(op="set", fields={"due": ""})["fields"] == {"due": None}
    assert sent(op="set", fields={"due": day(4), "nope": "x"})["fields"] == {"due": day(4)}
    assert sent(op="notes", notes="Ask the venue first")["fields"] == {"notes": "Ask the venue first"}
    assert sent(op="log", note="  Ben called back ")["note"] == "Ben called back"
    added = sent(op="material", ref="notes\\plan.md", label="The plan")
    assert (added["op"], added["ref"], added["label"]) == ("material", PLAN, "The plan")
    assert sent(op="material", ref=PLAN, remove=True)["remove"] is True
    assert sent(op="snooze", until=day(9))["until"] == day(9)
    closing = post(client, {"op": "done", "items": ["A-0004"], "note": "sent it", "outcome": "Booked for May"})
    assert wrote(closing)["notice"]["text"] == "Saved."
    assert any(o["op"] == "done" and o.get("note") == "sent it" for o in ops(vault)[-2:])
    assert any(o.get("fields") == {"outcome": "Booked for May"} for o in ops(vault)[-2:])


def test_a_new_item_opens_and_a_look_alike_asks_first(client: TestClient, vault: Vault) -> None:
    fields = {"title": "Order the badges", "who": "Ada Example", "kind": "task", "stream": "ops", "due": day(2)}
    answer = wrote(post(client, {"op": "add", "fields": {**fields, "description": " ", "source": ""}}))
    added = ops(vault)[-1]
    assert answer["added"] == "A-0008"
    assert (added["op"], added["by"], added["via"]) == ("add", "user", "page")
    assert added["fields"]["source"].startswith(f"page:{today().isoformat()}T")
    assert "description" not in added["fields"]
    # One that looks like an open item names it, its row ready to link, beside Add anyway.
    alike = {**fields, "title": "Order the badges, please"}
    refusal = post(client, {"op": "add", "fields": alike})
    assert refused(refusal).startswith("looks like an open item already: A-0008 Order the badges")
    assert [r["ref"] for r in refusal.json()["alikes"]] == ["A-0008"]
    forced = wrote(post(client, {"op": "add", "fields": {**alike, "source": LINK}, "force": True}))
    assert forced["added"] == "A-0009"
    assert ops(vault)[-1]["fields"]["source"] == LINK


def test_a_pointer_into_a_never_store_container_is_refused_naming_the_field(client: TestClient, vault: Vault) -> None:
    """A new item's source or a material's ref in a container the never-store list names is the writer's refusal on
    the page: its words, naming the field and never the value, and nothing written. A material already there still
    comes off, since a removal adds no pointer (DR-0690)."""
    listed_ref = "slack:D0PRIVATE/1791000005.000100"
    write(vault, {"op": "material", "item": "A-0004", "ref": listed_ref, "label": "the thread"})
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path)
    config.never_store.containers = ["D0PRIVATE"]
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    commit_config(vault.root)
    before = len(ops(vault))
    new = {"title": "Answer the thread", "who": "Ada Example", "kind": "reply", "source": f"r:{listed_ref}#2"}
    assert refused(post(client, {"op": "add", "fields": new})) == (
        "source points into a never-store container; leave it out"
    )
    material = {"op": "material", "items": ["A-0004"], "ref": "slack:D0PRIVATE/1791000006.000100", "label": "x"}
    assert refused(post(client, material)) == "ref points into a never-store container; leave it out"
    assert len(ops(vault)) == before
    wrote(post(client, {"op": "material", "items": ["A-0004"], "ref": listed_ref, "remove": True}))
    assert (ops(vault)[-1]["ref"], ops(vault)[-1]["remove"]) == (listed_ref, True)


def test_a_shell_a_forget_left_refuses_its_text_naming_its_number(
    client: TestClient, vault: Vault, owner: Callable[..., AtConsole]
) -> None:
    """An item whose source a forget took is a shell: the writer refuses its text and its log, naming its number,
    and the page shows those words beside the field (DR-0696)."""
    record = make_record(2)
    vault.write_run("chat", [record])
    number = write(vault, {"op": "add", "force": True, "fields": {**ADD_FIELDS, "source": record.rid}}).labels
    shell = next(iter(number.values()))
    owner("code")
    assert forget_now.forget(vault, "rids", [record.rid]).applied is not None
    title = post(client, {"op": "set", "items": [shell], "fields": {"title": "Ada again"}}, SAVE)
    assert refused(title) == f"{shell} is a shell a forget left; it takes no title"
    log = post(client, {"op": "log", "items": [shell], "note": "Ada again"})
    assert refused(log) == f"{shell} is a shell a forget left; it takes no log"
    # A drop still goes, its why the marker.
    assert wrote(post(client, {"op": "drop", "items": [shell], "note": "Forgotten"}))["done"]


def test_the_notice_undoes_the_batch_and_then_redoes_it(client: TestClient) -> None:
    done = wrote(post(client, {"op": "done", "items": ["A-0002"], "at": "A-0003"}))["done"]
    undone = wrote(post(client, {"op": "undo", "batch": done, "said": "undo"}))
    assert undone["notice"]["text"] == "Undone."
    assert undone["notice"]["lines"] == ["A-0002: undo: done"]
    assert (undone["notice"]["button"], undone["notice"]["then"]) == ("Redo", "redo")
    assert "A-0002" in rows(listed(client))
    redone = wrote(post(client, {"op": "undo", "batch": undone["done"], "said": "redo"}))
    assert (redone["notice"]["text"], redone["notice"]["button"]) == ("Redone.", "Undo")
    assert "A-0002" not in rows(listed(client))


def test_a_history_line_undoes_by_its_op_and_the_undo_s_own_line_redoes(client: TestClient) -> None:
    history = panel(client, "open=A-0001")["panel"]["history"]
    assert [(h["line"], h["button"]) for h in history] == [("triage: chase", "Undo"), ("added", None)]
    answer = wrote(post(client, {"op": "undo", "of": history[0]["op"], "said": "undo"}))
    assert answer["notice"]["text"] == "Undone."
    lines = panel(client, "open=A-0001")["panel"]["history"]
    assert [(h["line"], h["button"]) for h in lines] == [
        ("undo: triage: chase", "Redo"), ("triage: chase", None), ("added", None),
    ]  # fmt: skip
    assert lines[1]["undone_by"]
    redone = wrote(post(client, {"op": "undo", "of": lines[0]["op"], "said": "redo"}))
    assert redone["notice"]["text"] == "Redone."
    assert redone["notice"]["lines"] == ["A-0001: undo: undo"]


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ({"op": "nope", "items": ["A-0001"]}, "op is one of"),
        ({"op": "triage", "verdict": "me"}, "tick a row, or open an item, first"),
        ({"op": "set", "items": ["A-0001"]}, "set takes a field"),
        ({"op": "log", "items": ["A-0001", "A-0002"], "note": "x"}, "log takes one item"),
        ({"op": "done", "items": ["nope"]}, "item is an A- or M- number"),
        ({"op": "done", "items": ["A-0001"], "page": "nope"}, "page is one of"),
        ({"op": "log", "items": ["A-0001"], "at": "nope"}, "at is an A- or M- number"),
    ],
)
def test_a_body_that_doesn_t_read_is_refused_with_its_words(
    client: TestClient, body: dict[str, Any], said: str
) -> None:
    assert refused(post(client, body)).startswith(said)


def test_a_body_of_the_wrong_shape_is_refused_before_it_reads(client: TestClient, vault: Vault) -> None:
    before = len(ops(vault))
    for body in ({"op": "done", "items": ["A-0001"], "k": "abc"}, {"items": ["A-0001"]}, {"op": "done", "items": 3}):
        assert post(client, body).status_code == 422, body
    assert len(ops(vault)) == before


def test_a_write_the_writer_fails_is_refused_with_its_words(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(*_: Any, **__: Any) -> Any:
        raise LedgerError("the ops span two files; nothing written")

    monkeypatch.setattr(store, "write", failing)
    assert refused(post(client, {"op": "done", "items": ["A-0001"]})) == "the ops span two files; nothing written"


def test_a_write_takes_a_json_body_the_session_and_the_guard_s_yes(client: TestClient) -> None:
    form = {**SAME, "Content-Type": "application/x-www-form-urlencoded"}
    for path in (WRITE, SAVE, f"{API}/open"):
        answer = client.post(path, content=b"op=done&items=A-0001", headers=form)
        assert answer.status_code == 415, path
        assert answer.json()["detail"] == "a write's body is application/json"
    # A write with the cookie and no Origin isn't the page's; one with the token is never the page's.
    assert client.post(WRITE, json={"op": "done", "items": ["A-0001"]}).status_code == 403
    token = {**SAME, "Authorization": f"Bearer {TOKEN}"}
    assert client.post(WRITE, json={"op": "done", "items": ["A-0001"]}, headers=token).status_code == 403


def test_a_body_past_the_cap_is_refused_whatever_its_length_says(client: TestClient, vault: Vault) -> None:
    """413 when the Content-Length is over the cap, when there is none and the body streams, and when it says less
    than the body sends; nothing is written (DR-0709)."""
    before = len(ops(vault))
    big = json.dumps({"op": "log", "items": ["A-0001"], "note": "x" * (300 * 1024)}).encode()
    headers = {**SAME, "Content-Type": "application/json"}
    said = client.post(WRITE, content=big, headers=headers)
    assert (said.status_code, said.json()) == (413, {"detail": "a write's body is at most 256 KB"})
    chunks = (big[at : at + 8192] for at in range(0, len(big), 8192))
    assert client.post(SAVE, content=chunks, headers=headers).status_code == 413
    assert client.post(WRITE, content=big, headers={**headers, "Content-Length": "40"}).status_code == 413
    assert len(ops(vault)) == before
    # Under the cap, a streamed body is handed on whole.
    small = json.dumps({"op": "log", "items": ["A-0001"], "note": "y" * (200 * 1024)}).encode()
    pieces = (small[at : at + 8192] for at in range(0, len(small), 8192))
    assert client.post(WRITE, content=pieces, headers=headers).status_code == 200
    assert ops(vault)[-1]["note"] == "y" * (200 * 1024)


# -- saves on change ---------------------------------------------------------------------------------------------------


def test_a_save_answers_the_row_the_panel_and_the_version(client: TestClient, vault: Vault) -> None:
    title = {"op": "set", "items": ["A-0003"], "fields": {"title": "Pick the venue for May"}}
    answer = wrote(post(client, title, SAVE))
    assert (answer["row"]["ref"], answer["row"]["title"]) == ("A-0003", "Pick the venue for May")
    assert answer["panel"]["title"] == "Pick the venue for May"
    assert answer["panel"]["history"][0]["line"] == "title: Pick the venue for May"
    assert answer["version"] == day_routes.version(vault.root)
    notes = wrote(post(client, {"op": "notes", "items": ["A-0003"], "notes": "Two venues left"}, SAVE))
    assert notes["before"] == answer["version"]
    assert notes["panel"]["notes"] == "Two venues left"
    assert ops(vault)[-1]["fields"] == {"notes": "Two venues left"}
    # The row's title is cut as the list cuts it; the panel's is whole.
    long = "Pick the venue " + "and the caterer " * 6
    saved = wrote(post(client, {"op": "set", "items": ["A-0003"], "fields": {"title": long}}, SAVE))
    assert saved["row"]["title"] == views.cut(long) and saved["row"]["title"].endswith("…")
    assert saved["panel"]["title"] == long.strip()


def test_a_save_answers_the_version_from_before_its_own_write(client: TestClient, vault: Vault) -> None:
    """A write from the CLI while the owner types moves the version the save's write starts from, so the page, whose
    read is older, knows it missed one; a save straight after its read starts from the read's version (DR-0710)."""
    read_at = listed(client)["version"]
    first = wrote(post(client, {"op": "set", "items": ["A-0003"], "fields": {"who": "Cleo Example"}}, SAVE))
    assert first["before"] == read_at
    assert first["version"] != read_at
    write(vault, {"op": "set", "item": "A-0004", "fields": {"title": "Plan the spring visit, from the CLI"}})
    moved = day_routes.version(vault.root)
    second = wrote(post(client, {"op": "notes", "items": ["A-0003"], "notes": "typed while the CLI wrote"}, SAVE))
    assert second["before"] == moved != first["version"]
    assert second["version"] == day_routes.version(vault.root)


def test_a_refused_save_says_why(client: TestClient) -> None:
    assert refused(post(client, {"op": "set", "items": ["A-0003"], "fields": {"title": ""}}, SAVE)) == (
        "title can't be cleared"
    )
    assert refused(post(client, {"op": "done", "items": ["A-0003"]}, SAVE)) == "a save sets a field of an item"
    nothing = post(client, {"op": "set", "fields": {"title": "x"}, "items": ["nope"]}, SAVE)
    assert refused(nothing).startswith("item is an A- or M- number")


def test_a_save_whose_item_is_gone_by_its_read_says_so(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    real = day_routes.gather

    def gone(vault: Vault, state: DayState, **keys: Any) -> day_routes.Facts:
        found = real(vault, state, **keys)
        found.shown, found.missing = None, "A-0003 was undone"
        return found

    monkeypatch.setattr(day_routes, "gather", gone)
    answer = post(client, {"op": "set", "items": ["A-0003"], "fields": {"who": "Cleo Example"}}, SAVE)
    assert refused(answer) == "A-0003 was undone"


# -- the poll (DR-0711) ----------------------------------------------------------------------------------------------


def test_the_poll_says_nothing_changed_until_an_op_file_does(client: TestClient, vault: Vault) -> None:
    version = listed(client)["version"]
    assert read(client, f"{API}/changed?v={version}") == {"changed": False, "version": version}
    write(vault, {"op": "log", "item": "A-0001", "note": "Ada called"})
    moved = read(client, f"{API}/changed?v={version}")
    assert moved == {"changed": True, "version": day_routes.version(vault.root)}


def test_without_a_vault_the_reads_say_so_and_the_poll_is_quiet() -> None:
    sessions = Sessions()
    app = create_app(Settings(port=PORT, vault=None, token=lambda: TOKEN), sessions=sessions)
    with TestClient(app, base_url=OURS, follow_redirects=False) as client:
        started = sessions.redeem(sessions.new_code())
        assert started is not None
        client.cookies.set(COOKIE, started)
        assert read(client, f"{API}/changed?v=x") == {"changed": False, "version": "x"}
        assert "the service has no vault" in listed(client, status=400)["error"]
        assert "the service has no vault" in refused(post(client, {"op": "done", "items": ["A-0001"]}))


def test_the_version_is_the_same_without_an_op_folder(tmp_path: Path) -> None:
    assert day_routes.version(tmp_path) == day_routes.version(tmp_path / "nowhere")


# -- materials: the viewer and the launcher (DR-0664) -------------------------------------------------------------


def test_a_text_material_shows_in_the_panel_s_viewer(client: TestClient, vault: Vault) -> None:
    found = read(client, f"{API}/text?open=A-0005&text={PLAN}")
    assert (found["ref"], found["text"].split(), found["cut"]) == (PLAN, ["#", "The", "plan", "Book", "the", "room",
                                                                          "first."], False)  # fmt: skip
    # A long one shows its first 256 KB, cut.
    (vault.home.top / "notes" / "long.md").write_text("x" * (300 * 1024), encoding="utf-8")
    write(vault, {"op": "material", "item": "A-0005", "ref": "notes/long.md"})
    long = read(client, f"{API}/text?open=A-0005&text=notes/long.md")
    assert (long["cut"], len(long["text"])) == (True, 256 * 1024)


@pytest.mark.parametrize(
    ("ref", "said"),
    [
        ("notes/other.md", "A-0005 has no material notes/other.md"),
        ("notes/costs.xlsx", "a .xlsx file doesn't open from the page; open it yourself"),
        ("notes", "notes is a folder; show it in its folder instead"),
        ("notes/gone.md", "notes/gone.md isn't there"),
        ("../outside.md", "../outside.md doesn't open from the page: it has an empty, . or .. part"),
    ],
)
def test_a_text_material_that_can_t_show_says_why(client: TestClient, vault: Vault, ref: str, said: str) -> None:
    if ref not in ("notes/other.md", "notes/costs.xlsx"):
        write(vault, {"op": "material", "item": "A-0005", "ref": ref})
    assert read(client, f"{API}/text?open=A-0005&text={ref}", status=400)["error"] == said


def test_a_viewer_url_that_can_t_read_says_why(reader: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert read(reader, f"{API}/text?open=A-0005", status=400)["error"].startswith("text names")
    assert read(reader, f"{API}/text?open=nope&text=x", status=400)["error"].startswith("open is an")
    # A meeting's materials show too, and only those it names.
    assert read(reader, f"{API}/text?open=M-0001&text=x", status=400)["error"] == "M-0001 has no material x"
    assert read(reader, f"{API}/text?open=A-0099&text=x", status=400)["error"] == "no item A-0099"
    real = Path.open

    def locked(self: Path, *args: Any, **keys: Any) -> Any:
        if self.name == "plan.md":
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **keys)

    monkeypatch.setattr(Path, "open", locked)
    assert read(reader, f"{API}/text?open=A-0005&text={PLAN}", status=400)["error"] == (
        f"{PLAN} doesn't read: Permission denied"
    )


def open_material(client: TestClient, **body: Any) -> Any:
    return post(client, {"item": "A-0005", "ref": PLAN, **body}, f"{API}/open")


def test_a_material_opens_with_its_app_or_in_its_folder(client: TestClient, launched: list[tuple[str, bool]]) -> None:
    assert wrote(open_material(client))["notice"]["text"] == "Opened it with its app."
    shown = wrote(open_material(client, ref="notes/costs.xlsx", reveal=True))
    assert (shown["notice"]["text"], shown["notice"]["button"]) == ("Shown in its folder.", None)
    assert launched == [("plan.md", False), ("costs.xlsx", True)]
    assert refused(open_material(client, page="nope")).startswith("page is one of")
    assert refused(open_material(client, item="nope")).startswith("item is an A- or M- number")


def test_only_a_checked_path_inside_the_repo_opens(
    client: TestClient, vault: Vault, launched: list[tuple[str, bool]], tmp_path: Path
) -> None:
    top = vault.home.top
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.pdf").write_bytes(b"%PDF")
    (top / "notes" / "PLAN.PDF").write_bytes(b"%PDF")
    (top / "notes" / "run.exe").write_bytes(b"MZ")
    link_dir(top / "notes" / "out", outside)
    link_dir(top / "notes" / "git", top / ".git")
    refusals = {
        "../outside/x.pdf": "it has an empty, . or .. part",
        "C:x.pdf": "it holds a colon",
        "//host/share/x.pdf": "it is an absolute path or a network share's",
        "notes/plan.pdf.": "a part of it ends in a dot or a space",
        "notes/run.exe": "a .exe file doesn't open from the page",
        "notes/gone.pdf": "notes/gone.pdf isn't there",
        "notes/out/x.pdf": "it leads outside the repo",
        "notes/git/HEAD": "it leads into .git",
    }
    write(vault, *({"op": "material", "item": "A-0004", "ref": ref} for ref in [*refusals, "notes/PLAN.PDF", "notes"]))
    for ref, said in refusals.items():
        assert said in refused(open_material(client, item="A-0004", ref=ref)), ref
    # An upper-case suffix opens, and so does a folder.
    wrote(open_material(client, item="A-0004", ref="notes/PLAN.PDF"))
    wrote(open_material(client, item="A-0004", ref="notes"))
    assert launched == [("PLAN.PDF", False), ("notes", False)]
    # Only what the item names, and only an item that stands.
    assert "has no material" in refused(open_material(client, item="A-0004"))
    assert refused(open_material(client, item="A-0099")) == "no item A-0099"


def test_a_launcher_that_fails_says_so(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path: Path, *, reveal: bool) -> None:
        raise OSError(2, "No app for that")

    monkeypatch.setattr(day_routes, "launch", broken)
    assert refused(open_material(client)) == f"{PLAN} didn't open: No app for that"


def test_each_system_opens_with_its_own_opener(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF")
    ran: list[list[str]] = []
    started: list[str] = []
    day_routes.launch(f, reveal=False, system="win32", run=ran.append, start=started.append)
    day_routes.launch(f, reveal=True, system="win32", run=ran.append)
    day_routes.launch(tmp_path, reveal=True, system="win32", run=ran.append)
    day_routes.launch(f, reveal=False, system="darwin", run=ran.append)
    day_routes.launch(f, reveal=True, system="darwin", run=ran.append)
    day_routes.launch(f, reveal=True, system="linux", environ={"DISPLAY": ":0"}, run=ran.append)
    day_routes.launch(f, reveal=False, system="linux", environ={"WAYLAND_DISPLAY": "w"}, run=ran.append)
    assert started == [str(f)]
    assert ran == [
        ["explorer", f"/select,{f}"],
        ["explorer", str(tmp_path)],
        ["open", str(f)],
        ["open", "-R", str(f)],
        ["xdg-open", str(tmp_path)],
        ["xdg-open", str(f)],
    ]
    with pytest.raises(DayStateError, match="no display"):
        day_routes.launch(f, reveal=False, system="linux", environ={}, run=ran.append)
    spawned: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **keys: spawned.append((command, keys)))
    day_routes.launch(f, reveal=False, system="darwin")
    assert spawned[0][0] == ["open", str(f)]
    assert spawned[0][1]["stdout"] == subprocess.DEVNULL


# -- the parts' own rules ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "kind"),
    [
        (LINK, "url"),
        ("HTTP://example.invalid/x", "url"),
        (CHAT, "record"),
        ("r:calls:rec-1@01:00", "recording"),
        ("[r:chat:C0TEST/1]", "record"),
        ("minutes:2026-10-07-supplier-call", "record"),
        ("page:2026-10-19T10:30", "query"),
        ("mail:The offer | Mon", "query"),
        (PLAN, "text"),
        ("notes/costs.xlsx", "path"),
        ("C:\\x\\plan.pdf", "unsafe"),
        ("notes/../plan.md", "unsafe"),
    ],
)
def test_a_material_links_by_what_it_is(ref: str, kind: str) -> None:
    from urllib.parse import parse_qs, urlsplit

    found = day_view.link_of(ref, None, ["chat", "calls"])
    assert (found.kind, found.label) == (kind, ref)
    target = urlsplit(found.href)
    if kind == "url":
        assert found.href == ref
    elif kind == "recording":
        assert (target.path, parse_qs(target.query)) == ("/recording", {"rid": ["calls:rec-1"], "at": ["01:00"]})
    elif kind == "record":
        assert target.path == "/search"
        assert parse_qs(target.query)["hit"][0] in (f"r:{ref}", ref)
    elif kind == "query":
        assert (target.path, parse_qs(target.query)) == ("/search", {"query": [ref]})
    else:
        assert found.href == ""


@pytest.mark.parametrize(
    ("ref", "said"),
    [
        ("", "it names no path"),
        (" ", "it names no path"),
        ("/etc/x.pdf", "it is an absolute path or a network share's"),
        ("\\\\host\\share\\x.pdf", "it is an absolute path or a network share's"),
        ("C:x.pdf", "it holds a colon: a drive, a stream or another scheme"),
        ("notes//x.pdf", "it has an empty, . or .. part"),
        ("./x.pdf", "it has an empty, . or .. part"),
        ("notes\\..\\x.pdf", "it has an empty, . or .. part"),
        ("notes /x.pdf", "a part of it ends in a dot or a space"),
        ("notes/.GIT/config", "it is inside .git"),
        ("notes/x.pdf", None),
    ],
)
def test_a_path_is_checked_as_text_first(ref: str, said: str | None) -> None:
    assert day_view.path_problem(ref) == said


def test_the_snooze_choices_and_the_next_working_day() -> None:
    friday = datetime(2026, 10, 23, tzinfo=UTC).date()
    assert day_view.next_working_day(friday).isoformat() == "2026-10-26"
    assert day_view.next_working_day(friday + timedelta(days=1)).isoformat() == "2026-10-26"
    # On a Friday the next working day is the Monday, so the choices name it once.
    assert [s.until.isoformat() for s in day_view.snooze_choices(friday)] == [
        "2026-10-26", "2026-10-30", "2026-11-06",
    ]  # fmt: skip
    tuesday = datetime(2026, 10, 20, tzinfo=UTC).date()
    assert [s.label for s in day_view.snooze_choices(tuesday)] == ["Wed", "Mon 26 Oct", "+1 week", "+2 weeks"]
    assert day_view.snooze_choices(tuesday)[0].title == "Snooze to the next working day, Wed 21 Oct"
    assert day_view.day_text(datetime(2027, 1, 4, tzinfo=UTC).date(), tuesday) == "Mon 4 Jan 2027"


@pytest.mark.parametrize(("shown", "on", "text"), json.loads(DAYS.read_text(encoding="utf-8"))["days"])
def test_the_date_picker_shows_a_day_as_the_server_writes_it(shown: str, on: str, text: str) -> None:
    assert day_view.day_text(date.fromisoformat(shown), date.fromisoformat(on)) == text


def test_a_notice_says_what_the_last_write_did() -> None:
    line = views.BatchLine(ref="A-0001", kind="triage", line="triage: me")
    undo = views.BatchLine(ref="A-0001", kind="undo", line="undo: triage: me")
    batch = "01M3250V000000000000000001"

    def notice(lines: list[views.BatchLine], said: str | None = None) -> tuple[str, str | None, str | None]:
        found = day_view.build_notice(DayState(done=batch, said=said), lines)
        assert found is not None
        return found.text, found.button, found.then

    assert day_view.build_notice(DayState(), []) is None
    assert notice([]) == ("That change is no longer in the ledger's history.", None, None)
    assert notice([line]) == ("Saved.", "Undo", "undo")
    assert notice([undo], "undo") == ("Undone.", "Redo", "redo")
    assert notice([undo], "redo") == ("Redone.", "Undo", "undo")
    # A batch of undos that is a change of its own, such as every proposed point removed, says what it is: saved.
    assert notice([undo, undo]) == ("Saved.", "Undo", "undo")
    assert day_view.notice_of("opened", None, (), []).text == "Opened it with its app."


def test_a_due_says_how_near_it_is(client: TestClient, vault: Vault) -> None:
    write(vault, add("soon", "task", "Tomorrow's", "Ben Sample", "ops", due=day(1)))
    found = listed(client)
    assert row(found, "A-0008")["due"] == {"text": "tomorrow", "title": f"due {day(1)}", "tone": "soon"}
    near = (today() + timedelta(days=3)).strftime("%a")
    assert row(found, "A-0003")["due"]["text"] == near
    assert row(found, "A-0004")["due"]["text"] == f"{(today() + timedelta(days=30)).day} " + (
        f"{(today() + timedelta(days=30)):%b}"
    )
    closed = row(listed(client, "state=closed"), "A-0006")
    assert closed["due"]["text"] == f"done {today().day} {today():%b}"


# -- failures --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (API, None),
        (f"{API}/panel?open=A-0001", None),
        (f"{API}/text?open=A-0005&text={PLAN}", None),
        (WRITE, {"op": "done", "items": ["A-0001"]}),
        (SAVE, {"op": "set", "items": ["A-0001"], "fields": {"who": "x"}}),
        (f"{API}/open", {"item": "A-0005", "ref": PLAN}),
    ],
)
def test_a_failure_answers_500_and_says_no_words(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, body: dict[str, Any] | None
) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("Send Ada the dates")

    monkeypatch.setattr(day_routes, "gather", broken)
    monkeypatch.setattr(day_routes, "posted", broken)
    monkeypatch.setattr(day_routes, "open_material", broken)
    answer = client.get(path) if body is None else post(client, body, path)
    assert answer.status_code == 500
    assert answer.json() == {"error": display.FAILED}
    assert "Send Ada the dates" not in answer.text


def test_a_poll_that_fails_says_nothing_changed(reader: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(root: Path) -> str:
        raise OSError(5, "Access is denied")

    monkeypatch.setattr(day_routes, "version", broken)
    assert read(reader, f"{API}/changed?v=x") == {"changed": False, "version": "x"}


def test_a_look_alike_gone_by_the_page_s_read_is_left_out(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    alike = {"title": "Send Ada the dates again", "who": "Ada Example", "kind": "reply", "stream": "pilot"}
    real = day_routes.gather

    def and_a_gone_one(vault: Vault, state: DayState, **keys: Any) -> day_routes.Facts:
        return real(vault, state, **{**keys, "alikes": (*keys.get("alikes", ()), "A-0099")})

    monkeypatch.setattr(day_routes, "gather", and_a_gone_one)
    refusal = post(client, {"op": "add", "fields": alike})
    assert refused(refusal).startswith("looks like an open item already")
    assert [r["ref"] for r in refusal.json()["alikes"]] == ["A-0001"]
