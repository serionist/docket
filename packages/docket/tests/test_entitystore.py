from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import BEN_FILE, NORTHWIND_FILE, entity_vault, folder_vault, git, sh

from docket.errors import DocketError
from docket.vault import curatelog, entitystore
from docket.vault.config import NeverStore, Redaction, RedactionPattern
from docket.vault.curatelog import LOG_DIR, Line, fold, read_all
from docket.vault.entities import load_entities, load_file, render
from docket.vault.entitystore import EntityStore, RefusedError, Written, load_changes
from docket.vault.vault import Vault

NORTHWIND = NORTHWIND_FILE
BEN = BEN_FILE
CLEO = "entities/person/cleo-test.yaml"


def seed(vault: Vault, rel: str, text: str, *, commit: bool = True) -> None:
    path = vault.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    if commit:
        vault.commit([rel], f"seed {rel}")


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return entity_vault(tmp_path)


def changes(tmp_path: Path, text: str) -> list[entitystore.Planned]:
    path = tmp_path / "changes.yaml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return load_changes(path)


def apply(vault: Vault, tmp_path: Path, text: str) -> Written:
    return EntityStore(vault).apply(changes(tmp_path, text))


def refused(call: Any) -> list[str]:
    with pytest.raises(RefusedError) as e:
        call()
    return e.value.problems


def head(vault: Vault) -> str:
    return sh(vault.root, "git", "rev-parse", "HEAD").strip()


def message(vault: Vault, rev: str = "HEAD") -> str:
    return sh(vault.root, "git", "log", "-1", "--format=%B", rev).strip()


def entity(vault: Vault, rel: str) -> Any:
    found, problems = load_file(vault.root, vault.root / rel)
    assert problems == []
    return found


def clean(vault: Vault) -> bool:
    return sh(vault.root, "git", "status", "--porcelain", "--", "entities").strip() == ""


CREATE_AND_ADD = """\
changes:
  - create:
      id: cleo-test
      type: person
      name: {en: Cleo Test}
      aliases: [{text: Cleo}]
      role: rights agent
  - entity: northwind
    add:
      aliases: [{text: Nortwin, kind: asr}]
      names: {hu: Northwind Kft}
      ids: {email_domain: northwind.example}
      related: {person: [ben-sample]}
"""


def test_a_create_and_an_add_are_logged_written_and_committed_together(vault: Vault, tmp_path: Path) -> None:
    written = apply(vault, tmp_path, CREATE_AND_ADD)
    assert [line.op for line in written.lines] == ["create", "add", "add", "add", "add"]
    assert [line.key for line in written.lines] == [
        "new:-:cleo test",
        "alias:northwind:nortwin",
        "name:northwind:hu=northwind kft",
        "id:northwind:email_domain=northwind.example",
        "related:northwind:person=ben-sample",
    ]
    assert {(line.by, line.via, line.machine) for line in written.lines} == {("user", "cli", "test-box")}
    assert written.commit == head(vault)
    assert message(vault).splitlines()[0] == "Entities: 5 changes"
    assert f"Curate: {written.lines[0].id} create cleo-test" in message(vault)
    cleo = entity(vault, CLEO)
    assert (vault.root / CLEO).read_text(encoding="utf-8") == render(cleo)
    assert cleo.role == "rights agent"
    northwind = entity(vault, NORTHWIND)
    assert northwind.names == {"en": "Northwind", "hu": "Northwind Kft"}
    assert [a.text for a in northwind.aliases] == ["Northwind Traders", "Nortwin"]
    assert northwind.ids == {"email_domain": ("northwind.example",)}
    assert northwind.related == {"person": ("ben-sample",)}
    assert read_all(vault.root).lines == written.lines
    assert clean(vault)


def test_one_change_s_commit_names_its_entity_and_op(vault: Vault, tmp_path: Path) -> None:
    written = apply(vault, tmp_path, "changes: [{entity: northwind, add: {aliases: [{text: Nortwin}]}}]")
    assert message(vault).splitlines()[0] == "Entity: northwind add"
    assert written.lines[0].rel == f"{LOG_DIR}/{datetime.now(UTC):%Y-%m}-test-box.jsonl"


def test_what_an_entity_holds_already_is_counted_and_writes_nothing(vault: Vault, tmp_path: Path) -> None:
    apply(vault, tmp_path, CREATE_AND_ADD)
    before = head(vault)
    again = """\
changes:
  - entity: northwind
    add:
      aliases: [{text: NORTHWIND TRADERS}, {text: nortwin}, {text: northwind}]
      names: {en: northwind}
      ids: {email_domain: NorthWind.Example}
      related: {person: ben-sample}
"""
    written = apply(vault, tmp_path, again)
    assert written == Written(already=6)
    assert head(vault) == before


def test_changes_that_can_t_be_made_are_refused_by_position_and_nothing_is_written(
    vault: Vault, tmp_path: Path
) -> None:
    text = """\
changes:
  - entity: rita-sample
    add: {aliases: [{text: Rita}]}
  - create: {id: ben-sample, type: person, name: {en: Ben Sample}}
  - create: {id: cleo-test, type: person, name: {en: Cleo Test}}
  - create: {id: cleo-test, type: person, name: {en: Cleo Test}}
  - entity: northwind
    add: {names: {en: Northwind Ltd}}
"""
    before = head(vault)
    problems = refused(lambda: apply(vault, tmp_path, text))
    assert problems == [
        "change 1: no entity has that id",
        "change 2: an entity has that id already",
        "change 4: an entity has that id already",
        "change 5: the entity has another name in that language; change it in the file",
    ]
    for value in ("rita", "Ben Sample", "cleo", "Northwind Ltd"):
        assert value not in " ".join(problems)
    assert head(vault) == before
    assert not (vault.root / LOG_DIR).exists()


def test_a_file_with_a_comment_or_a_read_problem_is_never_rewritten(vault: Vault, tmp_path: Path) -> None:
    seed(vault, NORTHWIND, "id: northwind\ntype: customer\n# the biggest customer\nname: {en: Northwind}\n")
    seed(vault, BEN, "id: ben-sample\ntype: person\nname: {en: Ben Sample}\ncolour: blue\n")
    seed(vault, "entities/person/rita.yaml", "id: rita-sample\ntype: person\nname: {en: Rita Sample}\n")
    text = """\
changes:
  - {entity: northwind, add: {aliases: [{text: Nortwin}]}}
  - {entity: ben-sample, add: {aliases: [{text: Ben}]}}
  - {entity: rita, add: {aliases: [{text: Rita}]}}
"""
    problems = refused(lambda: apply(vault, tmp_path, text))
    assert problems[0] == (
        "change 1: the entity's file holds a comment on line 3, which a rewrite would lose; take it out, or make the "
        "change by hand"
    )
    assert problems[1] == (
        "change 2: the entity's file reads with a problem a rewrite would lose (unknown key 'colour'); fix it first"
    )
    assert problems[2].startswith("change 3: the entity's file reads with a problem a rewrite would lose (id ")
    assert "colour: blue" in (vault.root / BEN).read_text(encoding="utf-8")


def test_never_store_and_redaction_refuse_a_change_and_never_say_the_value(vault: Vault, tmp_path: Path) -> None:
    vault.config = vault.config.model_copy(
        update={
            "never_store": NeverStore(people=["Minta Dóra", "rita@example.com"], keywords=["harbour secret"]),
            "redaction": Redaction(patterns=[RedactionPattern(name="badge", pattern=r"BADGE-\d+")]),
        }
    )
    text = """\
changes:
  - create: {id: dora-minta, type: person, name: {en: Dóra Minta}}
  - create: {id: minta-dora, type: person, name: {en: D M}}
  - create: {id: cleo-test, type: person, name: {en: Cleo Test}, note: about the harbour secret}
  - entity: northwind
    add: {aliases: [{text: BADGE-1234}]}
  - entity: ben-sample
    add: {ids: {email: rita@example.com}}
  - entity: ben-sample
    add: {aliases: [{text: Ben}]}
"""
    problems = refused(lambda: apply(vault, tmp_path, text))
    assert problems == [
        "change 1: a value is on the never-store people list; leave it out",
        "change 2: a value is on the never-store people list; leave it out",
        "change 3: a value is on the never-store keywords list; leave it out",
        "change 4: the badge redaction rule would change a value; leave it out",
        "change 5: a value is on the never-store people list; leave it out",
    ]
    assert not (vault.root / LOG_DIR).exists()


def test_a_change_that_wouldn_t_read_back_is_refused(
    vault: Vault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(entitystore, "render", lambda e: render(e).replace("{text: x}", "{text: y}"))
    problems = refused(lambda: apply(vault, tmp_path, "changes: [{entity: northwind, add: {aliases: [{text: x}]}}]"))
    assert problems == ["change 1: the entity wouldn't read back as written"]
    monkeypatch.setattr(entitystore, "render", lambda e: render(e) + "colour: blue\n")
    problems = refused(lambda: apply(vault, tmp_path, "changes: [{entity: northwind, add: {aliases: [{text: x}]}}]"))
    assert problems == ["change 1: the entity wouldn't read back as written"]


def test_a_hand_edit_goes_in_on_its_own_first(vault: Vault, tmp_path: Path) -> None:
    seed(vault, NORTHWIND, (vault.root / NORTHWIND).read_text(encoding="utf-8") + "role: our customer\n", commit=False)
    seed(vault, "entities/person/rita-sample.yaml", "id: rita-sample\ntype: person\nname: {en: Rita}\n", commit=False)
    text = """\
changes:
  - {entity: northwind, add: {aliases: [{text: Nortwin}]}}
  - {entity: rita-sample, add: {aliases: [{text: Rita Sample}]}}
"""
    written = apply(vault, tmp_path, text)
    assert written.taken_in == (NORTHWIND, "entities/person/rita-sample.yaml")
    assert message(vault, "HEAD~1") == "Entity: rita-sample hand edit"
    assert message(vault, "HEAD~2") == "Entity: northwind hand edit"
    northwind = entity(vault, NORTHWIND)
    assert northwind.role == "our customer"
    assert [a.text for a in northwind.aliases] == ["Northwind Traders", "Nortwin"]
    assert clean(vault)


def fail_commits(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    real = vault.git.run

    def run(*args: str, **kwargs: Any) -> Any:
        if args and args[0] == "commit":
            raise DocketError("git commit: a hook said no")
        return real(*args, **kwargs)

    monkeypatch.setattr(vault.git, "run", run)


def test_a_failed_commit_puts_the_files_and_the_log_back(
    vault: Vault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apply(vault, tmp_path, "changes: [{entity: northwind, add: {aliases: [{text: Nortwin}]}}]")
    log_rel = read_all(vault.root).lines[0].rel
    before = {rel: (vault.root / rel).read_bytes() for rel in (NORTHWIND, log_rel)}
    fail_commits(vault, monkeypatch)
    text = """\
changes:
  - create: {id: cleo-test, type: person, name: {en: Cleo Test}}
  - {entity: northwind, add: {aliases: [{text: North Wind}]}}
"""
    with pytest.raises(DocketError, match="a hook said no"):
        apply(vault, tmp_path, text)
    assert {rel: (vault.root / rel).read_bytes() for rel in before} == before
    assert not (vault.root / CLEO).exists()
    assert sh(vault.root, "git", "diff", "--cached", "--name-only").strip() == ""
    assert clean(vault)


def leave(vault: Vault, *lines: Line) -> None:
    """Lines a write appended before it stopped: in the working tree, not committed."""
    for line in lines:
        path = vault.root / line.rel
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(before + line.text() + "\n", encoding="utf-8", newline="\n")


def stopped(op: str, n: int, **fields: Any) -> Line:
    return Line.model_validate(
        {
            "schema_version": 1,
            "id": f"20261001T091402-test-box-{n}",
            "op": op,
            "at": "2026-10-01T09:14:02Z",
            "machine": "test-box",
            "by": "user",
            "via": "cli",
            **fields,
        }
    )


def test_a_stopped_write_is_finished_by_the_next_write(vault: Vault, tmp_path: Path) -> None:
    create = stopped(
        "create",
        1,
        entity="cleo-test",
        key="new:-:cleo test",
        change={"entity": {"id": "cleo-test", "type": "person", "name": {"en": "Cleo Test"}}},
    )
    add = stopped("add", 2, entity="northwind", key="alias:northwind:nortwin", change={"alias": {"text": "Nortwin"}})
    stray = stopped("undo", 3, entity="northwind", key="x", of="20261001T091402-test-box-9")
    leave(vault, create, add, stray)
    written = apply(vault, tmp_path, "changes: [{entity: ben-sample, add: {aliases: [{text: Ben}]}}]")
    assert written.finished == (create.id, add.id, stray.id)
    assert message(vault, "HEAD~1").startswith("Entities: finish a write that stopped\n\nCurate: ")
    assert entity(vault, CLEO).names == {"en": "Cleo Test"}
    assert [a.text for a in entity(vault, NORTHWIND).aliases] == ["Northwind Traders", "Nortwin"]
    assert clean(vault)


def test_a_stopped_undo_is_finished_and_a_line_it_can_t_finish_still_goes_in(vault: Vault, tmp_path: Path) -> None:
    added = apply(vault, tmp_path, "changes: [{entity: northwind, add: {aliases: [{text: Nortwin}]}}]").lines[0]
    undo = stopped("undo", 1, entity="northwind", key=added.key, of=added.id)
    blocked = stopped("add", 2, entity="ben-sample", key="alias:ben-sample:ben", change={"alias": {"text": "Ben"}})
    seed(vault, BEN, "id: ben-sample\ntype: person\nname: {en: Ben Sample}\n# kept by hand\n")
    leave(vault, undo, blocked)
    written = EntityStore(vault).undo([])
    assert written.finished == (undo.id, blocked.id)
    assert [a.text for a in entity(vault, NORTHWIND).aliases] == ["Northwind Traders"]
    assert "Ben\n" not in (vault.root / BEN).read_text(encoding="utf-8")
    assert clean(vault)


def undo(vault: Vault, *ops: str) -> Written:
    return EntityStore(vault).undo(list(ops))


def test_undoing_adds_takes_out_each_part_and_stoplists_its_key(vault: Vault, tmp_path: Path) -> None:
    written = apply(vault, tmp_path, CREATE_AND_ADD)
    adds = [line.id for line in written.lines if line.op == "add"]
    undone = undo(vault, *adds)
    assert [line.op for line in undone.lines] == ["undo"] * 4
    assert [line.of for line in undone.lines] == adds
    assert message(vault).splitlines()[0] == "Entities: 4 changes"
    assert entity(vault, NORTHWIND) == load_entities(vault.root).by_id()["northwind"]
    northwind = entity(vault, NORTHWIND)
    assert (northwind.names, [a.text for a in northwind.aliases]) == ({"en": "Northwind"}, ["Northwind Traders"])
    assert (northwind.ids, northwind.related) == ({}, {})
    folded = fold(read_all(vault.root).lines)
    assert folded.stoplist == frozenset(line.key for line in written.lines[1:])
    assert set(folded.undone_by) == set(adds)


def test_undoing_one_of_several_ids_or_links_keeps_the_others(vault: Vault, tmp_path: Path) -> None:
    text = """\
changes:
  - entity: northwind
    add: {ids: {email: [a@northwind.example, b@northwind.example]}, related: {person: [ben-sample, cleo-test]}}
"""
    written = apply(vault, tmp_path, text)
    undo(vault, written.lines[0].id, written.lines[2].id)
    northwind = entity(vault, NORTHWIND)
    assert northwind.ids == {"email": ("b@northwind.example",)}
    assert northwind.related == {"person": ("cleo-test",)}


def test_undoing_a_create_deletes_its_file(vault: Vault, tmp_path: Path) -> None:
    written = apply(vault, tmp_path, "changes: [{create: {id: cleo-test, type: person, name: {en: Cleo Test}}}]")
    undone = undo(vault, written.lines[0].id)
    assert message(vault).splitlines()[0] == "Entity: cleo-test undo"
    assert not (vault.root / CLEO).exists()
    assert sh(vault.root, "git", "ls-files", "--", CLEO).strip() == ""
    assert fold(read_all(vault.root).lines).undone_creates == written.lines
    assert undone.only_logged == ()


def test_a_create_undone_with_its_later_adds_goes_in_one_commit(vault: Vault, tmp_path: Path) -> None:
    created = apply(vault, tmp_path, "changes: [{create: {id: cleo-test, type: person, name: {en: Cleo Test}}}]")
    added = apply(vault, tmp_path, "changes: [{entity: cleo-test, add: {aliases: [{text: Cleo}]}}]")
    problems = refused(lambda: undo(vault, created.lines[0].id))
    assert problems == [
        f"{created.lines[0].id}: the entity changed since it was created; undo {added.lines[0].id} first"
    ]
    undo(vault, created.lines[0].id, added.lines[0].id)
    assert not (vault.root / CLEO).exists()


def test_a_create_changed_by_hand_is_not_undone(vault: Vault, tmp_path: Path) -> None:
    created = apply(vault, tmp_path, "changes: [{create: {id: cleo-test, type: person, name: {en: Cleo Test}}}]")
    seed(vault, CLEO, (vault.root / CLEO).read_text(encoding="utf-8") + "role: rights agent\n")
    problems = refused(lambda: undo(vault, created.lines[0].id))
    assert problems == [
        f"{created.lines[0].id}: the entity was changed by hand since it was created; change or delete its file"
    ]


def test_an_add_whose_part_or_file_is_gone_is_only_logged(vault: Vault, tmp_path: Path) -> None:
    written = apply(vault, tmp_path, CREATE_AND_ADD)
    seed(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {en: Northwind}\n")
    (vault.root / CLEO).unlink()
    vault.commit([CLEO], "gone by hand")
    gone = [line.id for line in written.lines]
    undone = undo(vault, *gone)
    assert undone.only_logged == tuple(sorted(gone, reverse=True))
    assert undone.commit == head(vault)


def test_what_can_t_be_undone_is_refused_whole(vault: Vault, tmp_path: Path) -> None:
    written = apply(vault, tmp_path, CREATE_AND_ADD)
    first = undo(vault, written.lines[1].id).lines[0]
    problems = refused(lambda: undo(vault, "20261001T091402-test-box-99", first.id, written.lines[1].id))
    assert problems == [
        "20261001T091402-test-box-99: no op in the log has that id",
        f"{first.id}: an undo or a lift can't be undone",
        f"{written.lines[1].id}: already undone by {first.id}",
    ]


def test_an_entity_s_only_name_isn_t_undone(vault: Vault, tmp_path: Path) -> None:
    written = apply(vault, tmp_path, "changes: [{entity: northwind, add: {names: {hu: Northwind Kft}}}]")
    seed(vault, NORTHWIND, "id: northwind\ntype: customer\nname: {hu: Northwind Kft}\n")
    problems = refused(lambda: undo(vault, written.lines[0].id))
    only = "the name is the entity's only one; undo its create, or change the file"
    assert problems == [f"{written.lines[0].id}: {only}"]


def test_undoing_a_reject_lifts_its_key(vault: Vault, tmp_path: Path) -> None:
    reject = stopped("reject", 1, key="new:-:nortwin")
    leave(vault, reject)
    vault.commit([reject.rel], "a reject from another machine")
    undone = undo(vault, reject.id)
    folded = fold(read_all(vault.root).lines)
    assert folded.stoplist == frozenset()
    assert folded.lifted == {"new:-:nortwin": undone.lines[0].at}


def test_a_folder_vault_s_entity_commits_hold_only_its_own_paths(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    written = apply(vault, tmp_path, "changes: [{create: {id: cleo-test, type: person, name: {en: Cleo Test}}}]")
    names = git(top, "show", "--name-only", "--format=", "HEAD").split()
    assert names == [f"notes/vault/{written.lines[0].rel}", f"notes/vault/{CLEO}"]
    assert "README.md" in git(top, "diff", "--cached", "--name-only")
    undo(vault, written.lines[0].id)
    assert not (vault.root / CLEO).exists()


def test_a_log_folder_that_is_a_link_refuses_a_write(
    vault: Vault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(curatelog, "_linked", lambda path: path.name == "_log")
    with pytest.raises(DocketError, match="is a link"):
        apply(vault, tmp_path, "changes: [{entity: northwind, add: {aliases: [{text: Nortwin}]}}]")


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("changes: [", ["the file isn't YAML (line 1)"]),
        ("", ["the file: Input should be a valid dictionary or instance of ChangesFile"]),
        ("changes: []\n", ["the file: changes lists at least one change"]),
        (
            "changes: [{entity: northwind, add: {aliases: [{text: x}]}}]\nmore: 1\n",
            ["the file: Extra inputs are not permitted"],
        ),
        ("changes: [{entity: northwind}]", ["change 1: a change is create: {...}, or entity: <id> with add: {...}"]),
        (
            "changes: [{add: {aliases: [{text: x}]}}]",
            ["change 1: a change is create: {...}, or entity: <id> with add: {...}"],
        ),
        ("changes: [{entity: northwind, add: {}}]", ["change 1: add: add holds aliases, names, ids or related"]),
        (
            "changes: [{create: {id: Cleo Test, type: person, name: {en: Cleo Test}}}]",
            ["change 1: create id: an id: lowercase letters, digits and inner hyphens, at most 80 characters"],
        ),
        (
            "changes: [{create: {id: x, type: person, name: {Cleo Test: x}}}]",
            ["change 1: create name: a language code: two or three lowercase letters"],
        ),
        (
            "changes: [{create: {id: x, type: person, name: {en: x}, colour: Cleo Test}}]",
            ["change 1: create: Extra inputs are not permitted"],
        ),
        (
            "changes: [{entity: x, add: {aliases: [{text: x}, {text: Clio, kind: typo}]}}]",
            ["change 1: add aliases item 2 kind: Input should be 'plain' or 'asr'"],
        ),
    ],
)
def test_a_changes_file_is_refused_with_every_problem_by_position(tmp_path: Path, text: str, says: list[str]) -> None:
    assert refused(lambda: changes(tmp_path, text)) == says


def test_a_changes_file_that_isn_t_utf_8_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "changes.yaml"
    path.write_bytes(b"changes: [\xff]")
    assert refused(lambda: load_changes(path)) == ["the file isn't UTF-8"]


def test_a_changes_file_s_add_becomes_one_part_each(tmp_path: Path) -> None:
    planned = changes(tmp_path, CREATE_AND_ADD)
    assert [(p.n, p.entity) for p in planned] == [(1, "cleo-test")] + [(2, "northwind")] * 4
