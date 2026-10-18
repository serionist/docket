"""An invented call with both transcripts, the recorder's and Whisper's, for reconciling (#183): its people are the
test kit's cast, and Northwind, the customer, has the `asr` alias Nortwin.

| Line | The recorder heard | Whisper heard |
|---|---|---|
| 1 | the offer from Nortwin | the offer from Northwood: a name |
| 2 | ten thousand for the first batch | ten million: a number |
| 3 | Forty two units | 42 units: one number, written two ways |
| 4 | the offer is ten and the deadline is Friday | the new offer is ten: a word |

`build_reconcile_vault` keeps it in a vault this machine ingests `calls` in, with the entities committed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import make_config, make_record, template_with

from docket.vault import cursors
from docket.vault.cuts import CUT_MARKER
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

REC = "calls:rec-0101"
TRANSCRIPT = f"{REC}/transcript"
WHISPER = f"{REC}/whisper"
ENGINE = "whisper-large-v3@edaa852 faster-whisper-1.2.1"
VIA = "docket transcribe age1test"
# 00:30 on 15 October in the owner's time zone, Budapest; still the 14th in UTC.
WHEN = datetime(2026, 10, 14, 22, 30, tzinfo=UTC)
# (speaker, start, end, what the recorder heard, what Whisper heard)
CALL: list[tuple[str, int, int, str, str]] = [
    ("Ben Sample", 0, 5_000, "Morning, Rita. The offer from Nortwin came on Monday.",
     "Morning, Rita. The offer from Northwood came on Monday."),
    ("Rita Sample", 6_000, 11_000, "They want ten thousand for the first batch.",
     "They want ten million for the first batch."),
    ("Ben Sample", 12_000, 17_000, "Forty two units, and they ship in May.", "42 units, and they ship in May."),
    ("Dóra Minta", 18_000, 23_000, "So the offer is ten and the deadline is Friday.",
     "So the new offer is ten and the deadline is Friday."),
]  # fmt: skip
ENTITIES = {
    "entities/customer/northwind.yaml": "id: northwind\ntype: customer\nname: {en: Northwind}\n"
    "aliases:\n  - {text: Nortwin, kind: asr}\n  - {text: Northwind Traders}\n",
    "entities/person/ben-sample.yaml": "id: ben-sample\ntype: person\nname: {en: Ben Sample}\n",
    "entities/person/rita-sample.yaml": "id: rita-sample\ntype: person\nname: {en: Rita Sample}\n",
    "entities/person/dora-minta.yaml": "id: dora-minta\ntype: person\nname: {en: Dóra Minta}\n"
    "aliases:\n  - {text: Dóri}\n",
    "entities/project/crane-works.yaml": "id: crane-works\ntype: project\nname: {en: Crane Works}\n"
    "aliases:\n  - {text: CW Ltd}\n",
}


def segment(start: int, end: int, text: str, speaker: str | None = None) -> dict[str, Any]:
    return {"start_ms": start, "end_ms": end, "text": text, **({"speaker": speaker} if speaker else {})}


def transcript(lines: list[dict[str, Any]], rid: str = TRANSCRIPT, lang: str | None = "en", **fields: Any) -> RawRecord:
    """The recorder's transcript, under the recording."""
    container = rid.rpartition("/")[0]
    text = "\n".join(f"{s['speaker']}: {s['text']}" if s.get("speaker") else s["text"] for s in lines)
    base: dict[str, Any] = {"source": "calls", "kind": "transcript", "container": container, "thread": container}
    base |= {"author": None, "ts": WHEN, "engine": "recorder", "lang": lang}
    return make_record(rid=rid, segments=lines, text=text, **(base | fields))


def heard(lines: list[dict[str, Any]], rid: str = WHISPER, lang: str | None = "en") -> RawRecord:
    """Whisper's transcript, as `docket transcribe` stores it."""
    record = transcript(lines, rid, lang, engine=ENGINE, fidelity="derived", to=["Ben Sample", "Rita Sample"])
    return record.model_copy(update={"via": VIA})


def recording(rid: str = REC, title: str = "Supplier call", ts: datetime = WHEN) -> RawRecord:
    return make_record(
        rid=rid, source="calls", kind="recording", title=title, text="", ts=ts, author=None, container=None
    )


def pair(call: list[tuple[str, int, int, str, str]] = CALL, rec: str = REC) -> tuple[RawRecord, RawRecord]:
    """A call's two transcripts, under the recording `rec`."""
    mine = [segment(start, end, said, who) for who, start, end, said, _ in call]
    theirs = [segment(start, end, other) for _, start, end, _, other in call]
    return transcript(mine, f"{rec}/transcript"), heard(theirs, f"{rec}/whisper")


def cut(record: RawRecord, start: int, end: int) -> RawRecord:
    """A transcript's next version with a stretch cut: its segments there gone, `[cut]` over them."""
    kept = [s for s in record.segments or [] if s.end_ms <= start or s.start_ms >= end]
    lines = [s.model_dump(exclude_none=True) for s in kept] + [segment(start, end, CUT_MARKER)]
    lines.sort(key=lambda s: s["start_ms"])
    return transcript(lines, record.rid, record.lang, engine=record.engine, fidelity="cut").model_copy(
        update={"via": record.via, "to": record.to}
    )


def build_reconcile_vault(root: Path) -> Vault:
    config = make_config()
    vault = Vault.init(root / "vault", config, "test-box")
    for rel, text in ENTITIES.items():
        (vault.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault.root / rel).write_text(text, encoding="utf-8", newline="\n")
    vault.commit(list(ENTITIES), "The call's entities")
    vault.write_run("calls", [recording(), *pair()])
    cursors.save(vault, "calls", cursors.Cursor())
    return vault


def reconcile_template(factory: pytest.TempPathFactory) -> Path:
    """The vault above, built once per session; tests copy it."""
    return template_with(factory, build_reconcile_vault)
