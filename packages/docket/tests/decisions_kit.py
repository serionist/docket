"""An invented vault for the Briefs page's decisions: the briefs vault with decisions on the fleet-management project,
which has no brief. Built once per session; each test copies it. Each confirm and reject is the Briefs page's own click,
`DocStore.decide`.

| Decision | Where it stands |
|---|---|
| `decision:2026-10-05-pilot-first` | superseded by the dashboard decision |
| `decision:2026-10-10-dashboard-first` | current: it replaced the pilot decision |
| `decision:2026-10-12-daily-review` | rejected |
| `decision:2026-10-13-pilot-again` | proposed, contradicting the dashboard decision |
| `decision:2026-10-14-weekly-review` | proposed |
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from briefs_kit import build_briefs_vault
from docket_testkit import template_with

from docket import recall
from docket.vault.docstore import DocStore
from docket.vault.vault import Vault

SUBJECT = "fleet-management"
BRIEF = f"brief:{SUBJECT}"
FLEET = "recording-note:2026-10-13-fleet-review"
SUPPLIER = "recording-note:2026-10-07-supplier-call"
MAP = "- The dashboard shows each robot's battery and position on one map. @00:00-00:40"
ALERTS = "- Alerts go to the night shift by text message. @01:30-02:10"
DEADLINE = "- Dóra agrees the deadline with Ben on Friday. @02:30-03:20"
FRIDAY = "- Cleo sends the offer in writing by Friday. @04:00-05:00"
LABELS = "- The pallet labels at the harbour need a new printer. @07:00-08:00"
PILOT = "decision:2026-10-05-pilot-first"
DASHBOARD = "decision:2026-10-10-dashboard-first"
DAILY = "decision:2026-10-12-daily-review"
AGAIN = "decision:2026-10-13-pilot-again"
WEEKLY = "decision:2026-10-14-weekly-review"


def cite(vault: Vault, doc_id: str, text: str) -> str:
    doc = DocStore(vault).get(doc_id)
    assert doc is not None
    return f"[d:{doc_id}@{doc.meta.rev}#{next(n for n, line in doc.lines() if line == text)}]"


def candidate(doc_id: str, title: str, line: str, head: Sequence[str] = ()) -> str:
    front = [
        f"id: {doc_id}",
        "type: decision",
        "covers: []",
        "by: claude-test, decision 2026-10-14",
        f"entities: [{SUBJECT}]",
        "status: proposed",
        f"subject: {SUBJECT}",
        *head,
    ]
    return "---\n" + "\n".join(front) + f"\n---\n\n# Proposed: {title}\n\n{line}\n"


def propose(vault: Vault, doc_id: str, title: str, said: str, head: Sequence[str] = ()) -> None:
    recall.doc_put(vault, candidate(doc_id, title, f"- Said on the call. {said}", head))


def click(vault: Vault, doc_id: str, pick: str) -> None:
    store = DocStore(vault)
    doc = store.get(doc_id)
    assert doc is not None
    store.decide(doc_id, doc.meta.rev, pick)


def build_decisions_vault(root: Path) -> Vault:
    vault = build_briefs_vault(root)
    fill_decisions(vault)
    return vault


def fill_decisions(vault: Vault) -> None:
    """The five decisions on fleet management, each confirm and reject the page's own click, into a vault the briefs
    kit filled."""
    propose(vault, PILOT, "the pilot comes first", cite(vault, FLEET, MAP))
    click(vault, PILOT, "confirm")
    propose(
        vault, DASHBOARD, "the dashboard ships before the pilot", cite(vault, FLEET, ALERTS), [f"replaces: [{PILOT}]"]
    )
    click(vault, DASHBOARD, "confirm")
    propose(vault, DAILY, "a review every day", cite(vault, SUPPLIER, LABELS))
    click(vault, DAILY, "reject")
    propose(
        vault,
        AGAIN,
        "the pilot comes first after all",
        cite(vault, SUPPLIER, DEADLINE),
        [f"contradicts: [{DASHBOARD}]"],
    )
    propose(vault, WEEKLY, "a review every week", cite(vault, SUPPLIER, FRIDAY))


def decisions_template(factory: pytest.TempPathFactory) -> Path:
    """The vault above, built once per session; tests copy it."""
    return template_with(factory, build_decisions_vault)
