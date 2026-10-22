"""An invented vault for the curate miner: recordings with speaker labels and misheard names, notes covering them that
write the names right, and mail. Built once per session; each test copies it.

| Name | Written | Heard |
|---|---|---|
| Cleo Test | a speaker's label in every recording; `Cleo` in the notes | `Cleo` in the transcripts |
| Northwind | in the notes and in mail | `Nortwin` in three recordings' transcripts |
| Dóra | in the notes | `Dórával` in two recordings' transcripts |
| Anna | a speaker's label in two recordings | |
| Rita Sample | a speaker's label in two recordings; `Sample Rita`, once, in a note | |
| Dora | in two mails, without the accent | |
| Zephyrine | | once, in one recording |
| Harbour Fair | in two mails, always so | |
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from docket_testkit import copied, make_config, make_record, note

from docket import index, recall
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

RECORDINGS = 4
NORTHWIND = "entities/customer/northwind.yaml"
ANN = "entities/person/ann-sample.yaml"


def _when(n: int) -> datetime:
    return datetime(2026, 10, 5, 9, 0, tzinfo=UTC) + timedelta(days=n)


def rid(n: int) -> str:
    return f"plaud:rec-{n:04d}"


def _said(n: int) -> list[dict[str, Any]]:
    heard = [
        ("Cleo Test", "Okay, so we start with the pallets for Nortwin today."),
        ("Speaker 2", "Yes. And then Cleo said the proofs go back on Monday."),
        ("Cleo Test", "Okay. I talked to them about the OK from Nortwin as well."),
        ("Speaker 2", "Tegnap beszéltem Dórával a borítóról, ok."),
    ]
    if n == 0:
        heard.append(("Speaker 2", "We called Zephyrine about it."))
    if n == 3:
        heard = [("Anna", "Okay, the agenda is short."), ("Speaker 2", "Fine, okay, then we stop.")]
        heard.append(("Rita Sample", "Thanks, all."))
    if n == 2:
        heard += [("Anna", "Agreed, okay, we go."), ("Rita Sample", "I send the files tonight.")]
    return [
        {"start_ms": i * 10_000, "end_ms": i * 10_000 + 9_000, "speaker": who, "text": text}
        for i, (who, text) in enumerate(heard)
    ]


def _records() -> list[RawRecord]:
    out: list[RawRecord] = []
    for n in range(RECORDINGS):
        under = {"source": "plaud", "container": rid(n), "thread": rid(n), "author": None, "ts": _when(n)}
        said = _said(n)
        text = "\n".join(f"{s['speaker']}: {s['text']}" for s in said)
        out += [
            make_record(rid=rid(n), kind="recording", title=f"Weekly call {n}", text="", **under),
            make_record(rid=f"{rid(n)}/transaction", kind="transcript", segments=said, text=text, **under),
        ]
    mails = [
        ("harbour-1", "Ada Example <ada@example.com>", "The stand at the Harbour Fair is booked; Northwind joins."),
        ("harbour-2", "Ben Sample <ben@example.com>", "We meet the printers at the Harbour Fair; then Dora checks."),
        ("harbour-3", "Ben Sample <ben@example.com>", "The Harbour Fair catalogue goes out, and Dora signs it."),
    ]
    for n, (key, author, text) in enumerate(mails):
        out.append(
            make_record(
                rid=f"mail:<{key}@example.com>",
                source="mail",
                kind="mail",
                container=f"mail:{key}",
                thread=f"mail:<{key}@example.com>",
                author=author,
                title="The fair",
                text=text,
                ts=_when(n + 10),
            )
        )
    return out


def _notes() -> list[str]:
    lines = {
        0: ["- Northwind wants the pallets; Cleo Test agreed. @00:00", "- Cleo sends the proofs. @00:10"],
        1: ["- Cleo Test and Dóra on the cover; Northwind waits. @00:30"],
        2: ["- Dóra had the cover; Northwind confirmed the order. @00:30", "- Cleo Test takes the minutes. @00:00"],
        3: ["- A short call; Sample Rita joined late. @00:00"],
    }
    return [
        note(f"recording-note:2026-10-0{5 + n}-weekly-{n}", covers=[f"{rid(n)}/transaction"], lines=body)
        for n, body in lines.items()
    ]


def fill_curate(vault: Vault) -> None:
    """The recordings, mail and notes above, written into a vault, and its index built."""
    records = _records()
    vault.write_run("plaud", [r for r in records if r.source == "plaud"])
    vault.write_run("mail", [r for r in records if r.source == "mail"])
    for text in _notes():
        recall.doc_put(vault, text)
    index.rebuild(vault)


def build_curate_vault(folder: Path) -> Path:
    vault = Vault.init(folder / "vault", make_config(), "test-box")
    fill_curate(vault)
    return vault.root


def curate_vault(tmp_path: Path) -> Vault:
    """The invented vault above, with its index, copied for one test."""
    return Vault.open(copied(tmp_path, build_curate_vault, "curate-vault"))


def more_recordings(vault: Vault, heard: Sequence[str], noted: Sequence[str] = ()) -> None:
    """More recordings, `plaud:rec-x000` on, each a transcript of one turn by `Speaker 1`, and a note covering each of
    the first ones, its one line from `noted`; the index brought up to date."""
    more_calls(vault, [[("Speaker 1", text)] for text in heard], noted)


def more_calls(vault: Vault, calls: Sequence[Sequence[tuple[str, str]]], noted: Sequence[str] = ()) -> None:
    """More recordings, `plaud:rec-x000` on, each a transcript of its turns as (speaker, text), and a note covering each
    of the first ones, its one line from `noted`; the index brought up to date."""
    records: list[RawRecord] = []
    for n, turns in enumerate(calls):
        key = f"plaud:rec-x{n:03d}"
        under = {"source": "plaud", "container": key, "thread": key, "author": None, "ts": _when(20 + n)}
        said = [
            {"start_ms": i * 10_000, "end_ms": i * 10_000 + 9_000, "speaker": who, "text": text}
            for i, (who, text) in enumerate(turns)
        ]
        text = "\n".join(f"{who}: {words}" for who, words in turns)
        records.append(make_record(rid=f"{key}/transaction", kind="transcript", segments=said, text=text, **under))
    vault.write_run("plaud", records)
    for n, text in enumerate(noted):
        covers, lines = [f"plaud:rec-x{n:03d}/transaction"], [f"- {text} @00:00"]
        recall.doc_put(vault, note(f"recording-note:2026-11-{n + 1:02d}-extra-{n}", covers=covers, lines=lines))
    index.update(vault)


def entity(vault: Vault, rel: str, text: str) -> None:
    """An entity file, committed, and the index brought up to date."""
    path = vault.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    vault.commit([rel], f"seed {rel}")
    index.update(vault)
