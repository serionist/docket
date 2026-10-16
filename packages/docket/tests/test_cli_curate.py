from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from curate_kit import curate_vault
from docket_testkit import NORTHWIND_FILE, entity_vault, sh

from docket import render, userconfig
from docket.cli import main
from docket.curate import batch as curate
from docket.curate.propose import Card, CardSpelling
from docket.vault import curatelog
from docket.vault.config import NeverStore
from docket.vault.curatelog import LOG_DIR, AliasFields, Change, EntityFields, IdFields, Line, NameFields, RelatedFields
from docket.vault.entitystore import EntityStore, Written, load_changes
from docket.vault.vault import Vault

ADD = """\
changes:
  - create: {id: cleo-test, type: person, name: {en: Cleo Test}}
  - entity: northwind
    add: {aliases: [{text: Nortwin, kind: asr}]}
"""


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    v = entity_vault(tmp_path)
    monkeypatch.setenv("DOCKET_VAULT", str(v.root))
    return v


def changes_file(tmp_path: Path, text: str) -> str:
    path = tmp_path / "changes.yaml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return str(path)


def add(tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str]]:
    code = main(["curate", "add", changes_file(tmp_path, text)])
    return code, capsys.readouterr().out.splitlines()


def test_add_prints_each_change_and_the_commit(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = add(tmp_path, ADD, capsys)
    assert code == 0
    head = sh(vault.root, "git", "rev-parse", "HEAD").strip()
    assert out[0].endswith("  create cleo-test  person, Cleo Test")
    assert out[1].endswith("  add    northwind  alias Nortwin (asr)")
    assert out[2] == f"committed {head[:12]}"
    code, out = add(tmp_path, ADD.replace("create: {id: cleo-test", "create: {id: rita-sample"), capsys)
    assert out[-2:] == ["already there: 1", f"committed {sh(vault.root, 'git', 'rev-parse', 'HEAD').strip()[:12]}"]
    code, out = add(tmp_path, "changes: [{entity: northwind, add: {aliases: [{text: nortwin}]}}]", capsys)
    assert out == ["already there: 1", "nothing to write"]


def test_a_refused_add_prints_every_problem_and_writes_nothing(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = add(tmp_path, "changes: [{entity: rita-sample, add: {aliases: [{text: Rita}]}}]", capsys)
    assert code == 1
    assert out == ["refused; nothing was written", "  change 1: no entity has that id"]
    assert not (vault.root / LOG_DIR).exists()


def test_add_of_a_file_that_isn_t_there_says_so(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["curate", "add", str(tmp_path / "nope.yaml")]) == 2
    assert "no file" in capsys.readouterr().err


def test_undo_prints_what_it_undid_and_refuses_what_it_can_t(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add(tmp_path, ADD, capsys)
    assert main(["curate", "log", "--json"]) == 0
    ops = [line["id"] for line in json.loads(capsys.readouterr().out)]
    assert main(["curate", "undo", *ops]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split()[1] for line in out[:2]] == ["undo", "undo"]
    assert out[0].endswith(f"undoes {ops[0]}")
    assert not (vault.root / "entities/person/cleo-test.yaml").exists()
    assert main(["curate", "undo", ops[0]]) == 1
    assert capsys.readouterr().out.splitlines()[1].startswith(f"  {ops[0]}: already undone by ")


def test_log_lists_the_newest_first_with_what_undid_each(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["curate", "log"]) == 0
    assert capsys.readouterr().out == "no changes logged yet\n"
    add(tmp_path, ADD, capsys)
    main(["curate", "log", "--json"])
    first = json.loads(capsys.readouterr().out)[-1]["id"]
    main(["curate", "undo", first])
    capsys.readouterr()
    (vault.root / LOG_DIR / "notes.txt").write_text("x", encoding="utf-8")
    assert main(["curate", "log"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split()[3] for line in out[:3]] == ["undo", "add", "create"]
    assert out[2].endswith(f"(user, cli)  undone by {out[0].split()[2]}")
    assert out[3] == "! entities/_log/notes.txt: isn't a log file (<yyyy-mm>-<machine>.jsonl); skipped"
    main(["curate", "log", "-n", "1", "--entity", "northwind"])
    assert len(capsys.readouterr().out.splitlines()) == 2
    main(["curate", "log", "--json", "--entity", "cleo-test"])
    assert [line["op"] for line in json.loads(capsys.readouterr().out)] == ["undo", "create"]


def test_log_marks_a_line_a_stopped_write_left(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A kill between the file's rename and the commit: the line and the file are written, nothing is committed.
    with monkeypatch.context() as m:
        m.setattr(vault, "commit", lambda paths, message: None)
        EntityStore(vault).apply(load_changes(Path(changes_file(tmp_path, ADD))))
    assert main(["curate", "log"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert all(x.endswith("(user, cli)  not finished: the next write commits it") for x in out)
    assert main(["curate", "log", "--json"]) == 0
    assert [x.get("finished") for x in json.loads(capsys.readouterr().out)] == [False, False]
    add(tmp_path, "changes: [{entity: ben-sample, add: {aliases: [{text: Ben}]}}]", capsys)
    assert main(["curate", "log"]) == 0
    assert "not finished" not in capsys.readouterr().out


SECOND_BEN = """\
changes:
  - create: {id: ben-sample-2, type: person, name: {en: Ben Sample}}
  - entity: northwind
    add: {aliases: [{text: Nortwin, kind: asr}]}
"""
BENNIE = "changes: [{entity: ben-sample-2, add: {aliases: [{text: Bennie}]}}]"
# What a never-store list gained after the lines were logged, an id whose slug spells a listed name among them.
GAINED = ("nortwin", "bennie", "ben sample", "ben-sample")


def test_log_and_a_write_hide_what_a_list_gained_after_the_line_was_logged(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert add(tmp_path, SECOND_BEN, capsys)[0] == 0
    # A write that stopped before its commit: its line is in the log, not in HEAD.
    with monkeypatch.context() as m:
        m.setattr(vault, "commit", lambda paths, message: None)
        EntityStore(vault).apply(load_changes(Path(changes_file(tmp_path, BENNIE))))
    logged = curatelog.read_all(vault.root).lines
    config = vault.root / "vault.yaml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["never_store"] = {"keywords": ["Nortwin", "Bennie"], "people": ["Ben Sample"]}
    config.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    assert main(["curate", "log"]) == 0
    out = capsys.readouterr().out
    created, nortwin, bennie = logged
    hidden = curate.HIDDEN
    assert out.splitlines() == [
        f"{bennie.at:%Y-%m-%d %H:%M}Z  {bennie.id}  add    {hidden}  alias {hidden}  (user, cli)  not finished: the "
        "next write commits it",
        f"{nortwin.at:%Y-%m-%d %H:%M}Z  {nortwin.id}  add    northwind  alias {hidden} (asr)  (user, cli)",
        f"{created.at:%Y-%m-%d %H:%M}Z  {created.id}  create {hidden}  person, {hidden}  (user, cli)",
    ]
    assert main(["curate", "log", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    # Every field as logged, the ids too, so undo still takes them; only the values a list holds are replaced.
    assert [line["id"] for line in shown] == [bennie.id, nortwin.id, created.id]
    assert [set(line) - {"finished"} for line in shown] == [
        set(line.model_dump(exclude_none=True)) for line in (bennie, nortwin, created)
    ]
    assert [(line["entity"], line["key"]) for line in shown] == [
        (hidden, f"alias:{hidden}:{hidden}"), ("northwind", f"alias:northwind:{hidden}"), (hidden, f"new:-:{hidden}")
    ]  # fmt: skip
    assert shown[2]["change"]["entity"]["id"] == hidden and shown[2]["change"]["entity"]["name"] == {"en": hidden}
    for text in (out, json.dumps(shown)):
        assert not [word for word in GAINED if word in text.casefold()]

    # The next write finishes the stopped one and names it by its id; its own line on ben-sample-2 shows hidden.
    code, wrote = add(tmp_path, "changes: [{entity: ben-sample-2, add: {aliases: [{text: Sammy}]}}]", capsys)
    assert code == 0 and wrote[0] == f"finished first, what a stopped write left: {bennie.id}"
    sammy = curatelog.read_all(vault.root).lines[-1]
    assert wrote[1] == f"{sammy.id}  add    {hidden}  alias Sammy"
    assert not [word for word in GAINED if word in "\n".join(wrote).casefold()]
    # Undo takes the id as logged, and its own line hides the entity too.
    assert main(["curate", "undo", sammy.id]) == 0
    undone = capsys.readouterr().out.splitlines()
    assert undone[0].endswith(f"  undo   {hidden}  undoes {sammy.id}") and "ben" not in undone[0].casefold()
    # Only shown: the log keeps what it wrote.
    assert {line.entity for line in curatelog.read_all(vault.root).lines} == {"ben-sample-2", "northwind"}


def test_a_log_file_that_isn_t_utf_8_is_skipped_by_log_and_refuses_a_write(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = vault.root / LOG_DIR / "2026-09-test-box.jsonl"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\xff\xfe junk\n")
    assert main(["curate", "log"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "no changes logged yet",
        "! entities/_log/2026-09-test-box.jsonl: isn't UTF-8; skipped",
    ]
    assert main(["curate", "add", changes_file(tmp_path, ADD)]) == 2
    err = capsys.readouterr().err
    assert err == "docket: entities/_log/2026-09-test-box.jsonl isn't UTF-8; fix or delete it, then write again\n"


def line(change: Change | None, op: str = "add", **fields: str) -> Line:
    return Line.model_validate(
        {
            "schema_version": 1,
            "id": "20261001T091402-desk-1",
            "op": op,
            "at": "2026-10-01T09:14:02Z",
            "machine": "desk",
            "by": "user",
            "via": "page",
            "key": "k",
            **({"entity": "northwind", "change": change} if change else {}),
            **fields,
        }
    )


def test_each_kind_of_op_says_what_it_changed() -> None:
    lines = [
        line(Change(alias=AliasFields(text="North Wind"))),
        line(Change(name=NameFields(lang="hu", text="Northwind Kft"))),
        line(Change(id=IdFields(key="email_domain", value="northwind.example"))),
        line(Change(related=RelatedFields(type="person", target="ben-sample"))),
        line(None, "reject", key="new:-:nortwin"),
    ]
    out = render.curate_log(lines, {}, ())
    wanted = [
        "add    northwind  alias North Wind  (user, page)",
        "add    northwind  name hu Northwind Kft  (user, page)",
        "add    northwind  id email_domain northwind.example  (user, page)",
        "add    northwind  related person ben-sample  (user, page)",
        "reject -  new:-:nortwin  (user, page)",
    ]
    assert [x.split("-desk-1  ", 1)[1] for x in out] == wanted


def test_show_hides_what_a_never_store_list_holds_in_each_kind_of_change() -> None:
    lists = NeverStore(people=["Dora Minta"], keywords=["Kft", "northwind.example", "Dory", "Buyer", "secret"])
    dora = EntityFields(
        id="dora-minta", type="person", name={"en": "Dora Minta", "hu": "Minta Dora"},
        aliases=(AliasFields(text="Dory"), AliasFields(text="Dee")), ids={"email": ("dee@example.org",)},
        role="Buyer", note="Runs the secret pilot", related={"person": ("ben-sample", "dora-minta-2")},
    )  # fmt: skip
    lines = [
        line(Change(alias=AliasFields(text="Northwind Kft", kind="asr")), key="alias:northwind:northwind kft"),
        line(Change(name=NameFields(lang="hu", text="Northwind Kft")), key="name:northwind:hu=northwind kft"),
        line(Change(id=IdFields(key="domain", value="northwind.example")), key="id:northwind:domain=northwind.example"),
        line(
            Change(related=RelatedFields(type="person", target="ben-sample")), key="related:northwind:person=ben-sample"
        ),
        line(Change(entity=dora), "create", key="new:-:dora minta", entity="dora-minta"),
        line(None, "reject", key="new:-:dora minta"),
        line(Change(alias=AliasFields(text="North Wind")), key="alias:northwind:north wind"),
        # An id whose slug spells a listed name, as the line's entity and as a related link's other end.
        line(Change(alias=AliasFields(text="Dee")), key="alias:dora-minta-2:dee", entity="dora-minta-2"),
        line(
            Change(related=RelatedFields(type="person", target="dora-minta-2")),
            key="related:northwind:person=dora-minta-2",
        ),
    ]
    hidden = [curate.hidden_line(ln, lists) for ln in lines]
    view = curate.BatchView(round="r", started=datetime(2026, 10, 1, tzinfo=UTC), cards=[], left_out={}, applied=hidden)
    shown = [ln.split("-desk-1  ", 1)[1] for ln in render.curate_round(view) if ln.startswith("applied")]
    assert shown == [
        "add northwind  alias (on a never-store list) (asr)",
        "add northwind  name hu (on a never-store list)",
        "add northwind  id domain (on a never-store list)",
        "add northwind  related person ben-sample",
        "create (on a never-store list)  person, (on a never-store list); (on a never-store list)",
        "reject -  new:-:(on a never-store list)",
        "add northwind  alias North Wind",
        "add (on a never-store list)  alias Dee",
        "add northwind  related person (on a never-store list)",
    ]
    assert [ln.key for ln in hidden] == [
        "alias:northwind:(on a never-store list)", "name:northwind:hu=(on a never-store list)",
        "id:northwind:domain=(on a never-store list)", "related:northwind:person=ben-sample",
        "new:-:(on a never-store list)", "new:-:(on a never-store list)", "alias:northwind:north wind",
        "alias:(on a never-store list):dee", "related:northwind:person=(on a never-store list)",
    ]  # fmt: skip
    created = hidden[4].change.entity  # type: ignore[union-attr]
    assert created is not None and created.model_dump(mode="json") == {
        **dora.model_dump(mode="json"),
        "id": curate.HIDDEN,
        "related": {"person": ["ben-sample", curate.HIDDEN]},
        "name": {"en": curate.HIDDEN, "hu": curate.HIDDEN},
        "aliases": [
            {"text": curate.HIDDEN, "kind": "plain", "needs": []},
            {"text": "Dee", "kind": "plain", "needs": []},
        ],
        "role": curate.HIDDEN,
        "note": curate.HIDDEN,
    }
    # Only shown: the lines the log holds keep their values.
    assert lines[0].change.alias.text == "Northwind Kft"  # type: ignore[union-attr]


def test_show_hides_a_new_card_s_listed_name_with_the_id_made_from_it() -> None:
    spelling = CardSpelling(text="Dora Minta", trust="label", add="name", alone=True, chunks=3)
    heard = CardSpelling(text="Dora Mint", trust="asr", add="asr", alone=True, chunks=1)
    evidence = [("r:calls:rec-1@00:10-00:40", "Dora Minta"), ("r:calls:rec-2", "Dora Mint")]
    card = Card(
        id="0a1b2c", n=1, kind="new", key="new:-:dora minta", entity="dora-minta", type="person", name="Dora Minta",
        lang="en", spellings=[spelling, heard], chunks=4, containers=2, missed_chunks=1, missed_containers=1,
        newest="2026-10-12T09:00:00Z", evidence=evidence,
    )  # fmt: skip
    shown = curate.hidden_card(card, NeverStore(people=["Dora Minta"]))
    hidden = curate.HIDDEN
    assert (shown.key, shown.entity, shown.name) == ("new:-:(on a never-store list)", hidden, hidden)
    assert [s.text for s in shown.spellings] == [hidden, "Dora Mint"]
    assert shown.evidence == [("r:calls:rec-1@00:10-00:40", hidden), ("r:calls:rec-2", "Dora Mint")]
    cards = [curate.CardView(card=shown, state="open", snippets=[])]
    started = datetime(2026, 10, 1, tzinfo=UTC)
    view = curate.BatchView(round="r", started=started, cards=cards, left_out={}, applied=[])
    first = render.curate_round(view)[1]
    assert first.startswith(f" 1  0a1b2c  new person  {hidden} ({hidden})  missed in 1 chunk(s)")
    assert curate.hidden_card(card, NeverStore()) == card


def test_a_write_says_what_it_finished_and_took_in_first() -> None:
    written = Written(
        finished=("20261001T091402-desk-1",),
        taken_in=(NORTHWIND_FILE,),
        only_logged=("20261001T091402-desk-2",),
        commit="0123456789abcdef",
    )
    assert render.curate_written(written) == [
        "finished first, what a stopped write left: 20261001T091402-desk-1",
        f"a hand edit of {NORTHWIND_FILE} went in first, on its own",
        "only logged, the file no longer held what they added: 20261001T091402-desk-2",
        "committed 0123456789ab",
    ]


@pytest.fixture
def kit_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    v = curate_vault(tmp_path)
    monkeypatch.setenv("DOCKET_VAULT", str(v.root))
    return v


def test_a_round_from_start_to_status(kit_vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["curate", "show"]) == 2
    assert "no round on this machine yet" in capsys.readouterr().err
    assert main(["curate", "round"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("round    ") and out[0].endswith("open")
    assert out[1].startswith("left out ")
    assert out[2].startswith(" 1  ") and "new term  Northwind (northwind)  missed in 3 chunk(s)" in out[2]
    assert "Nortwin (heard, asr alias)" in out[3]
    assert "»Nortwin«" in out[4]
    assert any("Test Cleo (other order, not seen, alias)" in ln for ln in out)
    assert main(["curate", "show", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    cards = {c["card"]["name"]: c for c in shown["cards"]}
    assert cards["Northwind"]["state"] == "open" and len(cards["Northwind"]["snippets"]) == 3
    round_ = shown["round"]
    assert main(["curate", "accept", cards["Northwind"]["card"]["id"], "--type", "customer"]) == 0
    assert "create northwind" in capsys.readouterr().out
    assert main(["curate", "reject", "--round", round_, "2"]) == 0
    assert "reject -  new:-:dora" in capsys.readouterr().out
    assert main(["curate", "accept", "--round", "2020-01-01T000000-x", "3"]) == 2
    assert "isn't this machine's current one" in capsys.readouterr().err
    assert main(["curate", "lift", "new:-:doraval"]) == 0
    assert "lift   -  new:-:doraval" in capsys.readouterr().out
    changes = tmp_path / "edit.yaml"
    cleo = cards["Cleo Test"]["card"]["id"]
    changes.write_text(
        f"changes:\n  - create: {{id: cleo-test, type: person, name: {{en: Cleo Test}}, aliases: [{{text: Cleo}}]}}\n"
        f"    card: {cleo}\n",
        encoding="utf-8",
    )
    assert main(["curate", "add", str(changes)]) == 0
    added = capsys.readouterr().out
    assert "create cleo-test" in added and "reject cleo-test  alias:cleo-test:test cleo" in added
    assert main(["curate", "show"]) == 0
    states = [line.rsplit("  ", 1)[1] for line in capsys.readouterr().out.splitlines() if line[:2].strip().isdigit()]
    assert states[:4] == ["accepted", "rejected", "open", "accepted"]
    assert main(["curate", "status"]) == 0
    status = capsys.readouterr().out.splitlines()
    assert status[0].startswith("due      no, the last started ")
    assert status[1].startswith(f"round    {round_}: ")
    assert status[2].startswith("review   ") and status[3] == "stoplist 2 key(s)"
    assert status[4].startswith("questions set curate.questions in ")


def test_an_undo_in_the_open_round_reopens_the_card(kit_vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    main(["curate", "round"])
    capsys.readouterr()
    assert main(["curate", "accept", "--round", curate.load(kit_vault).round, "4"]) == 0  # type: ignore[union-attr]
    created = capsys.readouterr().out.split()[0]
    assert main(["curate", "undo", created]) == 0
    capsys.readouterr()
    main(["curate", "show", "--json"])
    assert {c["state"] for c in json.loads(capsys.readouterr().out)["cards"]} == {"open"}


def test_a_never_store_key_is_neither_lifted_nor_undone(kit_vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    main(["curate", "round"])
    capsys.readouterr()
    northwind = next(c for c in curate.load(kit_vault).cards if c.name == "Northwind")  # type: ignore[union-attr]
    assert main(["curate", "reject", northwind.id]) == 0
    rejected = [line.split()[0] for line in capsys.readouterr().out.splitlines() if "new:-:nortwin" in line]
    config = kit_vault.root / "vault.yaml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["never_store"] = {"keywords": ["Nortwin"]}
    config.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    before = len(curatelog.read_all(kit_vault.root).lines)
    assert main(["curate", "lift", "new:-:nortwin"]) == 1
    lifted = capsys.readouterr().out
    assert "key 1: its text is on the never-store keywords list; it stays on the stoplist" in lifted
    assert main(["curate", "undo", *rejected]) == 1
    undone = capsys.readouterr().out
    assert "its text is on the never-store keywords list; edit the entity file by hand" in undone
    assert "nortwin" not in (lifted + undone).lower()
    assert len(curatelog.read_all(kit_vault.root).lines) == before
    assert main(["curate", "lift", "new:-:northwind"]) == 0


def test_show_marks_an_applied_line_a_stopped_write_left(kit_vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    main(["curate", "round"])
    capsys.readouterr()
    begun = curate.load(kit_vault).started  # type: ignore[union-attr]
    at = begun + timedelta(seconds=30)
    line = Line.model_validate({
        "schema_version": 1, "id": f"{at:%Y%m%dT%H%M%S}-test-box-1", "op": "reject", "at": at, "machine": "test-box",
        "by": "tier1", "via": "cli", "key": "new:-:salt road",
    })  # fmt: skip
    path = kit_vault.root / line.rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line.text() + "\n", encoding="utf-8")
    assert main(["curate", "show"]) == 0
    applied = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("applied")]
    assert applied == [
        f"applied  by itself: {line.id}  reject -  new:-:salt road  not finished: the next write commits it"
    ]
    assert main(["curate", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["unfinished"] == [line.id]


def test_status_and_round_measure_your_questions(
    kit_vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "q.yaml"
    path.write_text(
        'schema_version: 1\nasked: "2026-10-14T10:00:00+02:00"\nquestions:\n'
        '  - q: Nortwin\n    want: ["r:plaud:rec-0000/transaction"]\n    kind: typo\n    lang: en\n',
        encoding="utf-8",
    )
    userconfig.set_section("curate", {"questions": str(path)})
    main(["curate", "round"])
    assert "questions before " in capsys.readouterr().out
    batch = curate.load(kit_vault)
    assert batch is not None
    for card in batch.cards[1:]:
        main(["curate", "reject", card.id])
    capsys.readouterr()
    assert main(["curate", "accept", batch.cards[0].id]) == 0
    assert "questions " in capsys.readouterr().out.splitlines()[-1]
    assert main(["curate", "status", "--eval"]) == 0
    assert "->" in capsys.readouterr().out.splitlines()[-1]


def test_an_empty_round_and_a_question_file_not_yet_measured_say_so() -> None:
    when = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
    applied = line(Change(alias=AliasFields(text="Nortwin")), by="tier1")
    view = curate.BatchView(round="2026-10-16T090000-desk", started=when, cards=[], left_out={}, applied=[applied])
    out = render.curate_round(view)
    assert out[1].startswith("applied  by itself: 20261001T091402-desk-1  add northwind  alias Nortwin")
    assert out[-1] == "no cards this round"
    assert render.curate_measures(None, None) == []
    status = curate.Status(round=None, cards=0, open=0, due=True, last=None, minutes={}, stoplist=0, questions="q.yaml")
    assert render.curate_status(status, "config.yaml")[-1] == "questions q.yaml, measured at a round"
