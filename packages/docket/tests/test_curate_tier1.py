"""Tier 1 (DR-0469 to DR-0471) on an invented vault of mail, chat and one recording two engines transcribed: each fact
applied to its one entity in one commit, by rule, with its evidence; two matches a card each; a refused fact skipped
and listed while the rest commit; only on the primary machine; an undone or hand-removed fact never back; and the
round, the page and `show` listing what it did."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from docket_testkit import copied, make_config, make_record
from docket_webkit import OURS, parse
from staging_kit import signed_in
from starlette.testclient import TestClient

from docket import index
from docket.cli import main
from docket.curate import batch, tier1
from docket.curate.propose import Card
from docket.curate.tier1 import Tier1
from docket.errors import DocketError
from docket.index import build as index_build
from docket.vault import curatelog
from docket.vault.curatelog import Change, IdFields, Line, folded, read_all
from docket.vault.entitystore import EntityStore, Planned, Reject
from docket.vault.vault import Vault

SAME = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}
RECORDING = "plaud:rec-two"
ENTITIES = {
    "entities/person/ada-example.yaml": "id: ada-example\ntype: person\nname: {en: Ada Example}\n",
    "entities/person/ben-sample.yaml": "id: ben-sample\ntype: person\nname: {en: Benny Sample}\nids: {chat: U0BEN}\n",
    "entities/person/cleo-test.yaml": (
        "id: cleo-test\ntype: person\nname: {en: Cleo Test}\nids: {email: cleo@example.com}\n"
    ),
    "entities/org/northwind.yaml": "id: northwind\ntype: org\nname: {en: Northwind}\n",
    "entities/person/rita-sample.yaml": "id: rita-sample\ntype: person\nname: {en: Rita Sample}\n",
    "entities/person/rita-sample-2.yaml": "id: rita-sample-2\ntype: person\nname: {en: Rita Sample}\n",
}


def _when(n: int) -> datetime:
    return datetime(2026, 10, 12, 9, 0, tzinfo=UTC) + timedelta(hours=n)


def _mail(n: int, author: str, text: str) -> Any:
    return make_record(
        rid=f"mail:<m{n}@example.com>", source="mail", kind="mail", container=f"mail:m{n}",
        thread=f"mail:<m{n}@example.com>", author=author, title="The order", text=text, ts=_when(n),
    )  # fmt: skip


def _chat(n: int, author: str, text: str) -> Any:
    return make_record(n, author=author, text=text, ts=_when(10 + n))


def _transcript(engine: str, said: list[tuple[int, str]]) -> Any:
    segments = [
        {"start_ms": at * 1000, "end_ms": at * 1000 + 4000, "speaker": "Speaker 1", "text": t} for at, t in said
    ]
    under = {"source": "plaud", "container": RECORDING, "thread": RECORDING, "author": None, "ts": _when(20)}
    text = "\n".join(f"Speaker 1: {t}" for _, t in said)
    return make_record(
        rid=f"{RECORDING}/{engine}", kind="transcript", engine=engine, segments=segments, text=text, **under
    )


def build_tier1_vault(folder: Path) -> Path:
    vault = Vault.init(folder / "vault", make_config(), "test-box")
    vault.write_run("mail", [
        _mail(1, "Ada Example <ada@example.com>", "The pallets ship on Monday."),
        _mail(2, "Cleo T. (Northwind) <cleo@example.com>", "The labels are ready."),
        _mail(3, "Rita Sample <rita@example.com>", "Here is the invoice."),
        _mail(4, "Zed Nobody <zed@example.com>", "Unsubscribe here."),
    ])  # fmt: skip
    vault.write_run("chat", [
        _chat(1, "Ada Example <U0ADA>", "Morning, all."),
        _chat(2, "Benny S. <U0BEN>", "On my way."),
        _chat(3, "U0ZED", "A bot that names no one."),
    ])  # fmt: skip
    vault.write_run("plaud", [
        _transcript("plaud", [(0, "Northwind confirmed the pallets."), (10, "Then Nortwind sent the labels.")]),
        _transcript("whisper", [(0, "Northwind confirmed the pallets."), (11, "Then Nortwind sent the labels.")]),
    ])  # fmt: skip
    for rel, text in ENTITIES.items():
        path = vault.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    vault.commit(list(ENTITIES), "seed the entities")
    index.rebuild(vault)
    return vault.root


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault.open(copied(tmp_path, build_tier1_vault, "tier1-vault"))


def entity_file(vault: Vault, rel: str) -> dict[str, Any]:
    return yaml.safe_load((vault.root / rel).read_text(encoding="utf-8"))


def config(vault: Vault, **fields: Any) -> None:
    path = vault.root / "vault.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update(fields)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    vault.config = Vault.open(vault.root).config


def facts(ran: Tier1) -> list[tuple[str | None, str | None, str]]:
    written = ran.written.lines if ran.written is not None else ()
    return [(ln.rule, ln.entity, ln.key) for ln in written]


APPLIED = [
    ("address", "ada-example", "id:ada-example:email=ada@example.com"),
    ("chat id", "ada-example", "id:ada-example:chat=u0ada"),
    ("engines", "northwind", "alias:northwind:nortwind"),
    ("shown name", "ben-sample", "alias:ben-sample:benny s"),
    ("shown name", "cleo-test", "alias:cleo-test:cleo t"),
]


def test_each_fact_goes_to_its_one_entity_in_one_commit(vault: Vault) -> None:
    commits = vault.git.run("rev-list", "--count", "HEAD").stdout.strip()
    ran = tier1.run(vault)
    assert sorted(facts(ran)) == APPLIED
    assert ran.skipped == () and ran.note is None
    assert int(vault.git.run("rev-list", "--count", "HEAD").stdout) == int(commits) + 1
    assert all(ln.by == "tier1" and ln.op == "add" and ln.round is None for ln in ran.written.lines)  # type: ignore[union-attr]
    address = next(ln for ln in ran.written.lines if ln.rule == "address")  # type: ignore[union-attr]
    assert address.evidence is not None and address.evidence.locators == ("r:mail:<m1@example.com>",)
    assert entity_file(vault, "entities/person/ada-example.yaml")["ids"] == {
        "email": "ada@example.com",
        "chat": "U0ADA",
    }
    assert {"text": "Cleo T."} in entity_file(vault, "entities/person/cleo-test.yaml")["aliases"]
    assert {"text": "Nortwind", "kind": "asr"} in entity_file(vault, "entities/org/northwind.yaml")["aliases"]
    # Never a new entity: the sender no entity names gives nothing.
    assert sorted(p.name for p in (vault.root / "entities" / "person").glob("*.yaml")) == [
        "ada-example.yaml", "ben-sample.yaml", "cleo-test.yaml", "rita-sample-2.yaml", "rita-sample.yaml"
    ]  # fmt: skip
    again = tier1.run(vault)
    assert again.written is None and again.skipped == ()


def test_two_matches_give_a_card_each_and_apply_nothing(vault: Vault) -> None:
    ran = tier1.run(vault)
    assert [(p.entity, p.id) for p in ran.proposals] == [
        ("rita-sample", IdFields(key="email", value="rita@example.com")),
        ("rita-sample-2", IdFields(key="email", value="rita@example.com")),
    ]
    assert "ids" not in entity_file(vault, "entities/person/rita-sample.yaml")
    round_ = batch.start(vault)
    cards = [c for c in round_.cards if c.kind == "id"]
    assert [(c.entity, c.fact, c.key) for c in cards] == [
        ("rita-sample", ("email", "rita@example.com"), "id:rita-sample:email=rita@example.com"),
        ("rita-sample-2", ("email", "rita@example.com"), "id:rita-sample-2:email=rita@example.com"),
    ]
    assert cards[0].evidence == [("r:mail:<m3@example.com>", "Rita Sample")] and cards[0].adds() == []
    written = batch.accept(vault, round_, cards[:1])
    assert [(ln.op, ln.key) for ln in written.lines] == [("add", "id:rita-sample:email=rita@example.com")]
    assert entity_file(vault, "entities/person/rita-sample.yaml")["ids"] == {"email": "rita@example.com"}
    assert [r.key for r in batch.rejects(cards[1], round_.round)] == ["id:rita-sample-2:email=rita@example.com"]
    batch.reject(vault, round_, cards[1:])
    states = batch.states(vault, round_, read_all(vault.root).lines)
    assert (states[cards[0].id], states[cards[1].id]) == ("accepted", "rejected")
    with pytest.raises(DocketError, match="gives an id: accept or reject it as it is"):
        batch.settle(
            vault,
            round_,
            [Planned(1, "rita-sample-2", Change(id=IdFields(key="email", value="x@y.z")), card=cards[1].id)],
        )
    # Neither comes back: one is held, one rejected.
    assert all(c.kind != "id" for c in batch.start(vault).cards)


def test_an_id_an_entity_holds_by_hand_closes_its_card(vault: Vault) -> None:
    round_ = batch.start(vault)
    card = next(c for c in round_.cards if c.entity == "rita-sample-2")
    path = vault.root / "entities/person/rita-sample-2.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "ids: {email: Rita@Example.com}\n", encoding="utf-8")
    assert batch.states(vault, round_, read_all(vault.root).lines)[card.id] == "done"


def test_a_refused_fact_is_skipped_and_listed_while_the_rest_commit(vault: Vault) -> None:
    config(vault, never_store={"keywords": ["Benny"]})
    path = vault.root / "entities/person/cleo-test.yaml"
    path.write_text("# kept by hand\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    vault.commit(["entities/person/cleo-test.yaml"], "a comment")
    ran = tier1.run(vault)
    assert sorted(facts(ran)) == [f for f in APPLIED if f[1] not in ("ben-sample", "cleo-test")]
    assert sorted(ran.skipped) == [
        "shown name for ben-sample: a value is on the never-store keywords list; leave it out",
        "shown name for cleo-test: the entity's file holds a comment on line 1, which a rewrite would lose; take it "
        "out, or make the change by hand",
    ]
    assert "Benny" not in " ".join(ran.skipped)
    # Each round lists them again, since they never reach the log.
    assert sorted(batch.start(vault).skipped) == sorted(ran.skipped)


def test_the_skipped_facts_go_with_the_round(vault: Vault) -> None:
    config(vault, never_store={"keywords": ["Benny"]})
    round_ = batch.start(vault)
    assert round_.skipped == ["shown name for ben-sample: a value is on the never-store keywords list; leave it out"]
    applied = [ln for ln in read_all(vault.root).lines if ln.by == "tier1"]
    assert len(applied) == 4 and all(ln.at >= round_.started for ln in applied)


def test_only_the_primary_machine_runs_tier1(vault: Vault) -> None:
    config(vault, primary_machine="laptop")
    ran = tier1.run(vault)
    assert ran == Tier1(note="tier 1 runs on the primary machine, laptop; this is test-box")
    assert batch.start(vault).tier1 == "tier 1 runs on the primary machine, laptop; this is test-box"
    assert read_all(vault.root).lines == ()


def test_a_log_line_that_doesn_t_read_stops_tier1(vault: Vault) -> None:
    folder = vault.root / curatelog.LOG_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026-10-test-box.jsonl").write_text("not a line\n", encoding="utf-8")
    assert (
        tier1.run(vault).note == "tier 1 didn't run: a line of the curate log doesn't read; docket curate log names it"
    )


def test_without_an_index_tier1_waits(vault: Vault) -> None:
    index_build.db_path(vault).unlink()
    assert tier1.run(vault).note == "tier 1 didn't run: there is no index to read yet"


def test_an_undone_or_hand_removed_fact_never_comes_back(vault: Vault) -> None:
    ran = tier1.run(vault)
    lines = {ln.key: ln for ln in ran.written.lines}  # type: ignore[union-attr]
    EntityStore(vault).undo([lines["alias:northwind:nortwind"].id])
    path = vault.root / "entities/person/ben-sample.yaml"
    path.write_text("id: ben-sample\ntype: person\nname: {en: Benny Sample}\nids: {chat: U0BEN}\n", encoding="utf-8")
    vault.commit(["entities/person/ben-sample.yaml"], "Benny S. out by hand")
    index.update(vault)
    again = tier1.run(vault)
    assert again.written is None


def test_two_engines_must_agree_where_the_recording_names_the_entity(vault: Vault) -> None:
    conn = index_build.open_index(vault)
    try:
        found = [f for f in tier1.find(conn, vault).facts if f.rule == "engines"]
    finally:
        conn.close()
    assert [(f.entities, f.text) for f in found] == [(("northwind",), "Nortwind")]
    # One engine alone: nothing.
    whisper = vault.root / "raw" / "plaud"
    for run in whisper.rglob("*.jsonl"):
        kept = [
            ln for ln in run.read_text(encoding="utf-8").splitlines() if '"engine":"whisper"' not in ln.replace(" ", "")
        ]
        run.write_text("\n".join(kept) + "\n", encoding="utf-8")
    index.rebuild(vault)
    conn = index_build.open_index(vault)
    try:
        assert [f for f in tier1.find(conn, vault).facts if f.rule == "engines"] == []
    finally:
        conn.close()


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


def test_the_page_and_show_list_what_tier1_did(
    client: TestClient, vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config(vault, never_store={"keywords": ["Benny"]})
    assert client.post("/curate/round", headers=SAME).status_code == 303
    html = client.get("/curate").text
    applied = parse(html).find_all("ol", class_="log-lines")[0]
    assert len(applied.find_all("li")) == 4 and len(applied.find_all("form")) == 4
    assert all(ln.via == "page" for ln in read_all(vault.root).lines)
    assert "shown name for ben-sample: a value is on the never-store keywords list" in html
    rita = next(c for c in batch.load(vault).cards if c.entity == "rita-sample")  # type: ignore[union-attr]
    panel = client.get(f"/curate?open={rita.id}").text
    assert parse(panel).find("h2", id="panel-title").text().endswith("Id for Rita Sample: email rita@example.com")
    assert "Edit" not in [a.text() for a in parse(panel).find_all("a", class_="button")]
    monkeypatch.setenv("DOCKET_VAULT", str(vault.root))
    assert main(["curate", "show"]) == 0
    out = capsys.readouterr().out
    assert out.count("applied  by itself:") == 4
    assert "skipped  by itself: shown name for ben-sample" in out
    assert "id for Rita Sample (rita-sample): email rita@example.com" in out


def test_another_machine_s_round_says_why_tier1_didn_t_run(client: TestClient, vault: Vault) -> None:
    config(vault, primary_machine="laptop")
    assert client.post("/curate/round", headers=SAME).status_code == 303
    assert "Tier 1 runs on the primary machine, laptop; this is test-box." in client.get("/curate").text
    assert read_all(vault.root).lines == ()


def test_a_tier1_line_is_logged_with_its_rule(vault: Vault) -> None:
    tier1.run(vault)
    line = next(ln for ln in read_all(vault.root).lines if ln.rule == "chat id")
    assert isinstance(line, Line) and line.change is not None and line.change.id == IdFields(key="chat", value="U0ADA")


# -- what tier 1 must never do by itself (#126's review) ---------------------------------------------------------------


def small_vault(tmp_path: Path, entities: dict[str, str], **runs: list[Any]) -> Vault:
    """A vault of a few entities and records, its index built."""
    v = Vault.init(tmp_path / "small", make_config(), "test-box")
    for rel, text in entities.items():
        path = v.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    v.commit(list(entities), "seed")
    for source, records in runs.items():
        v.write_run(source, records)
    index.rebuild(v)
    return v


ADA_BEN = {
    "entities/person/ada-example.yaml": "id: ada-example\ntype: person\nname: {en: Ada Example}\n",
    "entities/person/ben-sample.yaml": "id: ben-sample\ntype: person\nname: {en: Ben Sample}\n",
}
SHARED = "notifications@github.example"


def test_a_shared_sender_gives_no_one_its_address(tmp_path: Path) -> None:
    v = small_vault(tmp_path, ADA_BEN, mail=[
        _mail(1, f"Ada Example <{SHARED}>", "Merged the pull request."),
        _mail(2, f"Ben Sample <{SHARED}>", "Commented on the issue."),
    ])  # fmt: skip
    ran = tier1.run(v)
    assert ran.written is None and ran.proposals == ()
    assert "ids" not in entity_file(v, "entities/person/ada-example.yaml")
    assert "ids" not in entity_file(v, "entities/person/ben-sample.yaml")


ADA = "entities/person/ada-example.yaml"


def taken_back_vault(tmp_path: Path) -> tuple[Vault, Line]:
    """Ada's address, given by tier 1 while she was the only name shown with it, then shown with Ben's and Carol's."""
    v = small_vault(tmp_path, ADA_BEN, mail=[_mail(1, f"Ada Example <{SHARED}>", "Merged the pull request.")])
    first = tier1.run(v)
    assert facts(first) == [("address", "ada-example", f"id:ada-example:email={SHARED}")]
    v.write_run("mail", [
        _mail(2, f"Ben Sample <{SHARED}>", "Commented on the issue."),
        _mail(3, f"Carol Test <{SHARED}>", "Opened an issue."),
    ])  # fmt: skip
    index.update(v)
    return v, first.written.lines[0]  # type: ignore[union-attr]


def test_an_address_tier1_gave_is_taken_back_once_its_sender_looks_shared(tmp_path: Path) -> None:
    v, given = taken_back_vault(tmp_path)
    ran = tier1.run(v)
    assert ran.written is None and ran.skipped == () and ran.proposals == ()
    back = ran.taken_back.lines  # type: ignore[union-attr]
    assert [(ln.op, ln.by, ln.rule, ln.why, ln.of, ln.key) for ln in back] == [
        ("undo", "tier1", "address", "now shared", given.id, given.key)
    ]
    assert "ids" not in entity_file(v, ADA) and "aliases" not in entity_file(v, ADA)
    assert given.key in curatelog.fold(read_all(v.root).lines).stoplist
    # It stays taken back, and no one else gets it.
    index.update(v)
    again = tier1.run(v)
    assert again.written is None and again.taken_back is None
    assert "ids" not in entity_file(v, "entities/person/ben-sample.yaml")


def test_a_take_back_the_store_refuses_is_skipped_and_listed(tmp_path: Path) -> None:
    v, _ = taken_back_vault(tmp_path)
    path = v.root / ADA
    path.write_text("# kept by hand\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    v.commit([ADA], "a comment")
    ran = tier1.run(v)
    assert ran.taken_back is None and not ran.wrote
    assert ran.skipped == (
        "taking back address for ada-example: the entity's file holds a comment on line 1, which a rewrite would "
        "lose; take it out, or make the change by hand",
    )


def test_the_round_lists_what_tier1_took_back_a_never_store_value_hidden(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    v, _ = taken_back_vault(tmp_path)
    with signed_in(v) as client:
        assert client.post("/curate/round", headers=SAME).status_code == 303
        assert f"taken back: now shared, id:ada-example:email={SHARED}" in client.get("/curate").text
        monkeypatch.setenv("DOCKET_VAULT", str(v.root))
        assert main(["curate", "show"]) == 0
        shown = rf"taken back by itself: \S+  ada-example  now shared, id:ada-example:email={re.escape(SHARED)}\n"
        assert re.search(shown, capsys.readouterr().out)
        # Once a never-store list holds the value, neither shows it.
        config(v, never_store={"keywords": ["notifications"]})
        page = client.get("/curate").text
        assert "taken back: now shared, id:ada-example:email=(on a never-store list)" in page
        assert main(["curate", "show"]) == 0
        out = capsys.readouterr().out
        assert "now shared, id:ada-example:email=(on a never-store list)\n" in out
        assert f"email={SHARED}" not in out
        assert main(["curate", "show", "--json"]) == 0
        taken = json.loads(capsys.readouterr().out)["taken_back"]
        assert len(taken) == 1 and taken[0].endswith("email=(on a never-store list)")


def test_show_hides_a_value_tier1_applied_once_a_list_holds_it(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKET_VAULT", str(vault.root))
    with signed_in(vault) as client:
        assert client.post("/curate/round", headers=SAME).status_code == 303
        assert main(["curate", "show"]) == 0
        out = capsys.readouterr().out
        assert re.search(r"applied  by itself: \S+  add northwind  alias Nortwind \(asr\)\n", out)
        assert re.search(r"applied  by itself: \S+  add ada-example  id chat U0ADA\n", out)
        # Listed after the round: tier 1 applied both while no list held them.
        config(vault, never_store={"keywords": ["Nortwind", "U0ADA"]})
        page = client.get("/curate").text
        assert "alias (on a never-store list) (asr)" in page and "id chat (on a never-store list)" in page
        assert main(["curate", "show"]) == 0
        out = capsys.readouterr().out
        assert re.search(r"applied  by itself: \S+  add northwind  alias \(on a never-store list\) \(asr\)\n", out)
        assert re.search(r"applied  by itself: \S+  add ada-example  id chat \(on a never-store list\)\n", out)
        assert main(["curate", "show", "--json"]) == 0
        shown = json.loads(capsys.readouterr().out)
    for text in (out, json.dumps(shown)):
        assert "nortwind" not in text.casefold() and "u0ada" not in text.casefold()
    applied = {ln["entity"] + " " + ln["key"]: ln["change"] for ln in shown["applied"]}
    assert applied["northwind alias:northwind:(on a never-store list)"]["alias"]["text"] == batch.HIDDEN
    assert applied["ada-example id:ada-example:chat=(on a never-store list)"]["id"] == {
        "key": "chat", "value": batch.HIDDEN,
    }  # fmt: skip
    # The rest shows as logged, in the shape it always had, and the log keeps what it wrote.
    assert applied["ada-example id:ada-example:email=ada@example.com"]["id"]["value"] == "ada@example.com"
    assert set(shown["applied"][0]) == set(Line.model_fields)
    assert "alias:northwind:nortwind" in {ln.key for ln in read_all(vault.root).lines}


def show(capsys: pytest.CaptureFixture[str]) -> tuple[str, dict[str, Any]]:
    assert main(["curate", "show"]) == 0
    out = capsys.readouterr().out
    assert main(["curate", "show", "--json"]) == 0
    return out, json.loads(capsys.readouterr().out)


def test_show_hides_what_a_list_gains_on_a_card_after_the_round(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKET_VAULT", str(vault.root))
    with signed_in(vault) as client:
        assert client.post("/curate/round", headers=SAME).status_code == 303
        rita = next(c for c in batch.load(vault).cards if c.entity == "rita-sample")  # type: ignore[union-attr]
        out, _ = show(capsys)
        assert "id for Rita Sample (rita-sample): email rita@example.com" in out and "Here is the invoice." in out
        # The address and a word of the snippet listed after the round: the page and show hide both.
        config(vault, never_store={"keywords": ["rita@example.com", "invoice"]})
        assert "rita@example.com" not in client.get(f"/curate?open={rita.id}").text
        out, shown = show(capsys)
        assert "id for Rita Sample (rita-sample): email (on a never-store list)" in out
        for text in (out, json.dumps(shown)):
            assert "rita@example.com" not in text and "invoice" not in text
        card = next(c for c in shown["cards"] if c["card"]["id"] == rita.id)
        assert card["card"]["fact"] == ["email", batch.HIDDEN] and card["snippets"] == []
        assert card["card"]["key"] == "id:rita-sample:email=(on a never-store list)"
        assert set(card) == {"card", "state", "snippets"} and set(card["card"]) == set(Card.model_fields)
        # Her name too: each card's name, and its entity, whose slug spells the name: rita-sample-2 as well, the
        # second Rita Sample's.
        config(vault, never_store={"keywords": ["rita@example.com"], "people": ["Rita Sample"]})
        second = next(c for c in batch.load(vault).cards if c.entity == "rita-sample-2")  # type: ignore[union-attr]
        panels = [client.get(f"/curate?open={c.id}").text for c in (rita, second)]
        out, shown = show(capsys)
    hidden = re.escape(batch.HIDDEN)
    for card in (rita, second):
        assert re.search(rf"\n {card.n}  {card.id}  id for {hidden} \({hidden}\): email {hidden}  ", out)
    cards = {c["card"]["id"]: c["card"] for c in shown["cards"]}
    for card in (rita, second):
        assert (cards[card.id]["name"], cards[card.id]["entity"], cards[card.id]["key"]) == (
            batch.HIDDEN, batch.HIDDEN, "id:(on a never-store list):email=(on a never-store list)"
        )  # fmt: skip
    for text in (*panels, out, json.dumps(shown)):
        assert "rita" not in text.casefold()
    # Only shown: the round keeps its cards as they were mined.
    assert batch.load(vault).cards[0] == rita  # type: ignore[union-attr]


def test_two_people_whose_names_only_start_alike_share_an_address(tmp_path: Path) -> None:
    anna = {"entities/person/anna-example.yaml": "id: anna-example\ntype: person\nname: {en: Anna Example}\n"}
    v = small_vault(tmp_path, anna, mail=[
        _mail(1, "Ann Example <family@example.com>", "Dinner on Friday."),
        _mail(2, "Anna Example <family@example.com>", "The tickets are booked."),
    ])  # fmt: skip
    ran = tier1.run(v)
    assert ran.written is None and ran.proposals == ()


def test_a_shown_name_longer_than_the_holder_s_is_no_alias(tmp_path: Path) -> None:
    gabor = {"entities/person/kovacs-gabor.yaml": (
        "id: kovacs-gabor\ntype: person\nname: {hu: Kovács Gábor}\nids: {email: info@kovacs.example}\n"
    )}  # fmt: skip
    v = small_vault(tmp_path, gabor, mail=[_mail(1, "Kovács Gáborné <info@kovacs.example>", "Köszönöm a csomagot.")])
    assert tier1.run(v).written is None


def test_one_person_written_three_ways_still_gets_the_address_and_the_id(tmp_path: Path) -> None:
    ada = {ADA: ADA_BEN[ADA]}
    v = small_vault(tmp_path, ada, mail=[
        _mail(1, "Ada Example <ada@example.com>", "One."),
        _mail(2, "Example, Ada <ada@example.com>", "Two."),
        _mail(3, "A. Example <ada@example.com>", "Three."),
    ], chat=[_chat(1, "Ada Example <U0ADA>", "Hi."), _chat(2, "ada.e <U0ADA>", "Hi again.")])  # fmt: skip
    assert sorted(facts(tier1.run(v))) == [
        ("address", "ada-example", "id:ada-example:email=ada@example.com"),
        ("chat id", "ada-example", "id:ada-example:chat=u0ada"),
    ]


@pytest.mark.parametrize(
    ("a", "b", "one"),
    [
        ("Ada Example", "Example, Ada", True),
        ("A. Example", "Ada Example", True),
        ("Ada E.", "Ada Example", True),
        ("Ada", "Ada Example", True),
        ("Ann Example", "Anna Example", False),
        ("Gab Sample", "Gabriella Sample", False),
        ("Dan Kovács", "Dániel Kovács", False),
        ("Kovács Gáborné", "Kovács Gábor", False),
        ("Ada Example", "Adam Example", False),
        ("Bob Sample", "Robert Sample", False),
        ("Ada Example", "Ada Sample", False),
    ],
)
def test_two_names_are_one_person_s_word_for_word_or_by_an_initial(a: str, b: str, one: bool) -> None:
    assert tier1._one_person(folded(a).split(), folded(b).split()) is one  # pyright: ignore[reportPrivateUsage]
    assert tier1._shared([a, b]) is not one  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("shown", "own", "shortens"),
    [
        ("Cleo T.", "Cleo Test", True),
        ("Kat Nagy", "Katalin Nagy", True),
        ("Example, Ada", "Ada Example", True),
        ("Ka Nagy", "Katalin Nagy", False),
        ("Kovács Gáborné", "Kovács Gábor", False),
        ("Adam Example", "Ada Example", False),
        ("Ada B. Example", "Ada Example", False),
    ],
)
def test_a_shown_name_shortens_the_holder_s_never_lengthens_it(shown: str, own: str, shortens: bool) -> None:
    assert tier1._shortens(folded(shown).split(), folded(own).split()) is shortens  # pyright: ignore[reportPrivateUsage]


def chase_vault(tmp_path: Path, said: list[tuple[int, str]], entities: dict[str, str], **runs: list[Any]) -> Vault:
    """One recording two engines transcribed alike, a second later each."""
    later = [(at + 1, text) for at, text in said]
    rec = "plaud:rec-p"
    plaud = [_transcript_of(rec, "plaud", said), _transcript_of(rec, "whisper", later)]
    return small_vault(tmp_path, entities, plaud=plaud, **runs)


def _transcript_of(rec: str, engine: str, said: list[tuple[int, str]]) -> Any:
    segments = [
        {"start_ms": at * 1000, "end_ms": at * 1000 + 4000, "speaker": "Speaker 1", "text": text} for at, text in said
    ]
    under = {"source": "plaud", "container": rec, "thread": rec, "author": None, "ts": _when(20)}
    text = "\n".join(f"Speaker 1: {t}" for _, t in said)
    return make_record(rid=f"{rec}/{engine}", kind="transcript", engine=engine, segments=segments, text=text, **under)


CHASE = {
    "entities/person/chase-example.yaml": (
        "id: chase-example\ntype: person\nname: {en: Chase Example}\naliases: [{text: Chase}]\n"
    )
}


def engine_cards(ran: Tier1) -> list[tuple[str, str]]:
    return [(p.entity, p.text) for p in ran.proposals if p.alias is not None and p.alias.kind == "asr"]


def test_a_common_word_two_engines_capitalise_is_a_card_never_a_fact(tmp_path: Path) -> None:
    said = [(0, "Chase will send the plan."), (10, "Phase two starts on Monday.")]
    v = chase_vault(tmp_path, said, CHASE, mail=[_mail(1, "Zed Nobody <zed@example.com>", "The phase plan is here.")])
    ran = tier1.run(v)
    assert ran.written is None and engine_cards(ran) == [("chase-example", "Phase")]


def test_a_case_form_two_engines_agree_on_is_a_card_never_a_fact(tmp_path: Path) -> None:
    gabor = {"entities/person/gabor-example.yaml": (
        "id: gabor-example\ntype: person\nname: {en: Gábor Example}\naliases: [{text: Gábor}]\n"
    )}  # fmt: skip
    v = chase_vault(tmp_path, [(0, "Gábor küldi a tervet."), (10, "Kérdezd meg Gábort holnap.")], gabor)
    ran = tier1.run(v)
    assert ran.written is None and engine_cards(ran) == [("gabor-example", "Gábort")]


def test_a_case_form_matching_judges_common_is_a_card_never_a_fact(tmp_path: Path) -> None:
    # Marinát, Marina's accusative, is everywhere: matching judges it common for her, and tier 1 makes it a card.
    marina = {"entities/person/marina-sample.yaml": (
        "id: marina-sample\ntype: person\nname: {en: Marina Sample}\naliases: [{text: Marina}]\n"
    )}  # fmt: skip
    mail = [_mail(n, "Zed Nobody <zed@example.com>", "Igen, Marinát kérdeztem meg.") for n in range(60)]
    v = chase_vault(tmp_path, [(0, "Marina will send the plan."), (10, "Then ask Marinát.")], marina, mail=mail)
    ran = tier1.run(v)
    assert ran.written is None and engine_cards(ran) == [("marina-sample", "Marinát")]


def test_another_entity_s_name_two_engines_agree_on_is_a_card_never_a_fact(tmp_path: Path) -> None:
    people = {
        "entities/person/laura-example.yaml": (
            "id: laura-example\ntype: person\nname: {en: Laura Example}\naliases: [{text: Laura}]\n"
        ),
        "entities/person/lara-sample.yaml": "id: lara-sample\ntype: person\nname: {en: Lara Sample}\n",
    }
    v = chase_vault(tmp_path, [(0, "Laura sent the plan."), (10, "Then Lara will check it.")], people)
    ran = tier1.run(v)
    assert ran.written is None and engine_cards(ran) == [("laura-example", "Lara")]


def test_a_one_word_display_name_is_a_card_never_a_fact(tmp_path: Path) -> None:
    gabors = {
        "entities/person/gabor-kovacs.yaml": (
            "id: gabor-kovacs\ntype: person\nname: {hu: Kovács Gábor}\naliases: [{text: Gábor}]\n"
        ),
        "entities/person/gabor-szabo.yaml": "id: gabor-szabo\ntype: person\nname: {hu: Szabó Gábor}\n",
    }
    v = small_vault(tmp_path, gabors, chat=[make_record(1, author="Gábor <U0GSZ>", text="Szabó here.")])
    ran = tier1.run(v)
    assert ran.written is None
    assert [(p.entity, p.id) for p in ran.proposals] == [("gabor-kovacs", IdFields(key="chat", value="U0GSZ"))]


RITAS = {
    "entities/person/rita-sample.yaml": "id: rita-sample\ntype: person\nname: {en: Rita Sample}\n",
    "entities/person/rita-sample-2.yaml": "id: rita-sample-2\ntype: person\nname: {en: Rita Sample}\n",
}


def test_a_never_store_value_on_a_tier1_card_never_shows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    v = small_vault(tmp_path, RITAS, mail=[_mail(1, "Rita Sample <rita@phoenix-clinic.example>", "Your appointment.")])
    config(v, never_store={"keywords": ["phoenix clinic"]})
    round_ = batch.start(v)
    assert [c for c in round_.cards if c.kind == "id"] == []
    assert round_.left_out["never-store"] == 2
    monkeypatch.setenv("DOCKET_VAULT", str(v.root))
    for args in (["curate", "show"], ["curate", "show", "--json"]):
        assert main(args) == 0
        assert "phoenix" not in capsys.readouterr().out
    assert "phoenix" not in batch.batch_path(v).read_text(encoding="utf-8")


def test_the_plan_writes_nothing_and_its_apply_skips_what_the_log_took_since(vault: Vault) -> None:
    found = tier1.plan(vault)
    assert read_all(vault.root).lines == () and len(found.planned) == len(APPLIED)
    EntityStore(vault).reject([Reject("alias:northwind:nortwind", "Nortwind", "northwind", None, None)])
    ran = tier1.apply(vault, found)
    assert sorted(facts(ran)) == [f for f in APPLIED if f[0] != "engines"]
