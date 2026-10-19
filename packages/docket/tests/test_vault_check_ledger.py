"""`docket vault check` on the ledger (DR-0617, DR-0696): what it names in the op files, that a sound ledger passes,
and after a forget, a line changed to its mask explained by its op's id on the list and one not masked yet named."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from docket_testkit import empty_vault, git, make_config, sh
from forget_kit import forget
from ledger_kit import ADD_FIELDS, NOW, meeting, op, ulid, write_ops

from docket.ledger import check, ops, store
from docket.ledger.ops import Op
from docket.vault.vault import Vault

FILE = "ledger/ops/2026-10-test-box.jsonl"


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return empty_vault(tmp_path, make_config())


def ledger_problems(vault: Vault) -> list[tuple[str, str]]:
    return [(p.where, p.message) for p in vault.check() if p.where.startswith(("ledger", "A-"))]


def test_a_sound_ledger_passes(vault: Vault) -> None:
    store.write(vault, [{"op": "add", "fields": dict(ADD_FIELDS)}, {"op": "done", "item": "A-0001"}], now=NOW)
    store.write(vault, [{"op": "reopen", "item": "A-0001"}], now=NOW)
    assert ledger_problems(vault) == []
    assert check.problems(vault) == []


def test_every_line_the_fold_leaves_out_is_named(vault: Vault) -> None:
    folder = vault.root / "ledger/ops"
    write_ops(vault.root, [
        op("add", 1, machine="test-box"),
        op("add", 2, number=1, machine="test-box"),
        op("log", 3, 9, note="no add", machine="test-box"),
        op("log", 4, 5, note="before its add", machine="test-box"),
        op("add", 5, machine="test-box"),
        op("undo", 6, 1, of=ulid(40), machine="test-box"),
    ])  # fmt: skip
    path = vault.root / FILE
    misplaced = op("log", 7, 1, note="x", machine="test-box", day=date(2026, 9, 30)).text()
    twin = op("log", 8, 1, note="one text", machine="test-box")
    other = op("log", 8, 1, note="another text", machine="test-box")
    path.write_text(path.read_text(encoding="utf-8") + f"not json\n{misplaced}\n{twin.text()}\n{other.text()}\n",
                    encoding="utf-8", newline="\n")  # fmt: skip
    (folder / "notes.txt").write_text("", encoding="utf-8")
    messages = dict(ledger_problems(vault))
    assert messages == {
        "ledger/ops/notes.txt": "isn't an op file (<yyyy-mm>-<machine>.jsonl); skipped",
        f"{FILE}:7": "isn't a ledger op of schema 1; skipped",
        f"{FILE}:8": "belongs in ledger/ops/2026-09-test-box.jsonl, by its day and machine; skipped",
        f"{FILE}:10, {FILE}:9": f"op {ulid(8)} is on 2 texts; each is kept, ordered by its text",
        f"{FILE}:6": "an undo of an op that isn't an earlier one of its item, or of a renumber; left out",
        f"{FILE}:3": "names an item no add made; left out",
        f"{FILE}:4": "sorts before its item's add; left out",
        "A-0001": f"held by two items; the next write gives {ulid(2)} a new number",
    }


def test_meetings_points_and_streams_the_fold_leaves_out_are_named(vault: Vault) -> None:
    """What the meetings' fold names reaches `docket vault check` too (DR-0617, DR-0620 to DR-0623)."""
    write_ops(vault.root, [
        meeting(1, machine="test-box"),
        meeting(2, number=1, machine="test-box"),
        op("point_set", 3, 1, point=ulid(77), fields={"state": "answered"}, machine="test-box"),
        op("pin", 4, 4, machine="test-box"),
        op("log", 5, 4, note="on a later Docket's object", machine="test-box"),
        op("add", 6, machine="test-box"),
        op("point", 7, 6, fields={"text": "On an item"}, machine="test-box"),
        op("triage", 8, 1, verdict="me", machine="test-box"),
        op("point", 9, 1, fields={"text": "Dates"}, machine="test-box"),
        op("point_set", 10, 1, point=ulid(9), fields={"state": "rolled", "to": ulid(78)}, machine="test-box"),
    ])  # fmt: skip
    assert dict(ledger_problems(vault) + [(p.where, p.message) for p in vault.check() if p.where == "M-0001"]) == {
        f"{FILE}:3": "names no point of its meeting; left out",
        f"{FILE}:7": "an item takes no point; left out",
        f"{FILE}:8": "a meeting takes no triage; left out",
        f"{FILE}:10": "a roll whose to names no point and no meeting; left out",
        "M-0001": f"held by two meetings; the next write gives {ulid(2)} a new number",
    }


def test_a_committed_line_changed_or_gone_is_named(vault: Vault) -> None:
    store.write(vault, [{"op": "add", "fields": dict(ADD_FIELDS)}], now=NOW)
    path = vault.root / FILE
    text = path.read_text(encoding="utf-8")
    path.write_text(text + op("log", 99, 1, note="grown", machine="test-box").text() + "\n", encoding="utf-8",
                    newline="\n")  # fmt: skip
    assert not [p for p in ledger_problems(vault) if p[0] == FILE]
    path.write_text(text.replace("Send Ada", "Send Ben"), encoding="utf-8", newline="\n")
    gone = (FILE, "a committed op line changed or went; ops are written once, so put it back from git")
    assert gone in ledger_problems(vault)
    path.unlink()
    assert gone in ledger_problems(vault)


def test_op_files_that_lose_the_vault_s_union_merge_are_named(vault: Vault) -> None:
    (vault.root / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (vault.root / ".git" / "info" / "attributes").write_text("ledger/** -merge\n", encoding="utf-8")
    named = [p for p in vault.check() if p.where.startswith("ledger/ops/")]
    assert len(named) == 1
    assert "merge" in named[0].message
    assert sh(vault.root, "git", "check-attr", "merge", "--", "ledger/ops/2026-01-m.jsonl").strip().endswith("unset")


# -- after a forget (DR-0696) ---------------------------------------------------------------------------------------


def logged(vault: Vault) -> tuple[Op, str]:
    """An item with a log line, committed: the log's op and the file's text."""
    store.write(vault, [{"op": "add", "fields": dict(ADD_FIELDS)}], now=NOW)
    (log,) = store.write(vault, [{"op": "log", "item": "A-0001", "note": "Asked Ada"}], now=NOW).ops
    return log, (vault.root / FILE).read_text(encoding="utf-8")


def test_a_line_changed_to_its_mask_is_a_forget_not_committed_only_once_its_op_is_listed(vault: Vault) -> None:
    log, text = logged(vault)
    path = vault.root / FILE
    masked = text.replace(log.text(), ops.mask(log.text()))
    path.write_text(masked, encoding="utf-8", newline="\n")
    assert ledger_problems(vault) == [(FILE, check.CHANGED)]
    with pytest.raises(store.LedgerError, match="lost or changed a committed op line"):
        store.write(vault, [{"op": "log", "item": "A-0001", "note": "Booked"}], now=NOW)
    forget(vault, ops=[log.id])
    assert ledger_problems(vault) == [(FILE, check.MASKED)]
    # Changed to anything but its mask, it is named as before.
    path.write_text(text.replace("Asked Ada", "Asked Ben"), encoding="utf-8", newline="\n")
    assert (FILE, check.CHANGED) in ledger_problems(vault)
    # As committed, its text there: a forget not finished, by its line.
    path.write_text(text, encoding="utf-8", newline="\n")
    assert ledger_problems(vault) == [(f"{FILE}:2", check.UNMASKED)]
    # A write after a stopped apply commits the mask with its own op.
    path.write_text(masked, encoding="utf-8", newline="\n")
    store.write(vault, [{"op": "log", "item": "A-0001", "note": "Booked"}], now=NOW)
    assert ledger_problems(vault) == []


def test_a_twin_a_merge_committed_gone_beside_its_mask_is_explained(vault: Vault) -> None:
    log, text = logged(vault)
    forget(vault, ops=[log.id])
    path = vault.root / FILE
    path.write_text(f"{text}{ops.mask(log.text())}\n", encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-q", "-m", "Merge the forget", "--", FILE)
    assert ledger_problems(vault) == [(f"{FILE}:2", check.UNMASKED)]
    kept = text.replace(f"{log.text()}\n", "")
    path.write_text(f"{kept}{ops.mask(log.text())}\n", encoding="utf-8", newline="\n")
    assert ledger_problems(vault) == [(FILE, check.MASKED)]
    assert check.committed_kept(vault, FILE, {log.id}) and not check.committed_kept(vault, FILE)
