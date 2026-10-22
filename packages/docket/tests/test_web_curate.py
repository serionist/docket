"""The Curate page's JSON routes, signed in (DR-0468, DR-0464, DR-0786): a round started only by a write, its cards
beside the open one with snippets cut when shown, accept, edit, reject, undo and lift on the writer, each logged via
the page; a stale round refused and a changed entity file answered 409; the Guard, the session and the body on every
write; and nothing of a never-store value shown. The page itself is the app's document."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from curate_kit import ANN, NORTHWIND, curate_vault, entity, more_recordings
from docket_webkit import OURS, TOKEN
from staging_kit import signed_in
from starlette.testclient import TestClient

from docket import userconfig
from docket.curate import batch, schedule
from docket.curate.batch import Batch
from docket.curate.propose import Card
from docket.errors import DocketError
from docket.index import build as index_build
from docket.service.writer import IndexBuildingError, IndexUpdater
from docket.vault import curatelog
from docket.vault.curatelog import Change, EntityFields, IdFields, Line, NameFields, RelatedFields, read_all
from docket.vault.entitystore import EntityStore, Planned
from docket.vault.vault import Vault
from docket.web import curate as curate_routes
from docket.web import curate_view, display

API = "/api/web/curate"
ROUND, DECIDE, UNDO, LIFT = (f"{API}/{verb}" for verb in ("round", "decide", "undo", "lift"))
SAME = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}
HIDDEN = "(on a never-store list)"


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return curate_vault(tmp_path)


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


def read(client: TestClient, query: str = "", status: int = 200) -> dict[str, Any]:
    got = client.get(f"{API}?{query}" if query else API)
    assert got.status_code == status, got.text
    assert got.headers["cache-control"] == "no-store"
    return got.json()


def card_read(client: TestClient, query: str, status: int = 200) -> dict[str, Any]:
    got = client.get(f"{API}/panel?{query}")
    assert got.status_code == status, got.text
    return got.json()


def panel(client: TestClient, query: str) -> dict[str, Any]:
    found = card_read(client, query)["panel"]
    assert found is not None
    return found


def post(client: TestClient, path: str, sent: dict[str, Any], status: int = 200) -> dict[str, Any]:
    got = client.post(path, json=sent, headers=SAME)
    assert got.status_code == status, got.text
    return got.json()


def started(client: TestClient, vault: Vault) -> Batch:
    done = post(client, ROUND, {})
    found = batch.load(vault)
    assert found is not None
    assert done["notice"] == f"A new round: {len(found.cards)} cards."
    return found


def card(found: Batch, name: str) -> Card:
    return next(c for c in found.cards if c.name == name)


def decision(shown: dict[str, Any], act: str, edit: dict[str, Any] | None = None) -> dict[str, Any]:
    """A decision as the page posts it: the act, and the round, card and hash the card was shown with."""
    sent = {"act": act, "round": shown["round"], "card": shown["id"], "hash": shown["hash"]}
    return sent | ({"edit": edit} if edit is not None else {})


def edited(**fields: Any) -> dict[str, Any]:
    return {"keep": [], "kinds": {}, "type": "", "id": "", "names": {}, "role": ""} | fields


def log(vault: Vault) -> list[Line]:
    return list(read_all(vault.root).lines)


def never_store(vault: Vault, **lists: list[str]) -> None:
    """Never-store lists set in vault.yaml, as the user sets them after a round started."""
    config = vault.root / "vault.yaml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["never_store"] = lists
    config.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def plant(vault: Vault, at: datetime, by: str, key: str, round_: str | None = None, *, committed: bool = True) -> None:
    """A reject line as another writer logs it, committed: tier 1's, or one of an older round; or left uncommitted, as
    a write that stopped before its commit leaves it."""
    fields = {"schema_version": 1, "id": f"{at:%Y%m%dT%H%M%S}-test-box-1", "op": "reject", "at": at}
    line = Line.model_validate(fields | {"machine": "test-box", "by": by, "via": "cli", "key": key, "round": round_})
    path = vault.root / line.rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(line.text() + "\n")
    if committed:
        vault.commit([line.rel], "a planted line")


QUESTIONS = (
    'schema_version: 1\nasked: "2026-10-14T10:00:00+02:00"\nquestions:\n'
    '  - q: Nortwin pallets\n    want: ["r:plaud:rec-0000/transaction"]\n    kind: typo\n    lang: en\n'
)


# -- a round -----------------------------------------------------------------------------------------------------------


def test_the_page_without_a_round_offers_one_and_writes_nothing(client: TestClient, vault: Vault) -> None:
    page = client.get("/curate")
    assert page.status_code == 200 and 'id="root"' in page.text
    view = read(client)["view"]
    assert (view["lead"], view["round"], view["cards"]) == ("No round on this machine yet.", None, [])
    assert view["due"] == "No round yet: start one when you have a few minutes."
    assert not batch.batch_path(vault).exists()
    assert log(vault) == []
    assert vault.git.run("status", "--porcelain").stdout == ""


def test_a_round_starts_on_the_owner_s_click_and_lists_its_cards(client: TestClient, vault: Vault) -> None:
    found = started(client, vault)
    view = read(client)["view"]
    assert view["lead"].endswith(f": {len(found.cards)} cards, 0 decided.")
    assert [c["title"] for c in view["cards"]][:2] == ["Northwind", "Dóra"]
    assert view["cards"][0]["kind"] == "New term" and view["cards"][0]["url"] == f"/curate?open={found.cards[0].id}"
    assert view["left_out"]
    # Snippets are cut only for the open card: none in the list, and none kept in the batch.
    assert "pallets" not in json.dumps(view)
    assert "pallets" not in batch.batch_path(vault).read_text(encoding="utf-8")


def test_a_card_opens_beside_the_list_with_its_snippets(client: TestClient, vault: Vault) -> None:
    northwind = card(started(client, vault), "Northwind")
    got = card_read(client, f"open={northwind.id}")
    assert got["canonical"] == f"/curate?open={northwind.id}" and got["missing"] is None
    shown = got["panel"]
    assert (shown["n"], shown["kind"], shown["title"], shown["state"]) == (1, "New term", "Northwind", "open")
    assert [s["mark"] for s in shown["snippets"]][:1] == ["Nortwin"]
    assert shown["snippets"] and all(s["search"].startswith("/search?hit=r:plaud:rec-") for s in shown["snippets"])
    assert shown["snippets"][0]["search"] == f"/search?hit={shown['snippets'][0]['locator']}"
    assert (shown["close"], shown["edit_url"], shown["back"]) == (
        "/curate",
        f"/curate?edit={northwind.id}",
        f"/curate?open={northwind.id}",
    )
    assert shown["form"] is None and panel(client, f"edit={northwind.id}")["form"] is not None


def test_without_an_index_a_card_opens_without_snippets(client: TestClient, vault: Vault) -> None:
    northwind = card(started(client, vault), "Northwind")
    index_build.db_path(vault).unlink()
    assert panel(client, f"open={northwind.id}")["snippets"] == []


# -- decisions ---------------------------------------------------------------------------------------------------------


def test_accept_writes_the_entity_and_logs_it_via_the_page(client: TestClient, vault: Vault) -> None:
    found = started(client, vault)
    northwind = card(found, "Northwind")
    shown = panel(client, f"open={northwind.id}")
    assert (shown["round"], shown["hash"]) == (found.round, "none")
    done = post(client, DECIDE, decision(shown, "accept"))
    assert done["notice"] == f"Card {northwind.id} accepted and logged; the log below undoes it."
    assert (vault.root / "entities/term/northwind.yaml").is_file()
    [line] = log(vault)
    assert (line.op, line.via, line.round, line.card) == ("create", "page", found.round, northwind.id)
    assert panel(client, f"open={northwind.id}")["state"] == "accepted"
    assert read(client)["view"]["lead"].endswith("1 decided.")
    again = post(client, DECIDE, decision(shown, "accept"), status=409)
    assert again["error"] == curate_routes.CHANGED


def test_reject_stoplists_and_lift_takes_it_off(client: TestClient, vault: Vault) -> None:
    northwind = card(started(client, vault), "Northwind")
    done = post(client, DECIDE, decision(panel(client, f"open={northwind.id}"), "reject"))
    assert done["notice"].startswith(f"Card {northwind.id} rejected")
    lifts = read(client)["lifts"]
    assert [lift["shown"] for lift in lifts] == ["new:-:northwind", "new:-:nortwin"]
    assert all(lift["liftable"] and "northwind" not in lift["id"] for lift in lifts)
    assert post(client, LIFT, {"key": lifts[0]["id"]})["notice"] == "Lifted: curation may offer that spelling again."
    assert [ln.op for ln in log(vault)][-1] == "lift" and log(vault)[-1].via == "page"
    gone = post(client, LIFT, {"key": lifts[0]["id"]}, status=400)
    assert "isn't on the stoplist any more" in gone["error"]


def test_edit_accepts_the_card_as_typed_and_rejects_what_was_left_out(client: TestClient, vault: Vault) -> None:
    cleo = card(started(client, vault), "Cleo Test")
    shown = panel(client, f"edit={cleo.id}")
    form = shown["form"]
    assert form["id"] == "cleo-test" and [s["i"] for s in form["spellings"] if s["checked"]] == [0, 1]
    assert {n["code"]: n["value"] for n in form["names"]} == {"en": "Cleo Test", "hu": ""}
    typed = edited(
        type="person",
        id="cleo-test",
        names={"en": "Cleo Test", "hu": "Test Cleo"},
        keep=[0],
        kinds={"0": "plain"},
        role="rights agent",
    )
    done = post(client, DECIDE, decision(shown, "edit", typed))
    assert done["notice"].startswith(f"Card {cleo.id} accepted as you edited it")
    text = (vault.root / "entities/person/cleo-test.yaml").read_text(encoding="utf-8")
    assert "hu: Test Cleo" in text and "{text: Cleo}" in text and "rights agent" in text
    assert [(ln.op, ln.key, ln.via) for ln in log(vault)] == [("create", "new:-:cleo test", "page")]


def test_an_edit_that_makes_no_entity_is_refused_in_its_words(client: TestClient, vault: Vault) -> None:
    cleo = card(started(client, vault), "Cleo Test")
    shown = panel(client, f"edit={cleo.id}")
    bad = post(client, DECIDE, decision(shown, "edit", edited(type="person", id="Not An Id")), status=400)
    assert bad["error"].startswith("id:")
    kind = post(client, DECIDE, decision(shown, "edit", edited(keep=[0], kinds={"0": "loud"})), status=400)
    assert "a spelling is plain or asr" in kind["error"]
    bare = post(client, DECIDE, decision(shown, "edit"), status=400)
    assert bare["error"] == "an edit carries the card as edited"
    assert log(vault) == []


def test_an_alias_card_is_accepted_edited_or_rejected_as_aliases(client: TestClient, vault: Vault) -> None:
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    found = started(client, vault)
    alias = next(c for c in found.cards if c.kind == "alias")
    shown = panel(client, f"edit={alias.id}")
    assert (shown["kind"], shown["title"]) == ("Alias for Northwind", "Nortwin")
    assert shown["hash"] != "none" and not shown["form"]["new"]
    post(client, DECIDE, decision(shown, "edit", edited(keep=[0], kinds={"0": "asr"})))
    assert [(ln.op, ln.key) for ln in log(vault)] == [("add", "alias:northwind:nortwin")]
    undo = next(line for line in read(client)["view"]["log"] if line["undo"])
    assert post(client, UNDO, {"op": undo["id"]})["notice"] == "Undone: 1 change taken back and logged."
    # The undo in the open round reopened the card: nothing kept rejects it.
    post(client, DECIDE, decision(panel(client, f"edit={alias.id}"), "edit", edited()))
    assert [ln.op for ln in log(vault)] == ["add", "undo", "reject"]


def test_a_stale_round_writes_nothing_and_a_changed_file_answers_409(client: TestClient, vault: Vault) -> None:
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    found = started(client, vault)
    alias = next(c for c in found.cards if c.kind == "alias")
    sent = decision(panel(client, f"open={alias.id}"), "accept")
    stale = post(client, DECIDE, sent | {"round": "2020-01-01T000000-other"}, status=400)
    assert "isn't this machine's current one" in stale["error"]
    nowhere = post(client, DECIDE, sent | {"card": "abcdef"}, status=400)
    assert "no card abcdef" in nowhere["error"]
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind, hu: Northwind}\n")
    changed = post(client, DECIDE, sent, status=409)
    assert changed["error"].endswith("Here it is as it stands now.")
    assert panel(client, f"open={alias.id}")["hash"] != sent["hash"]
    assert log(vault) == []


def test_a_decision_without_a_round_or_one_the_page_didn_t_make_is_refused(client: TestClient, vault: Vault) -> None:
    sent = {"act": "accept", "round": "r", "card": "abcdef", "hash": "none"}
    assert "no round on this machine" in post(client, DECIDE, sent, status=400)["error"]
    for broken, says in (
        ({"act": "approve"}, "act is accept, reject or edit"),
        ({"round": ""}, "the decision names no round"),
        ({"card": "1"}, "card is a card's id"),
        ({"hash": ""}, "the decision names no entity file's hash"),
    ):
        assert says in post(client, DECIDE, sent | broken, status=400)["error"], broken
    for extra in ({"via": "cli"}, {"edit": {"keep": [0], "by": "claude"}}):
        assert client.post(DECIDE, json=sent | extra, headers=SAME).status_code == 422, extra
    assert client.post(ROUND, json={"cards": 3}, headers=SAME).status_code == 422
    assert "op is a logged op's id" in post(client, UNDO, {"op": "x; rm"}, status=400)["error"]
    unknown = post(client, UNDO, {"op": "20261016T090000-test-box-1"}, status=400)
    assert "no op in the log has that id" in unknown["error"]
    assert log(vault) == []


def test_after_the_last_card_the_question_set_is_measured_again(
    client: TestClient, vault: Vault, tmp_path: Path
) -> None:
    questions = tmp_path / "questions.yaml"
    questions.write_text(QUESTIONS, encoding="utf-8")
    userconfig.set_section("curate", {"questions": str(questions)})
    found = started(client, vault)
    assert found.before is not None
    for c in found.cards:
        post(client, DECIDE, decision(panel(client, f"open={c.id}"), "reject"))
    measured = batch.load(vault)
    assert measured is not None and measured.after is not None
    rows = read(client)["view"]["measures"]["rows"]
    assert rows[0]["kind"] == "all" and rows[0]["after"]


def test_the_measure_after_waits_out_an_index_being_built(
    client: TestClient, vault: Vault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = tmp_path / "questions.yaml"
    questions.write_text(QUESTIONS, encoding="utf-8")
    userconfig.set_section("curate", {"questions": str(questions)})
    found = started(client, vault)

    async def building(self: IndexUpdater) -> None:
        raise IndexBuildingError("the index is being built; try again in a minute")

    monkeypatch.setattr(IndexUpdater, "fresh", building)
    for c in found.cards:
        post(client, DECIDE, decision(panel(client, f"open={c.id}"), "reject"))
    after = batch.load(vault)
    assert after is not None and after.after is None
    assert read(client)["view"]["measures"]["rows"][0]["after"] == ""
    assert "the index is being built" in post(client, ROUND, {}, status=400)["error"]


# -- what the page shows -----------------------------------------------------------------------------------------------


def test_the_log_shows_each_kind_of_change_with_undo(client: TestClient, vault: Vault) -> None:
    store = EntityStore(vault)
    fields = EntityFields(id="ada-example", type="person", name={"en": "Ada Example"})
    store.apply([Planned(1, "ada-example", Change(entity=fields))])
    store.apply([
        Planned(1, "ada-example", Change(name=NameFields(lang="hu", text="Example Ada"))),
        Planned(2, "ada-example", Change(id=IdFields(key="email", value="ada@example.com"))),
        Planned(3, "ada-example", Change(related=RelatedFields(type="org", target="northwind"))),
    ])  # fmt: skip
    created = log(vault)[0]
    store.undo([log(vault)[-1].id])
    got = read(client)
    lines = {line["part"]: line for line in got["view"]["log"]}
    assert lines["related org northwind"]["undone_by"] == log(vault)[-1].id
    assert any(part.startswith("undoes ") for part in lines)
    assert "id email ada@example.com" in lines and "name hu Example Ada" in lines
    assert lines["person, Ada Example"]["undo"] and lines["person, Ada Example"]["id"] == created.id
    assert lines["person, Ada Example"]["at"] == display.at(created.at, ZoneInfo("Europe/Budapest"))
    assert got["view"]["minutes"] == "no review time logged yet"


def test_what_tier1_applied_shows_since_the_round_started(client: TestClient, vault: Vault) -> None:
    found = started(client, vault)
    plant(vault, found.started + timedelta(seconds=30), "tier1", "new:-:salt road")
    view = read(client)["view"]
    assert "1 change applied by themselves since" in view["lead"]
    assert [line["part"] for line in view["applied"]] == ["new:-:salt road"]


def test_never_store_values_never_show(client: TestClient, vault: Vault) -> None:
    northwind = card(started(client, vault), "Northwind")
    post(client, DECIDE, decision(panel(client, f"open={northwind.id}"), "reject"))
    never_store(vault, keywords=["Nortwin"])
    got = read(client)
    shown = json.dumps(got) + json.dumps(card_read(client, f"open={northwind.id}"))
    assert "Nortwin" not in shown and "nortwin" not in shown
    assert "new:-:northwind" in shown and HIDDEN in shown
    # No Lift and no Undo on the hidden key: their lines would commit it again, and the store refuses both.
    assert [lift["liftable"] for lift in got["lifts"]] == [True, False]
    reject = next(ln for ln in log(vault) if ln.key == "new:-:nortwin")
    assert not next(line for line in got["view"]["log"] if line["id"] == reject.id)["undo"]
    lift = post(client, LIFT, {"key": curate_routes.key_id("new:-:nortwin")}, status=400)
    assert "it stays on the stoplist" in lift["error"]
    undo = post(client, UNDO, {"op": reject.id}, status=400)
    assert "edit the entity file by hand" in undo["error"]
    assert "nortwin" not in (lift["error"] + undo["error"]).lower()
    assert [ln.op for ln in log(vault)] == ["reject", "reject"]


@pytest.mark.parametrize("lists", [{"keywords": ["Nortwin"]}, {"people": ["Nortwin"]}])
def test_a_spelling_listed_after_the_round_never_shows_on_its_card(
    client: TestClient, vault: Vault, lists: dict[str, list[str]]
) -> None:
    northwind = card(started(client, vault), "Northwind")
    never_store(vault, **lists)
    for query in (f"open={northwind.id}", f"edit={northwind.id}"):
        shown = json.dumps(card_read(client, query))
        assert "nortwin" not in shown.lower() and HIDDEN in shown, query
    # Its snippets all name it, so none is cut; the form still names the hidden spelling by its place.
    assert panel(client, f"open={northwind.id}")["snippets"] == []
    assert [s["i"] for s in panel(client, f"edit={northwind.id}")["form"]["spellings"]] == [0]


def test_a_name_listed_after_the_round_never_shows_on_its_card_or_form(client: TestClient, vault: Vault) -> None:
    cleo = card(started(client, vault), "Cleo Test")
    never_store(vault, people=["Cleo Test"])
    for query in (f"open={cleo.id}", f"edit={cleo.id}"):
        shown = panel(client, query)
        said = json.dumps(shown)
        assert "Cleo Test" not in said and "Test Cleo" not in said and "cleo-test" not in said, query
        assert (shown["n"], shown["kind"], shown["title"]) == (4, "New person", HIDDEN)
    form = panel(client, f"edit={cleo.id}")["form"]
    assert form["id"] == HIDDEN and {n["code"]: n["value"] for n in form["names"]}["en"] == HIDDEN


def test_the_forms_matching_leaves_out_are_listed(client: TestClient, vault: Vault) -> None:
    entity(vault, ANN, "id: ann-sample\ntype: person\nname: {en: Ann Sample}\naliases:\n  - {text: Ann}\n")
    assert any(d.startswith("ann-sample: an is under three letters") for d in read(client)["view"]["dropped"])


EVIL = "\"><img src=x onerror=alert(1)> 'onmouseover=alert(2) x=' <script>alert(3)</script>"


def test_names_spellings_and_snippets_come_as_text_and_write_as_typed(client: TestClient, vault: Vault) -> None:
    more_recordings(vault, [f"Speaker said {EVIL} and met Nortwin today", "Nortwin again, with no markup."])
    found = started(client, vault)
    first = found.cards[0]
    spellings = [s.model_copy(update={"text": EVIL}) if s.add != "name" else s for s in first.spellings]
    planted = first.model_copy(update={"name": EVIL, "spellings": spellings})
    batch.save(vault, found.model_copy(update={"cards": [planted, *found.cards[1:]]}))
    shown = panel(client, f"edit={planted.id}")
    assert shown["title"] == EVIL and EVIL in [s["text"] for s in shown["spellings"]]
    typed = edited(type="term", id="evil-one", names={"en": EVIL}, role=EVIL)
    post(client, DECIDE, decision(shown, "edit", typed))
    written = yaml.safe_load((vault.root / "entities/term/evil-one.yaml").read_text(encoding="utf-8"))
    assert (written["name"]["en"], written["role"]) == (EVIL, EVIL)


def test_a_card_decided_twice_or_from_a_second_tab_is_refused(client: TestClient, vault: Vault) -> None:
    northwind = card(started(client, vault), "Northwind")
    shown = panel(client, f"open={northwind.id}")
    post(client, DECIDE, decision(shown, "reject"))
    again = post(client, DECIDE, decision(shown, "reject"), status=400)
    assert f"card {northwind.id} is rejected already" in again["error"]
    other = post(client, DECIDE, decision(shown, "accept"), status=400)
    assert "rejected already" in other["error"]
    assert [ln.key for ln in log(vault)] == ["new:-:northwind", "new:-:nortwin"]


def test_an_edit_the_store_refuses_says_why_in_the_page_s_words(client: TestClient, vault: Vault) -> None:
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    cleo = card(started(client, vault), "Cleo Test")
    typed = edited(type="person", id="northwind", names={"en": "Cleo Test"}, role="rights agent typed", keep=[0])
    refused = post(client, DECIDE, decision(panel(client, f"edit={cleo.id}"), "edit", typed), status=400)
    assert refused["error"] == "an entity has that id already"
    assert log(vault) == []


def test_a_question_file_broken_after_the_round_never_fails_a_decision(
    client: TestClient, vault: Vault, tmp_path: Path
) -> None:
    questions = tmp_path / "questions.yaml"
    questions.write_text(QUESTIONS, encoding="utf-8")
    userconfig.set_section("curate", {"questions": str(questions)})
    found = started(client, vault)
    questions.write_text("schema_version: 1\nquestions: [oops\n", encoding="utf-8")
    for c in found.cards:
        post(client, DECIDE, decision(panel(client, f"open={c.id}"), "reject"))
    after = batch.load(vault)
    assert after is not None and after.after is None


def test_a_line_a_stopped_write_left_is_marked_not_finished(client: TestClient, vault: Vault) -> None:
    found = started(client, vault)
    plant(vault, found.started + timedelta(seconds=30), "tier1", "new:-:salt road", committed=False)
    view = read(client)["view"]
    marked = [line for line in view["applied"] + view["log"] if line["unfinished"]]
    assert len(marked) == 2 and all(line["part"] == "new:-:salt road" for line in marked)


@pytest.mark.parametrize(
    ("query", "says"),
    [
        ("open=nope", "open is a card's id"),
        ("edit=xyz", "edit is a card's id"),
        ("open=abcdef&edit=123456", "edit names the open card"),
        ("done=bogus", "done is what the last change did"),
    ],
)
def test_a_url_the_page_can_t_read_says_which_part(client: TestClient, query: str, says: str) -> None:
    assert says in card_read(client, query, status=400)["error"]


def test_the_url_reads_one_canonical_form_and_says_a_change_once(client: TestClient, vault: Vault) -> None:
    started(client, vault)
    done = read(client, "done=lifted-1")
    assert (done["canonical"], done["notice"]) == ("/curate", "Lifted: curation may offer that spelling again.")
    assert card_read(client, "edit=abcdef&open=abcdef")["canonical"] == "/curate?edit=abcdef"


def test_a_card_from_an_older_round_says_so(client: TestClient, vault: Vault) -> None:
    started(client, vault)
    got = card_read(client, "open=abcdef")
    assert got["panel"] is None and got["missing"] == "This round has no such card: the link is from an older round."


def test_a_failure_answers_500_with_no_words_and_a_refusal_400_with_its_own(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(vault: Vault, state: curate_view.CurateState) -> None:
        raise RuntimeError("Kovács Ödön said the price")

    monkeypatch.setattr(curate_view, "gather", broken)
    for path in (API, f"{API}/panel"):
        got = client.get(path)
        assert got.status_code == 500 and "Something failed in Docket" in got.json()["error"]
        assert "Kov" not in got.text

    def refused(vault: Vault, state: curate_view.CurateState) -> None:
        raise DocketError("another docket process holds the vault")

    monkeypatch.setattr(curate_view, "gather", refused)
    assert read(client, status=400)["error"] == "another docket process holds the vault"


# -- the Guard, the session and the body on every write ---------------------------------------------------------------

WRITES = {ROUND: {}, DECIDE: {"act": "accept", "round": "r", "card": "abcdef", "hash": "none"}, UNDO: {"op": "x"}}


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "null", "Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "cross-site"},
        {},
        SAME | {"Authorization": f"Bearer {TOKEN}"},
    ],
)
def test_every_write_passes_the_guard_and_takes_the_session_only(
    client: TestClient, vault: Vault, headers: dict[str, str]
) -> None:
    for path, sent in (WRITES | {LIFT: {"key": "x"}}).items():
        assert client.post(path, json=sent, headers=headers).status_code == 403, path
    assert not batch.batch_path(vault).exists()


def test_a_write_without_a_session_or_a_json_body_is_refused(client: TestClient, vault: Vault) -> None:
    form = SAME | {"Content-Type": "application/x-www-form-urlencoded"}
    for path in (ROUND, DECIDE, UNDO, LIFT):
        assert client.get(path).status_code == 405, path
        assert client.post(path, content=b"act=accept", headers=form).status_code == 415, path
    # The form routes of the server's own page are gone with it.
    assert client.post("/curate/round", data={}, headers=SAME).status_code == 404
    assert client.get("/curate/panel").status_code == 404
    assert client.get(API, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 403
    client.cookies.clear()
    assert client.post(ROUND, json={}, headers=SAME).status_code == 401
    assert not batch.batch_path(vault).exists()


def test_a_round_is_due_a_week_after_the_last(client: TestClient, vault: Vault) -> None:
    begun = schedule.now_utc() - timedelta(days=10)
    plant(vault, begun + timedelta(minutes=2), "user", "new:-:salt road", schedule.round_id(begun, "test-box"))
    view = read(client)["view"]
    assert view["due"] == "A round is due: the last started 10 days ago."
    assert view["minutes"] == "review 2 min in the last 1 week with a round"
    started(client, vault)
    assert read(client)["view"]["due"].startswith("The next round is due on")


def test_the_batch_holds_no_snippet(client: TestClient, vault: Vault) -> None:
    found = started(client, vault)
    kept = json.loads(batch.batch_path(vault).read_text(encoding="utf-8"))
    assert set(kept["cards"][0]) >= {"id", "evidence"} and "snippets" not in kept["cards"][0]
    assert curatelog.fold(log(vault)).stoplist == frozenset()
    assert found.cards
