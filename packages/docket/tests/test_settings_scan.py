"""The door's scan over the whole package (DR-0506), as test_release.py scans for the release's writers: vault.yaml is
written in one place, its general entry is called by the CLI's two writers alone, those writers by the command line
alone, the web and the service import from vault.settings only what pages may use, and write vault.yaml no other
way: no settings dumped whole, no file write in code that names the file."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

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


def _writes(node: ast.AST) -> bool:
    """A file written here: `write_text`, `write_bytes`, or `open` with a mode that writes, builtin or a path's."""
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        name = _called(call)
        if name in ("write_text", "write_bytes"):
            return True
        if name == "open":
            at = 0 if isinstance(call.func, ast.Attribute) else 1
            modes = [call.args[at]] if len(call.args) > at else []
            modes += [k.value for k in call.keywords if k.arg == "mode"]
            if any(
                isinstance(m, ast.Constant) and isinstance(m.value, str) and set(m.value) & set("wax+") for m in modes
            ):
                return True
    return False


def _names_the_file(node: ast.AST) -> bool:
    """Code that names vault.yaml: `CONFIG_FILE`, or the name itself as a path's part."""
    for found in ast.walk(node):
        if isinstance(found, ast.Name) and found.id == "CONFIG_FILE":
            return True
        if isinstance(found, ast.Attribute) and found.attr == "CONFIG_FILE":
            return True
        if isinstance(found, ast.Constant) and found.value == "vault.yaml":
            return True
    return False


def _direct_writes(src: Path) -> list[str]:
    """In web/ and service/: each settings object dumped whole (`.dump()` with no argument), and each function, or a
    module's own statements, that both names vault.yaml and writes a file."""
    found: list[str] = []
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src).as_posix()
        if not rel.startswith(("web/", "service/")):
            continue
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and _called(node) == "dump" and not node.args and not node.keywords:
                found.append(f"{rel}:{node.lineno}: a .dump() of the settings")
        defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        units: list[tuple[str, ast.AST]] = [
            (rel, ast.Module(body=[s for s in module.body if not isinstance(s, defs)], type_ignores=[]))
        ]
        units += [
            (f"{rel}:{n.lineno}", n) for n in ast.walk(module) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        found += [
            f"{where}: writes a file and names vault.yaml"
            for where, unit in units
            if _names_the_file(unit) and _writes(unit)
        ]
    return found


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


def test_the_writer_s_general_entry_is_called_only_by_the_cli_s_two_writers_and_the_stricter_operations() -> None:
    assert _calls("rewrite") == {
        ("vault/redaction.py", "set_rule"),
        ("sources/scaffold.py", "_register"),
        ("vault/settings.py", "add_listed"),
    }


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


def test_pages_and_the_service_write_vault_yaml_no_other_way() -> None:
    assert _direct_writes(SRC) == []


SNEAKY = {
    "both": """
from docket.vault.config import CONFIG_FILE, VaultConfig


def credentials_off(vault):
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path)
    looser = config.model_copy(update={"redaction": config.redaction.model_copy(update={"credentials": False})})
    path.write_text(looser.dump(), encoding="utf-8", newline="\\n")
""",
    "a write, the text built elsewhere": """
import yaml


def phone_off(vault, data):
    with (vault.root / "vault.yaml").open("w", encoding="utf-8") as out:
        out.write(yaml.safe_dump(data))
""",
    "a dump handed on": """
def credentials_off(config, put):
    put(config.model_copy(update={"redaction": {"credentials": False}}).dump())
""",
    "at the module's top": """
from pathlib import Path

from docket.vault.config import CONFIG_FILE

Path(CONFIG_FILE).write_bytes(b"redaction: {credentials: false}")
""",
}


@pytest.mark.parametrize("how", sorted(SNEAKY))
def test_a_page_that_writes_vault_yaml_around_the_writer_is_caught(how: str, tmp_path: Path) -> None:
    """The reviewer's probe: a module of the web that writes vault.yaml without write_config or rewrite, as Vault.init
    and the test kit write it, passes every other row; this one catches it."""
    sneaky = tmp_path / "docket" / "web" / "sneaky.py"
    sneaky.parent.mkdir(parents=True)
    sneaky.write_text(SNEAKY[how], encoding="utf-8")
    assert _direct_writes(tmp_path / "docket"), how


def test_a_file_written_elsewhere_or_named_in_words_isn_t_caught(tmp_path: Path) -> None:
    calm = tmp_path / "docket" / "service" / "calm.py"
    calm.parent.mkdir(parents=True)
    calm.write_text(
        '''
def history(path, line):
    """Appends a line; the vault's settings live in vault.yaml, which this never touches."""
    with path.open("a", encoding="utf-8") as out:
        out.write(line)


def read(vault, CONFIG_FILE):
    return (vault.root / CONFIG_FILE).read_text(encoding="utf-8")
''',
        encoding="utf-8",
    )
    assert _direct_writes(tmp_path / "docket") == []


def test_what_pages_may_import_is_there_and_never_the_general_entry() -> None:
    assert "rewrite" not in settings.FOR_PAGES
    assert all(hasattr(settings, name) for name in settings.FOR_PAGES)
