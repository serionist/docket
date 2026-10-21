"""Meetings' JSON routes, signed in (DR-0666, DR-0667, DR-0709 to DR-0711, DR-0768 to DR-0770): the meetings by day from
today, then the earlier days newest first, each by start, the dimmed ones and their badges; q and stream, and the
items' filters left out; the panel with its people's briefs, the recording, the points, the suggestions and the items
on it; each point button one batch naming the point by its id; a meeting's outcome and notes saved on change; and its
materials through the shared viewer. The clock is noon today in the owner's zone. Invented people only."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
from briefs_kit import brief
from day_kit import LONG, RECORDING, SAME, at, day_vault, meeting, meetings_week, signed_in, today
from docket_testkit import empty_vault, make_config, make_record
from starlette.testclient import TestClient

from docket import recall
from docket.index import build as index_build
from docket.ledger import ops as ledger_ops
from docket.ledger import store, views
from docket.vault.vault import Vault
from docket.web import day as day_routes
from docket.web.day_state import DayState

API = "/api/web/meetings"
WRITE = "/api/web/ledger/write"
SAVE = "/api/web/ledger/save"
BEN = "entities/person/ben-sample.yaml"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    found = day_vault(tmp_path)
    store.write(found, meetings_week(), by="claude", via="cli")
    monkeypatch.setattr(views, "now", lambda: at(0, 12).astimezone(UTC))
    return found


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


def read(client: TestClient, path: str, status: int = 200) -> Any:
    answer = client.get(path)
    assert answer.status_code == status, answer.text
    assert answer.headers["cache-control"] == "no-store"
    return answer.json()


def meetings(client: TestClient, query: str = "", status: int = 200) -> Any:
    return read(client, f"{API}{'?' if query else ''}{query}", status)


def panel(client: TestClient, query: str) -> Any:
    found = read(client, f"{API}/panel?{query}")
    assert found["panel"] is not None, found["missing"]
    return found["panel"]


def subjects(found: Any) -> list[list[str]]:
    return [[m["subject"] for m in d["meetings"]] for d in found["list"]["days"]]


def post(client: TestClient, body: dict[str, Any], path: str = WRITE, status: int = 200) -> Any:
    answer = client.post(path, json={"page": "meetings", **body}, headers=SAME)
    assert answer.status_code == status, answer.text
    return answer.json()


def ops(vault: Vault) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in sorted((vault.root / ledger_ops.OPS_DIR).glob("*.jsonl")):
        out += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


def batch(vault: Vault, answer: Any) -> list[dict[str, Any]]:
    return [o for o in ops(vault) if o.get("batch") == answer["done"]]


def points(client: TestClient, ref: str) -> list[tuple[str, str, str, bool]]:
    return [(p["text"], p["state"], p["answer"], p["proposed"]) for p in panel(client, f"open={ref}")["points"]]


def ref_of(found: Any, subject: str) -> str:
    return next(m["ref"] for d in found["list"]["days"] for m in d["meetings"] if m["subject"] == subject)


# -- the list --------------------------------------------------------------------------------------------------------


def test_meetings_go_by_day_from_today_then_the_earlier_days_newest_first(client: TestClient) -> None:
    found = meetings(client)
    assert found["canonical"] == "/meetings"
    labels = [d["label"] for d in found["list"]["days"]]
    assert labels[:2] == [f"Today, {at(0, 9):%a} {today().day} {at(0, 9):%b}", labels[1]]
    assert labels[1].startswith("Tomorrow") and labels[4].startswith("Yesterday")
    assert [d["ahead"] for d in found["list"]["days"]] == [True, True, True, True, False, False]
    # Each day by start; the retro twenty days back stays for its open point, the kick-off forty back with none goes.
    assert subjects(found) == [
        ["Morning stand-up", "Budget review"],
        ["Pilot sync"],
        ["Cancelled sync"],
        ["Venue walk"],
        ["Supplier call"],
        ["Spring retro"],
    ]
    line = {m["subject"]: m for d in found["list"]["days"] for m in d["meetings"]}
    assert (line["Morning stand-up"]["over"], line["Morning stand-up"]["now"]) == (True, False)
    assert (line["Budget review"]["now"], line["Budget review"]["time"]) == (True, "11:00-12:30")
    # Dimmed: cancelled and declined ones, and one with no points and no open items; each badge as its response says.
    assert {s for s, m in line.items() if m["dim"]} == {"Morning stand-up", "Cancelled sync", "Venue walk"}
    assert {s: m["response"] for s, m in line.items() if m["response"]} == {
        "Budget review": "tentative",
        "Cancelled sync": "cancelled",
        "Venue walk": "declined",
    }
    pilot = line["Pilot sync"]
    assert (pilot["points"], pilot["points_open"], pilot["items"]) == (2, 2, 1)
    assert found["list"]["counts"].startswith("7 meetings from ")
    assert found["list"]["counts"].endswith("; 5 points open")
    assert found["list"]["streams"] == ["ops", "pilot"]


@pytest.mark.parametrize(
    ("query", "shown", "canonical"),
    [
        ("q=supplier", [["Supplier call"]], "/meetings?q=supplier"),
        # An attendee's name and a point's text are a meeting's text too (DR-0665).
        ("q=dora+nobody", [["Budget review"]], "/meetings?q=dora+nobody"),
        ("q=spring+numbers", [["Budget review"]], "/meetings?q=spring+numbers"),
        ("stream=pilot", [["Pilot sync"], ["Supplier call"]], "/meetings?stream=pilot"),
        # The items' kind, who and k are never refused here, and the canonical URL leaves them out (DR-0768).
        ("kind=bogus&who=Ada&k=0&state=closed", None, "/meetings"),
        ("q=zeppelin", [], "/meetings?q=zeppelin"),
    ],
)
def test_q_and_stream_narrow_the_meetings(
    client: TestClient, query: str, shown: list[list[str]] | None, canonical: str
) -> None:
    found = meetings(client, query)
    assert found["canonical"] == canonical
    if shown is not None:
        assert subjects(found) == shown
    if shown == []:
        assert found["list"]["empty"] == "Nothing here matches."


def test_a_ledger_with_no_meetings_says_where_they_come_from(tmp_path: Path) -> None:
    with signed_in(empty_vault(tmp_path, make_config())) as client:
        found = meetings(client)
    assert (found["list"]["days"], found["list"]["empty"]) == ([], "No meetings in these four weeks.")


# -- the panel -------------------------------------------------------------------------------------------------------


def with_ben_s_brief(vault: Vault) -> None:
    """Ben's entity, by his name, an alias and his address, and a brief on him, in the index."""
    (vault.root / BEN).parent.mkdir(parents=True, exist_ok=True)
    (vault.root / BEN).write_text(
        "id: ben-sample\ntype: person\nname: {en: Ben Sample}\naliases:\n  - {text: Ben}\n"
        "ids: {email: ben@northwind.example}\n",
        encoding="utf-8",
        newline="\n",
    )
    vault.commit([BEN], "Ben's entity")
    said = make_record(7, text="Ben Sample sends the spring numbers on Friday.")
    vault.write_run("chat", [said])
    recall.doc_put(
        vault, brief("ben-sample", "Ben Sample", [said.rid], [[f"- He sends the numbers. [r:{said.rid}]"], [], []])
    )
    index_build.rebuild(vault)


def test_the_panel_links_its_people_to_their_briefs_and_its_recording(client: TestClient, vault: Vault) -> None:
    with_ben_s_brief(vault)
    found = meetings(client)
    budget = panel(client, f"open={ref_of(found, 'Budget review')}")
    ben = "/briefs?brief=brief:ben-sample"
    assert budget["organiser"] == {"name": "Ben Sample", "href": ben}
    # His name and his address find his brief; a name with no person, or a person with no brief, is plain text.
    assert [(a["name"], a["href"]) for a in budget["attendees"]] == [
        ("Ben Sample", ben),
        ("Ada Example", ""),
        ("ben@northwind.example", ben),
        ("Dora Nobody", ""),
    ]
    assert (budget["where"], budget["response"], budget["response_words"]) == ("Room 2", "tentative", "tentative")
    assert (budget["keyless"], budget["over"], budget["rolls"]) == (False, False, "")
    assert budget["facts"] == "added by claude; one of a series"
    # A doc labelled recording is no recording: it stays a link to its doc on the Search page.
    note = {"op": "material", "item": "M-0004", "ref": "recording-note:2026-10-07-supplier-call", "label": "recording"}
    store.write(vault, [note], by="claude", via="cli")
    supplier = panel(client, f"open={ref_of(found, 'Supplier call')}")
    assert [(r["kind"], r["href"]) for r in supplier["recordings"]] == [("recording", f"/recording?rid={RECORDING}")]
    # The recording is a material too, linked the same way, beside the plan and the note.
    assert [m["kind"] for m in supplier["materials"]] == ["recording", "text", "record"]
    assert supplier["keyless"] and supplier["over"]
    assert (
        supplier["rolls"]
        == "It's over: the next sync moves its 1 open point to the next meeting of its series, if there is one."
    )
    assert supplier["agenda"] == f"Supplier call, {supplier['when']}\n- The offer in writing"
    # A meeting's own add takes no Undo, as an item's never does.
    assert [(h["line"], h["button"]) for h in supplier["history"]][-1] == ("added", None)


def test_a_rolled_point_takes_no_button_and_each_end_of_the_roll_links_the_other(
    client: TestClient, vault: Vault
) -> None:
    # Yesterday's call gets its outcome and next week's is added, so the sync's rollover rolls its open point on.
    changes = [
        meeting("next", "Supplier call", 6, 15),
        {"op": "set", "item": "M-0004", "fields": {"outcome": "Agreed"}},
    ]
    store.write(vault, changes, by="claude", via="cli")
    store.write(vault, [{"op": "rollover"}], by="sync", via="tool")
    found = meetings(client)
    later = [m["ref"] for d in found["list"]["days"] for m in d["meetings"] if m["subject"] == "Supplier call"]
    gone, copy = panel(client, "open=M-0004")["points"][0], panel(client, f"open={later[0]}")["points"][0]
    assert (gone["state"], gone["went_to"], gone["came_from"]) == (
        "rolled",
        {"ref": later[0], "href": f"/meetings?open={later[0]}"},
        None,
    )
    assert (copy["state"], copy["came_from"]) == ("open", {"ref": "M-0004", "href": "/meetings?open=M-0004"})
    # A rolled point changes only by an undo, so the page sends no point_set for it: the writer refuses one.
    refused = post(
        client, {"op": "point_set", "items": ["M-0004"], "point": gone["id"], "fields": {"state": "open"}}, status=400
    )
    assert "rolled on" in refused["error"]
    assert panel(client, "open=M-0004")["rolls"] == ""


def test_the_panel_lists_its_items_and_once_asked_the_items_worth_raising(client: TestClient) -> None:
    plain = panel(client, "open=M-0001")
    assert plain["suggestions"] is None
    assert [(i["row"]["ref"], i["href"]) for i in plain["items"]] == [("A-0002", "/ledger?open=A-0002")]
    asked = read(client, f"{API}/panel?open=M-0001&suggest=1")
    assert asked["canonical"] == "/meetings?open=M-0001&suggest=1"
    assert [(s["row"]["ref"], s["why"]) for s in asked["panel"]["suggestions"]] == [("A-0002", "linked to the meeting")]
    # Suggest goes with an open meeting only.
    assert meetings(client, "suggest=1")["canonical"] == "/meetings"


@pytest.mark.parametrize(
    ("query", "said"),
    [
        ("open=A-0002", "A-0002 is an item; its panel is on the Ledger."),
        ("open=M-0099", "no meeting M-0099"),
    ],
)
def test_a_panel_that_can_t_open_says_why(client: TestClient, query: str, said: str) -> None:
    found = read(client, f"{API}/panel?{query}")
    assert (found["panel"], found["missing"]) == (None, said)


def test_a_stream_s_id_opens_no_panel_on_any_day_page(client: TestClient, vault: Vault) -> None:
    stream = next(o for o in ops(vault) if o["op"] == "stream")["id"]
    for path in (f"{API}/panel", "/api/web/ledger/panel"):
        assert read(client, f"{path}?open={stream}")["missing"] == f"{stream} is a stream; a stream has no panel yet."


# -- the points' buttons (DR-0667) ----------------------------------------------------------------------------------


def test_each_point_button_is_one_batch_by_user_via_page_naming_the_point_by_its_id(
    client: TestClient, vault: Vault
) -> None:
    first, proposed = (p["id"] for p in panel(client, "open=M-0001")["points"])
    answered = post(client, {"op": "point_set", "items": ["M-0001"], "point": first, "fields": {"answer": "the 12th"}})
    assert answered["notice"]["lines"] == ["M-0001: point answered, answer: the 12th"]
    written = batch(vault, answered)
    assert [(o["op"], o["point"], o["by"], o["via"]) for o in written] == [("point_set", first, "user", "page")]
    # Open again clears the answer; a drop and a new text are one change each.
    post(client, {"op": "point_set", "items": ["M-0001"], "point": first, "fields": {"state": "open", "answer": ""}})
    post(client, {"op": "point_set", "items": ["M-0001"], "point": first, "fields": {"text": "The venue's two dates"}})
    post(client, {"op": "point_set", "items": ["M-0001"], "point": first, "fields": {"state": "dropped"}})
    kept = post(client, {"op": "keep", "items": ["M-0001"], "point": proposed})
    assert [o["fields"] for o in batch(vault, kept)] == [{"proposed": None}]
    added = post(client, {"op": "point", "items": ["M-0001"], "fields": {"text": "Who prints the badges"}})
    assert added["notice"]["lines"] == ["M-0001: point added: Who prints the badges"]
    assert points(client, "M-0001") == [
        ("The venue's two dates", "dropped", "", False),
        ("Who books the room", "open", "", False),
        ("Who prints the badges", "open", "", False),
    ]
    # The notice's Undo takes the add back.
    post(client, {"op": "undo", "batch": added["done"], "said": "undo"})
    assert [p[0] for p in points(client, "M-0001")][-1] == "Who books the room"


def test_proposed_points_are_kept_or_removed_all_at_once_and_a_removal_is_one_undo_batch(
    client: TestClient, vault: Vault
) -> None:
    for text in ("Ask about parking", "Ask about the menu"):
        store.write(
            vault,
            [{"op": "point", "item": "M-0001", "fields": {"text": text, "proposed": True}}],
            by="sync",
            via="tool",
        )
    shown = panel(client, "open=M-0001")
    assert shown["proposed"] == 3
    proposed = [p["id"] for p in shown["points"] if p["proposed"]]
    removed = post(client, {"op": "remove", "items": ["M-0001"], "points": proposed[1:]})
    assert [(o["op"], o["of"]) for o in batch(vault, removed)] == [("undo", p) for p in proposed[1:]]
    assert points(client, "M-0001") == [
        ("The venue's dates", "open", "", False),
        ("Who books the room", "open", "", True),
    ]
    # Undone, the two come back; then keep takes every proposed one.
    post(client, {"op": "undo", "batch": removed["done"], "said": "undo"})
    kept = post(client, {"op": "keep", "items": ["M-0001"]})
    assert len(batch(vault, kept)) == 3
    assert panel(client, "open=M-0001")["proposed"] == 0


def test_a_point_made_an_item_takes_the_stream_given_or_the_meeting_s(client: TestClient, vault: Vault) -> None:
    found = meetings(client)
    budget = ref_of(found, "Budget review")
    point = panel(client, f"open={budget}")["points"][0]["id"]
    made = post(client, {"op": "point_item", "items": [budget], "point": point, "fields": {"kind": "", "who": " "}})
    add = next(o for o in batch(vault, made) if o["op"] == "add")
    # "Ben: the spring numbers" waits on Ben, in the meeting's stream, hanging on the meeting.
    assert (add["fields"]["kind"], add["fields"]["who"], add["fields"]["stream"]) == ("waiting", "Ben", "ops")
    shown = panel(client, f"open={budget}")
    assert shown["points"][0]["item"]["href"].startswith("/ledger?open=A-")
    assert [i["row"]["title"] for i in shown["items"]] == ["Ben: the spring numbers"]
    # The retro has no stream: the writer refuses an item from it unless one is given.
    retro = ref_of(found, "Spring retro")
    point = panel(client, f"open={retro}")["points"][0]["id"]
    refused = post(client, {"op": "point_item", "items": [retro], "point": point}, status=400)
    assert "has no stream" in refused["error"]
    made = post(client, {"op": "point_item", "items": [retro], "point": point, "fields": {"stream": "pilot"}})
    assert next(o for o in batch(vault, made) if o["op"] == "add")["fields"]["stream"] == "pilot"


def test_a_suggestion_raised_takes_its_item_s_whole_title(client: TestClient, vault: Vault) -> None:
    budget = ref_of(meetings(client), "Budget review")
    asked = read(client, f"{API}/panel?open={budget}&suggest=1")["panel"]["suggestions"]
    long = next(s for s in asked if s["row"]["title"].startswith("Ask the venue"))
    assert long["row"]["title"] != LONG, "the list cuts a long title"
    post(client, {"op": "point", "items": [budget], "fields": {"item": long["row"]["ref"]}})
    shown = read(client, f"{API}/panel?open={budget}&suggest=1")["panel"]
    assert (shown["points"][-1]["text"], shown["points"][-1]["item"]["ref"]) == (LONG, long["row"]["ref"])
    # On a point now, it is no longer suggested.
    assert long["row"]["ref"] not in [s["row"]["ref"] for s in shown["suggestions"]]


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ({"op": "point_set", "point": "1", "fields": {"state": "answered"}}, "point is a point's id, not '1'"),
        ({"op": "point", "fields": {}}, "a point needs text, or an item to raise"),
        ({"op": "point_set", "point": "STRANGER", "fields": {}}, "point_set takes a field: text, state, answer"),
        ({"op": "remove", "points": []}, "remove names the points to remove"),
        ({"op": "point", "fields": {"item": "A-0099"}}, "A-0099 isn't an item to raise"),
        ({"op": "point", "items": [], "fields": {"text": "x"}}, "point takes one meeting"),
        ({"op": "remove", "items": ["M-0099"], "points": ["01M5ET802AE7YNG13XVSJN0PRP"]}, "no meeting M-0099"),
        ({"op": "remove", "items": ["A-0002"], "points": ["01M5ET802AE7YNG13XVSJN0PRP"]}, "A-0002 isn't a meeting"),
    ],
)
def test_a_point_button_the_page_can_t_send_is_refused_and_writes_nothing(
    client: TestClient, vault: Vault, body: dict[str, Any], said: str
) -> None:
    before = len(ops(vault))
    assert post(client, {"items": ["M-0001"], **body}, status=400)["error"] == said
    assert len(ops(vault)) == before


def test_a_removal_takes_only_its_meeting_s_points_so_it_undoes_nothing_else(client: TestClient, vault: Vault) -> None:
    other = panel(client, f"open={ref_of(meetings(client), 'Supplier call')}")["points"][0]["id"]
    triage = next(o["id"] for o in ops(vault) if o["op"] == "triage")
    before = len(ops(vault))
    for stranger in (other, triage):
        refused = post(client, {"op": "remove", "items": ["M-0001"], "points": [stranger]}, status=400)
        assert refused["error"] == f"M-0001 has no point {stranger}"
    assert len(ops(vault)) == before


# -- the outcome and notes, saved on change (DR-0710, DR-0770) ------------------------------------------------------


def test_a_meeting_s_outcome_and_notes_save_on_change_with_its_line_and_panel(client: TestClient, vault: Vault) -> None:
    version = meetings(client)["version"]
    saved = post(client, {"op": "set", "items": ["M-0001"], "fields": {"outcome": "The 12th, the long hall"}}, SAVE)
    assert (saved["before"], saved["panel"]["outcome"], saved["line"]["ref"]) == (
        version,
        "The 12th, the long hall",
        "M-0001",
    )
    assert saved["version"] == day_routes.version(vault.root)
    noted = post(client, {"op": "set", "items": ["M-0001"], "fields": {"notes": "Ben brings the keys."}}, SAVE)
    assert noted["panel"]["notes"] == "Ben brings the keys."
    assert [o["fields"] for o in ops(vault)[-2:]] == [
        {"outcome": "The 12th, the long hall"},
        {"notes": "Ben brings the keys."},
    ]
    assert ops(vault)[-1]["via"] == "page"


def test_a_save_whose_meeting_is_gone_by_its_read_says_so(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    real = day_routes.gather

    def gone(vault: Vault, state: DayState, **keys: Any) -> day_routes.Facts:
        found = real(vault, state, **keys)
        found.held, found.missing = None, "M-0001 was undone"
        return found

    monkeypatch.setattr(day_routes, "gather", gone)
    answer = post(client, {"op": "set", "items": ["M-0001"], "fields": {"outcome": "x"}}, SAVE, status=400)
    assert answer["error"] == "M-0001 was undone"


@pytest.mark.parametrize(
    ("body", "said"),
    [
        (
            {"op": "set", "items": ["M-0001"], "fields": {"title": "x"}},
            "a save on Meetings sets a meeting's outcome or notes",
        ),
        ({"op": "notes", "items": ["M-0001"], "notes": "x"}, "a save on Meetings sets a meeting's outcome or notes"),
        ({"op": "set", "items": ["A-0002"], "fields": {"outcome": "x"}}, "A-0002 isn't a meeting"),
    ],
)
def test_a_save_on_meetings_takes_only_a_meeting_s_outcome_or_notes(
    client: TestClient, vault: Vault, body: dict[str, Any], said: str
) -> None:
    before = len(ops(vault))
    assert post(client, body, SAVE, status=400)["error"] == said
    assert len(ops(vault)) == before


# -- a meeting's materials, through the shared viewer and launcher (DR-0664) ----------------------------------------


def test_a_meeting_s_text_material_shows_in_the_viewer_and_opens_with_its_app(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    supplier = ref_of(meetings(client), "Supplier call")
    shown = read(client, f"/api/web/ledger/text?open={supplier}&text=notes/plan.md")
    assert shown["text"].startswith("# The plan")
    assert read(client, f"{API}/panel?open={supplier}&text=notes/plan.md")["canonical"].endswith("&text=notes/plan.md")
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(day_routes, "launch", lambda path, *, reveal: seen.append((Path(path).name, reveal)))
    opened = client.post(
        "/api/web/ledger/open", json={"page": "meetings", "item": supplier, "ref": "notes/plan.md"}, headers=SAME
    )
    assert opened.status_code == 200, opened.text
    assert seen == [("plan.md", False)]
