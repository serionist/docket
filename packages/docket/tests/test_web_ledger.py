"""The Ledger page over HTTP, signed in (DR-0661 to DR-0666): its groups and their order, the filters, the item panel,
every button's post as one batch by user via page, a bulk, undo and redo, a refusal kept as typed, the saves on
change, the poll, and the materials' checks with the launcher patched, so no test starts an app. Invented people
only."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from day_kit import CHAT, LINK, PLAN, SAME, ZONE, day, day_vault, hidden, signed_in, today
from docket_testkit import empty_vault, make_config
from docket_webkit import OURS, PORT, TOKEN, a11y_problems, html_problems, parse
from ledger_kit import ADD_FIELDS, op, ulid, write_ops
from starlette.testclient import TestClient

from docket.ledger import ops as ledger_ops
from docket.ledger import store, views
from docket.ledger.store import LedgerError
from docket.service.app import create_app
from docket.service.sessions import COOKIE, Sessions
from docket.service.state import Settings
from docket.vault.vault import Vault
from docket.web import day as day_routes
from docket.web import day_view
from docket.web.day_state import DayState, DayStateError, canonical
from docket.web.pages import DAY_KEYS
from docket.web.templating import PAGE_CSP

OPEN = ["A-0001", "A-0002", "A-0003", "A-0004", "A-0005"]
DUE = ["Overdue", "Due today", "Due this week", "Due later", "No due date"]


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return day_vault(tmp_path)


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """The launcher, patched: what would have opened, by name, and whether in its folder."""
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(day_routes, "launch", lambda path, *, reveal: seen.append((Path(path).name, reveal)))
    return seen


def page(client: TestClient, path: str = "/ledger", status: int = 200) -> str:
    answer = client.get(path)
    assert answer.status_code == status, answer.text
    assert answer.headers["Content-Security-Policy"] == PAGE_CSP
    assert answer.headers["Cache-Control"] == "no-store"
    assert not html_problems(answer.text), path
    fragment = path.startswith(("/ledger/panel", "/ledger/text"))
    assert not a11y_problems(answer.text, whole=not fragment), path
    return answer.text


def texts(html: str, cls: str) -> list[str]:
    return [el.text() for el in parse(html).find_all(class_=cls)]


def rows(html: str) -> list[str]:
    return [el.attrs["data-ref"] for el in parse(html).find_all("li", class_="row")]


def groups(html: str) -> list[str]:
    return [el.find("span").text() for el in parse(html).find_all("h2", class_="day-group-head")]


def post(client: TestClient, data: dict[str, Any], path: str = "/ledger/write") -> Any:
    return client.post(path, data=data, headers=SAME)


def back(answer: Any) -> dict[str, list[str]]:
    """Where a post's 303 goes, as its query."""
    assert answer.status_code == 303, answer.text
    url = urlsplit(answer.headers["location"])
    assert url.path == "/ledger"
    return parse_qs(url.query)


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


def test_the_ledger_lists_the_open_items_by_due_and_counts_every_state(client: TestClient, vault: Vault) -> None:
    html = page(client)
    tree = parse(html)
    assert tree.find("h1").text() == "Ledger"
    assert tree.find("a", aria_current="page").attrs["href"] == "/ledger"
    assert groups(html) == DUE
    assert rows(html) == OPEN
    assert texts(html, "counts") == ["5 open, 1 snoozed, 1 closed; 1 overdue"]
    assert [c.text() for c in tree.find_all("a", class_="state-chip")] == ["Open 5", "Snoozed 1", "Closed 1", "All 7"]
    row = tree.find("li", id="row-A-0001")
    assert row.attrs["data-triage"] == "chase"
    assert "2d late" in row.text()
    # Chase is pressed, so its button clears it, as No call does; the others set theirs, and each names its row.
    quick = row.find("form", class_="quick")
    assert [b.attrs["value"] for b in quick.find_all("button")] == [
        "triage:discuss", "triage:claude", "triage:me", "triage:", "triage:", "done",
    ]  # fmt: skip
    assert quick.find("button").text() == "Discuss A-0001"
    assert "M-0001" in tree.find("li", id="row-A-0002").text()
    assert "today" in tree.find("li", id="row-A-0002").text()
    # The poll carries the version the page read, and the keys are the old tasks app's.
    poll = tree.find("div", id="poll")
    assert poll.attrs["hx-trigger"] == "every 5s"
    assert poll.attrs["hx-get"] == f"/ledger/changed?page=ledger&v={day_routes.version(vault.root)}"
    dialog = tree.find("dialog", id="keys-dialog")
    keys = [k.attrs["data-key"] for k in dialog.find_all("kbd") if "data-key" in k.attrs]
    assert keys == [key for names, _ in DAY_KEYS for key in names]
    assert keys[:5] == ["j", "k", "Enter", "o", "x"]
    # The act bar carries no item until a row is ticked; its verdicts set, and the script decides a clear.
    act = tree.find("form", id="act")
    assert [b.attrs["value"] for b in act.find_all("button", class_="verdict")] == [
        "triage:discuss", "triage:claude", "triage:me", "triage:chase",
    ]  # fmt: skip
    # Drop comes first, so Enter in its why drops.
    assert act.find("button").attrs["value"] == "drop"


@pytest.mark.parametrize(
    ("path", "named", "listed"),
    [
        ("/ledger?state=snoozed", ["Back"], ["A-0007"]),
        ("/ledger?state=closed", ["Closed today"], ["A-0006"]),
        ("/ledger?state=all", [*DUE, "Back", "Closed today"], [*OPEN, "A-0007", "A-0006"]),
        ("/ledger?group=stream", ["ops", "pilot"], ["A-0003", "A-0004", "A-0005", "A-0001", "A-0002"]),
        ("/ledger?group=kind", ["Replies owed", "Decisions", "Tasks", "Waiting on others"],
         ["A-0001", "A-0003", "A-0002", "A-0004", "A-0005"]),
        ("/ledger?group=who", ["Ada Example", "Ben Sample"], ["A-0001", "A-0003", "A-0002", "A-0004", "A-0005"]),
        ("/ledger?group=meeting", ["M-0001", "No meeting"], ["A-0002", "A-0001", "A-0003", "A-0004", "A-0005"]),
        ("/ledger?state=all&group=who", ["Ada Example", "Ben Sample"],
         ["A-0001", "A-0003", "A-0006", "A-0007", "A-0002", "A-0004", "A-0005"]),
    ],
)  # fmt: skip
def test_each_state_and_grouping_lists_in_its_order(
    client: TestClient, path: str, named: list[str], listed: list[str]
) -> None:
    html = page(client, path)
    assert [g.split()[0] if g.startswith("Back") else g for g in groups(html)] == named
    assert rows(html) == listed


@pytest.mark.parametrize(
    ("path", "listed"),
    [
        ("/ledger?q=caterer", ["A-0005"]),
        # A log line, a meeting's subject, a material's label, every word in any order.
        ("/ledger?q=thread&state=closed", ["A-0006"]),
        ("/ledger?q=sync", ["A-0002"]),
        ("/ledger?q=plan.md", ["A-0005"]),
        ("/ledger?q=ROOM+book", ["A-0002"]),
        ("/ledger?stream=pilot", ["A-0001", "A-0002"]),
        ("/ledger?kind=task", ["A-0002", "A-0004"]),
        ("/ledger?who=BEN", ["A-0002", "A-0004", "A-0005"]),
        ("/ledger?q=zeppelin", []),
        ("/ledger?q=%21%21", []),
    ],
)
def test_q_and_the_filters_narrow_the_list(client: TestClient, path: str, listed: list[str]) -> None:
    html = page(client, path)
    assert rows(html) == listed
    if not listed:
        assert texts(html, "empty-line") == ["Nothing here matches."]


def test_a_snooze_whose_day_has_come_leads_the_open_items(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    later = datetime.combine(today() + timedelta(days=6), datetime.min.time(), ZONE).replace(hour=12)
    monkeypatch.setattr(views, "now", lambda: later.astimezone(UTC))
    html = page(client)
    assert groups(html)[0] == "Snooze ended"
    assert rows(html)[0] == "A-0007"
    assert "snooze ended" in parse(html).find("li", id="row-A-0007").text()
    assert texts(html, "counts") == ["6 open (1 with their snooze ended), 0 snoozed, 1 closed; 3 overdue"]
    assert texts(page(client, "/ledger?state=snoozed"), "empty-line") == ["Nothing is snoozed."]


def test_an_archived_item_shows_only_when_asked(client: TestClient, vault: Vault) -> None:
    write(vault, {"op": "archive", "item": "A-0006"})
    assert texts(page(client, "/ledger?state=closed"), "empty-line") == ["Nothing is closed."]
    html = page(client, "/ledger?state=closed&archived=1")
    assert rows(html) == ["A-0006"]
    assert "archived" in parse(html).find("li", id="row-A-0006").text()
    # Its panel has no call, and its row no tick.
    panel = page(client, "/ledger/panel?open=A-0006&archived=1")
    assert not parse(panel).find_all("section", class_="call")
    assert not parse(html).find("li", id="row-A-0006").find_all("input", class_="tick")


def test_a_list_shows_100_rows_and_show_more_goes_past_the_views_500(client: TestClient, vault: Vault) -> None:
    adds = [{"op": "add", "force": True, "fields": {"kind": "task", "title": f"Chore {n}", "who": "Ben Sample",
                                                    "stream": "ops", "source": CHAT}} for n in range(600)]  # fmt: skip
    write(vault, *adds)
    html = page(client)
    assert len(rows(html)) == 100
    more = parse(html).find("p", class_="more")
    assert more.find("a").attrs["href"] == "/ledger?k=200"
    assert "100 of 605" in more.text()
    assert len(rows(page(client, "/ledger?k=550"))) == 550
    wide = page(client, "/ledger?k=700")
    assert len(rows(wide)) == 605
    assert not parse(wide).find_all("p", class_="more")


def test_an_empty_ledger_says_how_items_come(tmp_path: Path) -> None:
    with signed_in(empty_vault(tmp_path, make_config())) as client:
        html = page(client)
        assert texts(html, "empty-line") == [
            "The ledger is empty. Claude adds what you owe and await as it syncs, or add an item here."
        ]
        assert texts(page(client, "/ledger?stream=pilot"), "empty-line") == ["Nothing here matches."]


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
    ],
)
def test_a_value_the_url_can_t_hold_is_the_page_s_error(client: TestClient, query: str, said: str) -> None:
    html = page(client, f"/ledger?{query}", status=400)
    assert texts(html, "notice")[0].startswith(said)
    assert not parse(html).find_all("div", id="poll")


def test_the_state_reads_into_one_canonical_url() -> None:
    state = DayState.read({"open": "a-12", "at": "A-0001", "k": "100", "q": "  two   words ", "drop": "1"})
    assert state.url() == "/ledger?open=A-0012&q=two+words&drop=1"
    assert state.url("/ledger/panel") == "/ledger/panel?open=A-0012&q=two+words&drop=1"
    assert DayState.read({"open": "M-7", "drop": "1", "text": "a.md"}).url() == "/ledger?open=M-0007"
    assert DayState.read({"redo": "1", "skipped": "A-1"}).url(notice=True) == "/ledger"
    noticed = DayState.read({"at": "A-0002", "done": "nothing", "redo": "1", "skipped": "A-1"})
    assert noticed.url(notice=True) == "/ledger?at=A-0002&done=nothing&redo=1&skipped=A-0001"
    assert canonical("http://127.0.0.1:8741/ledger/panel?open=a-12&at=A-1") == "/ledger?open=A-0012"
    assert canonical("/search?query=x") is None
    assert canonical("/ledger?k=0") is None
    with pytest.raises(DayStateError, match="page is one of"):
        day_routes.page_of(parse_qs_params("page=nope"))


def parse_qs_params(query: str) -> Any:
    from starlette.datastructures import QueryParams

    return QueryParams(query)


# -- the item panel ----------------------------------------------------------------------------------------------------


def test_an_item_opens_beside_the_list_with_its_call_fields_materials_source_and_history(client: TestClient) -> None:
    answer = client.get("/ledger/panel?open=A-0005")
    assert answer.headers["HX-Push-Url"] == "/ledger?open=A-0005"
    html = page(client, "/ledger/panel?open=A-0005")
    tree = parse(html)
    assert tree.find("section", class_="item-panel").attrs["data-item"] == "A-0005"
    assert tree.find("span", id="title-A-0005").text() == "Quote from the caterer"
    assert "open, no call yet" in tree.find("p", class_="panel-facts").text()
    assert [b.attrs["value"] for b in tree.find("div", class_="verdicts").find_all("button")] == [
        "triage:discuss", "triage:claude", "triage:me", "triage:chase",
    ]  # fmt: skip
    assert tree.find("textarea", id="f-A-0005-notes").text() == "Asked twice."
    assert tree.find("textarea", id="f-A-0005-description").text() == "The full quote with the menu"
    materials = tree.find_all("li", class_="material")
    assert [m.attrs["class"].split()[1] for m in materials] == ["kind-text", "kind-path", "kind-url"]
    view = materials[0].find("a", class_="button")
    assert view.attrs["href"] == f"/ledger?open=A-0005&text={PLAN}"
    assert view.attrs["hx-get"] == f"/ledger/text?open=A-0005&text={PLAN}"
    assert materials[2].find("a").attrs["href"] == LINK
    assert materials[2].find("a").attrs["rel"] == "noopener noreferrer"
    # Its source is a record of the vault's chat source: Search's panel opens on its raw.
    source = tree.find("section", class_="source-part").find("a")
    assert parse_qs(urlsplit(source.attrs["href"]).query) == {"hit": [f"r:{CHAT}"]}
    history = tree.find("ol", id="history-A-0005")
    lines = [li.find("p", class_="lt").text() for li in history.find_all("li")]
    assert lines == [
        "material added: The offer", "material added: notes/costs.xlsx", "material added: The plan", "added",
    ]  # fmt: skip
    buttons = [li.find_all("button") for li in history.find_all("li")]
    assert [b[0].text().split(":")[0].strip() if b else None for b in buttons] == ["Undo", "Undo", "Undo", None]
    assert texts(html, "facts")[0].startswith("opened")


def test_the_page_opens_the_panel_and_marks_its_row(client: TestClient) -> None:
    html = page(client, "/ledger?open=A-0003")
    tree = parse(html)
    assert "open" in tree.find("li", id="row-A-0003").attrs["class"].split()
    assert tree.find("section", class_="item-panel").attrs["data-item"] == "A-0003"
    # The selection goes from the open item to the row after it once a call is made.
    assert hidden(html, "call-form")["at"] == "A-0004"


def test_an_item_on_a_meeting_shows_the_meeting_with_its_points(client: TestClient, vault: Vault) -> None:
    tree = parse(page(client, "/ledger/panel?open=A-0002"))
    box = tree.find("section", class_="meeting-box")
    assert box.find("h3").text() == "On M-0001: Pilot sync"
    assert box.find("p", class_="meta").text().endswith("10:00-10:30")
    assert [p.text() for p in box.find_all("li", class_="point")] == [
        "#1 open The venue's dates",
        "#2 open Who books the room proposed",
    ]
    # The meeting's add undone: the item no longer hangs on a meeting that stands, and the panel shows none.
    meeting = next(o for o in ops(vault) if o["op"] == "meeting")
    write(vault, {"op": "undo", "of": meeting["id"]})
    assert not parse(page(client, "/ledger/panel?open=A-0002")).find_all("section", class_="meeting-box")


@pytest.mark.parametrize(
    ("ref", "said"),
    [
        ("A-0099", "no item A-0099"),
        ("M-0001", "M-0001 is a meeting; its panel comes with the Meetings page."),
    ],
)
def test_a_panel_that_can_t_open_says_why(client: TestClient, ref: str, said: str) -> None:
    html = page(client, f"/ledger/panel?open={ref}")
    assert texts(html, "notice") == [said]
    assert said in page(client, f"/ledger?open={ref}")


def test_a_meeting_s_id_opens_no_item_panel(client: TestClient, vault: Vault) -> None:
    meeting = next(o for o in ops(vault) if o["op"] == "meeting")["id"]
    html = page(client, f"/ledger/panel?open={meeting}")
    assert texts(html, "notice") == [f"{meeting} is a meeting or a stream; its panel comes with the Meetings page."]


def test_a_panel_url_that_doesn_t_read_is_the_panel_s_error(client: TestClient) -> None:
    html = page(client, "/ledger/panel?open=nope", status=400)
    assert texts(html, "notice")[0].startswith("open is an A- or M- number")
    assert not parse(page(client, "/ledger/panel")).find_all("section")


def test_the_drop_form_opens_with_its_why_focused(client: TestClient) -> None:
    tree = parse(page(client, "/ledger?open=A-0001&drop=1"))
    details = tree.find("details", class_="close-form")
    assert "open" in details.attrs
    assert "autofocus" in details.find("input", name="note").attrs
    # The row's drop opens this, its why focused.
    row = parse(page(client)).find("li", id="row-A-0001")
    (drop,) = [a for a in row.find_all("a") if a.attrs.get("data-key") == "Delete"]
    assert drop.attrs["href"] == "/ledger?open=A-0001&drop=1"
    assert drop.attrs["hx-get"] == "/ledger/panel?open=A-0001&drop=1"


def test_a_closed_item_reopens_and_a_snoozed_one_wakes(client: TestClient) -> None:
    closed = parse(page(client, "/ledger/panel?open=A-0006&state=closed"))
    assert [b.attrs["value"] for b in closed.find("section", class_="call").find_all("button")] == ["reopen"]
    assert "done" in closed.find("p", class_="panel-facts").text()
    snoozed = parse(page(client, "/ledger/panel?open=A-0007&state=snoozed"))
    assert "snoozed to" in snoozed.find("p", class_="panel-facts").text()
    assert "reopen" in [b.attrs["value"] for b in snoozed.find("form", class_="call-form").find_all("button")]


# -- the posts: one batch each, by user via page (DR-0661) ------------------------------------------------------------


def test_a_verdict_posts_one_batch_by_user_via_page_and_moves_the_selection_on(
    client: TestClient, vault: Vault
) -> None:
    form = hidden(page(client, "/ledger?open=A-0002"), "call-form")
    assert form == {"page": "ledger", "open": "A-0002", "item": "A-0002", "at": "A-0003"}
    answer = post(client, {**form, "op": "triage:claude"})
    last = ops(vault)[-1]
    assert (last["op"], last["verdict"], last["by"], last["via"]) == ("triage", "claude", "user", "page")
    assert back(answer) == {"open": ["A-0003"], "done": [last["batch"]]}
    html = page(client, answer.headers["location"])
    assert texts(html, "day-notice")[0].startswith("Saved. A-0002: triage: claude")
    undo = {"page": "ledger", "open": "A-0003", "op": "undo", "batch": last["batch"], "redo": "1"}
    assert hidden(html, "notice-undo") == undo
    # Posted twice, it changes nothing the second time and says so, with no Undo.
    again = post(client, {**form, "op": "triage:claude"})
    assert back(again) == {"open": ["A-0003"], "done": ["nothing"]}
    html = page(client, again.headers["location"])
    assert texts(html, "day-notice") == ["Nothing changed."]
    assert not parse(html).find_all("form", class_="notice-undo")


def test_a_clash_s_rows_are_named_by_their_ids_as_a_batch_takes_them(client: TestClient, vault: Vault) -> None:
    """Another clone's add claims A-0001 too: both rows are named by their ids until the renumber, the refs a batch
    takes (DR-0613), and a call posted on one writes, the writer renumbering it first."""
    theirs = op("add", 0, number=1, machine="laptop", fields={**ADD_FIELDS, "title": "Their copy of the dates"})
    write_ops(vault.root, [theirs])
    ours = next(o["id"] for o in ops(vault) if o["op"] == "add" and o["fields"]["title"] == "Send Ada the dates")
    html = page(client)
    assert {ours, ulid(0)} <= set(rows(html))
    assert "A-0001" not in rows(html)
    form = hidden(html, "quick")
    assert form["item"] == ours
    answer = post(client, {**form, "op": "triage:me"})
    assert back(answer)["done"]
    wrote = [o for o in ops(vault) if o.get("batch") == back(answer)["done"][0]]
    assert [o["op"] for o in wrote] == ["renumber", "triage"]
    assert parse(page(client, f"/ledger/panel?open={ours}")).find("section", class_="item-panel")


def test_a_row_s_quick_button_moves_the_selection_without_a_panel(client: TestClient, vault: Vault) -> None:
    form = hidden(page(client), "quick")
    assert form == {"page": "ledger", "item": "A-0001", "at": "A-0002"}
    assert back(post(client, {**form, "op": "done"}))["at"] == ["A-0002"]
    assert ops(vault)[-1]["op"] == "done"


def test_a_bulk_gives_each_op_the_rows_it_takes_and_names_the_rest(client: TestClient, vault: Vault) -> None:
    act = hidden(page(client, "/ledger?state=all"), "act-bar")
    answer = post(client, {**act, "op": "triage:me", "item": ["A-0001", "A-0003", "A-0006"]})
    query = back(answer)
    assert (query["state"], query["skipped"]) == (["all"], ["A-0006"])
    wrote = [o for o in ops(vault) if o.get("batch") == query["done"][0]]
    assert [(o["op"], o["verdict"]) for o in wrote] == [("triage", "me"), ("triage", "me")]
    html = page(client, answer.headers["location"])
    assert texts(html, "day-notice")[0].startswith("Saved. Skipped A-0006: the change doesn't take them as they stand.")
    # Reopen over open and closed rows reopens the closed one; over open rows only, the writer refuses it whole.
    answer = post(client, {**act, "op": "reopen", "item": ["A-0001", "A-0006"]})
    assert back(answer)["skipped"] == ["A-0001"]
    assert ops(vault)[-1]["op"] == "reopen"
    refused = post(client, {**act, "op": "reopen", "item": ["A-0001", "A-0003"]})
    assert refused.status_code == 400
    assert "A-0001 is already open" in texts(refused.text, "notice")[0]


def test_a_drop_with_no_why_is_refused_with_the_text_as_typed_and_no_poll(client: TestClient, vault: Vault) -> None:
    before = len(ops(vault))
    form = hidden(page(client, "/ledger?open=A-0003"), "close-fields")
    answer = post(client, {**form, "op": "drop", "note": "", "outcome": "We met in person"})
    assert answer.status_code == 400
    html = answer.text
    assert not html_problems(html)
    assert texts(html, "notice")[0] == "a drop needs a note: why"
    details = parse(html).find("details", class_="close-form")
    assert "open" in details.attrs
    assert details.find("textarea", name="outcome").text() == "We met in person"
    assert not parse(html).find_all("div", id="poll")
    assert len(ops(vault)) == before
    # The act bar's why is kept as typed too.
    act = hidden(page(client), "act-bar")
    answer = post(client, {**act, "op": "snooze:2000-01-01", "item": "A-0001", "note": "later"})
    assert answer.status_code == 400
    assert parse(answer.text).find("form", id="act").find("input", name="note").attrs["value"] == "later"


def test_each_panel_form_writes_its_change(client: TestClient, vault: Vault) -> None:
    html = page(client, "/ledger?open=A-0004")

    def sent(cls: str, opened: str = "A-0004", **fields: Any) -> dict[str, Any]:
        answer = post(client, {**hidden(html, cls), **fields})
        assert back(answer)["open"] == [opened], answer.headers["location"]
        return ops(vault)[-1]

    # An item's fields post as fields.<name>, so its who never reads as the page's who filter.
    assert sent("field-who", **{"fields.who": "Cleo Example"})["fields"] == {"who": "Cleo Example"}
    assert sent("field-due", **{"fields.due": ""})["fields"] == {"due": None}
    assert sent("field-notes", notes="Ask the venue first")["fields"] == {"notes": "Ask the venue first"}
    assert sent("log-add", note="Ben called back")["note"] == "Ben called back"
    added = sent("material-add", ref="notes\\plan.md", label="The plan")
    assert (added["op"], added["ref"], added["label"]) == ("material", PLAN, "The plan")
    html = page(client, "/ledger?open=A-0004")
    assert sent("material-remove")["remove"] is True
    # A call moves the selection on: from the open list's A-0004 to A-0005, and in the snoozed list to A-0007.
    assert sent("snooze-form", "A-0005", op="snooze", until=day(9))["until"] == day(9)
    html = page(client, "/ledger?open=A-0004&state=snoozed")
    assert sent("snooze-form", "A-0007", op=f"snooze:{day(10)}")["until"] == day(10)
    closing = sent("close-fields", "A-0007", op="done", note="sent it", outcome="Booked for May")
    assert (closing["op"], closing["note"]) == ("done", "sent it")
    assert any(o.get("fields") == {"outcome": "Booked for May"} for o in ops(vault)[-2:])


def test_a_new_item_opens_and_a_look_alike_asks_first(client: TestClient, vault: Vault) -> None:
    form = hidden(page(client), "new-form")
    fields = {"fields.title": "Order the badges", "fields.who": "Ada Example", "fields.kind": "task",
              "fields.stream": "ops", "fields.due": day(2)}  # fmt: skip
    answer = post(client, {**form, **fields, "fields.description": "", "fields.source": ""})
    added = ops(vault)[-1]
    assert back(answer)["open"] == ["A-0008"]
    assert (added["op"], added["by"], added["via"]) == ("add", "user", "page")
    assert added["fields"]["source"].startswith(f"page:{today().isoformat()}T")
    # One that looks like an open item names it beside Add anyway, its fields as typed.
    alike = {**fields, "fields.title": "Order the badges, please"}
    refused = post(client, {**form, **alike})
    assert refused.status_code == 400
    tree = parse(refused.text)
    assert texts(refused.text, "notice")[0].startswith("looks like an open item already: A-0008 Order the badges")
    assert "open" in tree.find("details", class_="new-item").attrs
    assert tree.find("input", name="fields.title").attrs["value"] == "Order the badges, please"
    assert [a.attrs["href"] for a in tree.find("div", class_="alikes").find_all("a")] == ["/ledger?open=A-0008"]
    assert tree.find("button", name="force").attrs["value"] == "true"
    forced = post(client, {**form, **alike, "force": "true", "fields.source": LINK})
    assert back(forced)["open"] == ["A-0009"]
    assert ops(vault)[-1]["fields"]["source"] == LINK


def test_the_notice_undoes_the_batch_and_then_redoes_it(client: TestClient) -> None:
    form = hidden(page(client, "/ledger?open=A-0002"), "call-form")
    done = back(post(client, {**form, "op": "done"}))["done"][0]
    html = page(client, f"/ledger?open=A-0003&done={done}")
    assert parse(html).find("form", class_="notice-undo").find("button").text() == "Undo"
    undone = post(client, hidden(html, "notice-undo"))
    assert back(undone)["redo"] == ["1"]
    html = page(client, undone.headers["location"])
    assert texts(html, "day-notice")[0].startswith("Undone. A-0002: undo: done")
    assert parse(html).find("form", class_="notice-undo").find("button").text() == "Redo"
    assert "A-0002" in rows(html)
    redone = post(client, hidden(html, "notice-undo"))
    html = page(client, redone.headers["location"])
    assert "redo" not in back(redone)
    assert texts(html, "day-notice")[0].startswith("Redone.")
    assert parse(html).find("form", class_="notice-undo").find("button").text() == "Undo"
    assert "A-0002" not in rows(html)


def test_a_history_line_undoes_by_its_op_and_the_undo_s_own_line_redoes(client: TestClient) -> None:
    html = page(client, "/ledger?open=A-0001")
    undo = parse(html).find("ol", id="history-A-0001").find("form", class_="history-undo")
    fields = hidden(html, "history-undo")
    assert fields["redo"] == "1"
    answer = post(client, {**fields, "op": undo.find("button").attrs["value"]})
    assert back(answer)["redo"] == ["1"]
    lines = parse(page(client, "/ledger?open=A-0001")).find("ol", id="history-A-0001").find_all("li")
    assert [li.find("p", class_="lt").text() for li in lines] == ["undo: triage: chase", "triage: chase", "added"]
    assert lines[0].find("button").text().startswith("Redo")
    assert "undone by" in lines[1].text()
    assert not lines[1].find_all("button")
    assert not lines[2].find_all("button")


@pytest.mark.parametrize(
    ("fields", "said"),
    [
        ({"op": "nope", "item": "A-0001"}, "op is one of"),
        ({"op": "triage:me"}, "tick a row, or open an item, first"),
        ({"op": "set", "item": "A-0001"}, "set takes a field"),
        ({"op": "log", "item": ["A-0001", "A-0002"], "note": "x"}, "log takes one item"),
        ({"op": "done", "item": "nope"}, "item is an A- or M- number"),
        ({"op": "done", "item": "A-0001", "page": "nope"}, "page is one of"),
        ({"op": "done", "item": "A-0001", "k": "abc"}, "k is a whole number"),
        ({"op": "log", "item": "A-0001", "at": "nope"}, "at is an A- or M- number"),
    ],
)
def test_a_post_that_doesn_t_read_is_the_page_s_error(client: TestClient, fields: dict[str, Any], said: str) -> None:
    answer = post(client, {"page": "ledger", **fields})
    assert answer.status_code == 400
    assert texts(answer.text, "notice")[0].startswith(said)


def test_a_write_the_writer_fails_is_the_page_s_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(*_: Any, **__: Any) -> Any:
        raise LedgerError("the ops span two files; nothing written")

    monkeypatch.setattr(store, "write", failing)
    answer = post(client, {"page": "ledger", "op": "done", "item": "A-0001"})
    assert answer.status_code == 400
    assert texts(answer.text, "notice")[0] == "the ops span two files; nothing written"


def test_a_post_needs_a_form_body_and_the_guard_s_yes(client: TestClient) -> None:
    json_body = {**SAME, "Content-Type": "application/json"}
    for path in ("/ledger/write", "/ledger/save", "/ledger/open"):
        assert client.post(path, content=b"{}", headers=json_body).status_code == 415
    assert client.post("/ledger/write", data={"op": "done", "item": "A-0001"}).status_code == 403


# -- saves on change ---------------------------------------------------------------------------------------------------


def test_a_save_answers_the_row_the_title_the_history_and_the_version_out_of_band(
    client: TestClient, vault: Vault
) -> None:
    html = page(client, "/ledger?open=A-0003")
    answer = post(client, {**hidden(html, "field-title"), "fields.title": "Pick the venue for May"}, "/ledger/save")
    assert answer.status_code == 200
    assert not html_problems(answer.text)
    assert not a11y_problems(answer.text, whole=False)
    tree = parse(answer.text)
    row = tree.find("li", id="row-A-0003")
    assert (row.attrs["hx-swap-oob"], row.find("span", class_="title").text()) == ("true", "Pick the venue for May")
    assert tree.find("span", id="title-A-0003").text() == "Pick the venue for May"
    assert "title: Pick the venue for May" in tree.find("ol", id="history-A-0003").text()
    poll = tree.find("div", id="poll")
    assert poll.attrs["hx-swap-oob"] == "true"
    assert poll.attrs["hx-get"].endswith(f"v={day_routes.version(vault.root)}")
    assert not tree.find_all("form", class_="field")
    notes = post(client, {**hidden(html, "field-notes"), "notes": "Two venues left"}, "/ledger/save")
    assert notes.status_code == 200
    assert ops(vault)[-1]["fields"] == {"notes": "Two venues left"}


def test_a_refused_save_answers_its_field_as_typed_and_marked_unsaved(client: TestClient) -> None:
    form = hidden(page(client, "/ledger?open=A-0003"), "field-title")
    answer = post(client, {**form, "fields.title": ""}, "/ledger/save")
    assert answer.status_code == 400
    tree = parse(answer.text)
    field = tree.find("form", id="field-A-0003-title")
    assert "data-unsaved" in field.attrs
    assert field.find("p", class_="field-problem").text() == "title can't be cleared"
    assert field.find("input", name="fields.title").attrs["value"] == ""
    assert not tree.find_all("div", id="poll")
    other = post(client, {**form, "op": "done"}, "/ledger/save")
    assert texts(other.text, "field-problem") == ["a save sets a field of an item"]
    nothing = post(client, {**form, "op": "set", "fields.title": "x", "item": "nope"}, "/ledger/save")
    assert texts(nothing.text, "field-problem")[0].startswith("item is an A- or M- number")


def test_a_save_whose_item_is_gone_by_its_read_says_so(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    form = hidden(page(client, "/ledger?open=A-0003"), "field-who")
    real = day_routes.gather

    def gone(vault: Vault, state: DayState, **keys: Any) -> day_routes.Facts:
        found = real(vault, state, **keys)
        found.shown, found.missing = None, "A-0003 was undone"
        return found

    monkeypatch.setattr(day_routes, "gather", gone)
    answer = post(client, {**form, "fields.who": "Cleo Example"}, "/ledger/save")
    assert texts(answer.text, "field-problem") == ["A-0003 was undone"]


# -- the poll (DR-0663) ----------------------------------------------------------------------------------------------


def test_the_poll_answers_nothing_until_an_op_file_changes(client: TestClient, vault: Vault) -> None:
    poll = parse(page(client)).find("div", id="poll").attrs["hx-get"]
    quiet = client.get(poll)
    assert (quiet.status_code, quiet.content) == (204, b"")
    write(vault, {"op": "log", "item": "A-0001", "note": "Ada called"})
    moved = client.get(poll)
    assert (moved.status_code, moved.headers["HX-Refresh"], moved.headers["Cache-Control"]) == (200, "true", "no-store")


def test_without_a_vault_the_page_says_so_and_the_poll_is_quiet() -> None:
    sessions = Sessions()
    app = create_app(Settings(port=PORT, vault=None, token=lambda: TOKEN), sessions=sessions)
    with TestClient(app, base_url=OURS, follow_redirects=False) as client:
        started = sessions.redeem(sessions.new_code())
        assert started is not None
        client.cookies.set(COOKIE, started)
        assert client.get("/ledger/changed?v=x").status_code == 204
        html = page(client, status=400)
        assert "the service has no vault" in texts(html, "notice")[0]


def test_the_version_is_the_same_without_an_op_folder(tmp_path: Path) -> None:
    assert day_routes.version(tmp_path) == day_routes.version(tmp_path / "nowhere")


# -- materials: the viewer and the launcher (DR-0664) -------------------------------------------------------------


def test_a_text_material_shows_in_the_panel_s_viewer(client: TestClient, vault: Vault) -> None:
    answer = client.get(f"/ledger/text?open=A-0005&text={PLAN}")
    assert answer.headers["HX-Push-Url"] == f"/ledger?open=A-0005&text={PLAN}"
    html = page(client, f"/ledger/text?open=A-0005&text={PLAN}")
    assert parse(html).find("pre", class_="viewer-text").text() == "# The plan Book the room first."
    whole = parse(page(client, f"/ledger?open=A-0005&text={PLAN}"))
    assert whole.find("div", id="viewer-A-0005").find("pre").text().startswith("# The plan")
    # A long one shows its first 256 KB, cut.
    (vault.home.top / "notes" / "long.md").write_text("x" * (300 * 1024), encoding="utf-8")
    write(vault, {"op": "material", "item": "A-0005", "ref": "notes/long.md"})
    long = parse(page(client, "/ledger/text?open=A-0005&text=notes/long.md"))
    assert long.find("span", class_="badge").text() == "its first 256 KB"
    assert len(long.find("pre").text()) == 256 * 1024


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
    html = page(client, f"/ledger/text?open=A-0005&text={ref}", status=400)
    assert texts(html, "notice") == [said]


def test_a_viewer_url_that_can_t_read_says_why(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert texts(page(client, "/ledger/text?open=A-0005", status=400), "notice")[0].startswith("text names")
    assert texts(page(client, "/ledger/text?open=nope&text=x", status=400), "notice")[0].startswith("open is an")
    # A meeting opens no item, so there is no material to show.
    met = page(client, "/ledger/text?open=M-0001&text=x", status=400)
    assert texts(met, "notice")[0].startswith("text names the material to show")
    real = Path.open

    def locked(self: Path, *args: Any, **keys: Any) -> Any:
        if self.name == "plan.md":
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **keys)

    monkeypatch.setattr(Path, "open", locked)
    html = page(client, f"/ledger/text?open=A-0005&text={PLAN}", status=400)
    assert texts(html, "notice") == [f"{PLAN} doesn't read: Permission denied"]


def test_a_material_opens_with_its_app_or_in_its_folder(client: TestClient, launched: list[tuple[str, bool]]) -> None:
    html = page(client, "/ledger?open=A-0005")
    plan, sheet = hidden(html, "material-open", 0), hidden(html, "material-open", 1)
    assert back(post(client, plan, "/ledger/open")) == {"open": ["A-0005"], "done": ["opened"]}
    assert back(post(client, {**sheet, "reveal": "1"}, "/ledger/open"))["done"] == ["shown"]
    assert launched == [("plan.md", False), ("costs.xlsx", True)]
    assert texts(page(client, "/ledger?done=opened"), "day-notice") == ["Opened it with its app."]
    assert texts(page(client, "/ledger?done=shown"), "day-notice") == ["Shown in its folder."]


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
    refused = {
        "../outside/x.pdf": "it has an empty, . or .. part",
        "C:x.pdf": "it holds a colon",
        "//host/share/x.pdf": "it is an absolute path or a network share's",
        "notes/plan.pdf.": "a part of it ends in a dot or a space",
        "notes/run.exe": "a .exe file doesn't open from the page",
        "notes/gone.pdf": "notes/gone.pdf isn't there",
        "notes/out/x.pdf": "it leads outside the repo",
        "notes/git/HEAD": "it leads into .git",
    }
    write(vault, *({"op": "material", "item": "A-0004", "ref": ref} for ref in [*refused, "notes/PLAN.PDF", "notes"]))
    form = {"page": "ledger", "open": "A-0004", "item": "A-0004"}
    for ref, said in refused.items():
        answer = post(client, {**form, "ref": ref}, "/ledger/open")
        assert answer.status_code == 400, ref
        assert said in texts(answer.text, "notice")[0], ref
    # An upper-case suffix opens, and so does a folder.
    assert back(post(client, {**form, "ref": "notes/PLAN.PDF"}, "/ledger/open"))["done"] == ["opened"]
    assert back(post(client, {**form, "ref": "notes"}, "/ledger/open"))["done"] == ["opened"]
    assert launched == [("PLAN.PDF", False), ("notes", False)]
    # Only what the item names, and only an item that stands.
    assert "has no material" in texts(post(client, {**form, "ref": PLAN}, "/ledger/open").text, "notice")[0]
    gone = post(client, {**form, "item": "A-0099", "ref": PLAN}, "/ledger/open")
    assert texts(gone.text, "notice")[0] == "no item A-0099"


def test_a_launcher_that_fails_says_so(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path: Path, *, reveal: bool) -> None:
        raise OSError(2, "No app for that")

    monkeypatch.setattr(day_routes, "launch", broken)
    form = hidden(page(client, "/ledger?open=A-0005"), "material-open")
    answer = post(client, form, "/ledger/open")
    assert texts(answer.text, "notice") == [f"{PLAN} didn't open: No app for that"]


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
    assert [b.value for b in day_view.snooze_choices(friday)] == [
        "snooze:2026-10-26", "snooze:2026-10-30", "snooze:2026-11-06",
    ]  # fmt: skip
    tuesday = datetime(2026, 10, 20, tzinfo=UTC).date()
    assert [b.label for b in day_view.snooze_choices(tuesday)] == ["Wed", "Mon 26 Oct", "+1 week", "+2 weeks"]
    assert day_view.day_text(datetime(2027, 1, 4, tzinfo=UTC).date(), tuesday) == "Mon 4 Jan 2027"


def test_a_notice_says_what_the_last_post_did() -> None:
    line = views.BatchLine(ref="A-0001", kind="triage", line="triage: me")
    undo = views.BatchLine(ref="A-0001", kind="undo", line="undo: triage: me")
    batch = "01M3250V000000000000000001"

    def said(lines: list[views.BatchLine], **keys: Any) -> tuple[str, str | None, bool]:
        found = day_view.build_notice(DayState(done=batch, **keys), lines)
        assert found is not None
        return found.text, found.button, found.then_redo

    assert day_view.build_notice(DayState(), []) is None
    assert said([]) == ("That change is no longer in the ledger's history.", None, False)
    assert said([line]) == ("Saved.", "Undo", True)
    assert said([undo], redo=True) == ("Undone.", "Redo", False)
    assert said([undo]) == ("Redone.", "Undo", True)


def test_a_due_says_how_near_it_is(client: TestClient, vault: Vault) -> None:
    fields = {"kind": "task", "title": "Tomorrow's", "who": "Ben Sample", "stream": "ops", "source": CHAT}
    write(vault, {"op": "add", "force": True, "fields": {**fields, "due": day(1)}})
    tree = parse(page(client))
    assert tree.find("li", id="row-A-0008").find("span", class_="due").text() == "tomorrow"
    near = (today() + timedelta(days=3)).strftime("%a")
    assert tree.find("li", id="row-A-0003").find("span", class_="due").text() == near


# -- failures --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/ledger", None),
        ("/ledger/panel?open=A-0001", None),
        (f"/ledger/text?open=A-0005&text={PLAN}", None),
        ("/ledger/write", {"page": "ledger", "op": "done", "item": "A-0001"}),
        ("/ledger/save", {"page": "ledger", "op": "set", "item": "A-0001", "fields.who": "x"}),
        ("/ledger/open", {"page": "ledger", "item": "A-0005", "ref": PLAN}),
    ],
)
def test_a_failure_answers_its_own_error_and_says_no_words(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, data: dict[str, str] | None
) -> None:
    def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("Send Ada the dates")

    monkeypatch.setattr(day_routes, "gather", broken)
    monkeypatch.setattr(day_routes, "posted", broken)
    answer = client.get(path) if data is None else post(client, data, path)
    assert answer.status_code == 500
    assert "Send Ada the dates" not in answer.text


def test_a_poll_that_fails_says_nothing_changed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(root: Path) -> str:
        raise OSError(5, "Access is denied")

    monkeypatch.setattr(day_routes, "version", broken)
    assert client.get("/ledger/changed?v=x").status_code == 204


def test_a_look_alike_gone_by_the_page_s_read_is_left_out(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    form = hidden(page(client), "new-form")
    alike = {"fields.title": "Send Ada the dates again", "fields.who": "Ada Example", "fields.kind": "reply",
             "fields.stream": "pilot"}  # fmt: skip
    real = day_routes.gather

    def and_a_gone_one(vault: Vault, state: DayState, **keys: Any) -> day_routes.Facts:
        return real(vault, state, **{**keys, "alikes": (*keys.get("alikes", ()), "A-0099")})

    monkeypatch.setattr(day_routes, "gather", and_a_gone_one)
    refused = post(client, {**form, **alike})
    assert refused.status_code == 400
    assert [a.attrs["href"] for a in parse(refused.text).find("div", class_="alikes").find_all("a")] == [
        "/ledger?open=A-0001"
    ]
