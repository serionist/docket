"""The invented vault the question set runs on: an invented small publisher of English and Hungarian translations.

Its records, docs and entities are fixture files under fixtures/eval-vault, written to be read: a question's answer
can be checked there. Seeded routine messages and mails in both languages are added in code: they share the
questions' words and hold none of their answers, so a question has something to be confused by (DR-0294).
"""

from __future__ import annotations

import random
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from docket_testkit import files_in, index_config, template_with

from docket.vault.records import RawRecord
from docket.vault.vault import Vault

EVAL_FIXTURE = Path(__file__).parent / "fixtures" / "eval-vault"
EVAL_QUESTIONS = EVAL_FIXTURE / "questions.yaml"
FETCHED = {"fetched": "2026-10-14T08:00:00Z", "via": "fixture"}
LAST_SEGMENT_MS = 30_000
FILLER_CHAT = 100
FILLER_MAIL = 50
FILLER_FROM = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
FILLER_TO = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)

# Routine office talk over the questions' words. None of it answers a question.
EN_THINGS = [
    "printer", "paper", "translator", "cover", "money", "budget", "invoice", "shipment", "delivery", "warehouse",
    "books", "booth", "poster", "audiobook", "studio", "voice", "e-book", "price", "discount", "contract", "proof",
    "picture", "novel", "printing", "release", "party", "edition", "copies", "distributor", "fair",
]  # fmt: skip
HU_THINGS = [
    "nyomda", "papír", "fordító", "borító", "pénz", "költségvetés", "számla", "szállítás", "raktár", "könyvek",
    "stand", "plakát", "hangoskönyv", "stúdió", "ár", "kedvezmény", "szerződés", "korrektúra", "kép", "regény",
    "nyomtatás", "megjelenés", "ünnepség", "kiadás", "példányok", "terjesztő", "vásár", "előleg", "határidő", "terem",
]  # fmt: skip
EN_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
HU_DAYS = ["hétfőn", "kedden", "szerdán", "csütörtökön", "pénteken"]
PEOPLE = ["Ben", "Dóra", "Cleo", "Ada"]
EN_LINES = [
    "Can someone check the {thing} folder before {day}?",
    "The {thing} list is in the shared folder, please keep it tidy.",
    "{person} is out on {day}; ask {other} about anything {thing} related.",
    "Thanks, the {thing} notes look fine to me.",
    "Reminder: a short {thing} catch-up on {day} at ten.",
    "Who looks after the {thing} paperwork this time?",
    "Filed the {thing} receipts under last month.",
    "No news on the {thing} side today.",
    "I moved the {thing} files into the archive folder.",
    "Is the {thing} spreadsheet up to date, or should I wait until {day}?",
]
HU_LINES = [
    "Megnézi valaki {day} a {thing} mappát?",
    "A {thing} lista a közös mappában van, kérlek, tartsátok rendben.",
    "{person} {day} nincs bent, {thing} ügyben {other} segít.",
    "Köszönöm, a {thing} jegyzetek rendben vannak.",
    "Emlékeztető: rövid {thing} egyeztetés {day} tízkor.",
    "Ki intézi most a {thing} papírokat?",
    "A {thing} bizonylatokat a múlt hónaphoz tettem.",
    "Ma nincs újdonság {thing} ügyben.",
    "A {thing} fájlokat átraktam az archívumba.",
    "Friss a {thing} táblázat, vagy várjak {day}?",
]


def _ms(at: str) -> int:
    minutes, seconds = at.split(":")
    return (int(minutes) * 60 + int(seconds)) * 1000


def _segments(lines: list[str]) -> list[dict[str, Any]]:
    """`mm:ss | speaker | text` lines as segments, each lasting until the next starts, the last for 30 seconds."""
    parts = [[p.strip() for p in line.split("|", 2)] for line in lines]
    starts = [_ms(at) for at, _, _ in parts]
    ends = [*starts[1:], starts[-1] + LAST_SEGMENT_MS]
    return [
        {"start_ms": start, "end_ms": end, "speaker": speaker, "text": text}
        for (_, speaker, text), start, end in zip(parts, starts, ends, strict=True)
    ]


def _read(name: str) -> Any:
    return yaml.safe_load((EVAL_FIXTURE / "records" / name).read_text(encoding="utf-8"))


def _message(channel: str, line: str, thread: str | None) -> dict[str, Any]:
    native, ts, author, text = (p.strip() for p in line.split("|", 3))
    rid = f"chat:{channel}/{native}"
    record = {"rid": rid, "kind": "message", "container": f"chat:{channel}", "ts": ts, "author": author, "text": text}
    return {**record, "thread": thread} if thread else record


def fixture_records() -> dict[str, list[dict[str, Any]]]:
    """The hand-written records by source, as raw record fields."""
    calls = [
        {**r, "kind": r.get("kind", "recording"), "segments": _segments(r["segments"])} for r in _read("calls.yaml")
    ]
    mail = [{**r, "kind": "mail"} for r in _read("mail.yaml")]
    cal = [{**r, "kind": "event"} for r in _read("cal.yaml")]
    chat: list[dict[str, Any]] = []
    for channel, parts in _read("chat.yaml").items():
        chat += [_message(channel, line, None) for line in parts.get("messages", [])]
        for thread in parts.get("threads", []):
            parent = f"chat:{channel}/{thread[0].split('|', 1)[0].strip()}"
            chat += [_message(channel, line, parent) for line in thread]
    return {"calls": calls, "mail": mail, "cal": cal, "chat": chat}


def filler_records() -> dict[str, list[dict[str, Any]]]:
    """Seeded routine chat messages and mails in both languages, spread over September and October."""
    rng = random.Random(66)  # noqa: S311 - seeded test text, not secrets
    span = int((FILLER_TO - FILLER_FROM).total_seconds())

    def line(english: bool) -> str:
        person, other = rng.sample(PEOPLE, 2)
        return rng.choice(EN_LINES if english else HU_LINES).format(
            thing=rng.choice(EN_THINGS if english else HU_THINGS),
            day=rng.choice(EN_DAYS if english else HU_DAYS),
            person=person,
            other=other,
        )

    def when() -> str:
        return (FILLER_FROM + timedelta(seconds=rng.randrange(span))).strftime("%Y-%m-%dT%H:%M:%SZ")

    chat = [
        {
            "rid": f"chat:C0OFFICE/{1789000000 + n * 100}.000100", "kind": "message", "container": "chat:C0OFFICE",
            "ts": when(), "author": rng.choice(["U0BEN", "U0DORA", "U0CLEO", "U0ADA"]), "text": line(n % 2 == 0),
        }
        for n in range(FILLER_CHAT)
    ]  # fmt: skip
    mail = []
    for n in range(FILLER_MAIL):
        english = n % 2 == 0
        title = f"Office note {n}" if english else f"Irodai hírek {n}"
        body = "\n\n".join(line(english) for _ in range(3))
        mail.append(
            {
                "rid": f"mail:<office-{n}@kestrel.example>", "kind": "mail", "container": "mail:office",
                "ts": when(), "author": "Office <office@kestrel.example>", "title": title, "text": body,
            }
        )  # fmt: skip
    return {"chat": chat, "mail": mail}


def eval_records() -> dict[str, list[RawRecord]]:
    """Every record of the eval vault by source: the fixture's and the filler."""
    out: dict[str, list[RawRecord]] = {}
    filler = filler_records()
    for source, records in fixture_records().items():
        every = [*records, *filler.get(source, [])]
        out[source] = [RawRecord.model_validate({**r, "source": source, **FETCHED}) for r in every]
    return out


def build_eval_vault(root: Path) -> Vault:
    """The eval vault: each source's records in one run, the entity files, the docs in one commit."""
    from docket.vault.docstore import DocStore, PutResult

    vault = Vault.init(root / "vault", index_config(), "test-box")
    for source, records in sorted(eval_records().items()):
        vault.write_run(source, records, datetime(2026, 10, 14, 7, 0, tzinfo=UTC))
    shutil.copytree(EVAL_FIXTURE / "entities", vault.root / "entities", dirs_exist_ok=True)
    vault.commit(files_in(vault, "entities"), "Entities")
    docs = [p.read_text(encoding="utf-8") for p in sorted((EVAL_FIXTURE / "docs").glob("*.md"))]
    stored = DocStore(vault).put_many(docs, "Eval vault docs")
    assert all(isinstance(r, PutResult) for r in stored), stored
    return vault


def eval_template(factory: pytest.TempPathFactory) -> Path:
    """The eval vault, built once per session; tests copy it."""
    return template_with(factory, build_eval_vault)
