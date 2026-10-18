"""The Briefs page's decisions (#162, DR-0603, DR-0566): an entity's decisions newest first, superseded ones folded,
waiting candidates beside what they contradict, and `POST /briefs/decision`, which writes a decision's status as
`docket decision set` does: confirm, reject and the three picks, a stale view, D3's re-check, and crafted posts; then
the command itself. On the invented decisions vault."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from decisions_kit import (
    AGAIN,
    BRIEF,
    DAILY,
    DASHBOARD,
    PILOT,
    SUBJECT,
    SUPPLIER,
    WEEKLY,
    cite,
    decisions_template,
    propose,
)
from docket_testkit import git
from docket_webkit import OURS, Element, a11y_problems, html_problems, parse, signed_in
from httpx2 import Response
from starlette.testclient import TestClient

from docket import console, decisions
from docket.cli import main
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.docstore import DocStore
from docket.vault.vault import Vault
from docket.web.decisions_view import DONE, DecisionDoc, part
from docket.web.templating import PAGE_CSP

PAGE = f"/briefs?brief={BRIEF}"
POST = "/briefs/decision"
SAME = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}
TEN = "- The consultant asks twelve million forints for the quarter, above the budget. @00:30-01:50"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return decisions_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


def vault_of(tmp_path: Path) -> Vault:
    return Vault.open(tmp_path / "vault")


def page(client: TestClient, path: str = PAGE, status: int = 200) -> Element:
    answer = client.get(path)
    assert answer.status_code == status, answer.text
    return checked(answer)


def checked(answer: Response) -> Element:
    assert answer.headers["Content-Security-Policy"] == PAGE_CSP
    assert not html_problems(answer.text)
    assert not a11y_problems(answer.text)
    return parse(answer.text)


def section(tree: Element) -> Element:
    return tree.find("section", class_="decisions")


def titles(part: Element) -> list[str]:
    return [h.text() for h in part.find_all("h4")]


def rev(tmp_path: Path, doc_id: str) -> int:
    doc = DocStore(vault_of(tmp_path)).get(doc_id)
    assert doc is not None
    return doc.meta.rev


def click(client: TestClient, tmp_path: Path, doc_id: str, pick: str, **fields: str) -> Response:
    form = {"decision": doc_id, "rev": str(rev(tmp_path, doc_id)), "pick": pick, "brief": BRIEF} | fields
    return client.post(POST, data=form, headers=SAME)


def said(answer: Response) -> str:
    """What a page says, its words as a reader reads them."""
    return parse(answer.text).find("main").text()


def standing(tmp_path: Path, doc_id: str) -> tuple[str, bool]:
    found = next(d for d in decisions.report(vault_of(tmp_path)).decisions if d.id == doc_id)
    return found.status, found.current


def committed(tmp_path: Path, doc_id: str) -> str:
    text = DocStore(vault_of(tmp_path)).committed_text(doc_id)
    assert text is not None
    return text


# -- the page ---------------------------------------------------------------------------------------------------------


def test_an_entity_with_decisions_and_no_brief_shows_them_waiting_first(client: TestClient) -> None:
    tree = page(client)
    assert tree.find("h1").text() == "Fleet management"
    assert tree.find("title").text() == "Fleet management - Briefs - Docket"
    assert "No brief yet." in tree.find("main").text()
    found = section(tree)
    waiting = found.find_all("div", class_="waiting")
    # The candidate that contradicts a decision first, beside it; then the plain one.
    assert [titles(w)[0] for w in waiting] == ["the pilot comes first after all", "a review every week"]
    assert titles(waiting[0].find("div", class_="against")) == ["the dashboard ships before the pilot"]
    assert [b.text() for b in waiting[0].find("form").find_all("button")] == [
        "The new one replaces it",
        "The old one stands",
        "Not a contradiction",
    ]
    assert [b.text() for b in waiting[1].find("form").find_all("button")] == ["Confirm", "Reject"]
    form = waiting[1].find("form")
    assert (form.attrs["method"], form.attrs["action"]) == ("post", POST)
    hidden = {i.attrs["name"]: i.attrs["value"] for i in form.find_all("input")}
    assert hidden == {"decision": WEEKLY, "rev": "1", "brief": BRIEF}
    # A proposed decision is never among the decided.
    decided = [a for a in found.find_all("article") if a.parent is found]
    assert [a.attrs["data-standing"] for a in decided] == ["current"]
    assert titles(decided[0])[0] == "the dashboard ships before the pilot"
    folded = decided[0].find("details")
    assert folded.find("summary").text() == "It replaced 1 earlier decision"
    assert titles(folded) == ["the pilot comes first"]
    rejected = [d for d in found.find_all("details") if d.find("summary").text().startswith("Rejected")]
    assert titles(rejected[0]) == ["a review every day"]
    # Every link plain, every state its URL: the page needs no script.
    main = tree.find("main")
    assert not [el for el in main.walk() if any(name.startswith("hx-") for name in el.attrs)]
    assert all(a.attrs["href"].startswith("/") for a in main.find_all("a"))


def test_the_list_says_what_waits_for_the_user(client: TestClient) -> None:
    waiting = page(client, "/briefs").find("section", class_="waiting-list")
    [link] = waiting.find_all("a")
    assert (link.text(), link.attrs["href"]) == ("Fleet management: 2 decisions", PAGE)


def test_a_brief_on_the_entity_shows_its_decisions_beneath_it(client: TestClient, tmp_path: Path) -> None:
    vault = vault_of(tmp_path)
    dashboard = next(d for d in decisions.report(vault).decisions if d.id == DASHBOARD)
    text = (
        f"---\nid: {BRIEF}\ntype: brief\ncovers: []\nby: claude-test, brief\nentities: [{SUBJECT}]\n---\n\n"
        f"# Fleet management\n\n## What changed\n\n- The price again. {cite(vault, SUPPLIER, TEN)}\n\n"
        f"## Last conversations\n\n## Decisions\n\n- {dashboard.title} {dashboard.cite}\n"
    )
    from docket import recall

    recall.doc_put(vault, text)
    tree = page(client)
    assert tree.find("section", class_="brief-column")
    assert titles(section(tree))[:1] == ["the pilot comes first after all"]


def test_another_entity_s_brief_has_no_decisions_part(client: TestClient) -> None:
    assert not page(client, "/briefs?brief=brief:ben-sample").find_all("section", class_="decisions")


def test_an_entity_with_neither_is_still_not_found(client: TestClient) -> None:
    page(client, "/briefs?brief=brief:harbour-pilot", status=404)
    assert "done is what a click did" in said(client.get(f"{PAGE}&done=decided"))


# -- the click --------------------------------------------------------------------------------------------------------


def test_confirm_writes_the_next_rev_by_the_owner_and_says_so_once(client: TestClient, tmp_path: Path) -> None:
    answer = click(client, tmp_path, WEEKLY, "confirm")
    assert answer.status_code == 303
    assert answer.headers["location"] == f"{PAGE}&done=confirm"
    assert standing(tmp_path, WEEKLY) == ("confirmed", True)
    text = committed(tmp_path, WEEKLY)
    assert "# a review every week\n" in text
    assert "by: Ada Example, Briefs page" in text
    assert (
        git(tmp_path / "vault", "log", "-1", "--format=%s").strip()
        == f"Decision: {WEEKLY} confirmed on the Briefs page"
    )
    tree = page(client, f"{PAGE}&done=confirm")
    assert DONE["confirm"] in tree.find("main").text()
    assert all("done=" not in a.attrs["href"] for a in tree.find("main").find_all("a"))
    assert "a review every week" not in " ".join(titles(tree.find("div", class_="waiting")))


def test_reject_keeps_it_marked_rejected(client: TestClient, tmp_path: Path) -> None:
    assert click(client, tmp_path, WEEKLY, "reject").status_code == 303
    assert standing(tmp_path, WEEKLY) == ("rejected", False)
    assert "# Rejected: a review every week\n" in committed(tmp_path, WEEKLY)


@pytest.mark.parametrize(
    ("pick", "after"),
    [
        ("replace", (("confirmed", True), ("confirmed", False), [DASHBOARD])),
        ("stand", (("rejected", False), ("confirmed", True), [])),
        ("unrelated", (("confirmed", True), ("confirmed", True), [])),
    ],
)
def test_a_contradiction_takes_one_of_three_picks(
    client: TestClient, tmp_path: Path, pick: str, after: tuple[tuple[str, bool], tuple[str, bool], list[str]]
) -> None:
    candidate, dashboard, supersedes = after
    before = committed(tmp_path, DASHBOARD)
    assert click(client, tmp_path, AGAIN, pick).status_code == 303
    assert (standing(tmp_path, AGAIN), standing(tmp_path, DASHBOARD)) == (candidate, dashboard)
    found = DocStore(vault_of(tmp_path)).get(AGAIN)
    assert found is not None
    assert found.meta.supersedes == supersedes
    assert found.meta.contradicts == ([DASHBOARD] if pick == "stand" else [])
    # The decision it contradicted takes no rev, whichever the pick.
    assert committed(tmp_path, DASHBOARD) == before
    if pick == "stand":
        kept = section(page(client)).find_all("details")
        assert any(d.find("summary").text() == "Contradicted 1 time; it stands" for d in kept)


def test_a_stale_view_writes_nothing_and_shows_the_page_as_it_stands(client: TestClient, tmp_path: Path) -> None:
    head = git(tmp_path / "vault", "rev-parse", "HEAD")
    answer = click(client, tmp_path, WEEKLY, "confirm", rev="0")
    assert answer.status_code == 409
    tree = checked(answer)
    assert "the decision changed since the page showed it" in tree.find("div", class_="warn").text()
    assert git(tmp_path / "vault", "rev-parse", "HEAD") == head
    already = click(client, tmp_path, DASHBOARD, "confirm", rev="2")
    assert already.status_code == 409
    assert f"{DASHBOARD} is confirmed already" in said(already)


def test_of_two_candidates_replacing_one_decision_the_first_click_wins(client: TestClient, tmp_path: Path) -> None:
    vault = vault_of(tmp_path)
    first, second = "decision:2026-10-15-charging-first", "decision:2026-10-16-safety-first"
    propose(vault, first, "charging comes first", cite(vault, SUPPLIER, TEN), [f"replaces: [{DASHBOARD}]"])
    anchor = "[r:calls:rec-0101@00:10]"
    text = (
        f"---\nid: {second}\ntype: decision\ncovers: [calls:rec-0101]\nby: x\nentities: [{SUBJECT}]\nstatus: proposed\n"
        f"subject: {SUBJECT}\nreplaces: [{DASHBOARD}]\n---\n\n# Proposed: safety comes first\n\n- Said. {anchor}\n"
    )
    from docket import recall

    recall.doc_put(vault, text)
    assert click(client, tmp_path, first, "confirm").status_code == 303
    assert standing(tmp_path, DASHBOARD) == ("confirmed", False)
    late = click(client, tmp_path, second, "confirm")
    assert late.status_code == 409
    assert "the decision it replaces was replaced meanwhile; the candidate needs a new rev" in said(late)
    assert standing(tmp_path, second) == ("proposed", False)


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({"decision": "brief:ben-sample"}, "decision is a decision's id"),
        ({"decision": "decision:2026-01-01-nothing"}, "no decision decision:2026-01-01-nothing"),
        ({"pick": "proposed"}, "pick is confirm, reject, replace, stand, unrelated"),
        ({"pick": "replace"}, "replace is a pick for a candidate that contradicts a decision"),
        ({"rev": "two"}, "rev is the rev of the decision the page showed"),
        ({"brief": "brief:ben-sample"}, f"{WEEKLY} isn't a decision on ben-sample"),
    ],
)
def test_a_crafted_post_is_refused_and_writes_nothing(
    client: TestClient, tmp_path: Path, fields: dict[str, str], says: str
) -> None:
    head = git(tmp_path / "vault", "rev-parse", "HEAD")
    form = {"decision": WEEKLY, "rev": "1", "pick": "confirm", "brief": BRIEF} | fields
    answer = client.post(POST, data=form, headers=SAME)
    assert answer.status_code == 400, answer.text
    assert says in said(answer)
    assert git(tmp_path / "vault", "rev-parse", "HEAD") == head


def test_a_plain_confirm_of_a_contradicting_candidate_is_refused(client: TestClient, tmp_path: Path) -> None:
    answer = click(client, tmp_path, AGAIN, "confirm")
    assert answer.status_code == 400
    assert "takes one of three picks" in said(answer)


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Origin": "http://evil.example"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({}, 403),
    ],
)
def test_only_the_browser_s_own_session_can_click(
    client: TestClient, tmp_path: Path, headers: dict[str, str], status: int
) -> None:
    form = {"decision": WEEKLY, "rev": "1", "pick": "confirm", "brief": BRIEF}
    assert client.post(POST, data=form, headers=headers).status_code == status
    assert client.get(POST).status_code == 405
    client.cookies.clear()
    assert client.post(POST, data=form, headers=SAME).status_code == 401
    assert standing(tmp_path, WEEKLY) == ("proposed", False)


def test_a_form_without_its_page_or_a_bad_page_is_refused(client: TestClient) -> None:
    form = {"decision": WEEKLY, "rev": "1", "pick": "confirm"}
    assert "brief is the page the decision is on" in said(client.post(POST, data=form, headers=SAME))
    bad = client.post(POST, data=form | {"brief": "fleet"}, headers=SAME)
    assert (bad.status_code, "brief is a brief's id" in said(bad)) == (400, True)
    assert client.post(POST, content=b"x=1", headers=SAME | {"Content-Type": "text/plain"}).status_code == 415


def test_a_decision_edited_by_hand_waits_for_its_take_in(client: TestClient, tmp_path: Path) -> None:
    path = DocStore(vault_of(tmp_path)).file(WEEKLY)
    path.write_text(path.read_text(encoding="utf-8").replace("every week", "every other week"), encoding="utf-8")
    answer = click(client, tmp_path, WEEKLY, "confirm")
    assert answer.status_code == 400
    assert "was edited by hand" in said(answer)


def test_a_subject_a_list_holds_now_takes_no_click(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "vault" / CONFIG_FILE
    config = VaultConfig.load(path)
    grown = config.never_store.model_copy(update={"people": ["Fleet Management"]})
    path.write_text(config.model_copy(update={"never_store": grown}).dump(), encoding="utf-8", newline="\n")
    assert page(client).find("h1").text() == "A hidden brief"
    answer = click(client, tmp_path, WEEKLY, "confirm")
    assert answer.status_code == 400
    assert "a never-store list holds it" in said(answer)


# -- the view, on what was read ---------------------------------------------------------------------------------------


def test_the_part_says_what_a_list_hides_and_when_there_is_nothing() -> None:
    empty = part(decisions.DecisionsReport(hidden=2), {}, "Fleet management")
    assert empty.hidden_note == "2 decisions hidden: a never-store list holds what they name."
    assert empty.empty.startswith("No decisions on Fleet management yet.")
    one = decisions.DecisionState(
        id=WEEKLY, subject=SUBJECT, title="t", status="proposed", current=False, rev=1, by="x"
    )
    found = part(
        decisions.DecisionsReport(decisions=[one], hidden=1),
        {WEEKLY: DecisionDoc(((13, "- a [r:calls:rec-0101@00:10]"), (14, ""), (15, "x")), hidden=frozenset({15}))},
        "F",
    )
    assert found.hidden_note == "1 decision hidden: a never-store list holds what it names."
    unread = one.model_copy(update={"id": "decision:2026-10-01-unread"})
    assert part(decisions.DecisionsReport(decisions=[unread]), {}, "F").waiting[0].lines == []
    [card] = found.waiting
    assert [(line.n, line.hidden) for line in card.lines] == [(13, False), (15, True)]
    assert card.lines[0].anchors[0].url.startswith("/recording?rid=calls:rec-0101")


def test_daily_and_pilot_are_where_the_kit_put_them(client: TestClient, tmp_path: Path) -> None:
    del client
    report = decisions.report(vault_of(tmp_path))
    assert {d.id: (d.status, d.current) for d in report.decisions} == {
        PILOT: ("confirmed", False),
        DASHBOARD: ("confirmed", True),
        DAILY: ("rejected", False),
        AGAIN: ("proposed", False),
        WEEKLY: ("proposed", False),
    }


# -- the command ------------------------------------------------------------------------------------------------------


def test_decision_set_writes_what_the_click_writes_saying_who(
    template: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude, after the owner's yes in chat, or the owner at their own terminal (DR-0603): the page's one writer, its
    `by` and its commit saying who."""
    shutil.copytree(template, tmp_path / "vault")
    root = str(tmp_path / "vault")
    shown = rev(tmp_path, WEEKLY)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert main(["decision", "set", WEEKLY, "confirm", "--rev", str(shown), "--vault", root]) == 0
    assert f"put {WEEKLY} rev {shown + 1}" in capsys.readouterr().out
    assert standing(tmp_path, WEEKLY) == ("confirmed", True)
    assert "by: Claude, with Ada Example's OK" in committed(tmp_path, WEEKLY)
    assert git(tmp_path / "vault", "log", "-1", "--format=%B").strip() == (
        f"Decision: {WEEKLY} confirmed on the command line\n\nDoc: {WEEKLY} rev {shown + 1}\n\n{console.BY_CLAUDE}"
    )
    monkeypatch.delenv("CLAUDECODE")
    again = rev(tmp_path, AGAIN)
    assert main(["decision", "set", AGAIN, "confirm", "--rev", str(again), "--vault", root]) == 2
    assert "takes one of three picks" in capsys.readouterr().err
    assert main(["decision", "set", AGAIN, "stand", "--rev", str(again - 1), "--vault", root]) == 2
    assert "the decision changed since" in capsys.readouterr().err
    assert main(["decision", "set", AGAIN, "stand", "--rev", str(again), "--vault", root]) == 0
    assert standing(tmp_path, AGAIN) == ("rejected", False)
    assert "by: Ada Example, command line" in committed(tmp_path, AGAIN)
    assert (
        git(tmp_path / "vault", "log", "-1", "--format=%s").strip() == f"Decision: {AGAIN} rejected on the command line"
    )
