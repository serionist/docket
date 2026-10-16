"""Tier 1 (DR-0469 to DR-0471) on an invented vault of mail, chat and one recording two engines transcribed: each fact
applied to its one entity in one commit, by rule, with its evidence; two matches a card each; a refused fact skipped
and listed while the rest commit; only on the primary machine; an undone or hand-removed fact never back; and the
round, the page and `show` listing what it did."""

from __future__ import annotations

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
from docket.curate.tier1 import Tier1
from docket.errors import DocketError
from docket.index import build as index_build
from docket.vault import curatelog
from docket.vault.curatelog import Change, IdFields, Line, read_all
from docket.vault.entitystore import EntityStore, Planned
from docket.vault.vault import Vault

SAME = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}
RECORDING = "plaud:rec-two"
ENTITIES = {
    "entities/person/ada-example.yaml": "id: ada-example\ntype: person\nname: {en: Ada Example}\n",
    "entities/person/ben-sample.yaml": "id: ben-sample\ntype: person\nname: {en: Ben Sample}\nids: {chat: U0BEN}\n",
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
    path.write_text("id: ben-sample\ntype: person\nname: {en: Ben Sample}\nids: {chat: U0BEN}\n", encoding="utf-8")
    vault.commit(["entities/person/ben-sample.yaml"], "Benny S. out by hand")
    index.update(vault)
    again = tier1.run(vault)
    assert again.written is None


def test_two_engines_must_agree_where_the_recording_names_the_entity(vault: Vault) -> None:
    conn = index_build.open_index(vault)
    try:
        found = [f for f in tier1.find(conn) if f.rule == "engines"]
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
        assert [f for f in tier1.find(conn) if f.rule == "engines"] == []
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
