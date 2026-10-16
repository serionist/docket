from __future__ import annotations

import json
from pathlib import Path

import pytest
from docket_testkit import NORTHWIND_FILE, entity_vault, sh

from docket import render
from docket.cli import main
from docket.vault.curatelog import LOG_DIR, AliasFields, Change, IdFields, Line, NameFields, RelatedFields
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
