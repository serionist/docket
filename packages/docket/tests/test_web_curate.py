"""The Curate page over HTTP, signed in (DR-0473): a round started only by a post, its cards beside the open one with
snippets cut when shown, accept, edit, reject, undo and lift through form posts on the writer, each logged via the page;
a stale round and a changed entity file refused; the Guard on every post; and nothing of a never-store value shown."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from curate_kit import ANN, NORTHWIND, curate_vault, entity, more_recordings
from docket_webkit import OURS, a11y_problems, html_problems, parse
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
from docket.web import curate_view
from docket.web.templating import PAGE_CSP

SAME = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return curate_vault(tmp_path)


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


def page(client: TestClient, path: str = "/curate", status: int = 200) -> str:
    answer = client.get(path)
    assert answer.status_code == status, answer.text
    assert answer.headers["Content-Security-Policy"] == PAGE_CSP
    assert answer.headers["Cache-Control"] == "no-store"
    assert answer.headers["Referrer-Policy"] == "same-origin"
    assert not html_problems(answer.text), path
    assert not a11y_problems(answer.text, whole=not path.startswith("/curate/panel")), path
    return answer.text


def texts(html: str, cls: str) -> list[str]:
    return [el.text() for el in parse(html).find_all(class_=cls)]


def said(html: str) -> str:
    """What the page's notices say, as text."""
    return " ".join(texts(html, "notice"))


def started(client: TestClient, vault: Vault) -> Batch:
    answer = client.post("/curate/round", headers=SAME)
    assert answer.status_code == 303, answer.text
    found = batch.load(vault)
    assert found is not None
    assert answer.headers["location"] == f"/curate?done=round-{len(found.cards)}"
    return found


def card(found: Batch, name: str) -> Card:
    return next(c for c in found.cards if c.name == name)


def decide(html: str, act: str) -> dict[str, Any]:
    """The open card's form for an act, as the browser posts it."""
    for form in parse(html).find_all("form", action="/curate/decide"):
        if any(b.attrs.get("value") == act for b in form.find_all("button")):
            fields: dict[str, Any] = {
                i.attrs["name"]: i.attrs["value"] for i in form.find_all("input") if i.attrs.get("type") == "hidden"
            }
            return fields | {"act": act}
    raise AssertionError(f"no {act} form")


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


# -- a round -----------------------------------------------------------------------------------------------------------


def test_the_page_without_a_round_offers_one_and_writes_nothing(client: TestClient, vault: Vault) -> None:
    html = page(client)
    assert texts(html, "lead") == ["No round on this machine yet."]
    assert "Start a round" in parse(html).find("form", action="/curate/round").text()
    assert "No round yet: start one" in html
    assert not batch.batch_path(vault).exists()
    assert log(vault) == []
    assert vault.git.run("status", "--porcelain").stdout == ""


def test_a_round_starts_on_the_user_s_click_and_lists_its_cards(client: TestClient, vault: Vault) -> None:
    found = started(client, vault)
    html = page(client, f"/curate?done=round-{len(found.cards)}")
    assert texts(html, "lead")[0].endswith(f": {len(found.cards)} cards, 0 decided.")
    assert f"A new round: {len(found.cards)} cards." in html
    titles = texts(html, "record-title")
    assert titles[:2] == ["Northwind", "Dóra"]
    assert "1. New term" in texts(html, "kind")[0]
    assert "Left out:" in html and "Start a new round" in html
    # Snippets are cut only for the open card: none in the list, and none kept in the batch.
    assert "pallets" not in html
    assert "pallets" not in batch.batch_path(vault).read_text(encoding="utf-8")


def test_a_card_opens_beside_the_list_with_its_snippets(client: TestClient, vault: Vault) -> None:
    northwind = card(started(client, vault), "Northwind")
    html = page(client, f"/curate?open={northwind.id}")
    panel = parse(html).find("section", class_="card-panel")
    assert panel.find("h2").text() == "1. New term: Northwind"
    marks = [m.text() for m in panel.find_all("mark")]
    assert marks[:1] == ["Nortwin"]
    links = [a.attrs["href"] for a in panel.find_all("a", class_="locator")]
    assert links and all(link.startswith("/search?hit=r%3Aplaud%3Arec-") for link in links)
    fragment = client.get(f"/curate/panel?open={northwind.id}")
    assert fragment.status_code == 200 and fragment.headers["HX-Push-Url"] == f"/curate?open={northwind.id}"
    assert not a11y_problems(fragment.text, whole=False)
    closed = client.get("/curate/panel", headers={"HX-Current-URL": f"{OURS}/curate"})
    assert closed.headers["HX-Replace-Url"] == "/curate" and "card-panel" not in closed.text


def test_without_an_index_a_card_opens_without_snippets(client: TestClient, vault: Vault) -> None:
    northwind = card(started(client, vault), "Northwind")
    index_build.db_path(vault).unlink()
    html = page(client, f"/curate?open={northwind.id}")
    assert "No snippet to show" in html


# -- decisions ---------------------------------------------------------------------------------------------------------


def test_accept_writes_the_entity_and_logs_it_via_the_page(client: TestClient, vault: Vault) -> None:
    found = started(client, vault)
    northwind = card(found, "Northwind")
    fields = decide(page(client, f"/curate?open={northwind.id}"), "accept")
    assert fields["round"] == found.round and fields["card"] == northwind.id and fields["hash"] == "none"
    answer = client.post("/curate/decide", data=fields, headers=SAME)
    assert answer.status_code == 303 and answer.headers["location"] == f"/curate?done=accepted-{northwind.id}"
    assert (vault.root / "entities/term/northwind.yaml").is_file()
    [line] = log(vault)
    assert (line.op, line.via, line.round, line.card) == ("create", "page", found.round, northwind.id)
    html = page(client, f"/curate?open={northwind.id}&done=accepted-{northwind.id}")
    assert "Decided: accepted." in html and f"Card {northwind.id} accepted" in html
    assert texts(html, "lead")[0].endswith("1 decided.")
    again = client.post("/curate/decide", data=fields, headers=SAME)
    assert again.status_code == 409 and "changed since the page showed the card" in said(again.text)


def test_reject_stoplists_and_lift_takes_it_off(client: TestClient, vault: Vault) -> None:
    northwind = card(started(client, vault), "Northwind")
    fields = decide(page(client, f"/curate?open={northwind.id}"), "reject")
    assert (
        client.post("/curate/decide", data=fields, headers=SAME)
        .headers["location"]
        .endswith(f"done=rejected-{northwind.id}")
    )
    html = page(client)
    stopped = texts(html, "stop-line")
    assert [s.split(" Lift")[0] for s in stopped] == ["new:-:northwind", "new:-:nortwin"]
    lift = parse(html).find_all("form", action="/curate/lift")[0].find("input").attrs["value"]
    assert "northwind" not in lift
    assert client.post("/curate/lift", data={"key": lift}, headers=SAME).headers["location"] == "/curate?done=lifted-1"
    assert [ln.op for ln in log(vault)][-1] == "lift" and log(vault)[-1].via == "page"
    gone = client.post("/curate/lift", data={"key": lift}, headers=SAME)
    assert gone.status_code == 400 and "isn't on the stoplist any more" in said(gone.text)


def test_edit_accepts_the_card_as_typed_and_rejects_what_was_left_out(client: TestClient, vault: Vault) -> None:
    cleo = card(started(client, vault), "Cleo Test")
    html = page(client, f"/curate?edit={cleo.id}")
    form = parse(html).find("form", class_="edit-form")
    assert form.find("input", name="id").attrs["value"] == "cleo-test"
    assert [i.attrs["value"] for i in form.find_all("input", name="keep") if "checked" in i.attrs] == ["0", "1"]
    fields = decide(html, "edit") | {"type": "person", "id": "cleo-test", "name.en": "Cleo Test"}
    fields |= {"name.hu": "Test Cleo", "keep": ["0"], "kind.0": "plain", "role": "rights agent"}
    answer = client.post("/curate/decide", data=fields, headers=SAME)
    assert answer.status_code == 303 and answer.headers["location"].endswith(f"done=edited-{cleo.id}")
    text = (vault.root / "entities/person/cleo-test.yaml").read_text(encoding="utf-8")
    assert "hu: Test Cleo" in text and "{text: Cleo}" in text and "rights agent" in text
    assert [(ln.op, ln.key, ln.via) for ln in log(vault)] == [("create", "new:-:cleo test", "page")]


def test_an_edit_that_makes_no_entity_shows_the_form_again_as_typed(client: TestClient, vault: Vault) -> None:
    cleo = card(started(client, vault), "Cleo Test")
    fields = decide(page(client, f"/curate?edit={cleo.id}"), "edit")
    bad = client.post(
        "/curate/decide", data=fields | {"type": "person", "id": "Not An Id", "name.en": "Cleo"}, headers=SAME
    )
    assert bad.status_code == 400 and "id:" in said(bad.text)
    assert parse(bad.text).find("input", name="id").attrs["value"] == "Not An Id"
    kind = client.post("/curate/decide", data=fields | {"keep": ["0"], "kind.0": "loud"}, headers=SAME)
    assert kind.status_code == 400 and "a spelling is plain or asr" in said(kind.text)
    assert log(vault) == []


def test_an_alias_card_is_accepted_edited_or_rejected_as_aliases(client: TestClient, vault: Vault) -> None:
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    found = started(client, vault)
    alias = next(c for c in found.cards if c.kind == "alias")
    html = page(client, f"/curate?edit={alias.id}")
    assert "Alias for Northwind: Nortwin" in parse(html).find("h2", id="panel-title").text()
    fields = decide(html, "edit")
    assert fields["hash"] != "none"
    kept = client.post("/curate/decide", data=fields | {"keep": ["0"], "kind.0": "asr"}, headers=SAME)
    assert kept.status_code == 303
    assert [(ln.op, ln.key) for ln in log(vault)] == [("add", "alias:northwind:nortwin")]
    undo = parse(page(client)).find("form", action="/curate/undo").find("input").attrs["value"]
    assert client.post("/curate/undo", data={"op": undo}, headers=SAME).headers["location"] == "/curate?done=undone-1"
    # The undo in the open round reopened the card: nothing ticked rejects it.
    fields = decide(page(client, f"/curate?edit={alias.id}"), "edit")
    assert client.post("/curate/decide", data=fields, headers=SAME).status_code == 303
    assert [ln.op for ln in log(vault)] == ["add", "undo", "reject"]


def test_a_stale_round_or_a_changed_file_writes_nothing(client: TestClient, vault: Vault) -> None:
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    found = started(client, vault)
    alias = next(c for c in found.cards if c.kind == "alias")
    fields = decide(page(client, f"/curate?open={alias.id}"), "accept")
    stale = client.post("/curate/decide", data=fields | {"round": "2020-01-01T000000-other"}, headers=SAME)
    assert stale.status_code == 400 and "isn't this machine's current one" in said(stale.text)
    nowhere = client.post("/curate/decide", data=fields | {"card": "abcdef"}, headers=SAME)
    assert nowhere.status_code == 400 and "no card abcdef" in said(nowhere.text)
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind, hu: Northwind}\n")
    changed = client.post("/curate/decide", data=fields, headers=SAME)
    assert changed.status_code == 409 and "Here it is as it stands now" in said(changed.text)
    assert parse(changed.text).find("section", class_="card-panel")
    assert log(vault) == []


def test_a_decision_without_a_round_or_a_form_the_page_made_is_refused(client: TestClient, vault: Vault) -> None:
    fields = {"act": "accept", "round": "r", "card": "abcdef", "hash": "none"}
    none = client.post("/curate/decide", data=fields, headers=SAME)
    assert none.status_code == 400 and "no round on this machine" in said(none.text)
    for broken, says in (
        ({"act": "approve"}, "act is accept, reject or edit"),
        ({"round": ""}, "the form names no round"),
        ({"card": "1"}, "card is a card's id"),
        ({"hash": ""}, "the form names no entity file's hash"),
    ):
        answer = client.post("/curate/decide", data=fields | broken, headers=SAME)
        assert answer.status_code == 400 and says in said(answer.text), broken
    bad_op = client.post("/curate/undo", data={"op": "x; rm"}, headers=SAME)
    assert bad_op.status_code == 400 and "op is a logged op's id" in said(bad_op.text)
    unknown = client.post("/curate/undo", data={"op": "20261016T090000-test-box-1"}, headers=SAME)
    assert unknown.status_code == 400 and "no op in the log has that id" in said(unknown.text)
    assert log(vault) == []


def test_after_the_last_card_the_question_set_is_measured_again(
    client: TestClient, vault: Vault, tmp_path: Path
) -> None:
    questions = tmp_path / "questions.yaml"
    questions.write_text(
        'schema_version: 1\nasked: "2026-10-14T10:00:00+02:00"\nquestions:\n'
        '  - q: Nortwin pallets\n    want: ["r:plaud:rec-0000/transaction"]\n    kind: typo\n    lang: en\n',
        encoding="utf-8",
    )
    userconfig.set_section("curate", {"questions": str(questions)})
    found = started(client, vault)
    assert found.before is not None
    for c in found.cards:
        fields = decide(page(client, f"/curate?open={c.id}"), "reject")
        assert client.post("/curate/decide", data=fields, headers=SAME).status_code == 303
    measured = batch.load(vault)
    assert measured is not None and measured.after is not None
    rows = [tr.text() for tr in parse(page(client)).find("table", class_="measures").find_all("tr")]
    assert rows[0] == "Kind Before After" and rows[1].startswith("all ")


def test_the_measure_after_waits_out_an_index_being_built(
    client: TestClient, vault: Vault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = tmp_path / "questions.yaml"
    questions.write_text(
        'schema_version: 1\nasked: "2026-10-14T10:00:00+02:00"\nquestions:\n'
        '  - q: Nortwin pallets\n    want: ["r:plaud:rec-0000/transaction"]\n    kind: typo\n    lang: en\n',
        encoding="utf-8",
    )
    userconfig.set_section("curate", {"questions": str(questions)})
    found = started(client, vault)

    async def building(self: IndexUpdater) -> None:
        raise IndexBuildingError("the index is being built; try again in a minute")

    monkeypatch.setattr(IndexUpdater, "fresh", building)
    for c in found.cards:
        fields = decide(page(client, f"/curate?open={c.id}"), "reject")
        assert client.post("/curate/decide", data=fields, headers=SAME).status_code == 303
    after = batch.load(vault)
    assert after is not None and after.after is None
    assert "after the last card" in page(client)
    refused = client.post("/curate/round", headers=SAME)
    assert refused.status_code == 400 and "the index is being built" in said(refused.text)


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
    html = page(client)
    lines = texts(html, "log-line")
    assert any("related org northwind" in ln and f"undone by {log(vault)[-1].id}" in ln for ln in lines)
    assert any("undoes " in ln for ln in lines)
    assert any("id email ada@example.com" in ln for ln in lines)
    assert any("name hu Example Ada" in ln for ln in lines)
    assert any("person, Ada Example" in ln and "Undo" in ln for ln in lines)
    assert created.id in html
    assert texts(html, "minutes") == ["no review time logged yet"]


def test_what_tier1_applied_shows_since_the_round_started(client: TestClient, vault: Vault) -> None:
    found = started(client, vault)
    plant(vault, found.started + timedelta(seconds=30), "tier1", "new:-:salt road")
    html = page(client)
    assert "1 change applied by themselves since" in texts(html, "lead")[0]
    assert "new:-:salt road" in html


def test_never_store_values_never_show(client: TestClient, vault: Vault) -> None:
    northwind = card(started(client, vault), "Northwind")
    fields = decide(page(client, f"/curate?open={northwind.id}"), "reject")
    assert client.post("/curate/decide", data=fields, headers=SAME).status_code == 303
    never_store(vault, keywords=["Nortwin"])
    html = page(client, f"/curate?open={northwind.id}")
    assert "Nortwin" not in html and "nortwin" not in html
    assert "new:-:northwind" in html and "(on a never-store list)" in html
    # No Lift and no Undo on the hidden key: their lines would commit it again, and the store refuses both.
    rows = parse(html).find_all("li", class_="stop-line")
    assert [len(r.find_all("form")) for r in rows] == [1, 0]
    reject = next(ln for ln in log(vault) if ln.key == "new:-:nortwin")
    assert reject.id not in [i.attrs["value"] for i in parse(html).find_all("input", name="op")]
    lift = client.post("/curate/lift", data={"key": curate_routes.key_id("new:-:nortwin")}, headers=SAME)
    assert lift.status_code == 400 and "it stays on the stoplist" in said(lift.text)
    undo = client.post("/curate/undo", data={"op": reject.id}, headers=SAME)
    assert undo.status_code == 400 and "edit the entity file by hand" in said(undo.text)
    assert "nortwin" not in said(lift.text).lower() + said(undo.text).lower()
    assert [ln.op for ln in log(vault)] == ["reject", "reject"]


@pytest.mark.parametrize("lists", [{"keywords": ["Nortwin"]}, {"people": ["Nortwin"]}])
def test_a_spelling_listed_after_the_round_never_shows_on_its_card(
    client: TestClient, vault: Vault, lists: dict[str, list[str]]
) -> None:
    northwind = card(started(client, vault), "Northwind")
    never_store(vault, **lists)
    for path in (f"/curate?open={northwind.id}", f"/curate?edit={northwind.id}", f"/curate/panel?edit={northwind.id}"):
        html = page(client, path)
        assert "nortwin" not in html.lower(), path
        assert "(on a never-store list)" in html, path
    # Its snippets all name it, so none is cut; the form still posts the hidden spelling by its place.
    assert parse(page(client, f"/curate?open={northwind.id}")).find_all("figure", class_="snippet") == []
    form = parse(page(client, f"/curate?edit={northwind.id}")).find("form", class_="edit-form")
    assert [i.attrs["value"] for i in form.find_all("input", name="keep")] == ["0"]


def test_a_name_listed_after_the_round_never_shows_on_its_card_or_form(client: TestClient, vault: Vault) -> None:
    cleo = card(started(client, vault), "Cleo Test")
    never_store(vault, people=["Cleo Test"])
    for path in (f"/curate?open={cleo.id}", f"/curate?edit={cleo.id}"):
        html = page(client, path)
        panel = parse(html).find("section", class_="card-panel")
        assert "Cleo Test" not in panel.text() and "Test Cleo" not in panel.text() and "cleo-test" not in html, path
        assert panel.find("h2").text() == "4. New person: (on a never-store list)"
    form = parse(page(client, f"/curate?edit={cleo.id}")).find("form", class_="edit-form")
    assert form.find("input", name="id").attrs["value"] == "(on a never-store list)"
    assert form.find("input", name="name.en").attrs["value"] == "(on a never-store list)"


def test_the_forms_matching_leaves_out_are_listed(client: TestClient, vault: Vault) -> None:
    entity(vault, ANN, "id: ann-sample\ntype: person\nname: {en: Ann Sample}\naliases:\n  - {text: Ann}\n")
    html = page(client)
    assert any(d.startswith("ann-sample: an is under three letters") for d in texts(html, "dropped"))


EVIL = "\"><img src=x onerror=alert(1)> 'onmouseover=alert(2) x=' <script>alert(3)</script>"


def test_names_spellings_and_snippets_render_as_text(client: TestClient, vault: Vault) -> None:
    more_recordings(vault, [f"Speaker said {EVIL} and met Nortwin today", "Nortwin again, with no markup."])
    found = started(client, vault)
    first = found.cards[0]
    spellings = [s.model_copy(update={"text": EVIL}) if s.add != "name" else s for s in first.spellings]
    planted = first.model_copy(update={"name": EVIL, "spellings": spellings})
    batch.save(vault, found.model_copy(update={"cards": [planted, *found.cards[1:]]}))
    for path in (f"/curate?open={planted.id}", f"/curate?edit={planted.id}", f"/curate/panel?edit={planted.id}"):
        html = client.get(path).text
        tree = parse(html)
        assert EVIL not in html, path
        assert tree.find_all("img") == [], path
        assert not [k for el in tree.walk() for k in el.attrs if k.lower().startswith("on")], path
    fields = decide(page(client, f"/curate?edit={planted.id}"), "edit") | {"type": "term", "id": "evil-one"}
    answer = client.post("/curate/decide", data=fields | {"name.en": EVIL, "role": EVIL}, headers=SAME)
    assert answer.status_code == 303
    html = page(client)
    assert EVIL not in html and parse(html).find_all("img") == []


def test_a_card_decided_twice_or_from_a_second_tab_is_refused(client: TestClient, vault: Vault) -> None:
    northwind = card(started(client, vault), "Northwind")
    reject = decide(page(client, f"/curate?open={northwind.id}"), "reject")
    accept = decide(page(client, f"/curate?open={northwind.id}"), "accept")
    assert client.post("/curate/decide", data=reject, headers=SAME).status_code == 303
    again = client.post("/curate/decide", data=reject, headers=SAME)
    assert again.status_code == 400 and f"card {northwind.id} is rejected already" in said(again.text)
    other = client.post("/curate/decide", data=accept, headers=SAME)
    assert other.status_code == 400 and "rejected already" in said(other.text)
    assert [ln.key for ln in log(vault)] == ["new:-:northwind", "new:-:nortwin"]


def test_an_edit_the_store_refuses_keeps_what_was_typed(client: TestClient, vault: Vault) -> None:
    entity(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    cleo = card(started(client, vault), "Cleo Test")
    fields = decide(page(client, f"/curate?edit={cleo.id}"), "edit")
    typed = fields | {"type": "person", "id": "northwind", "name.en": "Cleo Test", "role": "rights agent typed"}
    answer = client.post("/curate/decide", data=typed | {"name.hu": "Test Cleo", "keep": ["0"]}, headers=SAME)
    assert answer.status_code == 400
    assert said(answer.text) == "an entity has that id already"
    form = parse(answer.text).find("form", class_="edit-form")
    assert form.find("input", name="role").attrs["value"] == "rights agent typed"
    assert form.find("input", name="name.hu").attrs["value"] == "Test Cleo"
    assert log(vault) == []


def test_a_question_file_broken_after_the_round_never_fails_a_decision(
    client: TestClient, vault: Vault, tmp_path: Path
) -> None:
    questions = tmp_path / "questions.yaml"
    questions.write_text(
        'schema_version: 1\nasked: "2026-10-14T10:00:00+02:00"\nquestions:\n'
        '  - q: Nortwin pallets\n    want: ["r:plaud:rec-0000/transaction"]\n    kind: typo\n    lang: en\n',
        encoding="utf-8",
    )
    userconfig.set_section("curate", {"questions": str(questions)})
    found = started(client, vault)
    questions.write_text("schema_version: 1\nquestions: [oops\n", encoding="utf-8")
    for c in found.cards:
        fields = decide(page(client, f"/curate?open={c.id}"), "reject")
        assert client.post("/curate/decide", data=fields, headers=SAME).status_code == 303
    after = batch.load(vault)
    assert after is not None and after.after is None


def test_a_line_a_stopped_write_left_is_marked_not_finished(client: TestClient, vault: Vault) -> None:
    found = started(client, vault)
    plant(vault, found.started + timedelta(seconds=30), "tier1", "new:-:salt road", committed=False)
    html = page(client)
    marked = [ln for ln in texts(html, "log-line") if "not finished: the next write commits it" in ln]
    assert len(marked) == 2 and all("new:-:salt road" in ln for ln in marked)


@pytest.mark.parametrize(
    ("path", "says"),
    [
        ("/curate?open=nope", "open is a card's id"),
        ("/curate?edit=xyz", "edit is a card's id"),
        ("/curate?open=abcdef&edit=123456", "edit names the open card"),
        ("/curate?done=bogus", "done is what the last change did"),
    ],
)
def test_a_url_the_page_can_t_read_says_which_part(client: TestClient, path: str, says: str) -> None:
    assert says in said(page(client, path, status=400))


def test_a_card_from_an_older_round_and_a_panel_error(client: TestClient, vault: Vault) -> None:
    started(client, vault)
    assert "Not in this round" in page(client, "/curate?open=abcdef")
    bad = client.get("/curate/panel?open=nope")
    assert bad.status_code == 400 and "open is a card" in said(bad.text)


def test_a_failure_answers_the_error_page(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(vault: Vault, state: curate_view.CurateState) -> None:
        raise RuntimeError("Kovács Ödön said the price")

    monkeypatch.setattr(curate_view, "gather", broken)
    assert client.get("/curate").status_code == 500
    panel = client.get("/curate/panel")
    assert panel.status_code == 500 and "Something failed in Docket" in said(panel.text) and "Kov" not in panel.text

    def refused(vault: Vault, state: curate_view.CurateState) -> None:
        raise DocketError("another docket process holds the vault")

    monkeypatch.setattr(curate_view, "gather", refused)
    answer = client.get("/curate")
    assert answer.status_code == 400 and "another docket process holds the vault" in said(answer.text)


# -- the Guard on every post -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Origin": "http://evil.example"}, 403),
        ({"Origin": "null", "Sec-Fetch-Site": "same-origin"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({}, 403),
    ],
)
def test_every_post_passes_the_guard(client: TestClient, vault: Vault, headers: dict[str, str], status: int) -> None:
    for route in ("/curate/round", "/curate/decide", "/curate/undo", "/curate/lift"):
        assert client.post(route, data={}, headers=headers).status_code == status, route
    assert not batch.batch_path(vault).exists()


def test_a_post_without_a_session_is_refused_and_get_on_a_post_route_is_405(client: TestClient, vault: Vault) -> None:
    for route in ("/curate/round", "/curate/decide", "/curate/undo", "/curate/lift"):
        assert client.get(route).status_code == 405, route
    client.cookies.clear()
    assert client.post("/curate/round", headers=SAME).status_code == 401
    assert not batch.batch_path(vault).exists()


def test_a_post_that_isn_t_a_form_is_refused(client: TestClient) -> None:
    for route in ("/curate/round", "/curate/decide", "/curate/undo", "/curate/lift"):
        assert client.post(route, json={"act": "accept"}, headers=SAME).status_code == 415, route


def test_a_round_is_due_a_week_after_the_last(client: TestClient, vault: Vault) -> None:
    begun = schedule.now_utc() - timedelta(days=10)
    plant(vault, begun + timedelta(minutes=2), "user", "new:-:salt road", schedule.round_id(begun, "test-box"))
    html = page(client)
    assert "A round is due: the last started 10 days ago." in html
    assert texts(html, "minutes") == ["review 2 min in the last 1 week with a round"]
    started(client, vault)
    assert "The next round is due on" in page(client)


def test_the_batch_holds_no_snippet(client: TestClient, vault: Vault) -> None:
    found = started(client, vault)
    kept = json.loads(batch.batch_path(vault).read_text(encoding="utf-8"))
    assert set(kept["cards"][0]) >= {"id", "evidence"} and "snippets" not in kept["cards"][0]
    assert curatelog.fold(log(vault)).stoplist == frozenset()
    assert found.cards
