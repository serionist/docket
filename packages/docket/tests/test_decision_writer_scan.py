"""A decision's status has one writer (DR-0564), scanned over the whole package as test_settings_scan.py scans for
vault.yaml's: `DocStore.decide`, which only the Briefs page's click calls, writes the next rev
`decision_form.status_rev` builds, and nothing else calls either. doc_put writes proposed decisions only, and the
take-in refuses a status edited by hand; test_decisions.py holds those."""

from __future__ import annotations

import ast
from pathlib import Path

from docket.vault import docstore

SRC = Path(docstore.__file__).resolve().parents[1]


def _calls(name: str) -> set[tuple[str, str]]:
    """Where a function or method of this name is called: each file and the top-level function or class around it."""
    found: set[tuple[str, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"))
        for top in module.body:
            where = top.name if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else ""
            for node in ast.walk(top):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
                if called == name:
                    found.add((path.relative_to(SRC).as_posix(), where))
    return found


def test_only_the_briefs_page_s_click_writes_a_decision_s_status() -> None:
    assert _calls("decide") == {("web/briefs.py", "router")}
    assert _calls("status_rev") == {("vault/docstore.py", "DocStore")}
