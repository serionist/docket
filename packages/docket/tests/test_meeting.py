"""A recording's meeting as the entities know it (DR-0569, DR-0583): the family's people resolved to entities, their
related projects and the entities its records name, each once; the glossary's terms; and an entity a never-store list
holds left out of both whole. Names and records are invented."""

from __future__ import annotations

from docket_testkit import make_config, make_record

from docket import meeting
from docket.vault.config import NeverStore
from docket.vault.entities import Alias, Entity


def entity(eid: str, name: str, kind: str = "person", **fields: object) -> Entity:
    return Entity(id=eid, type=kind, path=f"entities/{kind}/{eid}.yaml", names={"en": name}, **fields)  # pyright: ignore[reportArgumentType]


BEN = entity("ben-sample", "Ben Sample", related={"project": ("crane-works", "northwind")})
RITA = entity("rita-sample", "Rita Sample", aliases=(Alias("Rita S."),))
DORA = entity("dora-minta", "Dóra Minta", aliases=(Alias("Dóri", "asr"),), ids={"email": ("dora@example.invalid",)})
CRANE = entity("crane-works", "Crane Works", "project")
NORTHWIND = entity("northwind", "Northwind", "customer", aliases=(Alias("Nortwin", "asr"),))
OLD = entity("old-project", "Old Project", "project", status="archived")
KNOWN = [BEN, RITA, DORA, CRANE, NORTHWIND, OLD]


def family(text: str = "The offer came from Northwind.") -> list:
    return [
        make_record(rid="calls:rec-1", source="calls", kind="recording", author="Ben Sample", to=["rita s."], text=""),
        make_record(
            rid="calls:rec-1/transcript",
            source="calls",
            kind="transcript",
            segments=[{"start_ms": 0, "end_ms": 900, "speaker": "DORA MINTA", "text": text}],
            text=text,
        ),
    ]


def test_the_meeting_is_its_people_their_projects_and_the_entities_its_records_name() -> None:
    found = meeting.entities(family(), KNOWN, make_config())
    # Rita by a plain alias, Dóra by her name in capitals; Ben's related project, not his customer; Northwind, said.
    assert [e.id for e in found] == ["ben-sample", "rita-sample", "dora-minta", "crane-works", "northwind"]


def test_an_entity_is_in_the_meeting_once() -> None:
    found = meeting.entities(family("Crane Works and Ben Sample, again."), KNOWN, make_config())
    assert [e.id for e in found].count("crane-works") == 1
    assert [e.id for e in found].count("ben-sample") == 1


def test_an_entity_a_list_holds_by_any_form_is_left_out_whole() -> None:
    config = make_config()
    # Dóra by her contact id alone, Northwind by its id, Rita by a keyword in her alias.
    config.never_store = NeverStore(people=["dora@example.invalid"], keywords=["northwind", "S."])
    found = meeting.entities(family(), KNOWN, config)
    assert [e.id for e in found] == ["ben-sample", "crane-works"]
    assert meeting.listed(DORA, config)
    assert not meeting.listed(BEN, config)


def test_the_glossary_is_every_active_entity_but_people_that_no_list_holds() -> None:
    config = make_config()
    assert [e.id for e in meeting.glossary(KNOWN, config)] == ["crane-works", "northwind"]
    config.never_store = NeverStore(keywords=["Crane Works"])
    assert [e.id for e in meeting.glossary(KNOWN, config)] == ["northwind"]


def test_forms_are_the_names_then_the_asr_aliases_or_every_alias() -> None:
    assert meeting.forms(DORA) == ["Dóra Minta", "Dóri"]
    assert meeting.forms(RITA) == ["Rita Sample"]
    assert meeting.forms(RITA, asr=False) == ["Rita Sample", "Rita S."]
