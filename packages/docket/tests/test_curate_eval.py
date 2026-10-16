"""The invented question set before and after one round: the eval vault's entities taken away, a round started on what
is left, and every card accepted as it is, as a user agreeing with each would.

What one round does, measured when this was built (recall at 10 / MRR):

| Kind | hand-written entities | no entities | one round |
|---|---|---|---|
| exact-name | 1.00 / 0.88 | 1.00 / 0.81 | 1.00 / 0.88 |
| inflection | 0.93 / 0.85 | 0.93 / 0.85 | 0.93 / 0.89 |
| typo | 0.75 / 0.46 | 0.75 / 0.51 | 0.75 / 0.48 |

Typo's recall can't move here: its misses are misspellings in the question itself, which no alias reaches. Its MRR falls
a little, as with the hand-written entities, since a name in a query becomes a lift.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import copy_vault
from eval_vault import EVAL_QUESTIONS, eval_template

from docket import index
from docket.curate import batch as curate
from docket.questions import EvalReport, load, run
from docket.vault.vault import Vault


@pytest.fixture
def vault(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Vault:
    v = copy_vault(eval_template(tmp_path_factory), tmp_path)
    for kind in ("person", "org", "product"):
        shutil.rmtree(v.root / "entities" / kind)
    index.update(v)
    return v


def measure(vault: Vault) -> EvalReport:
    index.update(vault)
    return run(vault, load(EVAL_QUESTIONS, ZoneInfo(vault.config.owner.time_zone)))


def test_one_accepted_round_finds_names_as_well_as_the_hand_written_entities(vault: Vault) -> None:
    none = measure(vault)
    batch = curate.start(vault)
    assert [c.name for c in batch.cards[:4]] == ["Ada Example", "Dóra Minta", "Cleo Test", "Ben Sample"]
    curate.accept(vault, batch, batch.cards)
    one = measure(vault)
    assert one.kinds["exact-name"].mrr > none.kinds["exact-name"].mrr
    assert one.kinds["inflection"].mrr > none.kinds["inflection"].mrr
    assert one.all.mrr > none.all.mrr
    for kind, score in none.kinds.items():
        assert one.kinds[kind].recall >= score.recall, kind
