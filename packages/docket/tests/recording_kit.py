"""An invented vault for the Recording page: recordings kept as Plaud's mapper keeps them, each a recording, its
transcript and its notes as three records. Built once per session; each test copies it.

| Rid | What |
|---|---|
| `plaud:rec-crane` | a call with minutes and a recording note on its transcript, and a second transcript under it |
| `plaud:rec-labels` | a call no doc covers |
| `plaud:rec-old` | a call only a legacy note covers |
| `plaud:rec-held` | held: on the hold list, nothing in the raw log |
| `plaud:rec-tour` | a call with a stretch cut; `with_audio` stores its audio, cut, and Whisper's transcript |
| `mail:<crane-1@harbour.example>` | a mail, not a recording |
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import make_config, make_record, template_with
from media_kit import tone

from docket import recall
from docket.media import crypto, layout
from docket.media import put as media
from docket.media.keystore import KeyStore
from docket.vault import cursors, holds
from docket.vault.config import SourceConfig
from docket.vault.cuts import CUT_MARKER
from docket.vault.gate import Gate
from docket.vault.processed import DocMeta, ProcessedDoc
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

CRANE = "plaud:rec-crane"
POLISHED = f"{CRANE}/transaction_polish"
RAW = f"{CRANE}/transaction"
LABELS = "plaud:rec-labels"
OLD = "plaud:rec-old"
HELD = "plaud:rec-held"
TOUR = "plaud:rec-tour"
TOUR_POLISHED = f"{TOUR}/transaction_polish"
WHISPER = "whisper-large-v3@edaa852 faster-whisper-1.2.1"
TOURED = [
    ("Ada Example", "Welcome to the yard tour."),
    ("Ben Sample", "The new gate opens on Friday."),
    ("Ben Sample", "We talked about something else here."),
    ("Ada Example", "The fence is done by the end of the month."),
]
# What Whisper heard: its own lines and boundaries, none in the cut stretch but its [cut] over it.
HEARD = [
    (500, 9_000, "Welcome to the yard tour."),
    (10_000, 18_000, "Thanks for coming."),
    (20_500, 38_000, "The new gate opens on Friday."),
    (40_000, 59_000, "[cut]"),
    (60_500, 78_000, "The fence is done by the end of the month."),
]
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


def tour() -> list[RawRecord]:
    """A call whose third line was cut by hand: its transcript keeps `[cut]` over the stretch."""
    under = {"source": "plaud", "container": TOUR, "thread": TOUR, "author": None}
    when = datetime(2026, 10, 14, 8, 30, tzinfo=UTC)
    said = lines(TOURED)
    said[2] = {"start_ms": 40_000, "end_ms": 59_000, "text": CUT_MARKER}
    text = "\n".join(f"{s['speaker']}: {s['text']}" if s.get("speaker") else s["text"] for s in said)
    return [
        make_record(rid=TOUR, kind="recording", title="Yard tour", text="", ts=when, **under),
        make_record(
            rid=TOUR_POLISHED,
            kind="transcript",
            segments=said,
            text=text,
            ts=when,
            engine="plaud/transaction_polish",
            fidelity="cut",
            **under,
        ),
    ]


def store_tone(vault: Vault, rid: str, seconds: float) -> Path:
    """A tone stored as a plaud recording's audio, through media_put and the gate, with a key store in a file of this
    vault's own: the stored file."""
    KeyStore(vault.home).init("file")
    cursors.save(vault, "plaud", cursors.Cursor())
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "tone.mp3").write_bytes(tone(seconds))
    media.put(vault, rid, path=str(drop / "tone.mp3"))
    Gate(vault).promote()
    [stored] = layout.stored_files(vault.root, rid)
    return stored


def with_audio(vault: Vault) -> None:
    """The tour's audio stored, cut where its transcript is, and Whisper's transcript of it in raw: a key store in a
    file of this test's own, so each test that copies the vault does this itself."""
    stored = store_tone(vault, TOUR, 80.0)
    under = {"source": "plaud", "container": TOUR, "thread": TOUR, "author": None}
    heard = [{"start_ms": s, "end_ms": e, "text": text} for s, e, text in HEARD]
    whisper = make_record(
        rid=f"{TOUR}/whisper", kind="transcript", segments=heard, text="\n".join(h["text"] for h in heard),
        ts=datetime(2026, 10, 14, 8, 30, tzinfo=UTC), engine=WHISPER, via=f"docket transcribe {crypto.key_id(stored)}",
        fidelity="derived", to=["Ada Example", "Ben Sample"], **under,
    )  # fmt: skip
    vault.write_run("plaud", [whisper])


def recording_sources() -> dict[str, SourceConfig]:
    return {"plaud": SourceConfig(sensitivity="mixed"), "mail": SourceConfig()}


def build_recording_vault(root: Path) -> Vault:
    config = make_config()
    config.sources = recording_sources()
    vault = Vault.init(root / "vault", config, "test-box")
    fill_recording_vault(vault)
    return vault


def fill_recording_vault(vault: Vault) -> None:
    """The calls, the mail, the docs and the hold above, into a vault with plaud, mixed, and mail as sources."""
    labels = [("Ada Example", "The pallet labels print too small."), ("Ben Sample", "I order a new printer today.")]
    records = [
        *call(CRANE, "Harbour crane check", SAID, 12, "transaction_polish", "transaction"),
        *call(LABELS, "Pallet labels", labels, 13, "transaction_polish"),
        *call(OLD, "Yard walk", labels, 5, "transaction_polish"),
        *tour(),
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


def recording_template(factory: pytest.TempPathFactory) -> Path:
    """The vault above, built once per session; tests copy it."""
    return template_with(factory, build_recording_vault)
