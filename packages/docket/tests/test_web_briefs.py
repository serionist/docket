"""The Briefs page's read, signed in, on the invented briefs vault (DR-0780, DR-0558): the briefs by entity type, a
brief with the raw its open line cites, the URL's states and refusals, and what a never-store list hides, by URL too.
The page itself is the app's document; its links are the URLs these answers carry."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
from briefs_kit import ABOUT_BEN, BEN, CALL, DORA, HARBOUR, NORTHWIND, NOTE, briefs_template
from docket_testkit import OFFER, git
from docket_webkit import TOKEN, signed_in
from starlette.testclient import TestClient

from docket.briefs import BriefsReport, BriefState, NewRecords
from docket.errors import DocketError
from docket.recall import DocLine, DocView
from docket.service.app import ServiceApp
from docket.service.writer import IndexUpdater
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.docstore import DocStore
from docket.vault.vault import Vault
from docket.web import briefs
from docket.web.briefs_state import BriefsState, BriefsStateError
from docket.web.briefs_view import Facts, build

API = "/api/web/briefs"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return briefs_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


def answer(client: TestClient, query: str = "", status: int = 200) -> dict[str, Any]:
    got = client.get(f"{API}?{query}" if query else API)
    assert got.status_code == status, got.text
    assert got.headers["cache-control"] == "no-store"
    return got.json()


def view(client: TestClient, query: str = "") -> dict[str, Any]:
    found = answer(client, query)["view"]
    assert found is not None
    return found


def rows(found: dict[str, Any]) -> dict[str, list[str]]:
    """Each group's entity type, with its briefs' names in order."""
    return {g["type"]: [r["name"] for r in g["rows"]] for g in found["groups"]}


def by_name(found: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["name"]: r for g in found["groups"] for r in g["rows"]}


def line_of(tmp_path: Path, doc_id: str, start: str) -> int:
    doc = DocStore(Vault.open(tmp_path / "vault")).get(doc_id)
    assert doc is not None
    return next(n for n, line in doc.lines() if line.startswith(start))


def lines(found: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {line["n"]: line for line in found["lines"]}


def list_more(tmp_path: Path, **values: list[str]) -> None:
    """vault.yaml's never-store lists grown, uncommitted: an edit that only keeps more out is in force at once."""
    path = tmp_path / "vault" / CONFIG_FILE
    config = VaultConfig.load(path)
    grown = config.never_store.model_copy(update=values)
    path.write_text(config.model_copy(update={"never_store": grown}).dump(), encoding="utf-8", newline="\n")


# -- the list ---------------------------------------------------------------------------------------------------------


def test_the_page_is_the_app_s_and_lists_the_briefs_by_entity_type(client: TestClient) -> None:
    assert '<div id="root">' in client.get("/briefs?brief=brief:ben-sample&line=3").text
    read = answer(client)
    assert (read["canonical"], read["notice"], read["waiting"], read["decisions"]) == ("/briefs", None, "", None)
    found = read["view"]
    assert found["mode"] == "list"
    assert rows(found) == {
        "person": ["Ben Sample"],
        "customer": ["Northwind"],
        "site": ["Harbour site"],
        "orphaned": ["Dóra Minta"],
    }
    assert found["summary"] == "4 briefs: 2 stale, 1 current, 1 orphaned"
    named = by_name(found)
    assert (named["Ben Sample"]["state"], named["Ben Sample"]["new"]) == ("stale", 1)
    assert (named["Northwind"]["state"], named["Northwind"]["flagged"]) == ("stale", 1)
    assert named["Northwind"]["url"] == f"/briefs?brief={NORTHWIND}"
    assert named["Northwind"]["rev"] == 1 and named["Northwind"]["age"]
    assert named["Harbour site"]["state"] == "current"
    assert named["Dóra Minta"]["orphan"] == "its entity is gone; cleo-test holds its name now"


def test_a_type_narrows_the_list_and_its_tabs_say_how_many(client: TestClient) -> None:
    found = view(client, "type=person")
    assert rows(found) == {"person": ["Ben Sample"]}
    tabs = {t["name"]: t for t in found["types"]}
    assert [(t["name"], t["count"]) for t in found["types"]] == [
        ("All", 4),
        ("person", 1),
        ("customer", 1),
        ("site", 1),
        ("orphaned", 1),
    ]
    assert tabs["person"]["on"] and not tabs["All"]["on"]
    assert tabs["All"]["url"] == "/briefs"
    # A brief opened from the list keeps the type, so the way back does too.
    assert by_name(found)["Ben Sample"]["url"] == f"/briefs?brief={BEN}&type=person"
    assert rows(view(client, "type=orphaned")) == {"orphaned": ["Dóra Minta"]}
    none = view(client, "type=project")
    assert (none["groups"], none["empty"], none["empty_how"]) == ([], "No project briefs.", "")


def test_a_vault_with_no_brief_says_how_one_is_written(client: TestClient, tmp_path: Path) -> None:
    for path in (tmp_path / "vault" / "processed" / "brief").glob("*.md"):
        path.unlink()
    found = view(client)
    assert (found["empty"], found["types"], found["summary"]) == ("No briefs yet.", [], "")
    assert found["empty_how"].startswith("Ask Claude for a brief")


# -- an open brief ----------------------------------------------------------------------------------------------------


def test_a_brief_opens_with_its_lines_and_a_line_with_the_raw_it_cites(client: TestClient, tmp_path: Path) -> None:
    found = view(client, f"brief={NORTHWIND}")
    assert (found["mode"], found["title"], found["id"], found["state"]) == ("brief", "Northwind", NORTHWIND, "stale")
    assert found["meta"][:2] == ["customer", "rev 1"]
    assert found["back"] == "/briefs"
    assert found["raw"] is None and found["choose"].startswith("Choose a line")
    mail = line_of(tmp_path, NORTHWIND, "- The offer comes by mail.")
    line = lines(found)[mail]
    assert line["url"] == f"/briefs?brief={NORTHWIND}&line={mail}#raw"
    assert line["anchors"][0]["url"] == f"/search?hit={quote(f'r:{OFFER}#1', safe=':@/,')}"
    assert not line["open"]
    # The line's own URL, as the app follows it: its query string read, its #raw where the raw is.
    opened = answer(client, line["url"].partition("?")[2].partition("#")[0])
    assert opened["canonical"] == f"/briefs?brief={NORTHWIND}&line={mail}"
    raw = opened["view"]["raw"]
    assert (raw["n"], raw["body"], raw["close"]) == (mail, "The offer comes by mail.", f"/briefs?brief={NORTHWIND}")
    [span] = raw["spans"]
    assert "The consultant's offer" in span["span"]["head"]
    assert span["link"]["text"] == "On the Search page"
    assert lines(opened["view"])[mail]["open"]


def test_a_person_s_brief_links_to_their_items_on_the_ledger(client: TestClient) -> None:
    """DR-0669: a person's brief, and only a person's, links to the Ledger narrowed to them."""
    ben = view(client, f"brief={BEN}")["ledger"]
    assert (ben["url"], ben["text"]) == ("/ledger?who=Ben+Sample", "Ben Sample's items on the Ledger")
    assert view(client, f"brief={NORTHWIND}")["ledger"] is None


def test_a_time_anchor_opens_the_recording_page_at_its_time(client: TestClient, tmp_path: Path) -> None:
    n = line_of(tmp_path, NORTHWIND, "- 7 Oct, supplier call")
    found = view(client, f"brief={NORTHWIND}&line={n}")
    assert lines(found)[n]["anchors"][0]["url"] == f"/recording?rid={CALL}&at=04:00-05:00#at"
    [span] = found["raw"]["spans"]
    assert span["link"]["url"] == f"/recording?rid={CALL}&at=04:00-05:00#at"
    assert "offer in writing" in str(span["span"]["segments"])


def test_a_ref_is_followed_one_level_with_its_own_raw(client: TestClient, tmp_path: Path) -> None:
    n = line_of(tmp_path, BEN, "- Ben wants the deadline")
    found = view(client, f"brief={BEN}&line={n}")
    deadline = line_of(tmp_path, NOTE, "- Dóra agrees the deadline")
    assert lines(found)[n]["refs"][0]["url"] == f"/search?hit={NOTE}&lines={deadline}"
    [cited] = found["raw"]["cited"]
    assert cited["link"]["text"] == f"{NOTE}, line {deadline}"
    assert cited["text"] == "Dóra agrees the deadline with Ben on Friday."
    assert cited["spans"][0]["link"]["url"] == f"/recording?rid={CALL}&at=02:30-03:20#at"


def test_a_flagged_line_is_marked_and_says_what_changed(client: TestClient, tmp_path: Path) -> None:
    n = line_of(tmp_path, NORTHWIND, "- The consultant's price")
    found = view(client, f"brief={NORTHWIND}&line={n}")
    assert lines(found)[n]["flagged"]
    assert "1 line cites a line that changed since; marked below." in found["notices"]
    assert "reads otherwise" in str(found["raw"])


def test_a_stale_brief_lists_its_new_records(client: TestClient) -> None:
    found = view(client, f"brief={BEN}")
    assert "1 record about Ben Sample since rev 1:" in found["notices"]
    [new] = found["new"]
    assert (new["text"], new["url"]) == (ABOUT_BEN.rid, f"/search?hit={quote(f'r:{ABOUT_BEN.rid}', safe=':@/,')}")


def test_an_orphan_names_the_entity_that_holds_its_name(client: TestClient) -> None:
    found = view(client, f"brief={DORA}")
    assert found["meta"][:2] == ["orphaned", "rev 1"]
    assert "Its entity is gone; cleo-test holds its name now." in found["notices"]


def test_a_current_brief_has_no_notice_and_its_way_back_keeps_the_type(client: TestClient) -> None:
    found = view(client, f"brief={HARBOUR}&type=site")
    assert (found["notices"], found["back"]) == ([], "/briefs?type=site")


def test_a_line_that_cites_nothing_or_isn_t_there_says_so(client: TestClient, tmp_path: Path) -> None:
    n = line_of(tmp_path, HARBOUR, "## Last conversations")
    heading = view(client, f"brief={HARBOUR}&line={n}")
    assert lines(heading)[n]["url"] is None
    assert heading["raw"]["empty"] == f"Line {n} cites nothing: it has no anchors or refs."
    for far in (2, 99):
        assert f"line {far} isn't in the doc's body" in str(view(client, f"brief={HARBOUR}&line={far}")["raw"])


# -- never-store ------------------------------------------------------------------------------------------------------


def test_a_brief_a_list_now_holds_is_hidden_and_counted(client: TestClient, tmp_path: Path) -> None:
    list_more(tmp_path, people=["ben@northwind.example"])
    found = view(client)
    assert "Ben Sample" not in str(found)
    assert found["hidden_note"] == "1 brief hidden: a never-store list holds its entity."
    hidden = answer(client, f"brief={BEN}&line=10")
    assert (hidden["view"]["mode"], hidden["view"]["title"]) == ("hidden", "A hidden brief")
    assert "Ben" not in str(hidden["view"]) and "ben-sample" not in str(hidden["view"])
    assert hidden["decisions"] is None
    # A listed person opens nothing either, brief or no brief: by the name an id spells, or by a contact id.
    list_more(tmp_path, people=["ben@northwind.example", "Vera Nobody", "U0CLEO"])
    for nobody in ("brief:vera-nobody", "brief:cleo-test"):
        assert view(client, f"brief={nobody}")["mode"] == "hidden"
    assert "cleo" not in str(view(client, "brief=brief:cleo-test")).casefold()


def test_a_line_a_list_now_names_is_hidden_and_opens_nothing(client: TestClient, tmp_path: Path) -> None:
    n = line_of(tmp_path, NORTHWIND, "- 7 Oct, supplier call")
    list_more(tmp_path, keywords=["offer in writing"])
    found = view(client, f"brief={NORTHWIND}&line={n}")
    line = lines(found)[n]
    assert (line["body"], line["hidden"], line["url"], line["anchors"]) == (
        "a line hidden: it names a never-store value",
        True,
        None,
        [],
    )
    raw = found["raw"]
    assert raw["empty"] == f"Line {n} is hidden: it names a never-store value, so nothing of it opens."
    assert (raw["spans"], raw["cited"]) == ([], [])
    assert "offer in writing" not in str(found)


def test_a_cited_line_a_list_now_names_is_hidden(client: TestClient, tmp_path: Path) -> None:
    n = line_of(tmp_path, BEN, "- Ben wants the deadline")
    list_more(tmp_path, keywords=["agrees the deadline"])
    [cited] = view(client, f"brief={BEN}&line={n}")["raw"]["cited"]
    assert (cited["text"], cited["hidden"], cited["spans"]) == ("a line hidden: it names a never-store value", True, [])


# -- the URL and its refusals -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "named"),
    [
        ("brief=northwind", "brief is a brief's id"),
        ("brief=minutes:2026-10-13-fleet-review", "brief is a brief's id"),
        ("brief=brief:Not%20One", "brief is a brief's id"),
        (f"brief={BEN}&line=0", "line is a line's number"),
        (f"brief={BEN}&line=two", "line is a line's number"),
        ("type=people", "type is an entity type"),
        ("done=decided", "done is what a click did"),
    ],
)
def test_a_value_the_url_can_t_hold_is_refused_in_its_words(client: TestClient, query: str, named: str) -> None:
    assert named in answer(client, query, status=400)["error"]


def test_a_brief_that_isn_t_there_says_so(client: TestClient) -> None:
    found = view(client, "brief=brief:fleet-management")
    assert found["mode"] == "missing"
    assert found["message"].startswith("No brief brief:fleet-management.")
    assert found["link"] == {"text": "All briefs", "url": "/briefs", "title": ""}


def test_the_url_reads_and_writes_one_canonical_form_and_says_a_click_once(client: TestClient) -> None:
    state = BriefsState.read({"type": "person", "line": "12", "brief": BEN, "other": "kept out", "done": "reject"})
    assert state == BriefsState(BEN, 12, "person", "reject")
    assert state.url() == f"/briefs?brief={BEN}&line=12&type=person"
    assert state.link() == f"{state.url()}#raw"
    assert BriefsState(BEN).link() == f"/briefs?brief={BEN}"
    assert BriefsState().url() == "/briefs"
    # A line is read only with a brief.
    assert BriefsState.read({"line": "4"}) == BriefsState()
    with pytest.raises(BriefsStateError, match="type is an entity type"):
        BriefsState.read({"type": "Person"})
    # A URL from before the app, naming what a click did: its notice once, and a canonical URL without it.
    said = answer(client, f"other=1&brief={HARBOUR}&done=confirm")
    assert (said["canonical"], said["notice"]) == (f"/briefs?brief={HARBOUR}", "Confirmed: it is a decision now.")


def test_the_token_never_reads_the_page(client: TestClient) -> None:
    token = client.get(API, headers={"Authorization": f"Bearer {TOKEN}"})
    assert token.status_code == 403
    client.cookies.clear()
    assert client.get(API).status_code == 401


def test_the_index_being_built_waits_and_a_failure_says_no_words(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = client.app
    assert isinstance(app, ServiceApp)
    updater = app.service.updater
    app.service.updater = IndexUpdater(lambda: time.sleep(0.5), wait_seconds=0.01)
    waiting = answer(client, f"brief={BEN}")
    assert (waiting["view"], waiting["canonical"]) == (None, f"/briefs?brief={BEN}")
    assert "the index is being built" in waiting["waiting"]
    app.service.updater = updater

    def failing(*_args: object) -> object:
        raise RuntimeError("words from a record")

    def refusing(*_args: object) -> object:
        raise DocketError("the index can't be read")

    monkeypatch.setattr(briefs, "gather", refusing)
    assert answer(client, status=400)["error"] == "the index can't be read"
    monkeypatch.setattr(briefs, "gather", failing)
    failed = client.get(API)
    assert failed.status_code == 500
    assert "words from a record" not in failed.text


# -- the view, on facts alone -----------------------------------------------------------------------------------------


def test_a_brief_with_its_history_not_here_says_so() -> None:
    now = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
    found = BriefState(
        id=BEN, entity="ben-sample", name="Ben Sample", type="person", rev=2,
        new=NewRecords(count=7, newest=["a", "b", "c", "d", "e"]), state="stale", why="its history isn't here",
    )  # fmt: skip
    doc = DocView(
        id=BEN, type="brief", rev=2, path="processed/brief/ben-sample.md", by="x", covers=[], supersedes=[],
        entities=["ben-sample"], superseded_by=[], text="",
        lines=[DocLine(n=1, text="---"), DocLine(n=2, text="---"), DocLine(n=3, text="# Ben Sample"),
               DocLine(n=4, text="- Said twice. @00:10")],
    )  # fmt: skip
    doc = doc.model_copy(update={"covers": ["calls:rec-1", "calls:rec-2"]})
    view_ = build(Facts(now, ZoneInfo("UTC"), BriefsReport(briefs=[found]), found, doc=doc), BriefsState(BEN))
    assert view_.meta == ["person", "rev 2"]
    assert view_.notices == [
        "Stale: its history isn't here, so Docket can't tell which records it hasn't seen.",
        "7 records about Ben Sample since rev 2 (the newest five):",
    ]
    assert [link.text for link in view_.new] == ["a", "b", "c", "d", "e"]
    # A short anchor on a brief covering two records names no record, so it links nowhere.
    [anchor] = view_.lines[-1].anchors
    assert (anchor.text, anchor.url) == ("@00:10", "")


def test_an_orphan_no_one_entity_holds_the_name_of_says_so() -> None:
    gone = BriefState(id="brief:vera", entity="vera", name="Vera", type="", rev=1, state="orphaned")
    view_ = build(Facts(datetime.now(UTC), ZoneInfo("UTC"), BriefsReport(briefs=[gone])), BriefsState())
    [group] = view_.groups
    assert (group.type, group.rows[0].orphan) == ("orphaned", "its entity is gone, and no one entity holds its name")


def test_a_decisions_line_from_before_the_log_is_marked_and_the_brief_stale(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "vault"
    path = root / "processed" / "brief" / "harbour-site.md"
    text = path.read_text(encoding="utf-8").replace("rev: 1", "rev: 2").rstrip("\n")
    path.write_text(text + "\n- A new printer was decided. [r:chat:C0OPS/1791400800.000100]\n", encoding="utf-8")
    git(root, "add", "--", str(path))
    git(root, "commit", "-qm", f"Doc: {HARBOUR} rev 2")
    n = line_of(tmp_path, HARBOUR, "- A new printer was decided.")
    found = view(client, f"brief={HARBOUR}")
    assert (lines(found)[n]["unconfirmed"], lines(found)[n]["flagged"]) == (True, False)
    assert any("a Decisions line cites no confirmed decision" in notice for notice in found["notices"])
    assert by_name(view(client))["Harbour site"]["state"] == "stale"
