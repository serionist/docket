from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import INDEX_FIXTURE

from docket.vault.entities import (
    Alias,
    Entity,
    comment_line,
    entity_files,
    load_entities,
    load_file,
    load_text,
    render,
)


def write(root: Path, rel: str, text: str | bytes) -> Path:
    path = root / "entities" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8", newline="\n")
    return path


def messages(root: Path) -> list[str]:
    return [str(p) for p in load_entities(root).problems]


def test_the_design_s_examples_load(tmp_path: Path) -> None:
    for path in (INDEX_FIXTURE / "entities").rglob("*.yaml"):
        write(tmp_path, path.relative_to(INDEX_FIXTURE / "entities").as_posix(), path.read_text(encoding="utf-8"))
    found = load_entities(tmp_path)
    assert found.problems == []
    by_id = found.by_id()
    northwind = by_id["northwind"]
    assert northwind.type == "customer"
    assert northwind.names == {"en": "Northwind", "hu": "Northwind"}
    assert Alias("Nortwin", "asr") in northwind.aliases
    assert Alias("Norton", "asr", ("ben-sample", "harbour-site")) in northwind.aliases
    assert northwind.related == {"site": ("harbour-site",), "person": ("ben-sample",)}
    assert northwind.ids == {"email_domain": ("northwind.example",)}
    assert by_id["ben-sample"].role == "the customer's site lead"
    assert by_id["fleet-management"].note is not None
    assert [e.id for e in found.entities] == sorted(by_id)


def test_values_stay_text_and_a_bom_and_crlf_are_read(tmp_path: Path) -> None:
    write(
        tmp_path,
        "term/no.yaml",
        "\N{BYTE ORDER MARK}id: no\r\ntype: term\r\nname: {en: no, hu: '2026'}\r\naliases:\r\n  - {text: 2026}\r\n"
        "ids: {code: [no, 'yes']}\r\nschema: 1\r\n".encode(),
    )
    found = load_entities(tmp_path)
    assert found.problems == []
    entity = found.entities[0]
    assert entity.names == {"en": "no", "hu": "2026"}
    assert entity.aliases == (Alias("2026"),)
    assert entity.ids == {"code": ("no", "yes")}


def test_a_file_that_can_t_be_an_entity_is_skipped_with_its_reason(tmp_path: Path) -> None:
    cases = {
        "person/wrong-name.yaml": "id: other\ntype: person\nname: {en: X}\n",
        "person/wrong-type.yaml": "id: wrong-type\ntype: customer\nname: {en: X}\n",
        "person/no-name.yaml": "id: no-name\ntype: person\n",
        "person/Bad_Id.yaml": "id: Bad_Id\ntype: person\nname: {en: X}\n",
        "person/con.yaml": "id: con\ntype: person\nname: {en: X}\n",
        "person/later.yaml": "id: later\ntype: person\nname: {en: X}\nschema: 2\n",
        "person/broken.yaml": "id: [unclosed\n",
        "person/list.yaml": "- just\n- a list\n",
        "person/latin1.yaml": b"id: latin1\ntype: person\nname: {en: \xe9}\n",
        "person/names-bad.yaml": "id: names-bad\ntype: person\nname: Just A String\n",
        "person/names-empty.yaml": "id: names-empty\ntype: person\nname: {english: X, en: '...'}\n",
        "person/notes.txt": "not yaml",
        "loose.yaml": "id: loose\ntype: person\nname: {en: X}\n",
    }
    for rel, text in cases.items():
        write(tmp_path, rel, text)
    write(tmp_path, "person/deeper/x.yaml", "id: x\n")
    found = load_entities(tmp_path)
    assert found.entities == []
    said = "\n".join(str(p) for p in found.problems)
    for needle in (
        "wrong-name.yaml: id 'other' isn't the file's name",
        "wrong-type.yaml: type 'customer' isn't the folder's name",
        "no-name.yaml: name is required",
        "Bad_Id.yaml: id is a slug",
        "con.yaml: id 'con' is a name Windows reserves",
        "later.yaml: schema '2' is a format this Docket doesn't read",
        "broken.yaml: isn't YAML",
        "list.yaml: an entity is keys and values",
        "latin1.yaml: isn't UTF-8",
        "names-bad.yaml: name is a map",
        "names-empty.yaml: name gives the name in at least one language",
        "names-empty.yaml: name: english: a language code",
        "notes.txt: an entity file ends in .yaml",
        "entities/loose.yaml: an entity file goes in entities/<type>/<id>.yaml",
    ):
        assert needle in said, needle
    assert "x.yaml" not in said


def test_unknown_keys_and_values_are_problems_and_the_rest_loads(tmp_path: Path) -> None:
    write(
        tmp_path,
        "vendor/acme.yaml",
        "id: acme\ntype: vendor\nname: {en: Acme}\nalias: [Acmee]\nstatus: gone\nrole: [a, list]\nnote: {a: map}\n"
        "aliases:\n  - {text: Acme Ltd, kind: exact, color: red}\n  - {text: '---'}\n  - just text\n"
        "  - {text: ACM, needs: {a: b}}\n"
        "related: {customer: [ghost], 'Bad Key': [x]}\nids: [not, a, map]\n",
    )
    write(tmp_path, "person/x.yaml", "id: x\ntype: person\nname: {en: X}\naliases: not-a-list\nrelated: [x]\n")
    found = load_entities(tmp_path)
    acme = found.by_id()["acme"]
    assert acme.status == "active"
    assert acme.aliases == (Alias("Acme Ltd"),)
    assert acme.related == {"customer": ("ghost",)}
    assert acme.ids == {}
    assert acme.role is None and acme.note is None
    said = "\n".join(str(p) for p in found.problems)
    for needle in (
        "unknown key 'alias'",
        "type 'vendor' isn't one this Docket knows",
        "status is active or archived, not 'gone'",
        "role is text",
        "note is text",
        "aliases item 1: unknown key 'color'",
        "aliases item 1: kind is asr or absent, not 'exact'; read as plain",
        "aliases item 2: text is the alias",
        "aliases item 3: an alias is",
        "aliases item 4: needs is a list of entity ids; the alias is left out",
        "related: Bad Key: a key of lowercase letters",
        "ids is a map",
        "related customer: no entity has the id 'ghost'",
        "x.yaml: aliases is a list",
        "x.yaml: related is a map",
    ):
        assert needle in said, needle


def test_a_key_given_twice_is_a_problem_and_the_last_is_read(tmp_path: Path) -> None:
    write(
        tmp_path,
        "customer/contoso.yaml",
        "id: contoso\ntype: customer\nname: {en: Contoso}\naliases:\n  - {text: Contoso Ltd}\n"
        "  - {text: Kontozo, kind: asr, kind: plain}\nstatus: active\naliases:\n  - {text: Contoso Group}\n",
    )
    write(tmp_path, "person/odd.yaml", "id: odd\ntype: person\nname: {en: Odd}\n? [a, b]\n: c\n")
    found = load_entities(tmp_path)
    assert [e.id for e in found.entities] == ["contoso"]
    assert found.entities[0].aliases == (Alias("Contoso Group"),)
    said = messages(tmp_path)
    assert "entities/customer/contoso.yaml: line 8: 'aliases' is given twice; the last one is read" in said
    assert "entities/customer/contoso.yaml: line 6: 'kind' is given twice; the last one is read" in said
    assert any(p.startswith("entities/person/odd.yaml: isn't YAML") for p in said)


def test_an_ids_key_can_name_a_source_with_a_dot_and_values_stay_as_written(tmp_path: Path) -> None:
    write(tmp_path, "person/ben.yaml", "id: ben\ntype: person\nname: {en: Ben}\nids: {slack.work: U0BeN}\n")
    found = load_entities(tmp_path)
    assert found.problems == []
    assert found.entities[0].ids == {"slack.work": ("U0BeN",)}


def test_one_id_in_two_types_keeps_the_first_in_path_order(tmp_path: Path) -> None:
    write(tmp_path, "org/same.yaml", "id: same\ntype: org\nname: {en: Same Org}\n")
    write(tmp_path, "person/same.yaml", "id: same\ntype: person\nname: {en: Same Person}\n")
    found = load_entities(tmp_path)
    assert [(e.id, e.type) for e in found.entities] == [("same", "org")]
    assert "entities/person/same.yaml: entities/org/same.yaml has the id 'same' too; skipped" in messages(tmp_path)


def test_needs_naming_no_entity_is_a_problem(tmp_path: Path) -> None:
    write(tmp_path, "person/a.yaml", "id: a\ntype: person\nname: {en: A}\naliases: [{text: Ay, needs: ghost}]\n")
    assert "entities/person/a.yaml: needs of 'Ay': no entity has the id 'ghost'" in messages(tmp_path)


def test_underscored_and_hidden_files_are_not_entities(tmp_path: Path) -> None:
    write(tmp_path, "_proposals-test-box.jsonl", "{}\n")
    write(tmp_path, ".hidden/x.yaml", "id: x\n")
    write(tmp_path, "person/.x.yaml", "id: x\n")
    write(tmp_path, "person/_draft.yaml", "id: x\n")
    (tmp_path / "entities" / ".gitkeep").write_text("", encoding="utf-8")
    assert entity_files(tmp_path) == []
    assert load_entities(tmp_path).files == []
    assert entity_files(tmp_path / "nowhere") == []


def test_load_file_reports_a_single_file(tmp_path: Path) -> None:
    path = write(tmp_path, "person/solo.yaml", "id: solo\ntype: person\nname: {en: Solo}\n")
    entity, problems = load_file(tmp_path, path)
    assert entity is not None and entity.path == "entities/person/solo.yaml" and problems == []
    assert str(load_entities(tmp_path).entities[0].names) == "{'en': 'Solo'}"


def test_render_writes_one_form_that_reads_back_as_the_entity(tmp_path: Path) -> None:
    for path in (INDEX_FIXTURE / "entities").rglob("*.yaml"):
        rel = path.relative_to(INDEX_FIXTURE).as_posix()
        entity, problems = load_text(rel, path.read_text(encoding="utf-8"))
        assert problems == [] and entity is not None
        assert load_text(rel, render(entity)) == (entity, [])
    northwind = load_text(
        "entities/customer/northwind.yaml",
        (INDEX_FIXTURE / "entities/customer/northwind.yaml").read_text(encoding="utf-8"),
    )[0]
    assert northwind is not None
    assert render(northwind) == (
        "id: northwind\n"
        "type: customer\n"
        "name: {en: Northwind, hu: Northwind}\n"
        "aliases:\n"
        "  - {text: Northwind Traders}\n"
        "  - {text: Nortwin, kind: asr}\n"
        "  - {text: North Wind, kind: asr}\n"
        "  - {text: Norton, kind: asr, needs: [ben-sample, harbour-site]}\n"
        "related: {site: [harbour-site], person: [ben-sample]}\n"
        "ids: {email_domain: northwind.example}\n"
    )


def test_render_quotes_what_yaml_would_read_otherwise() -> None:
    odd = Entity(
        id="no",
        type="term",
        path="entities/term/no.yaml",
        names={"en": "no", "hu": "2026"},
        aliases=(Alias("a: b, c"), Alias("yes", "asr", ("no",)), Alias("Dóra [x] {y} #z \"q\" 'r'")),
        related={"person": ("no",)},
        ids={"code": ("no", "yes"), "one": ("#x",)},
        status="archived",
        role="line one\nline two",
        note="x",
    )
    text = render(odd)
    assert "id: 'no'\n" in text
    assert "status: archived\n" in text
    assert load_text(odd.path, text) == (odd, [])
    assert comment_line(text) is None


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("# who they are\nid: x\n", 1),
        ("id: x  # the slug\n", 1),
        ("id: x\naliases:\n  - {text: A}   # misheard\n", 3),
        ("\N{BYTE ORDER MARK}id: x\r\n# after a CRLF\r\n", 2),
        ("id: x\nname: {en: 'C # team'}\n", None),
        ('id: x\nname: {en: "C # team"}\n', None),
        ("id: a#b\n", None),
    ],
)
def test_a_comment_is_found_outside_every_value(text: str, line: int | None) -> None:
    assert comment_line(text) == line
