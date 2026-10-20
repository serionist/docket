"""The Queue's JSON routes, signed in (DR-0666, DR-0709 to DR-0711): its groups in their order, the untriaged first with
a snooze that ended leading them, then each call by due; the filters it shares with the Ledger and the Ledger's own it
leaves out; its panel; the writes it sends through the shared routes as page queue; and the names the who fields
suggest, from the ledger and the vault's people. Invented people only."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from day_kit import SAME, ZONE, day, day_vault, signed_in, today
from docket_testkit import empty_vault, make_config
from ledger_kit import ADD_FIELDS, op, write_ops
from starlette.testclient import TestClient

from docket.index import build as index_build
from docket.ledger import ops as ledger_ops
from docket.ledger import store, views
from docket.vault.vault import Vault
from docket.web import day as day_routes

API = "/api/web/queue"
WRITE = "/api/web/ledger/write"
SAVE = "/api/web/ledger/save"
WHO = "/api/web/ledger/who"
UNTRIAGED = ["A-0002", "A-0003", "A-0004", "A-0005"]


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return day_vault(tmp_path)


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


def read(client: TestClient, path: str, status: int = 200) -> Any:
    answer = client.get(path)
    assert answer.status_code == status, answer.text
    assert answer.headers["cache-control"] == "no-store"
    return answer.json()


def queued(client: TestClient, query: str = "", status: int = 200) -> Any:
    return read(client, f"{API}{'?' if query else ''}{query}", status)


def rows(found: Any) -> list[str]:
    return [r["ref"] for g in found["list"]["groups"] for r in g["rows"]]


def groups(found: Any) -> list[str]:
    return [g["label"] for g in found["list"]["groups"]]


def post(client: TestClient, body: dict[str, Any], path: str = WRITE) -> Any:
    answer = client.post(path, json={"page": "queue", **body}, headers=SAME)
    assert answer.status_code == 200, answer.text
    return answer.json()


def ops(vault: Vault) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in sorted((vault.root / ledger_ops.OPS_DIR).glob("*.jsonl")):
        out += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


def write(vault: Vault, *changes: dict[str, Any]) -> store.Written:
    return store.write(vault, list(changes), by="user", via="cli")


# -- the list --------------------------------------------------------------------------------------------------------


def test_the_queue_lists_the_untriaged_first_by_due_then_each_call(client: TestClient, vault: Vault) -> None:
    found = queued(client)
    assert found["canonical"] == "/queue"
    assert found["version"] == day_routes.version(vault.root)
    assert found["today"] == today().isoformat()
    assert groups(found) == ["Untriaged", "Chase"]
    assert rows(found) == [*UNTRIAGED, "A-0001"]
    assert [(g["key"], g["verdict"], g["total"]) for g in found["list"]["groups"]] == [
        ("untriaged", None, 4),
        ("verdict-chase", "chase", 1),
    ]
    assert found["list"]["chips"] == []
    assert found["list"]["counts"] == "5 open: 4 untriaged, 1 chase; 1 overdue"
    assert (found["list"]["shown"], found["list"]["total"], found["list"]["more"]) == (5, 5, None)
    # Its rows are the Ledger's: the call they hold and the quick buttons that make the next one.
    late = found["list"]["groups"][1]["rows"][0]
    assert (late["triage"], late["due"]["tone"]) == ("chase", "late")
    assert [b["writes"] for b in late["quick"]] == ["discuss", "claude", "me", None]
    assert found["verdicts"][0]["key"] == "1"
    assert found["notice"] is None


def test_the_calls_follow_the_views_order_each_by_due_with_none_last(client: TestClient, vault: Vault) -> None:
    write(
        vault,
        {"op": "triage", "item": "A-0005", "verdict": "me"},
        {"op": "triage", "item": "A-0004", "verdict": "me"},
        {"op": "triage", "item": "A-0003", "verdict": "discuss"},
        {"op": "triage", "item": "A-0002", "verdict": "claude"},
    )
    found = queued(client)
    assert groups(found) == ["Discuss", "Claude", "Me", "Chase"]
    assert [g["verdict"] for g in found["list"]["groups"]] == ["discuss", "claude", "me", "chase"]
    # Me: the one due in a month before the one with no due.
    assert rows(found) == ["A-0003", "A-0002", "A-0004", "A-0005", "A-0001"]
    assert found["list"]["counts"] == "5 open: 0 untriaged, 1 discuss, 1 claude, 2 me, 1 chase; 1 overdue"


def test_a_snooze_whose_day_has_come_leads_the_untriaged(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    later = datetime.combine(today() + timedelta(days=6), datetime.min.time(), ZONE).replace(hour=12)
    monkeypatch.setattr(views, "now", lambda: later.astimezone(UTC))
    found = queued(client)
    assert groups(found) == ["Snooze ended", "Untriaged", "Chase"]
    assert found["list"]["groups"][0]["tone"] == "today"
    assert rows(found)[:2] == ["A-0007", "A-0002"]
    woke = found["list"]["groups"][0]["rows"][0]
    assert woke["due"] == {"text": "snooze ended", "title": f"back since {day(5)}", "tone": "today"}
    assert woke["triage"] is None and woke["callable"]
    assert found["list"]["counts"] == "6 open: 5 untriaged (1 with their snooze ended), 1 chase; 3 overdue"


@pytest.mark.parametrize(
    ("query", "shown"),
    [
        ("q=venue", ["A-0003"]),
        ("stream=pilot", ["A-0002", "A-0001"]),
        ("kind=task", ["A-0002", "A-0004"]),
        ("who=ADA", ["A-0003", "A-0001"]),
        ("q=zeppelin", []),
    ],
)
def test_q_and_the_filters_narrow_the_queue(client: TestClient, query: str, shown: list[str]) -> None:
    found = queued(client, query)
    assert rows(found) == shown
    assert found["canonical"] == f"/queue?{query}"
    if not shown:
        assert found["list"]["empty"] == "Nothing here matches."
        assert found["list"]["counts"] == "0 open: 0 untriaged; 0 overdue"


def test_an_empty_queue_says_so(tmp_path: Path) -> None:
    with signed_in(empty_vault(tmp_path, make_config())) as client:
        found = queued(client)
    assert found["list"]["groups"] == []
    assert found["list"]["empty"] == "Nothing is open: the queue is empty."


def test_the_ledger_s_own_parameters_are_left_out_and_never_refused(client: TestClient) -> None:
    found = queued(client, "state=bogus&group=nope&archived=yes&open=A-0003&k=2")
    assert found["canonical"] == "/queue?open=A-0003&k=2"
    filters = found["filters"]
    assert (filters["state"], filters["group"], filters["archived"]) == ("open", "due", False)
    # k cuts across the groups, and Show more asks for the next hundred.
    assert rows(found) == ["A-0002", "A-0003"]
    assert (found["list"]["shown"], found["list"]["total"], found["list"]["more"]) == (2, 5, 102)
    assert queued(client, "k=0", status=400)["error"].startswith("k is a whole number")


def test_the_queue_s_panel_answers_its_own_canonical_url(client: TestClient) -> None:
    found = read(client, f"{API}/panel?open=A-0002&state=closed&drop=1")
    assert found["canonical"] == "/queue?open=A-0002&drop=1"
    assert found["panel"]["ref"] == "A-0002"
    assert found["panel"]["meeting"]["ref"] == "M-0001"
    assert read(client, f"{API}/panel?open=M-0001")["missing"].startswith("M-0001 is a meeting")


# -- the writes, as page queue ---------------------------------------------------------------------------------------


def test_a_call_from_the_queue_is_one_batch_by_user_via_page(client: TestClient, vault: Vault) -> None:
    answer = post(client, {"op": "triage", "verdict": "claude", "items": ["A-0002"], "at": "A-0003"})
    assert answer["at"] == "A-0003"
    assert answer["notice"]["lines"] == ["A-0002: triage: claude"]
    written = [o for o in ops(vault) if o.get("batch") == answer["done"]]
    assert [(o["op"], o["verdict"], o["by"], o["via"]) for o in written] == [("triage", "claude", "user", "page")]
    # The row leaves the untriaged for its call's group, and the version moved past the page's.
    found = queued(client)
    assert groups(found) == ["Untriaged", "Claude", "Chase"]
    assert found["list"]["groups"][1]["rows"][0]["ref"] == "A-0002"
    assert found["version"] == answer["version"]
    # The notice's Undo puts it back.
    undone = post(client, {"op": "undo", "batch": answer["done"], "said": "undo"})
    assert undone["notice"]["text"] == "Undone."
    assert rows(queued(client))[0] == "A-0002"


def test_a_bulk_from_the_queue_skips_and_names_the_rows_it_doesn_t_take(client: TestClient, vault: Vault) -> None:
    write(vault, {"op": "done", "item": "A-0004"})
    answer = post(client, {"op": "triage", "verdict": "me", "items": ["A-0003", "A-0004"], "bulk": True})
    assert answer["skipped"] == ["A-0004"]
    assert answer["notice"]["text"] == "Saved. Skipped A-0004: the change doesn't take them as they stand."
    assert [g["label"] for g in queued(client)["list"]["groups"]] == ["Untriaged", "Me", "Chase"]


def test_a_save_from_the_queue_answers_the_row_and_the_panel(client: TestClient) -> None:
    saved = post(client, {"op": "set", "items": ["A-0003"], "fields": {"who": "Cleo Example"}}, SAVE)
    assert (saved["row"]["who"], saved["panel"]["who"]) == ("Cleo Example", "Cleo Example")


def test_a_write_naming_no_day_page_is_refused(client: TestClient, vault: Vault) -> None:
    before = len(ops(vault))
    answer = client.post(WRITE, json={"page": "today", "op": "triage", "items": ["A-0002"]}, headers=SAME)
    assert answer.status_code == 400
    assert answer.json()["error"] == "page is one of ledger, queue, not 'today'"
    assert len(ops(vault)) == before


# -- the names the who fields suggest (DR-0714) ----------------------------------------------------------------------


def people(client: TestClient) -> list[tuple[str, int, list[str]]]:
    return [(p["name"], p["uses"], p["also"]) for p in read(client, WHO)["people"]]


def test_the_who_fields_suggest_the_ledger_s_names_the_most_used_first(client: TestClient, vault: Vault) -> None:
    # Ada names four items, Ben three; with the index not built the ledger is folded in memory and has no entities.
    assert people(client) == [("Ada Example", 4, []), ("Ben Sample", 3, [])]
    write(vault, *[{"op": "set", "item": f"A-000{n}", "fields": {"who": "Ben Sample"}} for n in (1, 3)])
    assert people(client) == [("Ben Sample", 5, []), ("Ada Example", 2, [])]


def test_two_names_as_often_used_go_by_the_newest_item(client: TestClient, vault: Vault) -> None:
    # Cleo's one item was opened on 18 Oct 2026, Dora's today: Dora's comes first, though Cleo's name sorts first.
    write_ops(vault.root, [op("add", 50, fields={**ADD_FIELDS, "who": "Cleo Example"})])
    added = {"kind": "task", "title": "Book the train", "who": "Dora Example", "stream": "ops", "source": "page:x"}
    write(vault, {"op": "add", "fields": added})
    assert [p[:2] for p in people(client)][2:] == [("Dora Example", 1), ("Cleo Example", 1)]


def test_the_vault_s_people_join_with_their_other_names_when_the_index_has_them(
    client: TestClient, vault: Vault
) -> None:
    files = {
        "entities/person/ada-example.yaml": "id: ada-example\ntype: person\nname: {en: Ada Example}\n"
        "aliases:\n  - {text: Ada E.}\n  - {text: Adda, kind: asr}\n",
        "entities/person/cleo-example.yaml": "id: cleo-example\ntype: person\n"
        "name: {en: Cleo Example, hu: Example Cleo}\n",
        "entities/person/old-example.yaml": "id: old-example\ntype: person\nname: {en: Old Example}\n"
        "status: archived\n",
        "entities/customer/northwind.yaml": "id: northwind\ntype: customer\nname: {en: Northwind}\n",
    }
    for rel, text in files.items():
        (vault.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault.root / rel).write_text(text, encoding="utf-8", newline="\n")
    vault.commit(list(files), "Four entities")
    index_build.rebuild(vault)
    assert people(client) == [
        ("Ada Example", 4, ["Ada E."]),
        ("Ben Sample", 3, []),
        ("Cleo Example", 0, ["Example Cleo"]),
    ]
