"""An invented vault for the Recording page: recordings kept as Plaud's mapper keeps them, each a recording, its
transcript and its notes as three records. Built once per session; each test copies it.

| Rid | What |
|---|---|
| `plaud:rec-crane` | a call with minutes and a recording note on its transcript, and a second transcript under it |
| `plaud:rec-labels` | a call no doc covers |
| `plaud:rec-old` | a call only a legacy note covers |
| `plaud:rec-held` | held: on the hold list, nothing in the raw log |
| `mail:<crane-1@harbour.example>` | a mail, not a recording |
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import make_config, make_record, template_with

from docket import recall
from docket.vault import holds
from docket.vault.config import SourceConfig
from docket.vault.processed import DocMeta, ProcessedDoc
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

CRANE = "plaud:rec-crane"
POLISHED = f"{CRANE}/transaction_polish"
RAW = f"{CRANE}/transaction"
LABELS = "plaud:rec-labels"
OLD = "plaud:rec-old"
HELD = "plaud:rec-held"
MAIL = "mail:<crane-1@harbour.example>"
MINUTES = "minutes:2026-10-12-harbour-crane-check"
NOTE = "recording-note:2026-10-12-harbour-crane-check"
LEGACY = "legacy-note:old-yard-walk"

SAID = [
    ("Ada Example", "Morning. Let's go through the crane check at the harbour."),
    ("Ben Sample", "The east crane passed the load test on Monday."),
    ("Ben Sample", "The west crane needs a new brake pad before it lifts again."),
    ("Ada Example", "How long does the part take?"),
    ("Ben Sample", "Ten days from the supplier, maybe twelve."),
    ("Ada Example", "Then we move the container work to the east crane until then."),
    ("Cleo Test", "I can rebook the night shift for that."),
    ("Ada Example", "Good. Who tells the shipping office?"),
    ("Cleo Test", "I send them the new plan by Thursday."),
    ("Ben Sample", "The inspector comes back on the twentieth."),
    ("Ada Example", "Let's keep that date and check the brake again the day before."),
    ("Ada Example", "That's all. Thanks, everyone."),
]


def lines(said: list[tuple[str, str]], step: int = 20_000) -> list[dict[str, Any]]:
    """Segments a turn each, `step` milliseconds apart, each ending a second before the next starts."""
    return [
        {"start_ms": i * step, "end_ms": (i + 1) * step - 1000, "speaker": who, "text": text}
        for i, (who, text) in enumerate(said)
    ]


def call(rid: str, title: str, said: list[tuple[str, str]], day: int, *blocks: str) -> list[RawRecord]:
    """A recording, a transcript per block and the recorder's own summary, as Plaud's mapper writes them."""
    under = {"source": "plaud", "container": rid, "thread": rid, "author": None}
    when = datetime(2026, 10, day, 8, 30, tzinfo=UTC)
    segments = lines(said)
    text = "\n".join(f"{who}: {words}" for who, words in said)
    return [
        make_record(rid=rid, kind="recording", title=title, text="", ts=when, **under),
        *(
            make_record(
                rid=f"{rid}/{b}", kind="transcript", segments=segments, text=text, ts=when, engine=f"plaud/{b}", **under
            )
            for b in blocks
        ),
        make_record(
            rid=f"{rid}/summary", kind="vendor-summary", text="## Summary\n\nThe crane check.", ts=when, **under
        ),
    ]


def doc(doc_id: str, covers: list[str], body: list[str]) -> str:
    head = [f"id: {doc_id}", f"type: {doc_id.partition(':')[0]}", "covers:", *(f"  - {rid}" for rid in covers)]
    head.append("by: claude-test, sync 2026-10-12T12:00")
    return "---\n" + "\n".join(head) + "\n---\n\n" + "\n".join(body) + "\n"


def build_recording_vault(root: Path) -> Vault:
    config = make_config()
    config.sources = {"plaud": SourceConfig(sensitivity="mixed"), "mail": SourceConfig()}
    vault = Vault.init(root / "vault", config, "test-box")
    labels = [("Ada Example", "The pallet labels print too small."), ("Ben Sample", "I order a new printer today.")]
    records = [
        *call(CRANE, "Harbour crane check", SAID, 12, "transaction_polish", "transaction"),
        *call(LABELS, "Pallet labels", labels, 13, "transaction_polish"),
        *call(OLD, "Yard walk", labels, 5, "transaction_polish"),
    ]
    vault.write_run("plaud", records)
    mail = make_record(
        rid=MAIL, source="mail", kind="mail", container="mail:crane", title="Crane check", text="The plan.",
        author="ben@example.invalid",
    )  # fmt: skip
    vault.write_run("mail", [mail])
    recall.doc_put(
        vault,
        doc(
            MINUTES,
            [POLISHED],
            ["# Minutes: harbour crane check", "", "- Decision: the east crane takes it. @01:40-02:19"],
        ),
    )
    recall.doc_put(
        vault,
        doc(
            NOTE,
            [POLISHED, RAW, MAIL],
            [
                "# Harbour crane check",
                "",
                "## Cranes",
                "",
                f"- The west crane needs a new brake pad; the part takes ten days. [r:{POLISHED}@00:40-01:39]",
                f"- The container work moves to the east crane meanwhile. [r:{POLISHED}@01:40-02:19]",
                "",
                "## Next steps",
                "",
                f"- Cleo sends the shipping office the new plan by Thursday. [r:{POLISHED}@02:40] [r:{MAIL}]",
                f"- The inspector comes back on the twentieth. [r:{RAW}@03:00-03:19]",
            ],
        ),
    )
    old = DocMeta(id=LEGACY, type="legacy-note", covers=[OLD], by="claude-test", old_paths=["notes/yard-walk.md"])
    recall.doc_put(vault, ProcessedDoc(old, "# Yard walk\n\nThe labels print too small.\n", 0).render())
    holds.add(vault, "plaud", [HELD.partition(":")[2]])
    return vault


def recording_template(factory: pytest.TempPathFactory) -> Path:
    """The vault above, built once per session; tests copy it."""
    return template_with(factory, build_recording_vault)
