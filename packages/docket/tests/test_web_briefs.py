"""The Briefs page over HTTP, signed in, on the invented briefs vault (DR-0557, DR-0558): the briefs by entity type, a
brief beside the raw its open line cites, the URL's states, and what a never-store list hides, by URL too."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
from briefs_kit import ABOUT_BEN, BEN, CALL, DORA, HARBOUR, NORTHWIND, NOTE, briefs_template
from docket_testkit import OFFER, git, indexed, read_only
from docket_webkit import Element, a11y_problems, html_problems, parse, serving, signed_in
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
from docket.web.templating import PAGE_CSP


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return briefs_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


@pytest.fixture(scope="module")
def seen(tmp_path_factory: pytest.TempPathFactory, template: Path) -> Path:
    """One copy, its index built, for the tests that only read: a write into it fails the run (DR-0531)."""
    return read_only(indexed(tmp_path_factory, template))


@pytest.fixture
def reader(seen: Path) -> Iterator[TestClient]:
    with serving(seen) as found:
        yield found


def page(client: TestClient, path: str, status: int = 200) -> Element:
    """A state of the page: its status, the page CSP, no-store, and the checks every page passes."""
    answer = client.get(path)
    assert answer.status_code == status, answer.text
    assert answer.headers["Content-Security-Policy"] == PAGE_CSP
    assert answer.headers["Cache-Control"] == "no-store"
    assert not html_problems(answer.text), path
    assert not a11y_problems(answer.text), path
    return parse(answer.text)


def rows(tree: Element) -> dict[str, list[str]]:
    """Each group's heading, with its briefs' names in order."""
    return {
        g.find("h2").text(): [a.text() for a in g.find_all("a", class_="brief-name")]
        for g in tree.find_all("section", class_="brief-group")
    }


def line_of(root: Path, doc_id: str, start: str) -> int:
    doc = DocStore(Vault.open(root)).get(doc_id)
    assert doc is not None
    return next(n for n, line in doc.lines() if line.startswith(start))


def lines(tree: Element) -> dict[int, Element]:
    return {
        int(d.attrs["data-line"]): d for d in tree.find("section", class_="brief-column").find_all("div", class_="line")
    }


def list_more(tmp_path: Path, **values: list[str]) -> None:
    """vault.yaml's never-store lists grown, uncommitted: an edit that only keeps more out is in force at once."""
    path = tmp_path / "vault" / CONFIG_FILE
    config = VaultConfig.load(path)
    grown = config.never_store.model_copy(update=values)
    path.write_text(config.model_copy(update={"never_store": grown}).dump(), encoding="utf-8", newline="\n")


# -- the list ---------------------------------------------------------------------------------------------------------


def test_the_list_groups_the_briefs_by_entity_type_with_plain_links(reader: TestClient) -> None:
    tree = page(reader, "/briefs")
    assert tree.find("title").text() == "Briefs - Docket"
    assert rows(tree) == {
        "person": ["Ben Sample"],
        "customer": ["Northwind"],
        "site": ["Harbour site"],
        "orphaned": ["Dóra Minta"],
    }
    assert tree.find("header", class_="briefs-head").find("p", class_="meta").text() == (
        "4 briefs: 2 stale, 1 current, 1 orphaned"
    )
    found = {r.find("a").text(): r for r in tree.find_all("li", class_="brief-row")}
    assert found["Ben Sample"].attrs["data-state"] == "stale"
    assert "1 new record" in found["Ben Sample"].text()
    assert "1 flagged line" in found["Northwind"].text()
    assert found["Harbour site"].find("span", class_="state").text() == "current"
    assert "its entity is gone; cleo-test holds its name now" in found["Dóra Minta"].text()
    assert found["Northwind"].find("a").attrs["href"] == f"/briefs?brief={NORTHWIND}"
    assert "rev 1, " in found["Northwind"].find("span", class_="meta").text()
    # Every state is its URL, and every link a plain one: the page needs no script to work.
    main = tree.find("main")
    assert not [el for el in main.walk() if any(name.startswith("hx-") for name in el.attrs)]
    assert all(a.attrs["href"].startswith("/") for a in main.find_all("a"))
    nav = tree.find("nav", class_="nav")
    assert nav.find("a", href="/briefs").attrs["aria-current"] == "page"


def test_a_type_narrows_the_list_and_its_chips_say_how_many(reader: TestClient) -> None:
    tree = page(reader, "/briefs?type=person")
    assert rows(tree) == {"person": ["Ben Sample"]}
    chips = {a.text(): a for a in tree.find("nav", aria_label="Entity types").find_all("a")}
    assert list(chips) == ["All 4", "person 1", "customer 1", "site 1", "orphaned 1"]
    assert chips["person 1"].attrs["aria-current"] == "true"
    assert chips["All 4"].attrs["href"] == "/briefs"
    # A brief opened from the list keeps the type, so the way back does too.
    assert tree.find("a", class_="brief-name").attrs["href"] == f"/briefs?brief={BEN}&type=person"
    assert rows(page(reader, "/briefs?type=orphaned")) == {"orphaned": ["Dóra Minta"]}
    none = page(reader, "/briefs?type=project")
    assert none.find("div", class_="empty").text() == "No project briefs."


def test_a_vault_with_no_brief_says_how_one_is_written(client: TestClient, tmp_path: Path) -> None:
    for path in (tmp_path / "vault" / "processed" / "brief").glob("*.md"):
        path.unlink()
    tree = page(client, "/briefs")
    assert "No briefs yet." in tree.find("div", class_="empty").text()
    assert not tree.find_all("nav", aria_label="Entity types")


# -- an open brief ----------------------------------------------------------------------------------------------------


def test_a_brief_opens_beside_the_raw_its_line_cites(reader: TestClient, seen: Path) -> None:
    tree = page(reader, f"/briefs?brief={NORTHWIND}")
    assert tree.find("title").text() == "Northwind - Briefs - Docket"
    assert tree.find("h1").text() == "Northwind"
    assert tree.find("p", class_="eyebrow").text().startswith("brief customer \N{MIDDLE DOT} rev 1 \N{MIDDLE DOT} ")
    assert "Choose a line" in tree.find("section", class_="raw-column").text()
    mail = line_of(seen, NORTHWIND, "- The offer comes by mail.")
    shown = lines(tree)
    link = shown[mail].find("a", class_="line-link")
    assert link.attrs["href"] == f"/briefs?brief={NORTHWIND}&line={mail}#raw"
    anchor = shown[mail].find("a", class_="anchor")
    assert anchor.attrs["href"] == f"/search?hit={quote(f'r:{OFFER}#1', safe=':@/,')}"
    opened = page(reader, link.attrs["href"].partition("#")[0])
    raw = opened.find("section", class_="raw-column")
    assert raw.attrs["id"] == "raw"
    assert raw.find("h2").text() == f"Line {mail}"
    assert raw.find("p", class_="raw-line").text() == "The offer comes by mail."
    assert "The consultant's offer" in raw.find("article", class_="span").text()
    assert raw.find("p", class_="raw-link").find("a").text() == "On the Search page"
    assert lines(opened)[mail].find("a", class_="line-link").attrs["aria-current"] == "true"


def test_a_person_s_brief_links_to_their_items_on_the_ledger(reader: TestClient) -> None:
    """DR-0669: a person's brief, and only a person's, links to the Ledger narrowed to them."""
    links = page(reader, f"/briefs?brief={BEN}").find("p", class_="briefs-locator").find_all("a")
    assert [(a.attrs["href"], a.text()) for a in links][1:] == [
        ("/ledger?who=Ben+Sample", "Ben Sample's items on the Ledger")
    ]
    assert len(page(reader, f"/briefs?brief={NORTHWIND}").find("p", class_="briefs-locator").find_all("a")) == 1


def test_a_time_anchor_opens_the_recording_page_at_its_time(reader: TestClient, seen: Path) -> None:
    n = line_of(seen, NORTHWIND, "- 7 Oct, supplier call")
    tree = page(reader, f"/briefs?brief={NORTHWIND}&line={n}")
    assert lines(tree)[n].find("a", class_="anchor").attrs["href"] == f"/recording?rid={CALL}&at=04:00-05:00#at"
    raw = tree.find("section", class_="raw-column")
    assert raw.find("p", class_="raw-link").find("a").attrs["href"] == f"/recording?rid={CALL}&at=04:00-05:00#at"
    assert "offer in writing" in raw.find("article", class_="span").text()


def test_a_ref_is_followed_one_level_with_its_own_raw(reader: TestClient, seen: Path) -> None:
    n = line_of(seen, BEN, "- Ben wants the deadline")
    tree = page(reader, f"/briefs?brief={BEN}&line={n}")
    ref = lines(tree)[n].find("a", class_="ref")
    deadline = line_of(seen, NOTE, "- Dóra agrees the deadline")
    assert ref.attrs["href"] == f"/search?hit={NOTE}&lines={deadline}"
    cited = tree.find("article", class_="cited-line")
    assert f"{NOTE}, line {deadline}" in cited.find("header").text()
    assert cited.find("p", class_="raw-line").text() == "Dóra agrees the deadline with Ben on Friday."
    assert cited.find("p", class_="raw-link").find("a").attrs["href"] == f"/recording?rid={CALL}&at=02:30-03:20#at"


def test_a_flagged_line_is_marked_and_says_what_changed(reader: TestClient, seen: Path) -> None:
    n = line_of(seen, NORTHWIND, "- The consultant's price")
    tree = page(reader, f"/briefs?brief={NORTHWIND}&line={n}")
    assert "flagged" in lines(tree)[n].attrs["class"]
    assert lines(tree)[n].find("span", class_="flag").text() == "changed under it"
    notices = [d.text() for d in tree.find("main").find_all("div", class_="warn")]
    assert "1 line cites a line that changed since; marked below." in notices
    raw = tree.find("section", class_="raw-column")
    assert "reads otherwise" in raw.find("article", class_="cited-line").text()


def test_a_stale_brief_lists_its_new_records(reader: TestClient) -> None:
    tree = page(reader, f"/briefs?brief={BEN}")
    assert "1 record about Ben Sample since rev 1:" in tree.find("main").text()
    [new] = tree.find("ul", class_="new-records").find_all("a")
    assert new.text() == ABOUT_BEN.rid
    assert new.attrs["href"] == f"/search?hit={quote(f'r:{ABOUT_BEN.rid}', safe=':@/,')}"


def test_an_orphan_names_the_entity_that_holds_its_name(reader: TestClient) -> None:
    tree = page(reader, f"/briefs?brief={DORA}")
    assert tree.find("p", class_="eyebrow").text().startswith("brief orphaned \N{MIDDLE DOT} rev 1")
    assert "Its entity is gone; cleo-test holds its name now." in tree.find("main").text()


def test_a_current_brief_has_no_notice(reader: TestClient) -> None:
    tree = page(reader, f"/briefs?brief={HARBOUR}&type=site")
    assert not tree.find("main").find_all("div", class_="warn")
    assert tree.find("p", class_="briefs-locator").find("a").attrs["href"] == "/briefs?type=site"


def test_a_line_that_cites_nothing_or_isn_t_there_says_so(reader: TestClient, seen: Path) -> None:
    n = line_of(seen, HARBOUR, "## Last conversations")
    heading = page(reader, f"/briefs?brief={HARBOUR}&line={n}")
    assert not lines(heading)[n].find_all("a")
    assert f"Line {n} cites nothing" in heading.find("section", class_="raw-column").text()
    for far in (2, 99):
        past = page(reader, f"/briefs?brief={HARBOUR}&line={far}")
        assert f"line {far} isn't in the doc's body" in past.find("section", class_="raw-column").text()


# -- never-store ------------------------------------------------------------------------------------------------------


def test_a_brief_a_list_now_holds_is_hidden_and_counted(client: TestClient, tmp_path: Path) -> None:
    list_more(tmp_path, people=["ben@northwind.example"])
    tree = page(client, "/briefs")
    assert "Ben Sample" not in tree.find("main").text()
    assert "1 brief hidden: a never-store list holds its entity." in tree.find("main").text()
    hidden = page(client, f"/briefs?brief={BEN}&line=10")
    assert hidden.find("h1").text() == "A hidden brief"
    assert hidden.find("title").text() == "A hidden brief - Briefs - Docket"
    main = hidden.find("main").text()
    assert "Ben" not in main
    assert "ben-sample" not in main
    # A listed person opens nothing either, brief or no brief: by the name an id spells, or by a contact id.
    list_more(tmp_path, people=["ben@northwind.example", "Vera Nobody", "U0CLEO"])
    for nobody in ("brief:vera-nobody", "brief:cleo-test"):
        assert page(client, f"/briefs?brief={nobody}").find("h1").text() == "A hidden brief"
    assert "cleo" not in page(client, "/briefs?brief=brief:cleo-test").find("main").text().casefold()


def test_a_line_a_list_now_names_is_hidden_and_opens_nothing(client: TestClient, tmp_path: Path) -> None:
    n = line_of(tmp_path / "vault", NORTHWIND, "- 7 Oct, supplier call")
    list_more(tmp_path, keywords=["offer in writing"])
    tree = page(client, f"/briefs?brief={NORTHWIND}&line={n}")
    line = lines(tree)[n]
    assert line.text().strip() == f"{n} a line hidden: it names a never-store value"
    assert not line.find_all("a")
    raw = tree.find("section", class_="raw-column")
    assert raw.text().strip().endswith(f"Line {n} is hidden: it names a never-store value, so nothing of it opens.")
    assert not raw.find_all("article")
    assert "offer in writing" not in tree.text()


def test_a_cited_line_a_list_now_names_is_hidden(client: TestClient, tmp_path: Path) -> None:
    n = line_of(tmp_path / "vault", BEN, "- Ben wants the deadline")
    list_more(tmp_path, keywords=["agrees the deadline"])
    tree = page(client, f"/briefs?brief={BEN}&line={n}")
    cited = tree.find("article", class_="cited-line")
    assert cited.find("p", class_="raw-line").text() == "a line hidden: it names a never-store value"
    assert not cited.find_all("article", class_="span")


# -- the URL and its errors -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "named"),
    [
        ("brief=northwind", "brief is a brief's id"),
        ("brief=minutes:2026-10-13-fleet-review", "brief is a brief's id"),
        ("brief=brief:Not%20One", "brief is a brief's id"),
        (f"brief={BEN}&line=0", "line is a line's number"),
        (f"brief={BEN}&line=two", "line is a line's number"),
        ("type=people", "type is an entity type"),
    ],
)
def test_a_value_the_url_can_t_hold_is_the_page_s_own_error(reader: TestClient, query: str, named: str) -> None:
    tree = page(reader, f"/briefs?{query}", status=400)
    assert named in tree.find("div", class_="error").text()


def test_a_brief_that_isn_t_there_says_so(reader: TestClient) -> None:
    tree = page(reader, "/briefs?brief=brief:fleet-management", status=404)
    assert "No brief brief:fleet-management." in tree.find("main").text()
    assert tree.find("main").find("a", class_="button").attrs["href"] == "/briefs"


def test_the_url_reads_and_writes_one_canonical_form() -> None:
    state = BriefsState.read({"type": "person", "line": "12", "brief": BEN, "other": "kept out"})
    assert state == BriefsState(BEN, 12, "person")
    assert state.url() == f"/briefs?brief={BEN}&line=12&type=person"
    assert state.link() == f"{state.url()}#raw"
    assert BriefsState(BEN).link() == f"/briefs?brief={BEN}"
    assert BriefsState().url() == "/briefs"
    # A line is read only with a brief.
    assert BriefsState.read({"line": "4"}) == BriefsState()
    with pytest.raises(BriefsStateError, match="type is an entity type"):
        BriefsState.read({"type": "Person"})


def test_the_index_being_built_waits_and_a_failure_is_the_error_page(
    reader: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = reader.app
    assert isinstance(app, ServiceApp)
    updater = app.service.updater
    app.service.updater = IndexUpdater(lambda: time.sleep(0.5), wait_seconds=0.01)
    waiting = page(reader, "/briefs", status=503)
    assert "the index is being built" in waiting.find("div", class_="error").text()
    app.service.updater = updater

    def failing(*_args: object) -> object:
        raise RuntimeError("words from a record")

    def refusing(*_args: object) -> object:
        raise DocketError("the index can't be read")

    monkeypatch.setattr(briefs, "gather", refusing)
    assert "the index can't be read" in page(reader, "/briefs", status=400).find("div", class_="error").text()
    monkeypatch.setattr(briefs, "gather", failing)
    failed = reader.get("/briefs")
    assert failed.status_code == 500
    assert "Something failed in Docket" in failed.text
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
    view = build(Facts(now, ZoneInfo("UTC"), BriefsReport(briefs=[found]), found, doc=doc), BriefsState(BEN))
    assert view.meta == "person \N{MIDDLE DOT} rev 2"
    assert view.notices == [
        "Stale: its history isn't here, so Docket can't tell which records it hasn't seen.",
        "7 records about Ben Sample since rev 2 (the newest five):",
    ]
    assert [link.text for link in view.new] == ["a", "b", "c", "d", "e"]
    # A short anchor on a brief covering two records names no record, so it links nowhere.
    [anchor] = view.lines[-1].anchors
    assert (anchor.text, anchor.url) == ("@00:10", "")


def test_an_orphan_no_one_entity_holds_the_name_of_says_so() -> None:
    gone = BriefState(id="brief:vera", entity="vera", name="Vera", type="", rev=1, state="orphaned")
    view = build(Facts(datetime.now(UTC), ZoneInfo("UTC"), BriefsReport(briefs=[gone])), BriefsState())
    [group] = view.groups
    assert (group.type, group.rows[0].orphan) == ("orphaned", "its entity is gone, and no one entity holds its name")


def test_a_decisions_line_from_before_the_log_is_marked_and_the_brief_stale(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "vault"
    path = root / "processed" / "brief" / "harbour-site.md"
    text = path.read_text(encoding="utf-8").replace("rev: 1", "rev: 2").rstrip("\n")
    path.write_text(text + "\n- A new printer was decided. [r:chat:C0OPS/1791400800.000100]\n", encoding="utf-8")
    git(root, "add", "--", str(path))
    git(root, "commit", "-qm", f"Doc: {HARBOUR} rev 2")
    n = line_of(tmp_path / "vault", HARBOUR, "- A new printer was decided.")
    tree = page(client, f"/briefs?brief={HARBOUR}")
    assert [s.text() for s in lines(tree)[n].find_all("span", class_="flag")] == ["not a confirmed decision"]
    assert "a Decisions line cites no confirmed decision" in tree.find("main").text()
    listed = {r.find("a").text(): r for r in page(client, "/briefs").find_all("li", class_="brief-row")}
    assert listed["Harbour site"].attrs["data-state"] == "stale"
