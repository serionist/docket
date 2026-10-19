"""Ops have one writer, `ledger/store.py` (DR-0618), scanned over the whole package as test_decision_writer_scan.py
scans for a decision's status: only the store's three writes commit ops, `write`, the calendar step's `write_calendar`
(#200) and the import's `write_import` (#202), only the store writes a file in the ledger's package, only the forget's
apply writes masks through it (#212), and the op folder is spelled only where the layout names it, its name read
by the history rewrite, which reads every blob the folder's files have had (DR-0697)."""

from __future__ import annotations

import ast
from pathlib import Path

from docket.ledger import store

SRC = Path(store.__file__).resolve().parents[1]


def _uses(name: str) -> set[tuple[str, str]]:
    """Where a name is called or read: each file and the top-level function or class around it."""
    found: set[tuple[str, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"))
        for top in module.body:
            where = top.name if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else ""
            for node in ast.walk(top):
                used = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else ""
                if used == name:
                    found.add((path.relative_to(SRC).as_posix(), where))
    return found


def test_only_the_writer_commits_ops() -> None:
    assert _uses("commit_ops") == {
        ("ledger/store.py", "write"), ("ledger/store.py", "write_calendar"), ("ledger/store.py", "write_import"),
    }  # fmt: skip


def test_only_the_forget_s_apply_writes_masks() -> None:
    """The one change a committed line takes is to its mask, by the apply, which lists it first (DR-0693)."""
    assert _uses("write_masks") == {("forget/apply.py", "_apply")}


def test_only_the_store_writes_a_file_in_the_ledger_s_package() -> None:
    assert {where for where in _uses("write_replacing") if where[0].startswith("ledger/")} == {
        ("ledger/store.py", "commit_ops"),
        ("ledger/store.py", "_save_calendar_pending"),
        ("ledger/store.py", "write_masks"),
    }
    for path in sorted((SRC / "ledger").glob("*.py")):
        if path.name != "store.py":
            text = path.read_text(encoding="utf-8")
            assert "write_text" not in text and "write_bytes" not in text and ".open(" not in text, path.name


def test_the_op_folder_is_spelled_only_where_the_layout_names_it() -> None:
    spelled = {
        p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py") if "ledger/ops" in p.read_text(encoding="utf-8")
    }
    assert spelled == {"ledger/ops.py", "vault/vault.py"}
    assert {where[0] for where in _uses("OPS_DIR")} == {
        "ledger/ops.py", "ledger/store.py", "ledger/check.py", "forget/rewrite.py",
    }  # fmt: skip
