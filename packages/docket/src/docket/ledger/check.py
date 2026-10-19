"""What `docket vault check` says about the ledger (DR-0617, DR-0696), and the rule a write shares with it: a committed
op line is never changed or removed, but to its mask (DR-0610, DR-0693).

The check names what the reading and the fold left out, a number two items hold, a tracked op file that lost or
changed a line HEAD holds, and a stream's STATUS.md that lags the ledger or a hand edit holds (DR-0675). HEAD alone is
compared, so a commit that removes lines, as an import's revert does before it runs again, is no problem. A forget's
apply is the one change a committed line may take: to its mask, its op id on the forgotten list, a twin of it that
masking made gone. A file changed only so is a forget whose commit hasn't landed, and a listed op's line that isn't its
mask yet is a forget not finished here; the next gate call finishes either. Op files are read raw here, never through
the masking parse, and lines are named by number, never by their text.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import TYPE_CHECKING

from docket.ledger import fold, ops, status, store
from docket.vault import forgotten

if TYPE_CHECKING:
    from docket.vault.vault import Vault

CHANGED = "a committed op line changed or went; ops are written once, so put it back from git"
MASKED = "a forget masked listed op lines and isn't committed yet; the next gate call commits it"
UNMASKED = "a forget isn't finished here: this op is listed and its line isn't masked yet; the next gate call masks it"


def _gone(vault: Vault, rel: str) -> tuple[list[str], set[str]]:
    """The lines HEAD holds in an op file that the file lacks now, and the file's lines."""
    path = vault.root / rel
    now = set(path.read_bytes().decode("utf-8", errors="replace").split("\n")) if path.is_file() else set[str]()
    return [line for line in vault.git.show_at_head(rel).split("\n") if line.strip() and line not in now], now


def _masked(line: str, now: set[str], listed: Collection[str]) -> bool:
    """Whether a committed line went only to its mask: its op id listed, and its mask in the file (DR-0696)."""
    return ops.id_of(line) in listed and ops.mask(line) in now


def committed_kept(vault: Vault, rel: str, listed: Collection[str] = frozenset()) -> bool:
    """Whether every line HEAD holds in an op file is still in it, or, its op id in `listed`, its mask is: a file may
    only grow, and a forget masks a line in place."""
    gone, now = _gone(vault, rel)
    return all(_masked(line, now, listed) for line in gone)


def unmasked(root: Path, listed: Collection[str]) -> list[tuple[str, str]]:
    """Each line of an op file whose op id is listed and that isn't its mask yet, by its number: a forget the next gate
    call finishes here (DR-0696)."""
    out: list[tuple[str, str]] = []
    for rel, path in ops.listing(root)[0] if listed else []:
        text = ops.utf8(path)
        if text is not None:
            out += [(f"{rel}:{n}", UNMASKED) for n, raw, masked in ops.listed_lines(text, listed) if raw != masked]
    return out


def notes(vault: Vault) -> list[str]:
    """What `docket vault check` says beside its problems: calendar records whose meetings a stopped step left for the
    next one (DR-0647). The session-start day says the same line."""
    waiting = len(store.calendar_pending(vault))
    return [f"{waiting} calendar record(s) wait for their meetings; `docket ledger calendar`"] if waiting else []


def problems(vault: Vault) -> list[tuple[str, str]]:
    """The ledger's problems, each as (where, what)."""
    listed = forgotten.load(vault.root, vault.home).ops
    ledger = fold.load(vault.root, masked=listed)
    found = list(ledger.problems)
    for rel in vault.git.changed_against_head(ops.OPS_DIR):
        gone, now = _gone(vault, rel)
        if gone:
            found.append((rel, MASKED if all(_masked(line, now, listed) for line in gone) else CHANGED))
    found += unmasked(vault.root, listed)
    return found + status.problems(vault, ledger.streams.values())
