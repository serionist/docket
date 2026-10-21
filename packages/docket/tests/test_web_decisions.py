"""The Briefs page's decisions (#162, DR-0781, DR-0782): an entity's decisions newest first, superseded ones folded,
waiting candidates with what they contradict, and `POST /api/web/briefs/decision`, which writes a decision's status as
`docket decision set` does: confirm, reject and the three picks, a stale view, the re-check of what it replaces,
every refusal of a crafted body and of a request that isn't the browser's own; then the command itself. On the
invented decisions vault."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
from docket_webkit import OURS, TOKEN, signed_in
from httpx2 import Response
from starlette.testclient import TestClient

from docket import console, decisions, recall
from docket.cli import main
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.docstore import DocStore
from docket.vault.vault import Vault
from docket.web.decisions_view import DONE, DecisionDoc, part

API = "/api/web/briefs"
POST = "/api/web/briefs/decision"
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


def read(client: TestClient, query: str = f"brief={BRIEF}", status: int = 200) -> dict[str, Any]:
    got = client.get(f"{API}?{query}")
    assert got.status_code == status, got.text
    return got.json()


def part_of(client: TestClient) -> dict[str, Any]:
    found = read(client)["decisions"]
    assert found is not None
    return found


def titles(cards: list[dict[str, Any]]) -> list[str]:
    return [c["title"] for c in cards]


def rev(tmp_path: Path, doc_id: str) -> int:
    doc = DocStore(vault_of(tmp_path)).get(doc_id)
    assert doc is not None
    return doc.meta.rev


def body(tmp_path: Path, doc_id: str, pick: str, **fields: Any) -> dict[str, Any]:
    return {"decision": doc_id, "rev": rev(tmp_path, doc_id), "pick": pick, "brief": BRIEF} | fields


def click(client: TestClient, tmp_path: Path, doc_id: str, pick: str, **fields: Any) -> Response:
    return client.post(POST, json=body(tmp_path, doc_id, pick, **fields), headers=SAME)


def standing(tmp_path: Path, doc_id: str) -> tuple[str, bool]:
    found = next(d for d in decisions.report(vault_of(tmp_path)).decisions if d.id == doc_id)
    return found.status, found.current


def committed(tmp_path: Path, doc_id: str) -> str:
    text = DocStore(vault_of(tmp_path)).committed_text(doc_id)
    assert text is not None
    return text


def head(tmp_path: Path) -> str:
    return git(tmp_path / "vault", "rev-parse", "HEAD")


# -- the read ---------------------------------------------------------------------------------------------------------


def test_an_entity_with_decisions_and_no_brief_shows_them_waiting_first(client: TestClient) -> None:
    found = read(client)
    view = found["view"]
    assert (view["mode"], view["title"]) == ("entity", "Fleet management")
    assert view["message"].startswith("No brief yet.")
    assert view["back"] == "/briefs"
    decided = found["decisions"]
    waiting = decided["waiting"]
    # The candidate that contradicts a decision first, with it; then the plain one.
    assert titles(waiting) == ["the pilot comes first after all", "a review every week"]
    assert titles(waiting[0]["against"]) == ["the dashboard ships before the pilot"]
    assert [p["label"] for p in waiting[0]["picks"]] == [
        "The new one replaces it",
        "The old one stands",
        "Not a contradiction",
    ]
    assert [(p["value"], p["primary"]) for p in waiting[1]["picks"]] == [("confirm", True), ("reject", False)]
    assert (waiting[1]["id"], waiting[1]["rev"], waiting[1]["standing"]) == (WEEKLY, 1, "proposed")
    assert waiting[1]["verify"] == f"/search?hit={WEEKLY}"
    # A proposed decision is never among the decided; what is decided folds what it superseded.
    assert [(c["title"], c["standing"]) for c in decided["current"]] == [
        ("the dashboard ships before the pilot", "current")
    ]
    assert [(c["title"], c["standing"]) for c in decided["current"][0]["folded"]] == [
        ("the pilot comes first", "superseded")
    ]
    assert [(c["title"], c["standing"]) for c in decided["rejected"]] == [("a review every day", "rejected")]
    # Only a waiting decision takes a pick.
    assert decided["current"][0]["picks"] == [] and waiting[0]["against"][0]["picks"] == []


def test_the_list_names_what_waits_for_the_owner(client: TestClient) -> None:
    [link] = read(client, "")["view"]["to_decide"]
    assert (link["text"], link["url"]) == ("Fleet management: 2 decisions", f"/briefs?brief={BRIEF}")


def test_a_brief_on_the_entity_shows_its_decisions_with_it(client: TestClient, tmp_path: Path) -> None:
    vault = vault_of(tmp_path)
    dashboard = next(d for d in decisions.report(vault).decisions if d.id == DASHBOARD)
    text = (
        f"---\nid: {BRIEF}\ntype: brief\ncovers: []\nby: claude-test, brief\nentities: [{SUBJECT}]\n---\n\n"
        f"# Fleet management\n\n## What changed\n\n- The price again. {cite(vault, SUPPLIER, TEN)}\n\n"
        f"## Last conversations\n\n## Decisions\n\n- {dashboard.title} {dashboard.cite}\n"
    )
    recall.doc_put(vault, text)
    found = read(client)
    assert found["view"]["mode"] == "brief"
    assert titles(found["decisions"]["waiting"])[:1] == ["the pilot comes first after all"]


def test_another_entity_s_brief_has_no_decisions_and_one_with_neither_is_missing(client: TestClient) -> None:
    assert read(client, "brief=brief:ben-sample")["decisions"] is None
    neither = read(client, "brief=brief:harbour-pilot")
    assert (neither["view"]["mode"], neither["decisions"]) == ("missing", None)


# -- the pick ---------------------------------------------------------------------------------------------------------


def test_confirm_writes_the_next_rev_by_the_owner_and_answers_what_it_did(client: TestClient, tmp_path: Path) -> None:
    answer = click(client, tmp_path, WEEKLY, "confirm")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"decision": WEEKLY, "pick": "confirm", "notice": DONE["confirm"], "rev": 2}
    assert standing(tmp_path, WEEKLY) == ("confirmed", True)
    text = committed(tmp_path, WEEKLY)
    assert "# a review every week\n" in text
    assert "by: Ada Example, Briefs page" in text
    assert (
        git(tmp_path / "vault", "log", "-1", "--format=%s").strip()
        == f"Decision: {WEEKLY} confirmed on the Briefs page"
    )
    # The page read again shows it decided, and no longer waiting.
    found = part_of(client)
    assert "a review every week" not in titles(found["waiting"])
    assert "a review every week" in titles(found["current"])


def test_reject_keeps_it_marked_rejected(client: TestClient, tmp_path: Path) -> None:
    assert click(client, tmp_path, WEEKLY, "reject").json()["notice"] == DONE["reject"]
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
    assert click(client, tmp_path, AGAIN, pick).status_code == 200
    assert (standing(tmp_path, AGAIN), standing(tmp_path, DASHBOARD)) == (candidate, dashboard)
    found = DocStore(vault_of(tmp_path)).get(AGAIN)
    assert found is not None
    assert found.meta.supersedes == supersedes
    assert found.meta.contradicts == ([DASHBOARD] if pick == "stand" else [])
    # The decision it contradicted takes no rev, whichever the pick.
    assert committed(tmp_path, DASHBOARD) == before
    if pick == "stand":
        [current] = part_of(client)["current"]
        assert [(c["title"], c["standing"]) for c in current["contradicted"]] == [
            ("the pilot comes first after all", "rejected")
        ]


def test_a_stale_view_writes_nothing_and_answers_409(client: TestClient, tmp_path: Path) -> None:
    before = head(tmp_path)
    moved = click(client, tmp_path, WEEKLY, "confirm", rev=0)
    assert moved.status_code == 409
    assert moved.json()["error"] == "the decision changed since the page showed it; here it is as it stands"
    already = click(client, tmp_path, DASHBOARD, "confirm", rev=2)
    assert (already.status_code, already.json()["error"]) == (409, f"{DASHBOARD} is confirmed already")
    assert head(tmp_path) == before


def test_of_two_candidates_replacing_one_decision_the_first_pick_wins(client: TestClient, tmp_path: Path) -> None:
    vault = vault_of(tmp_path)
    first, second = "decision:2026-10-15-charging-first", "decision:2026-10-16-safety-first"
    propose(vault, first, "charging comes first", cite(vault, SUPPLIER, TEN), [f"replaces: [{DASHBOARD}]"])
    anchor = "[r:calls:rec-0101@00:10]"
    text = (
        f"---\nid: {second}\ntype: decision\ncovers: [calls:rec-0101]\nby: x\nentities: [{SUBJECT}]\nstatus: proposed\n"
        f"subject: {SUBJECT}\nreplaces: [{DASHBOARD}]\n---\n\n# Proposed: safety comes first\n\n- Said. {anchor}\n"
    )
    recall.doc_put(vault, text)
    assert click(client, tmp_path, first, "confirm").status_code == 200
    assert standing(tmp_path, DASHBOARD) == ("confirmed", False)
    late = click(client, tmp_path, second, "confirm")
    assert late.status_code == 409
    assert late.json()["error"] == "the decision it replaces was replaced meanwhile; the candidate needs a new rev"
    assert standing(tmp_path, second) == ("proposed", False)


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({"decision": "brief:ben-sample"}, "decision is a decision's id"),
        ({"decision": "decision:2026-01-01-nothing"}, "no decision decision:2026-01-01-nothing"),
        ({"pick": "proposed"}, "pick is confirm, reject, replace, stand, unrelated"),
        ({"pick": "replace"}, "replace is a pick for a candidate that contradicts a decision"),
        ({"brief": "brief:ben-sample"}, f"{WEEKLY} isn't a decision on ben-sample"),
        ({"brief": "fleet"}, "brief is a brief's id"),
        ({"brief": ""}, "brief is the page the decision is on"),
    ],
)
def test_a_crafted_pick_is_refused_in_its_words_and_writes_nothing(
    client: TestClient, tmp_path: Path, fields: dict[str, str], says: str
) -> None:
    before = head(tmp_path)
    answer = client.post(
        POST, json={"decision": WEEKLY, "rev": 1, "pick": "confirm", "brief": BRIEF} | fields, headers=SAME
    )
    assert answer.status_code == 400, answer.text
    assert says in answer.json()["error"]
    assert head(tmp_path) == before


@pytest.mark.parametrize(
    "sent",
    [
        {"decision": WEEKLY, "rev": "two", "pick": "confirm", "brief": BRIEF},
        {"decision": WEEKLY, "pick": "confirm", "brief": BRIEF},
        {"decision": WEEKLY, "rev": 1, "pick": "confirm", "brief": BRIEF, "by": "someone else"},
    ],
)
def test_a_body_that_isn_t_a_pick_s_is_refused_before_the_writer(
    client: TestClient, tmp_path: Path, sent: dict[str, Any]
) -> None:
    before = head(tmp_path)
    assert client.post(POST, json=sent, headers=SAME).status_code == 422
    assert head(tmp_path) == before


def test_a_plain_confirm_of_a_contradicting_candidate_is_refused(client: TestClient, tmp_path: Path) -> None:
    answer = click(client, tmp_path, AGAIN, "confirm")
    assert answer.status_code == 400
    assert "takes one of three picks" in answer.json()["error"]


def test_only_the_browser_s_own_session_picks(client: TestClient, tmp_path: Path) -> None:
    """The Guard's same-origin write, a JSON body only, the session cookie only: the token never picks (DR-0781)."""
    sent = {"decision": WEEKLY, "rev": 1, "pick": "confirm", "brief": BRIEF}
    for headers in ({"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "cross-site"}, {}):
        assert client.post(POST, json=sent, headers=headers).status_code == 403, headers
    assert client.post(POST, json=sent, headers=SAME | {"Authorization": f"Bearer {TOKEN}"}).status_code == 403
    form = SAME | {"Content-Type": "application/x-www-form-urlencoded"}
    assert client.post(POST, content=b"decision=x&rev=1", headers=form).status_code == 415
    assert client.get(POST).status_code == 405
    # The form route of the server's own page is gone with it.
    assert client.post("/briefs/decision", data=sent, headers=SAME).status_code == 404
    client.cookies.clear()
    assert client.post(POST, json=sent, headers=SAME).status_code == 401
    assert standing(tmp_path, WEEKLY) == ("proposed", False)


def test_a_decision_edited_by_hand_waits_for_its_take_in(client: TestClient, tmp_path: Path) -> None:
    path = DocStore(vault_of(tmp_path)).file(WEEKLY)
    path.write_text(path.read_text(encoding="utf-8").replace("every week", "every other week"), encoding="utf-8")
    answer = click(client, tmp_path, WEEKLY, "confirm")
    assert answer.status_code == 400
    assert "was edited by hand" in answer.json()["error"]


def test_a_subject_a_list_holds_now_is_hidden_and_takes_no_pick(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "vault" / CONFIG_FILE
    config = VaultConfig.load(path)
    grown = config.never_store.model_copy(update={"people": ["Fleet Management"]})
    path.write_text(config.model_copy(update={"never_store": grown}).dump(), encoding="utf-8", newline="\n")
    found = read(client)
    assert (found["view"]["mode"], found["decisions"]) == ("hidden", None)
    answer = click(client, tmp_path, WEEKLY, "confirm")
    assert answer.status_code == 400
    assert "a never-store list holds it" in answer.json()["error"]


# -- the part, on what was read ---------------------------------------------------------------------------------------


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


def test_decision_set_writes_what_the_pick_writes_saying_who(
    template: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude, after the owner's yes in chat, or the owner at their own terminal (DR-0782): the page's one writer, its
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
