"""An invented vault for the Briefs page: the index fixture with four briefs on it. Built once per session; each test
copies it.

| Brief | Its state |
|---|---|
| `brief:northwind` | stale: a line cites the supplier note's line its rev 2 changed; another cites a mail |
| `brief:ben-sample` | stale: a chat record about Ben came after it |
| `brief:harbour-site` | current: its lines cite chat messages |
| `brief:dora-minta` | orphaned: Dóra's entity went, and Cleo holds her name now |
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from docket_testkit import INDEX_FIXTURE, OFFER, build_index_vault, make_record, template_with

from docket import recall
from docket.vault.docstore import DocStore
from docket.vault.vault import Vault

NOTE = "recording-note:2026-10-07-supplier-call"
CALL = "calls:rec-0100"
TEN = "- The consultant asks ten million forints for the quarter, above the budget. @00:30-01:50"
DEADLINE = "- Dóra agrees the deadline with Ben on Friday. @02:30-03:20"
COLD = "chat:C0OPS/1791400000.000100"
ORDERED = "chat:C0OPS/1791400800.000100"
NORTHWIND = "brief:northwind"
BEN = "brief:ben-sample"
HARBOUR = "brief:harbour-site"
DORA = "brief:dora-minta"
# The chat record about Ben that came after his brief.
ABOUT_BEN = make_record(901, text="Ben Sample confirmed the Friday deadline.")


def brief(entity: str, title: str, covers: Sequence[str], sections: Sequence[Sequence[str]]) -> str:
    """A brief's text: the frontmatter, the title, and the three sections' lines."""
    head = [f"id: brief:{entity}", "type: brief", "covers:", *(f"  - {rid}" for rid in covers)]
    head += ["by: claude-test, brief 2026-10-15", f"entities: [{entity}]"]
    body = [f"# {title}"]
    for name, lines in zip(("What changed", "Last conversations", "Decisions"), sections, strict=True):
        body += ["", f"## {name}", "", *lines]
    return "---\n" + "\n".join(head) + "\n---\n\n" + "\n".join(body) + "\n"


def cite(vault: Vault, text: str) -> str:
    doc = DocStore(vault).get(NOTE)
    assert doc is not None
    return f"[d:{NOTE}@{doc.meta.rev}#{next(n for n, line in doc.lines() if line == text)}]"


def build_briefs_vault(root: Path) -> Vault:
    vault = build_index_vault(root)
    fill_briefs(vault)
    return vault


def fill_briefs(vault: Vault) -> None:
    """The four briefs, the supplier note's next rev, a chat about Ben after his brief and Dóra's entity gone, into a
    vault the index fixture filled."""
    briefs = [
        brief(
            "northwind",
            "Northwind",
            [CALL, OFFER],
            [
                [
                    f"- The consultant's price is above the budget. {cite(vault, TEN)}",
                    f"- The offer comes by mail. [r:{OFFER}#1]",
                ],
                [
                    f"- 7 Oct, supplier call: Cleo sends the offer in writing by Friday. [r:{CALL}@04:00-05:00]",
                    f"- Dóra said she agrees the deadline with Ben on Friday. {cite(vault, DEADLINE)}",
                ],
                [],
            ],
        ),
        brief(
            "ben-sample",
            "Ben Sample",
            [CALL],
            [
                [f"- Ben wants the deadline agreed on Friday. {cite(vault, DEADLINE)}"],
                [f"- 7 Oct, supplier call: the price is above the budget. [r:{CALL}@00:30-01:50]"],
                [],
            ],
        ),
        brief(
            "harbour-site",
            "Harbour site",
            [COLD, ORDERED],
            [
                [f"- The label printer by dock two fails in the cold. [r:{COLD}]"],
                [f"- A cold model was ordered, the chat said. [r:{ORDERED}]"],
                [],
            ],
        ),
        brief(
            "dora-minta", "Dóra Minta", [], [[f"- She agrees the deadline on Friday. {cite(vault, DEADLINE)}"], [], []]
        ),
    ]
    for text in briefs:
        recall.doc_put(vault, text)
    # The supplier note's next rev changes the line the Northwind brief cites first.
    note = (INDEX_FIXTURE / "docs" / "03-note-supplier.md").read_text(encoding="utf-8")
    recall.doc_put(
        vault,
        note.replace("type: recording-note\n", "type: recording-note\nrev: 1\n").replace(
            "ten million", "twelve million"
        ),
    )
    vault.write_run("chat", [ABOUT_BEN])
    vault.commit([], "Raw: a chat about Ben")
    # Dóra's entity goes, and Cleo holds her name now.
    people = vault.root / "entities" / "person"
    cleo = people / "cleo-test.yaml"
    cleo.write_text(cleo.read_text(encoding="utf-8") + "aliases:\n  - {text: Dóra Minta}\n", encoding="utf-8")
    (people / "dora-minta.yaml").unlink()
    vault.commit(["entities/person/cleo-test.yaml", "entities/person/dora-minta.yaml"], "Dóra is Cleo")


def briefs_template(factory: pytest.TempPathFactory) -> Path:
    """The vault above, built once per session; tests copy it."""
    return template_with(factory, build_briefs_vault)
