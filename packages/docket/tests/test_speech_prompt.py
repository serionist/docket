"""The vocabulary prompt a Hungarian recording gets (DR-0569): the family's people resolved to entities, their related
projects and the entities its records name, by their names and `asr` aliases; a name a never-store list holds or the
redaction rules would change is left out. Names and records are invented."""

from __future__ import annotations

from docket_testkit import make_config, make_record

from docket.speech import prompt
from docket.vault.config import NeverStore, Redaction, RedactionPattern
from docket.vault.entities import Alias, Entity
from docket.vault.records import RawRecord


def entity(
    eid: str,
    name: str,
    *,
    kind: str = "person",
    aliases: tuple[Alias, ...] = (),
    related: tuple[str, ...] = (),
) -> Entity:
    return Entity(
        id=eid,
        type=kind,
        path=f"entities/{eid}.md",
        names={"en": name},
        aliases=aliases,
        related={"project": related} if related else {},
    )


ENTITIES = [
    entity("ada-example", "Ada Example", aliases=(Alias("Ádá", "asr"), Alias("Ada E.")), related=("crane-works",)),
    entity("ben-sample", "Ben Sample"),
    entity("crane-works", "Crane Works", kind="project"),
    entity("dora-minta", "Dóra Minta", aliases=(Alias("Dóri", "asr"),)),
    entity("eve-other", "Eve Other"),
    entity("hidden-person", "Hidden Person"),
    entity("secretco", "Secretco Ltd", kind="org"),
    entity("pattern-project", "Zeta-1234", kind="project"),
]


def family(*, text: str = "Dóra Minta joins the review; Secretco Ltd sends the drawings.") -> list[RawRecord]:
    return [
        make_record(
            rid="plaud:rec-a", source="plaud", kind="recording", author="Ada Example", to=["Hidden Person"], text=""
        ),
        make_record(
            rid="plaud:rec-a/transaction_polish",
            source="plaud",
            kind="transcript",
            segments=[{"start_ms": 0, "end_ms": 900, "speaker": "Ben Sample", "text": text}],
            text=text,
        ),
    ]


def test_the_prompt_holds_the_family_s_people_their_projects_and_the_names_its_records_say() -> None:
    config = make_config()
    config.never_store = NeverStore(people=["Hidden Person"], keywords=["Secretco"])
    config.redaction = Redaction(patterns=[RedactionPattern(name="codes", pattern=r"Zeta-\d+")])
    found = prompt.vocabulary("hu", family(text="Zeta-1234 and Dóra Minta, Secretco Ltd too."), ENTITIES, config)
    # A plain alias finds the person but isn't said; the asr ones are. Hidden Person and Secretco are on the
    # never-store lists, and the redaction rules would change Zeta-1234: none of them goes in.
    assert found == "Résztvevők és témák: Ada Example, Ádá, Ben Sample, Crane Works, Dóra Minta, Dóri."


def test_an_entity_a_list_holds_by_one_name_is_left_out_with_every_other_form() -> None:
    config = make_config()
    # Dóra is listed by her name alone, and the second Ben by his id; their unlisted nickname, asr alias and name
    # stay out with it.
    config.never_store = NeverStore(people=["Dóra Minta", "Ben Other"])
    ben = entity("ben-other", "Benedek", aliases=(Alias("Bence", "asr"),))
    records = [*family(), make_record(rid="plaud:rec-a/notes", source="plaud", kind="note", text="Benedek joins.")]
    found = prompt.vocabulary("hu", records, [*ENTITIES, ben], config)
    assert found is not None
    assert "Dóri" not in found and "Dóra" not in found and "Bence" not in found and "Benedek" not in found
    assert "Ada Example" in found


def test_only_hungarian_gets_a_prompt_and_none_without_a_name() -> None:
    config = make_config()
    assert prompt.vocabulary("en", family(), ENTITIES, config) is None
    assert prompt.vocabulary(None, family(), ENTITIES, config) is None
    assert prompt.vocabulary("hu", family(), [], config) is None


def test_the_prompt_stays_short() -> None:
    many = [entity(f"p-{n}", f"Person Number{n:02d}") for n in range(60)]
    records = [
        make_record(
            rid="plaud:rec-b", source="plaud", kind="recording", to=[f"Person Number{n:02d}" for n in range(60)]
        )
    ]
    found = prompt.vocabulary("hu", records, many, make_config())
    assert found is not None and len(found) <= prompt.MAX_CHARS
    assert found.count(",") + 1 <= prompt.MAX_NAMES
    long = [entity(f"l-{n}", f"Name{n} " + "x" * 60) for n in range(20)]
    records = [make_record(rid="plaud:rec-c", source="plaud", kind="recording", to=[e.names["en"] for e in long])]
    found = prompt.vocabulary("hu", records, long, make_config())
    assert found is not None and len(found) <= prompt.MAX_CHARS and found.endswith(".")
