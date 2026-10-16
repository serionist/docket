"""The door's scan over the whole package (DR-0506), as test_release.py scans for the release's writers: vault.yaml is
written in one place, its general entry is called by the CLI's two writers alone, those writers by the command line
alone, and the web and the service import from vault.settings only what pages may use."""

from __future__ import annotations

import ast
from pathlib import Path

from docket.vault import settings

SRC = Path(settings.__file__).resolve().parents[1]


def _modules() -> list[tuple[str, ast.Module]]:
    return [
        (p.relative_to(SRC).as_posix(), ast.parse(p.read_text(encoding="utf-8"))) for p in sorted(SRC.rglob("*.py"))
    ]


def _called(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _calls(name: str) -> set[tuple[str, str]]:
    """Where a function of this name is called: each file and the top-level function or class around the call."""
    found: set[tuple[str, str]] = set()
    for rel, module in _modules():
        for top in module.body:
            where = top.name if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else ""
            for node in ast.walk(top):
                if isinstance(node, ast.Call) and _called(node) == name:
                    found.add((rel, where))
    return found


def test_vault_yaml_is_written_only_by_its_one_writer() -> None:
    assert _calls("write_config") == {("vault/settings.py", "rewrite")}


def test_the_writer_s_general_entry_is_called_only_by_the_cli_s_two_writers() -> None:
    assert _calls("rewrite") == {("vault/redaction.py", "set_rule"), ("sources/scaffold.py", "_register")}


def test_the_writers_that_can_loosen_are_called_only_by_the_command_line() -> None:
    for name in ("set_rule", "new_source", "new_folder_source"):
        assert {rel for rel, _ in _calls(name)} == {"cli.py"}, name


def test_pages_and_the_service_import_only_what_pages_may_use() -> None:
    for rel, module in _modules():
        if not rel.startswith(("web/", "service/")):
            continue
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                names = {a.name for a in node.names}
                if node.module == "docket.vault.settings":
                    assert names <= settings.FOR_PAGES, (rel, names - settings.FOR_PAGES)
                assert not (node.module == "docket.vault" and "settings" in names), rel
                assert not (node.module == "docket.sources" and "scaffold" in names), rel
                assert node.module != "docket.sources.scaffold", rel
                assert not (node.module == "docket.vault.redaction" and "set_rule" in names), rel
                assert not (node.module == "docket.vault.config" and "write_config" in names), rel
            if isinstance(node, ast.Import):
                imported = {a.name for a in node.names}
                assert not imported & {"docket.vault.settings", "docket.sources.scaffold"}, rel


def test_what_pages_may_import_is_there_and_never_the_general_entry() -> None:
    assert "rewrite" not in settings.FOR_PAGES
    assert all(hasattr(settings, name) for name in settings.FOR_PAGES)
