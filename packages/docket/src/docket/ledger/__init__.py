"""The ledger: items owed and awaited, meetings with their talking points, and streams, kept as ops in the vault and
folded into their state and history (#198, #199, #201).

| Module | What |
|---|---|
| `ops` | the op line, its files, how a line reads, the objects adds make, and the ids (DR-0610 to DR-0613, DR-0620) |
| `fold` | items from their ops: undo, archive, numbers; `load`, the vault's ledger read and folded |
| `meetings` | meetings, points and streams from theirs: keys, copies, M- numbers (DR-0621 to DR-0625) |
| `store` | the one writer of ops, a batch's changes among them (DR-0639 to DR-0642) |
| `batch` | a batch's ops read from JSON, and what a write wrote as the `ledger_batch` tool's result and lines |
| `views` | the `ledger` tool's reads: snapshot, show, queue, list, meetings and suggest (DR-0638, DR-0660) |
| `check` | what `docket vault check` says about the ledger (DR-0617) |

The fold's tables in the index, which the writer and the reads ask, are `docket.index.ledger` (DR-0616).
"""

from docket.ledger.fold import load

__all__ = ["load"]
