"""The ledger: items owed and awaited, kept as ops in the vault and folded into their state and history (#198).

| Module | What |
|---|---|
| `ops` | the op line, its files, how a line reads, and the ids (DR-0610 to DR-0613) |
| `fold` | items from their ops: undo, archive, numbers; `load`, the vault's ledger read and folded |
| `store` | the one writer of ops |
| `check` | what `docket vault check` says about the ledger (DR-0617) |
"""

from docket.ledger.fold import load

__all__ = ["load"]
