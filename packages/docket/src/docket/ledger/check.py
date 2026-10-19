"""What `docket vault check` says about the ledger (DR-0617), and the rule a write shares with it: a committed op line
is never changed or removed (DR-0610).

The check names what the reading and the fold left out, a number two items hold, a tracked op file that lost or
changed a line HEAD holds, and a stream's STATUS.md that lags the ledger or a hand edit holds (DR-0675). HEAD alone is
compared, so a commit that removes lines, as an import's revert does before it runs again, is no problem. A forget's
apply (#212) will be the one change a committed line may take, explained by the forgotten list; until it lands, every
such change is named.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docket.ledger import fold, ops, status

if TYPE_CHECKING:
    from docket.vault.vault import Vault


def committed_kept(vault: Vault, rel: str) -> bool:
    """Whether every line HEAD holds in an op file is still in it: a file may only grow."""
    path = vault.root / rel
    now = set(path.read_bytes().decode("utf-8", errors="replace").split("\n")) if path.is_file() else set[str]()
    return all(line in now for line in vault.git.show_at_head(rel).split("\n") if line.strip())


def problems(vault: Vault) -> list[tuple[str, str]]:
    """The ledger's problems, each as (where, what)."""
    ledger = fold.load(vault.root)
    found = list(ledger.problems)
    found += [
        (rel, "a committed op line changed or went; ops are written once, so put it back from git")
        for rel in vault.git.changed_against_head(ops.OPS_DIR)
        if not committed_kept(vault, rel)
    ]
    return found + status.problems(vault, ledger.streams.values())
